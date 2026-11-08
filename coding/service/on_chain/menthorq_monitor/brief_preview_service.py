"""
Preview of the MenthorQ-style monitor's Telegram output, for the GUI's
MenthorQ Monitor tab: the brief, which urgent-alert rules would fire, and
the inputs the latest record was built from.

Nothing is stored and nothing is sent. Like ``scripts/menthorq_brief.py
--dry-run``, the earlier records the brief compares with (previous run,
24 h, 7 d) and the daily-close record of the previous UTC date are
recomputed from the stored snapshots at their own instants (point in time,
no lookahead), so the monitor tables are neither needed nor written. The
brief is the same ``format_brief`` text Telegram gets; only its title says
it is a preview. Alerts are shown before the cooldown (the cooldown needs
the delivery log, which a preview does not read).
"""

import html
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_CALL_RESISTANCE_MOVE,
    ALERT_KEY_CROSSED_ABOVE_HVL,
    ALERT_KEY_CROSSED_BELOW_HVL,
    ALERT_KEY_HVL_MOVE,
    ALERT_KEY_NET_GEX_TURNED_NEGATIVE,
    ALERT_KEY_NET_GEX_TURNED_POSITIVE,
    ALERT_KEY_PUT_SUPPORT_MOVE,
    ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE,
    ALERT_KEY_SPOT_NEAR_PUT_SUPPORT,
    AlertThresholds,
    UrgentAlert,
)
from coding.core.analytics.menthorq_monitor.level_record import MenthorqLevelRecord
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    COMPARISON_ONE_DAY,
    COMPARISON_PREVIOUS,
    RecordComparison,
    spot_distances,
)
from coding.core.analytics.menthorq_replication_calculator import (
    IV_SOURCE_MARK_IV,
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    data_caveats,
    format_brief,
    format_duration,
    format_gex,
    format_strike,
    format_utc,
    stale_data_warning,
    telegram_length,
)
from coding.service.on_chain.menthorq_monitor.brief_delivery_service import evaluate_urgent_alerts_of_record
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import (
    MenthorqMonitorService,
    daily_close_as_of,
)
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings

NOT_COMPUTED = "not computed"
PREVIEW_TITLE = "Preview (references replayed, not stored)"


@dataclass(frozen=True)
class AlertRuleCheck:
    """One urgent-alert rule as it stands at the preview instant."""

    rule: str
    enabled: bool
    threshold: str
    current_value: str
    would_fire: bool


@dataclass(frozen=True)
class BriefPreview:
    """Everything the MenthorQ Monitor tab shows about the brief."""

    as_of_utc: datetime
    brief_html: str
    brief_length: int
    latest: MenthorqLevelRecord
    daily_close: Optional[MenthorqLevelRecord]
    comparisons: List[RecordComparison]
    alerts: List[UrgentAlert]
    alert_checks: List[AlertRuleCheck]
    urgent_alert_html: Optional[str]
    info_rows: List[Tuple[str, str]]
    warnings: List[str]


def telegram_html_to_plain_text(text: str) -> str:
    """
    What Telegram displays for a message in its HTML mode using only <b>,
    <i>, <pre> and <code> (with or without a class attribute): the tags
    removed and &amp; &lt; &gt; turned back.
    """
    return html.unescape(re.sub(r"</?(?:b|i|pre|code)(?:\s[^>]*)?>", "", text))


