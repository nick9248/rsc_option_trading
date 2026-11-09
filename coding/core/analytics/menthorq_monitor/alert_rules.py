"""
URGENT alert rules of the MenthorQ-style monitor, evaluated after each
hourly live run.

Rules (all "%" are percent of the latest spot, see level_trend_calculator).
ON by default (the owner's choice for the first test):
  - HVL moved by MORE than ``hvl_move_percent_of_spot`` (5) vs 24 h ago.
  - Call Resistance moved by MORE than
    ``call_resistance_move_percent_of_spot`` (10) vs 24 h ago.
  - Put Support moved by MORE than ``put_support_move_percent_of_spot`` (5)
    vs 24 h ago.
  - Total net GEX changed sign (see the event rules below).
OFF by default (implemented; switched on by setting, see monitor_settings):
  - Spot crossed HVL (gamma regime changed; see the event rules below).
  - Spot is within ``spot_near_wall_percent`` of Call Resistance or Put
    Support (latest record only).
A percent threshold of None switches that rule off.

Event rules (net GEX sign flip, spot crossing HVL) are judged against the
state last REPORTED to the owner when one exists (``last_reported_states``,
from the delivered event alerts): the alert for the current state fires
while it differs from the last reported one, so an event whose send failed
or fell inside a mute is still reported at a later run, and an event that
reverted before it was reported is not. Without any delivered event of that
family the baseline is the state about 24 h ago (``event_baseline``: the
reference record of the 24 h comparison, records whose state is unknown
skipped), so a first-ever event that was muted or failed is still reported
while it differs from that baseline. Only when no earlier stored record
exists at all is the trigger the change since the previous run.

The thresholds are chosen by hand, not fitted; a backtest is meant to
replace them. An alert whose key was delivered less than ``cooldown`` ago
is not repeated (crossings and sign flips carry their direction in the key,
so a crossing back is a different alert). The three 24 h level-move alerts
use at least the 24 h comparison window as cooldown (``cooldown_for_key``).

Pure: no database, network or plotting imports.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Mapping, Optional, Sequence

from coding.core.analytics.menthorq_monitor.level_record import (
    GAMMA_REGIME_NEGATIVE,
    GAMMA_REGIME_POSITIVE,
    LEVEL_LABELS,
    MenthorqLevelRecord,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    CROSSED_ABOVE_HVL,
    CROSSED_BELOW_HVL,
    ONE_DAY_COMPARISON_OFFSET,
    RecordComparison,
    spot_distances,
)

DEFAULT_HVL_MOVE_PERCENT_OF_SPOT = 5.0
DEFAULT_CALL_RESISTANCE_MOVE_PERCENT_OF_SPOT = 10.0
DEFAULT_PUT_SUPPORT_MOVE_PERCENT_OF_SPOT = 5.0
DEFAULT_ALERT_COOLDOWN = timedelta(hours=12)

ALERT_KEY_HVL_MOVE = "hvl_move_24h"
ALERT_KEY_CALL_RESISTANCE_MOVE = "call_resistance_move_24h"
ALERT_KEY_PUT_SUPPORT_MOVE = "put_support_move_24h"
ALERT_KEY_CROSSED_BELOW_HVL = "spot_crossed_below_hvl"
ALERT_KEY_CROSSED_ABOVE_HVL = "spot_crossed_above_hvl"
ALERT_KEY_NET_GEX_TURNED_NEGATIVE = "net_gex_turned_negative"
ALERT_KEY_NET_GEX_TURNED_POSITIVE = "net_gex_turned_positive"
ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE = "spot_near_call_resistance"
ALERT_KEY_SPOT_NEAR_PUT_SUPPORT = "spot_near_put_support"
# Sent instead of the movement alerts while the latest record's data is stale.
ALERT_KEY_DATA_STALE = "data_stale"
# Sent when the hourly computation itself raised (database error, replication bug).
ALERT_KEY_MONITOR_ERROR = "monitor_error"
# A level move vs 24 h ago stays true for the whole comparison window after the
# move, so these keys are held for at least that window (one move, one alert).
LEVEL_MOVE_ALERT_KEYS = (ALERT_KEY_HVL_MOVE, ALERT_KEY_CALL_RESISTANCE_MOVE, ALERT_KEY_PUT_SUPPORT_MOVE)
LEVEL_MOVE_ALERT_COOLDOWN = ONE_DAY_COMPARISON_OFFSET
# Event families: the state an event alert reports, by key (see last_reported_states).
EVENT_FAMILY_NET_GEX_SIGN = "net_gex_sign"
EVENT_FAMILY_HVL_REGIME = "hvl_regime"
EVENT_STATE_POSITIVE = "positive"
EVENT_STATE_NEGATIVE = "negative"
EVENT_ALERT_STATES = {
    ALERT_KEY_NET_GEX_TURNED_NEGATIVE: (EVENT_FAMILY_NET_GEX_SIGN, EVENT_STATE_NEGATIVE),
    ALERT_KEY_NET_GEX_TURNED_POSITIVE: (EVENT_FAMILY_NET_GEX_SIGN, EVENT_STATE_POSITIVE),
    ALERT_KEY_CROSSED_BELOW_HVL: (EVENT_FAMILY_HVL_REGIME, EVENT_STATE_NEGATIVE),
    ALERT_KEY_CROSSED_ABOVE_HVL: (EVENT_FAMILY_HVL_REGIME, EVENT_STATE_POSITIVE),
}
EVENT_FAMILY_KEYS = {
    family: tuple(key for key, (key_family, _) in EVENT_ALERT_STATES.items() if key_family == family)
    for family in (EVENT_FAMILY_NET_GEX_SIGN, EVENT_FAMILY_HVL_REGIME)
}
URGENT_ALERT_KEYS = (
    ALERT_KEY_HVL_MOVE, ALERT_KEY_CALL_RESISTANCE_MOVE, ALERT_KEY_PUT_SUPPORT_MOVE,
    ALERT_KEY_CROSSED_BELOW_HVL, ALERT_KEY_CROSSED_ABOVE_HVL,
    ALERT_KEY_NET_GEX_TURNED_NEGATIVE, ALERT_KEY_NET_GEX_TURNED_POSITIVE,
    ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE, ALERT_KEY_SPOT_NEAR_PUT_SUPPORT,
    ALERT_KEY_DATA_STALE, ALERT_KEY_MONITOR_ERROR,
)


@dataclass(frozen=True)
class AlertThresholds:
    """
    Which urgent rules are on, their thresholds (percent of spot) and the
    cooldown. A percent of None switches that rule off.
    """

    hvl_move_percent_of_spot: Optional[float] = DEFAULT_HVL_MOVE_PERCENT_OF_SPOT
    call_resistance_move_percent_of_spot: Optional[float] = DEFAULT_CALL_RESISTANCE_MOVE_PERCENT_OF_SPOT
    put_support_move_percent_of_spot: Optional[float] = DEFAULT_PUT_SUPPORT_MOVE_PERCENT_OF_SPOT
    net_gex_sign_change: bool = True
    spot_crosses_hvl: bool = False
    spot_near_wall_percent: Optional[float] = None
    cooldown: timedelta = DEFAULT_ALERT_COOLDOWN

    def describe(self) -> List[str]:
        """One plain-text line per rule, saying whether it is on."""
        def move_rule(level_text: str, threshold: Optional[float]) -> str:
            if threshold is None:
                return f"{level_text} move vs 24h: OFF"
            return f"{level_text} move > {threshold:g}% of spot vs 24h: ON"

        near_wall = ("Spot near Call Resistance / Put Support: OFF"
                     if self.spot_near_wall_percent is None else
                     f"Spot within {self.spot_near_wall_percent:g}% of Call Resistance / "
                     "Put Support: ON")
        return [
            move_rule("HVL", self.hvl_move_percent_of_spot),
            move_rule("Call Resistance", self.call_resistance_move_percent_of_spot),
            move_rule("Put Support", self.put_support_move_percent_of_spot),
            f"Total net GEX sign change: {'ON' if self.net_gex_sign_change else 'OFF'}",
            f"Spot crosses HVL: {'ON' if self.spot_crosses_hvl else 'OFF'}",
            near_wall,
            f"Cooldown per alert: {self.cooldown.total_seconds() / 3600:g}h "
            f"(24h moves: {cooldown_for_key(ALERT_KEY_HVL_MOVE, self.cooldown).total_seconds() / 3600:g}h)",
        ]


@dataclass(frozen=True)
class UrgentAlert:
    """One triggered rule. ``expiry_roll`` lists expiries settled over the compared window."""

    key: str
    title: str
    comparison_label: str
    level_name: Optional[str] = None
    change_usd: Optional[float] = None
    change_percent_of_spot: Optional[float] = None
    expiry_roll: tuple = ()


def _level_move_alerts(
    day_comparison: RecordComparison, thresholds: AlertThresholds
) -> List[UrgentAlert]:
    rules = (
        ("hvl", ALERT_KEY_HVL_MOVE, thresholds.hvl_move_percent_of_spot),
        ("call_resistance", ALERT_KEY_CALL_RESISTANCE_MOVE,
         thresholds.call_resistance_move_percent_of_spot),
        ("put_support", ALERT_KEY_PUT_SUPPORT_MOVE, thresholds.put_support_move_percent_of_spot),
    )
    alerts = []
    for level_name, key, threshold in rules:
        if threshold is None:
            continue
        change = day_comparison.level_changes.get(level_name)
        if change is None or change.change_percent_of_spot is None:
            continue
        if abs(change.change_percent_of_spot) > threshold:
            alerts.append(UrgentAlert(
                key=key,
                title=f"{LEVEL_LABELS[level_name]} moved more than {threshold:g}% of spot in 24h",
                comparison_label=day_comparison.label,
                level_name=level_name,
                change_usd=change.change_usd,
                change_percent_of_spot=change.change_percent_of_spot,
                expiry_roll=tuple(day_comparison.settled_expiries),
            ))
    return alerts


def _near_wall_alerts(latest: MenthorqLevelRecord, threshold: float) -> List[UrgentAlert]:
    distances = spot_distances(latest)
    alerts = []
    for level_name, key in (("call_resistance", ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE),
                            ("put_support", ALERT_KEY_SPOT_NEAR_PUT_SUPPORT)):
        distance = distances[level_name]
        if distance.level_value is None or distance.distance_percent_of_spot is None:
            continue
        if abs(distance.distance_percent_of_spot) <= threshold:
            alerts.append(UrgentAlert(
                key=key,
                title=(f"Spot within {threshold:g}% of {LEVEL_LABELS[level_name]} "
                       f"{distance.level_value:,.0f} (distance "
                       f"{distance.distance_percent_of_spot:+.2f}% of spot)"),
                comparison_label="latest",
            ))
    return alerts


def net_gex_sign_state(record: MenthorqLevelRecord) -> Optional[str]:
    """"positive" / "negative" sign of total net GEX; None when missing or exactly 0."""
    if record.total_net_gex is None or record.total_net_gex == 0:
        return None
    return EVENT_STATE_POSITIVE if record.total_net_gex > 0 else EVENT_STATE_NEGATIVE


def hvl_regime_state(record: MenthorqLevelRecord) -> Optional[str]:
    """"positive" (spot above HVL) / "negative" (below); None when missing or spot exactly at HVL."""
    if record.gamma_regime == GAMMA_REGIME_POSITIVE:
        return EVENT_STATE_POSITIVE
    if record.gamma_regime == GAMMA_REGIME_NEGATIVE:
        return EVENT_STATE_NEGATIVE
    return None


def event_state(family: str, record: MenthorqLevelRecord) -> Optional[str]:
    """The state of event ``family`` in ``record`` ("positive" / "negative"); None when unknown."""
    return net_gex_sign_state(record) if family == EVENT_FAMILY_NET_GEX_SIGN else hvl_regime_state(record)


@dataclass(frozen=True)
class EventBaseline:
    """
    The 24 h baseline of the event rules for families with NO delivered
    event (see ``event_baseline``): family -> the state about 24 h ago
    (``reference_states``) and family -> the current state
    (``current_states``). A family whose state is unknown is absent.
    """

    reference_states: Mapping[str, str]
    current_states: Mapping[str, str]


def _newest_known_state(family: str, records: Sequence[MenthorqLevelRecord]) -> Optional[str]:
    """The state of the newest record of ``records`` (oldest first) whose state is known."""
    for record in reversed(records):
        state = event_state(family, record)
        if state is not None:
            return state
    return None


def event_baseline(
    latest: MenthorqLevelRecord, earlier_records: Sequence[MenthorqLevelRecord]
) -> EventBaseline:
    """
    The 24 h baseline for event families without a delivered event.

    Reference record: the newest of ``earlier_records`` at or before
    ``latest`` minus 24 h (the 24 h comparison reference); when none is
    that old, the oldest one (the oldest record within the last 24 h). The
    reference state of a family is the state of the newest record at or
    before the reference record whose state is known: a record whose state
    is None (e.g. HVL dropped at the chain edge, spot exactly at HVL, net
    GEX missing or 0) is skipped, so it cannot hide a crossing. The current
    state is the latest's or, when that is unknown, the newest known state
    of ``earlier_records``. Without earlier records both maps are empty
    (the caller then uses the change since the previous run).

    Args:
        latest: The record being evaluated.
        earlier_records: Computed records of the same currency, mode and
            settings strictly before ``latest``, oldest first, covering the
            last 24 h and the 24 h comparison tolerance before that.
    """
    if not earlier_records:
        return EventBaseline(reference_states={}, current_states={})
    target = latest.as_of_utc - ONE_DAY_COMPARISON_OFFSET
    old_enough = [index for index, record in enumerate(earlier_records) if record.as_of_utc <= target]
    reference_index = old_enough[-1] if old_enough else 0
    reference_states = {}
    current_states = {}
    for family in EVENT_FAMILY_KEYS:
        reference_state = _newest_known_state(family, earlier_records[:reference_index + 1])
        if reference_state is not None:
            reference_states[family] = reference_state
        current_state = event_state(family, latest) or _newest_known_state(family, earlier_records)
        if current_state is not None:
            current_states[family] = current_state
    return EventBaseline(reference_states=reference_states, current_states=current_states)


def _event_state_to_report(
    family: str,
    latest: MenthorqLevelRecord,
    previous_comparison: RecordComparison,
    last_reported_states: Mapping[str, str],
    baseline: Optional[EventBaseline],
) -> Optional[str]:
    """
    The state whose event alert fires for ``family``, or None. With a
    reported state: the current state when it differs from it. Without one
    but with a 24 h baseline state: the current state (unknown states
    skipped) when it differs from the baseline. Otherwise: the new state
    when it changed since the previous run.
    """
    current = event_state(family, latest)
    if family in last_reported_states:
        return current if current is not None and current != last_reported_states[family] else None
    if baseline is not None and family in baseline.reference_states:
        current_known = baseline.current_states.get(family)
        return (current_known if current_known is not None
                and current_known != baseline.reference_states[family] else None)
    if family == EVENT_FAMILY_NET_GEX_SIGN:
        return current if previous_comparison.net_gex_sign_flipped else None
    if previous_comparison.hvl_crossing == CROSSED_BELOW_HVL:
        return EVENT_STATE_NEGATIVE
    if previous_comparison.hvl_crossing == CROSSED_ABOVE_HVL:
        return EVENT_STATE_POSITIVE
    return None


def _event_alerts(
    latest: MenthorqLevelRecord,
    previous_comparison: RecordComparison,
    thresholds: AlertThresholds,
    last_reported_states: Mapping[str, str],
    baseline: Optional[EventBaseline],
) -> List[UrgentAlert]:
    """The HVL-crossing and net-GEX sign-flip alerts that are on and fire."""
    families = []
    if thresholds.spot_crosses_hvl:
        families.append(EVENT_FAMILY_HVL_REGIME)
    if thresholds.net_gex_sign_change:
        families.append(EVENT_FAMILY_NET_GEX_SIGN)
    alerts = []
    for family in families:
        state = _event_state_to_report(family, latest, previous_comparison, last_reported_states, baseline)
        if state is None:
            continue
        negative = state == EVENT_STATE_NEGATIVE
        if family == EVENT_FAMILY_HVL_REGIME:
            key = ALERT_KEY_CROSSED_BELOW_HVL if negative else ALERT_KEY_CROSSED_ABOVE_HVL
            title = ("Spot crossed BELOW HVL (negative gamma regime)" if negative
                     else "Spot crossed ABOVE HVL (positive gamma regime)")
            changed_since_previous_run = previous_comparison.hvl_crossing is not None
        else:
            key = ALERT_KEY_NET_GEX_TURNED_NEGATIVE if negative else ALERT_KEY_NET_GEX_TURNED_POSITIVE
            title = f"Total net GEX turned {'NEGATIVE' if negative else 'POSITIVE'}"
            changed_since_previous_run = bool(previous_comparison.net_gex_sign_flipped)
        if not changed_since_previous_run:
            # Changed before the previous run but not reported yet (failed send or mute).
            if family in last_reported_states:
                title += f" (earlier, not reported yet; last reported: {last_reported_states[family]})"
            elif baseline is not None:
                title += f" (earlier, not reported yet; about 24h ago: {baseline.reference_states[family]})"
        alerts.append(UrgentAlert(
            key=key, title=title, comparison_label=previous_comparison.label,
            change_usd=(previous_comparison.net_gex_change
                        if family == EVENT_FAMILY_NET_GEX_SIGN and changed_since_previous_run else None),
            expiry_roll=tuple(previous_comparison.settled_expiries) if changed_since_previous_run else ()))
    return alerts


def evaluate_urgent_alerts(
    latest: MenthorqLevelRecord,
    previous_comparison: RecordComparison,
    day_comparison: RecordComparison,
    thresholds: AlertThresholds = AlertThresholds(),
    last_reported_states: Optional[Mapping[str, str]] = None,
    baseline: Optional[EventBaseline] = None,
) -> List[UrgentAlert]:
    """
    Every rule that is on and fires, before the cooldown is applied.

    Args:
        latest: The latest record (for the spot-near-wall rule and the
            current event states).
        previous_comparison: Latest vs the previous run.
        day_comparison: Latest vs 24 hours ago.
        thresholds: Which rules are on and their thresholds.
        last_reported_states: Event family (``EVENT_FAMILY_*``) -> state
            ("positive" / "negative") of the newest DELIVERED event alert of
            that family. A family without a delivered event is absent; it
            is then judged against ``baseline``.
        baseline: The 24 h baseline (``event_baseline``) for families
            without a delivered event; a family absent from it (or None)
            uses the change since the previous run.
    """
    alerts = _level_move_alerts(day_comparison, thresholds)
    alerts.extend(_event_alerts(latest, previous_comparison, thresholds, last_reported_states or {},
                                baseline))
    if thresholds.spot_near_wall_percent is not None:
        alerts.extend(_near_wall_alerts(latest, thresholds.spot_near_wall_percent))
    return alerts


def cooldown_for_key(key: str, cooldown: timedelta) -> timedelta:
    """
    The cooldown of one alert key: the configured ``cooldown``, but at least
    the 24 h comparison window for the 24 h level-move keys (their condition
    stays true for that whole window after one move).
    """
    if key in LEVEL_MOVE_ALERT_KEYS:
        return max(cooldown, LEVEL_MOVE_ALERT_COOLDOWN)
    return cooldown


def apply_cooldown(
    alerts: List[UrgentAlert],
    last_delivered_by_key: Mapping[str, Optional[datetime]],
    now: datetime,
    cooldown: timedelta = DEFAULT_ALERT_COOLDOWN,
) -> List[UrgentAlert]:
    """
    Drop alerts whose key was delivered less than its cooldown
    (``cooldown_for_key``) before ``now`` (an alert delivered exactly that
    long ago may repeat).
    """
    kept = []
    for alert in alerts:
        last = last_delivered_by_key.get(alert.key)
        if last is not None and now - last < cooldown_for_key(alert.key, cooldown):
            continue
        kept.append(alert)
    return kept
