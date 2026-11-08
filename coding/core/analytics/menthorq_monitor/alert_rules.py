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
family the trigger is the change since the previous run.

The thresholds are chosen by hand, not fitted; a backtest is meant to
replace them. An alert whose key was delivered less than ``cooldown`` ago
is not repeated (crossings and sign flips carry their direction in the key,
so a crossing back is a different alert). The three 24 h level-move alerts
use at least the 24 h comparison window as cooldown (``cooldown_for_key``).

Pure: no database, network or plotting imports.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Mapping, Optional

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
    ALERT_KEY_DATA_STALE,
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


def _event_state_to_report(
    family: str,
    latest: MenthorqLevelRecord,
    previous_comparison: RecordComparison,
    last_reported_states: Mapping[str, str],
) -> Optional[str]:
    """
    The state whose event alert fires for ``family``, or None. With a
    reported state: the current state when it differs from it. Without one:
    the new state when it changed since the previous run.
    """
    current = (net_gex_sign_state(latest) if family == EVENT_FAMILY_NET_GEX_SIGN
               else hvl_regime_state(latest))
    if family in last_reported_states:
        return current if current is not None and current != last_reported_states[family] else None
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
) -> List[UrgentAlert]:
    """The HVL-crossing and net-GEX sign-flip alerts that are on and fire."""
    families = []
    if thresholds.spot_crosses_hvl:
        families.append(EVENT_FAMILY_HVL_REGIME)
    if thresholds.net_gex_sign_change:
        families.append(EVENT_FAMILY_NET_GEX_SIGN)
    alerts = []
    for family in families:
        state = _event_state_to_report(family, latest, previous_comparison, last_reported_states)
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
            title += f" (earlier, not reported yet; last reported: {last_reported_states[family]})"
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
            that family. A family without a delivered event is absent; its
            trigger is then the change since the previous run.
    """
    alerts = _level_move_alerts(day_comparison, thresholds)
    alerts.extend(_event_alerts(latest, previous_comparison, thresholds, last_reported_states or {}))
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
