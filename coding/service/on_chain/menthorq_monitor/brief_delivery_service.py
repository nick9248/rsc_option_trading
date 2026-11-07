"""
Telegram delivery of the MenthorQ-style monitor: urgent alerts (after each
hourly run), the weekly brief and the on-demand brief (/brief).

Sending reuses ``TelegramAlertService`` (one attempt, never raises). A
message counts as delivered only when Telegram accepted it; only then is it
written to ``menthorq_alert_deliveries``. That table drives the 12 h alert
cooldown and the once-per-week rule of the weekly brief, and it is the
retry: an undelivered weekly brief is tried again at the next hourly run
(within ``WEEKLY_BRIEF_CATCH_UP``), an undelivered alert is re-evaluated at
the next run.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from coding.core.analytics.menthorq_monitor.alert_rules import (
    AlertThresholds,
    apply_cooldown,
    evaluate_urgent_alerts,
)
from coding.core.analytics.menthorq_monitor.level_record import (
    MODE_DAILY_CLOSE,
    MODE_LIVE,
    MenthorqLevelRecord,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    COMPARISON_ONE_DAY,
    COMPARISON_PREVIOUS,
)
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    escape,
    format_brief,
    format_urgent_alert,
)
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from coding.service.scanner.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)

TELEGRAM_PARSE_MODE = "HTML"
DELIVERY_KEY_WEEKLY_BRIEF = "weekly_brief"
DELIVERY_KEY_ON_DEMAND_BRIEF = "on_demand_brief"
# A weekly brief missed (service down, Telegram failing) is still sent this
# long after its scheduled time, never later.
WEEKLY_BRIEF_CATCH_UP = timedelta(hours=24)


def latest_weekly_brief_instant(now: datetime, settings: MenthorqMonitorSettings) -> datetime:
    """The newest scheduled weekly-brief instant at or before ``now`` (UTC)."""
    utc_now = now.astimezone(timezone.utc)
    days_back = (utc_now.weekday() - settings.weekly_brief_weekday) % 7
    candidate = datetime.combine(
        (utc_now - timedelta(days=days_back)).date(), settings.weekly_brief_time_utc,
        tzinfo=timezone.utc)
    if candidate > utc_now:
        candidate -= timedelta(days=7)
    return candidate


class MenthorqBriefDeliveryService:
    """Builds and sends the monitor's Telegram messages."""

    def __init__(
        self,
        monitor_service: MenthorqMonitorService,
        telegram: TelegramAlertService,
        thresholds: AlertThresholds = AlertThresholds(),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            monitor_service: Record access (and its repository for the
                delivery log).
            telegram: Sender (its chat is the owner chat).
            thresholds: Urgent-alert thresholds and cooldown.
            clock: Wall clock (UTC-aware) for the delivery time.
        """
        self.monitor_service = monitor_service
        self.repository = monitor_service.repository
        self.settings = monitor_service.settings
        self.telegram = telegram
        self.thresholds = thresholds
        self.clock = clock

    def build_brief(self, now: datetime, title: str) -> Optional[str]:
        """
        The brief from the newest stored live record at or before ``now``,
        or None when none is stored.
        """
        latest = self.monitor_service.latest_record(MODE_LIVE, now)
        if latest is None:
            return None
        return format_brief(
            latest,
            self.monitor_service.build_comparisons(latest),
            self.monitor_service.latest_record(MODE_DAILY_CLOSE, now),
            title,
        )

    def _deliver(self, key: str, message: str, record_id: Optional[int]) -> bool:
        if not self.telegram.send(message, parse_mode=TELEGRAM_PARSE_MODE):
            logger.warning("MenthorQ monitor: %s NOT delivered (Telegram send failed)", key)
            return False
        self.repository.save_menthorq_alert_delivery(
            self.settings.currency, key, self.clock(), message, record_id)
        logger.info("MenthorQ monitor: %s delivered", key)
        return True

    def send_urgent_alerts(self, latest: MenthorqLevelRecord, now: datetime) -> List[str]:
        """
        Evaluate the urgent rules on ``latest`` and send ONE message with
        every alert not on cooldown.

        Returns:
            Keys of the alerts delivered (empty when none fired, all were on
            cooldown, or the send failed).
        """
        if latest.spot is None:
            logger.info("MenthorQ monitor: no alerts evaluated (latest record not computed)")
            return []
        comparisons = {comparison.label: comparison
                       for comparison in self.monitor_service.build_comparisons(latest)}
        alerts = evaluate_urgent_alerts(
            comparisons[COMPARISON_PREVIOUS], comparisons[COMPARISON_ONE_DAY], self.thresholds)
        last_delivered = {
            alert.key: self.repository.get_last_menthorq_alert_delivery(latest.currency, alert.key)
            for alert in alerts
        }
        due = apply_cooldown(alerts, last_delivered, now, self.thresholds.cooldown)
        skipped = sorted({alert.key for alert in alerts} - {alert.key for alert in due})
        if skipped:
            logger.info("MenthorQ monitor: alerts on cooldown (not repeated): %s", skipped)
        if not due:
            return []
        message = format_urgent_alert(latest, due)
        if not self.telegram.send(message, parse_mode=TELEGRAM_PARSE_MODE):
            logger.warning("MenthorQ monitor: urgent alerts %s NOT delivered (Telegram send failed)",
                           [alert.key for alert in due])
            return []
        delivered_at = self.clock()
        for alert in due:
            self.repository.save_menthorq_alert_delivery(
                latest.currency, alert.key, delivered_at, message, latest.record_id)
        logger.info("MenthorQ monitor: urgent alerts delivered: %s", [alert.key for alert in due])
        return [alert.key for alert in due]

    def send_weekly_brief_if_due(self, now: datetime) -> bool:
        """
        Send the weekly brief when its scheduled instant has passed (within
        ``WEEKLY_BRIEF_CATCH_UP``) and it was not delivered since then.

        Returns:
            True when a brief was delivered by this call.
        """
        scheduled = latest_weekly_brief_instant(now, self.settings)
        if now - scheduled > WEEKLY_BRIEF_CATCH_UP:
            return False
        last = self.repository.get_last_menthorq_alert_delivery(
            self.settings.currency, DELIVERY_KEY_WEEKLY_BRIEF)
        if last is not None and last >= scheduled:
            return False
        message = self.build_brief(now, "Weekly brief")
        if message is None:
            logger.warning("MenthorQ monitor: weekly brief due but no live record is stored")
            return False
        return self._deliver(DELIVERY_KEY_WEEKLY_BRIEF, message, None)

    def send_on_demand_brief(self, now: Optional[datetime] = None) -> bool:
        """Send the brief now (the /brief command); says so when no record exists."""
        now = now or self.clock()
        message = self.build_brief(now, "On-demand brief")
        if message is None:
            message = (f"<b>{escape(self.settings.currency)} MenthorQ-style levels</b>\n"
                       "No live record is stored yet.")
        return self._deliver(DELIVERY_KEY_ON_DEMAND_BRIEF, message, None)
