"""
Telegram delivery of the MenthorQ-style monitor: urgent alerts (after each
hourly run, unless muted with /mute), the weekly brief and the on-demand
brief (/brief).

Sending reuses ``TelegramAlertService`` (one attempt, never raises). A
message counts as delivered only when Telegram accepted it; only then is it
written to ``menthorq_alert_deliveries``. That table drives the 12 h alert
cooldown and the once-per-week rule of the weekly brief, and it is the
retry: an undelivered weekly brief is tried again at the next hourly run
(within ``WEEKLY_BRIEF_CATCH_UP``), an undelivered alert is re-evaluated at
the next run.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from coding.core.analytics.menthorq_monitor.alert_mute import is_muted
from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_DATA_STALE,
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
    format_stale_data_alert,
    format_urgent_alert,
    stale_data_warning,
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
# The scheduled weekly job and the hourly catch-up run in different scheduler
# threads; this makes "check, send, record" of the weekly brief one step.
WEEKLY_BRIEF_LOCK = threading.Lock()


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
        thresholds: Optional[AlertThresholds] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            monitor_service: Record access (and its repository for the
                delivery log and the mute state).
            telegram: Sender (its chat is the owner chat).
            thresholds: Urgent-alert rules and cooldown; the settings'
                ``alert_thresholds`` when omitted.
            clock: Wall clock (UTC-aware) for the delivery time.
        """
        self.monitor_service = monitor_service
        self.repository = monitor_service.repository
        self.settings = monitor_service.settings
        self.telegram = telegram
        self.thresholds = thresholds if thresholds is not None else self.settings.alert_thresholds
        self.clock = clock

    def alerts_muted(self, now: datetime) -> bool:
        """
        True while the owner's /mute is in force. When the state cannot be
        read (e.g. migration 032 not applied) it is logged and alerts are
        treated as NOT muted: a mute that cannot be read could not have been
        saved either, and an extra alert is safer than a silently lost one.
        """
        try:
            state = self.repository.get_menthorq_monitor_state(self.settings.currency)
        except Exception:
            logger.exception("MenthorQ monitor: mute state not readable; alerts treated as not muted")
            return False
        return is_muted(state, now)

    def build_brief(self, now: datetime, title: str) -> Optional[str]:
        """
        The brief from the newest stored live record at or before ``now``,
        or None when none is stored. The record's snapshot is also judged
        against ``now``, so a record stored long ago gets the DATA STALE line.
        """
        latest = self.monitor_service.latest_record(MODE_LIVE, now)
        if latest is None:
            return None
        return format_brief(
            latest,
            self.monitor_service.build_comparisons(latest),
            self.monitor_service.latest_record(MODE_DAILY_CLOSE, now),
            title,
            stale_limit=self.settings.stale_snapshot_limit,
            now=now,
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
        every alert not on cooldown. While muted nothing is sent: the held
        back keys are recorded in ``menthorq_monitor_state`` (not as
        delivered, so the cooldown does not start and a condition that
        still holds after the mute is reported then).

        When the data behind ``latest`` is stale (``stale_data_warning``
        with ``now``), the movement rules are NOT evaluated (their moves
        would be spurious); one ``data_stale`` alert is sent instead, under
        the same cooldown and mute rules.

        Returns:
            Keys of the alerts delivered (empty when none fired, all were on
            cooldown, alerts are muted, or the send failed).
        """
        if latest.spot is None:
            logger.info("MenthorQ monitor: no alerts evaluated (latest record not computed)")
            return []
        stale_warning = stale_data_warning(latest, self.settings.stale_snapshot_limit, now)
        if stale_warning is not None:
            logger.warning("MenthorQ monitor: data stale (%s); movement alerts paused", stale_warning)
            last = self.repository.get_last_menthorq_alert_delivery(latest.currency, ALERT_KEY_DATA_STALE)
            if last is not None and now - last < self.thresholds.cooldown:
                logger.info("MenthorQ monitor: alerts on cooldown (not repeated): %s",
                            [ALERT_KEY_DATA_STALE])
                return []
            return self._send_urgent(latest, [ALERT_KEY_DATA_STALE],
                                     format_stale_data_alert(latest, stale_warning), now)
        comparisons = {comparison.label: comparison
                       for comparison in self.monitor_service.build_comparisons(latest)}
        alerts = evaluate_urgent_alerts(
            latest, comparisons[COMPARISON_PREVIOUS], comparisons[COMPARISON_ONE_DAY], self.thresholds)
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
        return self._send_urgent(latest, [alert.key for alert in due],
                                 format_urgent_alert(latest, due), now)

    def _send_urgent(
        self, latest: MenthorqLevelRecord, keys: List[str], message: str, now: datetime
    ) -> List[str]:
        """
        Send one urgent ``message`` for ``keys`` unless muted (then the keys
        are recorded as held back), and record each key as delivered when
        Telegram accepted it.

        Returns:
            ``keys`` when delivered, else empty.
        """
        if self.alerts_muted(now):
            logger.info("MenthorQ monitor: urgent alerts %s NOT sent (muted)", keys)
            try:
                self.repository.save_menthorq_suppressed_alerts(latest.currency, keys, self.clock())
            except Exception:
                logger.exception("MenthorQ monitor: could not record the muted alerts")
            return []
        if not self.telegram.send(message, parse_mode=TELEGRAM_PARSE_MODE):
            logger.warning("MenthorQ monitor: urgent alerts %s NOT delivered (Telegram send failed)", keys)
            return []
        delivered_at = self.clock()
        for key in keys:
            self.repository.save_menthorq_alert_delivery(
                latest.currency, key, delivered_at, message, latest.record_id)
        logger.info("MenthorQ monitor: urgent alerts delivered: %s", keys)
        return list(keys)

    def send_weekly_brief_if_due(self, now: datetime) -> bool:
        """
        Send the weekly brief when its scheduled instant has passed (within
        ``WEEKLY_BRIEF_CATCH_UP``) and it was not delivered since then.
        Check, send and record run under ``WEEKLY_BRIEF_LOCK``, so the
        scheduled job and the hourly catch-up never both send it.

        Returns:
            True when a brief was delivered by this call.
        """
        scheduled = latest_weekly_brief_instant(now, self.settings)
        if now - scheduled > WEEKLY_BRIEF_CATCH_UP:
            return False
        with WEEKLY_BRIEF_LOCK:
            last = self.repository.get_last_menthorq_alert_delivery(
                self.settings.currency, DELIVERY_KEY_WEEKLY_BRIEF)
            if last is not None and last >= scheduled:
                return False
            message = self.build_brief(now, "Weekly brief")
            if message is None:
                logger.warning("MenthorQ monitor: weekly brief due but no live record is stored")
                return False
            return self._deliver(DELIVERY_KEY_WEEKLY_BRIEF, message, None)

    def build_on_demand_brief(self, now: Optional[datetime] = None) -> str:
        """The /brief reply at ``now`` (the clock when omitted); says so when no record exists."""
        now = now or self.clock()
        message = self.build_brief(now, "On-demand brief")
        if message is None:
            message = (f"<b>{escape(self.settings.currency)} MenthorQ-style levels</b>\n"
                       "No live record is stored yet.")
        return message

    def deliver_on_demand_brief(self, message: str) -> bool:
        """Send a built /brief reply and record it as delivered when Telegram accepted it."""
        return self._deliver(DELIVERY_KEY_ON_DEMAND_BRIEF, message, None)

    def send_on_demand_brief(self, now: Optional[datetime] = None) -> bool:
        """Build and send the brief now; says so when no record exists."""
        return self.deliver_on_demand_brief(self.build_on_demand_brief(now))