def monitor_output_directory(
    base_directory: Path, mode_is_live: bool, menthorq_date: date, now: datetime
) -> Path:
    """
    Dated folder of one Run: ``<base>/<YYYY-MM-DD>`` for a daily close,
    ``<base>/live_<YYYYMMDDTHHMM>Z`` (run time, UTC) for a live run.

    Raises:
        ValueError: when ``now`` is naive.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if mode_is_live:
        return base_directory / f"live_{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M')}Z"
    return base_directory / menthorq_date.isoformat()


def generated_chart_files(chart_output: Dict[str, Any]) -> List[str]:
    """
    PNG file names of one replication run, in display order: the
    all-expiry charts, then one IV*OI chart per expiry.
    """
    names = list(chart_output.get("charts", {}).values())
    names += [entry["chart"] for entry in chart_output.get("per_expiry_iv_times_open_interest", [])]
    return names


def preview_as_of_for_daily_close(utc_date: date) -> datetime:
    """
    The brief instant of a daily-close Run: 23:59 UTC of ``utc_date``, the
    data instant of that date's MenthorQ chart.
    """
    return daily_close_as_of(utc_date)


def _format_level_move(comparison: RecordComparison, level_name: str) -> str:
    if comparison.reference is None:
        return f"{NOT_COMPUTED} (no record 24h earlier)"
    change = comparison.level_changes.get(level_name)
    if change is None or change.change_percent_of_spot is None or change.change_usd is None:
        return f"{NOT_COMPUTED} (level or spot missing)"
    return f"{change.change_percent_of_spot:+.2f}% of spot ({change.change_usd:+,.0f} USD)"


def _format_regime_change(comparison: RecordComparison) -> str:
    if comparison.reference is None:
        return f"{NOT_COMPUTED} (no previous record)"
    if comparison.regime_before is None or comparison.regime_after is None:
        return f"{NOT_COMPUTED} (spot or HVL missing)"
    return f"{comparison.regime_before} -> {comparison.regime_after}"


def _format_net_gex_change(comparison: RecordComparison, latest: MenthorqLevelRecord) -> str:
    reference = comparison.reference
    if reference is None:
        return f"{NOT_COMPUTED} (no previous record)"
    if reference.total_net_gex is None or latest.total_net_gex is None:
        return f"{NOT_COMPUTED} (net GEX missing)"
    return f"{format_gex(reference.total_net_gex)} -> {format_gex(latest.total_net_gex)}"


def alert_rule_checks(
    latest: MenthorqLevelRecord,
    previous_comparison: RecordComparison,
    day_comparison: RecordComparison,
    alerts: Sequence[UrgentAlert],
    thresholds: AlertThresholds,
    alerts_enabled: bool,
) -> List[AlertRuleCheck]:
    """
    One row per urgent-alert rule. ``would_fire`` comes from the alerts
    ``evaluate_urgent_alerts`` returned, so it cannot disagree with the rule
    the collector applies.
    """
    fired = {alert.key for alert in alerts}
    rows = []
    for rule, level_name, key, threshold in (
            ("HVL move vs 24h ago", "hvl", ALERT_KEY_HVL_MOVE, thresholds.hvl_move_percent_of_spot),
            ("Call Resistance move vs 24h ago", "call_resistance", ALERT_KEY_CALL_RESISTANCE_MOVE,
             thresholds.call_resistance_move_percent_of_spot),
            ("Put Support move vs 24h ago", "put_support", ALERT_KEY_PUT_SUPPORT_MOVE,
             thresholds.put_support_move_percent_of_spot)):
        rows.append(AlertRuleCheck(
            rule=rule, enabled=alerts_enabled and threshold is not None,
            threshold="off" if threshold is None else f"|move| > {threshold:g}% of spot",
            current_value=_format_level_move(day_comparison, level_name),
            would_fire=key in fired))
    rows.append(AlertRuleCheck(
        rule="Spot crossed HVL since previous run",
        enabled=alerts_enabled and thresholds.spot_crosses_hvl,
        threshold="gamma regime changed" if thresholds.spot_crosses_hvl else "off",
        current_value=_format_regime_change(previous_comparison),
        would_fire=bool(fired & {ALERT_KEY_CROSSED_BELOW_HVL, ALERT_KEY_CROSSED_ABOVE_HVL})))
    rows.append(AlertRuleCheck(
        rule="Total net GEX sign change since previous run",
        enabled=alerts_enabled and thresholds.net_gex_sign_change,
        threshold="sign changed" if thresholds.net_gex_sign_change else "off",
        current_value=_format_net_gex_change(previous_comparison, latest),
        would_fire=bool(fired & {ALERT_KEY_NET_GEX_TURNED_NEGATIVE,
                                 ALERT_KEY_NET_GEX_TURNED_POSITIVE})))
    near_wall = thresholds.spot_near_wall_percent
    rows.append(AlertRuleCheck(
        rule="Spot near Call Resistance / Put Support",
        enabled=alerts_enabled and near_wall is not None,
        threshold="off" if near_wall is None else f"distance <= {near_wall:g}% of spot",
        current_value=_format_wall_distances(latest),
        would_fire=bool(fired & {ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE,
                                 ALERT_KEY_SPOT_NEAR_PUT_SUPPORT})))
    return rows


def _format_wall_distances(latest: MenthorqLevelRecord) -> str:
    """"Call Resistance +5.60%, Put Support -1.00%" (of spot); missing parts "not computed"."""
    distances = spot_distances(latest)
    parts = []
    for level_name, label in (("call_resistance", "Call Resistance"), ("put_support", "Put Support")):
        percent = distances[level_name].distance_percent_of_spot
        parts.append(f"{label} {NOT_COMPUTED}" if percent is None else f"{label} {percent:+.2f}%")
    if all(distances[name].distance_percent_of_spot is None
           for name in ("call_resistance", "put_support")):
        return NOT_COMPUTED
    return ", ".join(parts)


def _format_age(age: Optional[timedelta]) -> str:
    """timedelta(hours=26, minutes=5) -> "1 d 2 h 5 min"; None -> "not computed"."""
    return NOT_COMPUTED if age is None else format_duration(age)


def iv_source_summary(iv_source_counts: Dict[str, int]) -> str:
    """
    {"mark_iv": 900, "rebuilt_from_mark_price": 3, "missing": 1} ->
    "mark_iv 900, rebuilt from mark_price 3, missing 1 (of 904 rows)";
    empty -> "not computed". (Written here rather than reusing
    menthorq_chart_summary, which imports the chart renderer: this package
    stays free of matplotlib.)
    """
    if not iv_source_counts:
        return NOT_COMPUTED
    total = sum(iv_source_counts.values())
    return (f"mark_iv {iv_source_counts.get(IV_SOURCE_MARK_IV, 0):,}, "
            f"rebuilt from mark_price {iv_source_counts.get(IV_SOURCE_REBUILT_FROM_MARK_PRICE, 0):,}, "
            f"missing {iv_source_counts.get(IV_SOURCE_MISSING, 0):,} (of {total:,} rows)")


def _utc_or_not_computed(instant: Optional[datetime]) -> str:
    return NOT_COMPUTED if instant is None else format_utc(instant)


def info_rows(
    latest: MenthorqLevelRecord, daily_close: Optional[MenthorqLevelRecord], now: datetime
) -> List[Tuple[str, str]]:
    """(label, value) rows describing the inputs of the brief; missing = "not computed"."""
    snapshot_age = (None if latest.snapshot_captured_at_utc is None
                    else latest.as_of_utc - latest.snapshot_captured_at_utc)
    record_age = (None if latest.snapshot_captured_at_utc is None
                  else now - latest.snapshot_captured_at_utc)
    spot_text = (NOT_COMPUTED if latest.spot is None else
                 f"{format_strike(latest.spot)} (trade at {_utc_or_not_computed(latest.spot_time_utc)})")
    return [
        ("Brief data time", format_utc(latest.as_of_utc)),
        ("Snapshot captured", _utc_or_not_computed(latest.snapshot_captured_at_utc)),
        ("Snapshot older than data time by", _format_age(snapshot_age)),
        ("Data age at Run (now - snapshot)", _format_age(record_age)),
        ("Index price", spot_text),
        ("Open interest from", _utc_or_not_computed(latest.open_interest_captured_at_utc)),
        ("IV source", iv_source_summary(latest.iv_source_counts)),
        ("Universe / gamma at", f"{latest.expiry_universe} / {latest.gamma_underlying}"),
        ("Expiries", ", ".join(latest.expirations) if latest.expirations else NOT_COMPUTED),
        ("Daily-close column", NOT_COMPUTED if daily_close is None
         else f"{daily_close.as_of_utc.astimezone(timezone.utc).date().isoformat()} "
              f"(23:59 UTC, open interest 00:00 UTC)"),
    ]


class MenthorqBriefPreviewService:
    """Builds the brief, the alert check and the input summary without storing or sending."""

    def __init__(
        self,
        monitor_service: MenthorqMonitorService,
        thresholds: Optional[AlertThresholds] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            monitor_service: Computes records (only its compute methods are
                used, never ``store``).
            thresholds: Urgent-alert thresholds; the settings' ``alert_thresholds``
                (what the collector applies) when omitted.
            clock: Wall clock (UTC-aware) for the data age.
        """
        self.monitor_service = monitor_service
        self.settings = monitor_service.settings
        self.thresholds = thresholds if thresholds is not None else self.settings.alert_thresholds
        self.clock = clock

    def build_preview(self, as_of: datetime) -> BriefPreview:
        """
        The brief Telegram would get at ``as_of`` and the alert rules that
        would fire then (before the cooldown).

        Args:
            as_of: Timezone-aware data instant of the live record.

        Raises:
            ValueError: when ``as_of`` is naive.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        service = self.monitor_service
        latest = service.compute_live_record(as_of)
        comparisons = service.build_replayed_comparisons(latest)
        daily_close_record = service.compute_daily_close_record(
            (latest.as_of_utc - timedelta(days=1)).date())
        daily_close = daily_close_record if daily_close_record.spot is not None else None
        stale_limit = self.settings.stale_snapshot_limit
        brief = format_brief(latest, comparisons, daily_close, title=PREVIEW_TITLE,
                             stale_limit=stale_limit)
        stale_warning = stale_data_warning(latest, stale_limit)
        warnings = ([] if stale_warning is None else [f"DATA STALE: {stale_warning}"]) \
            + data_caveats(latest, stale_limit)

        by_label = {comparison.label: comparison for comparison in comparisons}
        previous, day = by_label[COMPARISON_PREVIOUS], by_label[COMPARISON_ONE_DAY]
        # The collector's own rule set (not computed -> none; stale -> data_stale only).
        # The record is recomputed at as_of, so its data time is the "now" of that run.
        evaluation = evaluate_urgent_alerts_of_record(
            latest, lambda: comparisons, self.thresholds, stale_limit)
        alerts = evaluation.alerts
        checks = alert_rule_checks(latest, previous, day, alerts, self.thresholds,
                                   self.settings.alerts_enabled)
        if evaluation.stale_warning is not None:
            checks = [replace(check, current_value=f"{check.current_value} (paused: data stale)")
                      for check in checks]
        return BriefPreview(
            as_of_utc=latest.as_of_utc,
            brief_html=brief,
            brief_length=telegram_length(brief),
            latest=latest,
            daily_close=daily_close,
            comparisons=comparisons,
            alerts=alerts,
            alert_checks=checks,
            urgent_alert_html=evaluation.message(latest, alerts) if alerts else None,
            info_rows=info_rows(latest, daily_close, self.clock()),
            warnings=warnings,
        )


def build_brief_preview_service(
    currency: str,
    expiry_universe: str,
    gamma_underlying: str,
    repository: Optional[DatabaseRepository] = None,
) -> MenthorqBriefPreviewService:
    """
    A preview service with the monitor settings from the environment
    (.env), overridden by the currency, universe and gamma underlying the
    user picked.

    Raises:
        ValueError: when a MENTHORQ_* setting in the environment is invalid.
    """
    settings = replace(MenthorqMonitorSettings.from_environment(), currency=currency,
                       expiry_universe=expiry_universe, gamma_underlying=gamma_underlying)
    return MenthorqBriefPreviewService(MenthorqMonitorService(repository=repository,
                                                              settings=settings))
