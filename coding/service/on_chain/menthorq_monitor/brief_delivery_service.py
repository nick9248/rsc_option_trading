"""
Telegram delivery of the MenthorQ-style monitor: urgent alerts (after each
hourly run, unless muted with /mute), the weekly brief and the on-demand
brief (/brief).

Sending reuses ``TelegramAlertService`` (one attempt, never raises). A
message counts as delivered only when Telegram accepted it; only then is it
written to ``menthorq_alert_deliveries`` (when that write fails after the
send, the delivery is logged at ERROR and kept in memory for the life of
the process, so it is not resent). That table drives the 12 h alert
cooldown and the once-per-week rule of the weekly brief, and it is the
retry of the weekly brief: an undelivered one is tried again at the next
hourly run (within ``WEEKLY_BRIEF_CATCH_UP``).

Each run evaluates the rules on the newest record. The state rules (the
24 h level moves, spot near a wall, data stale) are re-evaluated every run,
so one that failed or was muted is sent at a later run while its condition
still holds. The EVENT rules (net GEX sign flip, spot crossing HVL) compare
the newest record with the state last REPORTED to the owner (the newest
delivered alert of that event family in ``menthorq_alert_deliveries``):
an event whose send failed or that fell inside a mute is re-evaluated every
run and sent once it can be (cooldown, mute and stale rules as usual),
unless the state reverted to the reported one first.

A family with no delivered event yet (first-ever sign flip, first-ever HVL
crossing) is compared with the state about 24 h ago instead
(``event_baseline``: the reference record of the 24 h comparison, or the
oldest stored record within the last 24 h; records whose state is unknown
are skipped). So a first event that was muted or whose send failed is still
sent later while the state differs from that baseline. Remaining limit:
when no stored record older than the latest exists (e.g. the first hour of
a brand-new deployment) the trigger is the change since the previous run.

When the hourly computation itself raises (``send_monitor_error_alert``),
one ``monitor_error`` alert names the exception, under the same cooldown
and mute rules, so a persistent failure is not silent.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from coding.core.analytics.menthorq_monitor.alert_mute import is_muted
from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_DATA_STALE,
    ALERT_KEY_MONITOR_ERROR,
    EVENT_ALERT_STATES,
    EVENT_FAMILY_KEYS,
    AlertThresholds,
    EventBaseline,
    UrgentAlert,
    apply_cooldown,
    enabled_event_families,
    evaluate_urgent_alerts,
    event_baseline,
)
from coding.core.analytics.menthorq_monitor.level_record import (
    MODE_DAILY_CLOSE,
    MODE_LIVE,
    MenthorqLevelRecord,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    COMPARISON_ONE_DAY,
    COMPARISON_PREVIOUS,
    RecordComparison,
)
from coding.core.database.config import DatabaseConfig
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    escape,
    format_brief,
    format_monitor_error_alert,
    format_stale_data_alert,
    format_urgent_alert,
    no_usable_data_warning,
    stale_data_warning,
)
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from coding.service.notifications.telegram_alert_service import TelegramAlertService

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


def scrub_secrets(text: str, telegram: TelegramAlertService) -> str:
    """``text`` with the bot token and the database password replaced by placeholders."""
    scrubbed = telegram.scrub_token(text)
    database_password = DatabaseConfig().password
    if database_password:
        scrubbed = scrubbed.replace(database_password, "<database password>")
    return scrubbed


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


@dataclass(frozen=True)
class UrgentAlertEvaluation:
    """
    The urgent alerts of one record before the cooldown. ``stale_warning``
    is set when the record's data is stale or the record has no levels
    (not computed): then ``alerts`` is the single ``data_stale`` alert and
    the movement rules were not evaluated.
    """

    alerts: List[UrgentAlert]
    stale_warning: Optional[str]

    def message(self, latest: MenthorqLevelRecord, due: Sequence[UrgentAlert]) -> str:
        """The URGENT message for the ``due`` alerts (those that passed the cooldown)."""
        if self.stale_warning is not None:
            return format_stale_data_alert(latest, self.stale_warning)
        return format_urgent_alert(latest, due)


def evaluate_urgent_alerts_of_record(
    latest: MenthorqLevelRecord,
    comparisons: Callable[[], Sequence[RecordComparison]],
    thresholds: AlertThresholds,
    stale_limit: timedelta,
    now: Optional[datetime] = None,
    last_reported_states: Optional[Mapping[str, str]] = None,
    baseline: Optional[EventBaseline] = None,
) -> UrgentAlertEvaluation:
    """
    The one rule set of the collector and of the GUI preview: for a record
    that was not computed (no levels, ``has_levels``) one ``data_stale``
    alert saying why ("monitor has no usable data: <reasons>"), so losing
    every input is never silent; while the data is stale
    (``stale_data_warning`` with ``now``) the same ``data_stale`` alert with
    the age; in both cases the movement rules are not evaluated (their
    moves would be spurious). Otherwise ``evaluate_urgent_alerts`` on the
    previous-run and 24 h comparisons.

    Args:
        latest: The record to evaluate.
        comparisons: Builds the comparisons (called only when needed).
        thresholds: Which rules are on and their thresholds.
        stale_limit: The DATA STALE limit.
        now: Wall clock to judge a stored record against; None judges
            against the record's own data time only.
        last_reported_states: Event family -> last reported state (see
            ``evaluate_urgent_alerts``); None (the preview, which reads no
            delivery log) uses the change since the previous run.
        baseline: The 24 h baseline for event families without a
            delivered event (see ``evaluate_urgent_alerts``); None uses the
            change since the previous run for them.
    """
    stale_warning = (no_usable_data_warning(latest) if not latest.has_levels
                     else stale_data_warning(latest, stale_limit, now))
    if stale_warning is not None:
        stale_alert = UrgentAlert(key=ALERT_KEY_DATA_STALE,
                                  title="Data stale: movement alerts paused", comparison_label="latest")
        return UrgentAlertEvaluation(alerts=[stale_alert], stale_warning=stale_warning)
    by_label = {comparison.label: comparison for comparison in comparisons()}
    return UrgentAlertEvaluation(
        alerts=evaluate_urgent_alerts(latest, by_label[COMPARISON_PREVIOUS],
                                      by_label[COMPARISON_ONE_DAY], thresholds,
                                      last_reported_states, baseline),
        stale_warning=None)


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
        # Key -> delivery time of messages Telegram accepted but whose delivery row
        # could not be saved; honoured like a stored row for the life of the process.
        self._unsaved_deliveries: Dict[str, datetime] = {}

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

    def last_delivery(self, key: str, tolerate_repository_errors: bool = False) -> Optional[datetime]:
        """
        When ``key`` was last delivered: the newer of the stored delivery row
        and a delivery whose row could not be saved (kept in memory).

        Args:
            key: Alert or delivery key.
            tolerate_repository_errors: False (default): a delivery-log read
                error is raised. True: it is logged and only the in-memory
                delivery counts (the monitor-error alert, whose cause is
                often that very database error).
        """
        try:
            stored = self.repository.get_last_menthorq_alert_delivery(self.settings.currency, key)
        except Exception:
            if not tolerate_repository_errors:
                raise
            logger.exception("MenthorQ monitor: delivery log not readable; %s cooldown from this "
                             "process's memory only", key)
            stored = None
        remembered = self._unsaved_deliveries.get(key)
        known = [instant for instant in (stored, remembered) if instant is not None]
        return max(known) if known else None

    def last_reported_event_states(self) -> Dict[str, str]:
        """
        Event family -> the state its newest DELIVERED event alert reported
        (e.g. "net_gex_sign" -> "negative" after ``net_gex_turned_negative``),
        from the delivery log and the deliveries kept in memory. A family
        with no delivered event is absent.
        """
        states = {}
        for family, keys in EVENT_FAMILY_KEYS.items():
            candidates = []
            stored = self.repository.get_last_menthorq_alert_delivery_of_keys(self.settings.currency, list(keys))
            if stored is not None:
                candidates.append((stored[1], stored[0]))
            candidates += [(delivered_at, key) for key, delivered_at in self._unsaved_deliveries.items()
                           if key in keys]
            if candidates:
                _, newest_key = max(candidates)
                states[family] = EVENT_ALERT_STATES[newest_key][1]
        return states

    def _record_delivery(self, keys: List[str], message: str, record_id: Optional[int]) -> None:
        """
        Write one delivery row per key, in one transaction, for a message
        Telegram already accepted. A failing write is logged at ERROR and
        never raised: the keys are remembered in memory instead, so the
        cooldown and the weekly-once check still see the delivery and the
        message is not resent while this process runs.
        """
        delivered_at = self.clock()
        try:
            self.repository.save_menthorq_alert_deliveries(
                self.settings.currency, list(keys), delivered_at, message, record_id)
        except Exception:
            logger.exception("MenthorQ monitor: %s delivered by Telegram but the delivery row was NOT "
                             "saved; remembered in memory, not resent while the collector runs", keys)
            for key in keys:
                self._unsaved_deliveries[key] = delivered_at

    def _deliver(self, key: str, message: str, record_id: Optional[int]) -> bool:
        if not self.telegram.send(message, parse_mode=TELEGRAM_PARSE_MODE):
            logger.warning("MenthorQ monitor: %s NOT delivered (Telegram send failed)", key)
            return False
        self._record_delivery([key], message, record_id)
        logger.info("MenthorQ monitor: %s delivered", key)
        return True

    def send_urgent_alerts(self, latest: MenthorqLevelRecord, now: datetime) -> List[str]:
        """
        Evaluate the urgent rules on ``latest`` and send ONE message with
        every alert not on cooldown. While muted nothing is sent: the held
        back keys are recorded in ``menthorq_monitor_state`` (not as
        delivered, so the cooldown does not start). A 24 h level move (or
        another state rule) that still holds after the mute is reported
        then; so is a held-back or failed EVENT alert (net GEX sign flip,
        spot crossing HVL), because events are judged against the last
        REPORTED state (``last_reported_event_states``), unless the state
        has reverted to it. A family with no delivered event yet is judged
        against the state about 24 h ago (``event_baseline``, see the module
        docstring).

        When the data behind ``latest`` is stale (``stale_data_warning``
        with ``now``) or ``latest`` has no levels (not computed: every input
        lost), the movement rules are NOT evaluated (their moves would be
        spurious); one ``data_stale`` alert with the age or the not-computed
        reasons is sent instead, under the same cooldown and mute rules.

        Returns:
            Keys of the alerts delivered (empty when none fired, all were on
            cooldown, alerts are muted, or the send failed).
        """
        last_reported_states = self.last_reported_event_states()
        baseline = None
        # The 24 h baseline query is needed only by a rule that is on and has no delivered event.
        if any(family not in last_reported_states for family in enabled_event_families(self.thresholds)):
            baseline = event_baseline(latest, self.monitor_service.event_baseline_records(latest))
        evaluation = evaluate_urgent_alerts_of_record(
            latest, lambda: self.monitor_service.build_comparisons(latest), self.thresholds,
            self.settings.stale_snapshot_limit, now, last_reported_states, baseline)
        if evaluation.stale_warning is not None:
            logger.warning("MenthorQ monitor: data stale or not computed (%s); movement alerts paused",
                           evaluation.stale_warning)
        alerts = evaluation.alerts
        last_delivered = {alert.key: self.last_delivery(alert.key) for alert in alerts}
        due = apply_cooldown(alerts, last_delivered, now, self.thresholds.cooldown)
        skipped = sorted({alert.key for alert in alerts} - {alert.key for alert in due})
        if skipped:
            logger.info("MenthorQ monitor: alerts on cooldown (not repeated): %s", skipped)
        if not due:
            return []
        return self._send_urgent(latest.currency, latest.record_id, [alert.key for alert in due],
                                 evaluation.message(latest, due), now)

    def send_monitor_error_alert(self, error: BaseException, now: datetime) -> bool:
        """
        Tell the owner that the hourly computation raised (``error``): one
        ``monitor_error`` alert under the same 12 h cooldown and mute rules
        as the other urgent alerts. The text names the exception class and
        its message (bot token and database password scrubbed, cut to 200
        characters, escaped), never a traceback. When the delivery log
        cannot be read (often the cause of the error) only the deliveries
        kept in memory count for the cooldown, so a database that stays
        down gives one alert per cooldown, not one per hour. Never raises.

        Returns:
            True when the alert was delivered by this call.
        """
        try:
            error_text = scrub_secrets(str(error), self.telegram)
            last_delivered = self.last_delivery(ALERT_KEY_MONITOR_ERROR, tolerate_repository_errors=True)
            alert = UrgentAlert(key=ALERT_KEY_MONITOR_ERROR, title="Monitor error", comparison_label="latest")
            if not apply_cooldown([alert], {alert.key: last_delivered}, now, self.thresholds.cooldown):
                logger.info("MenthorQ monitor: monitor-error alert on cooldown (not repeated)")
                return False
            message = format_monitor_error_alert(type(error).__name__, error_text)
            return bool(self._send_urgent(self.settings.currency, None, [ALERT_KEY_MONITOR_ERROR],
                                          message, now))
        except Exception:
            logger.exception("MenthorQ monitor: the monitor-error alert itself failed")
            return False

    def _send_urgent(
        self, currency: str, record_id: Optional[int], keys: List[str], message: str, now: datetime
    ) -> List[str]:
        """
        Send one urgent ``message`` for ``keys`` unless muted (then the keys
        are recorded as held back), and record every key as delivered, in
        one transaction, when Telegram accepted it. When that write fails
        no row is saved, the error is logged and the keys are remembered in
        memory (``_record_delivery``), so the cooldown still holds.

        Returns:
            ``keys`` when delivered, else empty.
        """
        if self.alerts_muted(now):
            logger.info("MenthorQ monitor: urgent alerts %s NOT sent (muted)", keys)
            try:
                self.repository.save_menthorq_suppressed_alerts(currency, keys, self.clock())
            except Exception:
                logger.exception("MenthorQ monitor: could not record the muted alerts")
            return []
        if not self.telegram.send(message, parse_mode=TELEGRAM_PARSE_MODE):
            logger.warning("MenthorQ monitor: urgent alerts %s NOT delivered (Telegram send failed)", keys)
            return []
        self._record_delivery(keys, message, record_id)
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
            last = self.last_delivery(DELIVERY_KEY_WEEKLY_BRIEF)
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
