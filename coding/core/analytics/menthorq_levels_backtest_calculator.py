"""
Pure helpers for the daily backtest of the MenthorQ-style levels
(``replicate_menthorq``): one summary row per day, day-over-day and n-day
level changes, attribution of a change to an expiry settling, forward
realized-volatility outcomes and alert counts.

Conventions (taken from the replication, not re-derived here):
  - A day's levels are computed at 23:59 UTC with open interest from the
    snapshot at or before 00:00 UTC of the same date.
  - Call Resistance / Put Support / HVL = highest positive net GEX strike /
    lowest negative net GEX strike / argmin of cumulative net GEX
    (``DEFAULT_LEVEL_CANDIDATES``).
  - Total net GEX is reported in two units: USD per 1% move
    (gamma * OI * S^2 * 0.01, ``GexDexCalculator``) and MenthorQ's bar unit
    gamma * OI * S (the same number divided by S * 0.01).

Missing inputs give None ("not computed"), never 0. Pure: no database.
"""

import math
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from coding.core.analytics.menthorq_replication_calculator import (
    GEX_PER_ONE_PERCENT_MOVE,
    LEVEL_NAMES,
    PANEL_NAMES,
    PANEL_RANKING_ABSOLUTE_NET,
    parse_expiry_settlement,
    select_drawn_levels,
)

REGIME_SPOT_ABOVE_HVL = "spot_above_hvl"
REGIME_SPOT_BELOW_HVL = "spot_below_hvl"
REGIME_SPOT_AT_HVL = "spot_at_hvl"

CHANGE_NOT_COMPUTED = "not_computed"
CHANGE_UNCHANGED = "unchanged"
CHANGE_EXPIRY_SETTLEMENT = "expiry_settlement"
CHANGE_POSITIONING_OR_REPRICING = "positioning_or_repricing"
CHANGE_MIXED = "mixed"

HOURS_PER_YEAR = 365.0 * 24.0


def summarize_replication(
    replication: Mapping[str, Any], panel_ranking: str = PANEL_RANKING_ABSOLUTE_NET
) -> Dict[str, Any]:
    """
    The per-day numbers of one ``replicate_menthorq`` result.

    Args:
        replication: Output of ``replicate_menthorq``.
        panel_ranking: Which panel ranking to report (default |net GEX|).

    Returns:
        {"expirations", "levels" {call_resistance, put_support, hvl},
         "total_net_gex_usd_per_one_percent", "total_net_gex_gamma_open_interest_spot",
         "panels" {panel: {"expiration", call_resistance, put_support, hvl}},
         "expirations_not_computed", "instruments_missing_gamma"}.
        Values the replication could not compute stay None.
    """
    spot = replication["settings"]["index_price"]
    exposure = replication["iv_times_open_interest"]["all_expiries_exposure"]
    total_usd = exposure["total_net_gex"]
    panels: Dict[str, Dict[str, Any]] = {}
    for panel_name in PANEL_NAMES:
        expiration = replication["panels"][panel_ranking].get(panel_name)
        panel_levels: Dict[str, Optional[float]] = (
            select_drawn_levels(replication["gex_by_expiration"][expiration]["levels"])
            if expiration is not None else {level_name: None for level_name in LEVEL_NAMES})
        panels[panel_name] = {"expiration": expiration, **panel_levels}
    return {
        "expirations": list(replication["settings"]["expirations"]),
        "levels": select_drawn_levels(replication["iv_times_open_interest"]["levels"]),
        "total_net_gex_usd_per_one_percent": total_usd,
        "total_net_gex_gamma_open_interest_spot": (
            total_usd / (spot * GEX_PER_ONE_PERCENT_MOVE)
            if total_usd is not None and spot else None
        ),
        "panels": panels,
        "expirations_not_computed": [
            expiration for expiration, expiry_exposure in replication["gex_by_expiration"].items()
            if expiry_exposure["total_net_gex"] is None
        ],
        "instruments_missing_gamma": exposure["instruments_missing_gamma"],
    }


def gamma_regime(spot: Optional[float], hvl: Optional[float]) -> Optional[str]:
    """"spot_above_hvl" / "spot_below_hvl" / "spot_at_hvl", None when either is missing."""
    if spot is None or hvl is None:
        return None
    if spot > hvl:
        return REGIME_SPOT_ABOVE_HVL
    if spot < hvl:
        return REGIME_SPOT_BELOW_HVL
    return REGIME_SPOT_AT_HVL


def percent_of(value: Optional[float], reference: Optional[float]) -> Optional[float]:
    """100 * value / reference; None when either is missing or the reference is 0."""
    if value is None or reference is None or reference == 0:
        return None
    return 100.0 * value / reference


def expiry_list_changes(
    previous_expirations: Sequence[str], current_expirations: Sequence[str], current_as_of: datetime
) -> Dict[str, List[str]]:
    """
    How the expiry list moved from one day to the next.

    Returns:
        {"settled": in the previous list, gone now and settled at or before
         ``current_as_of``; "removed_unsettled": gone but not yet settled
         (a data anomaly, e.g. missing rows); "added": new in the current list}.
    """
    current = set(current_expirations)
    previous = set(previous_expirations)
    settled, removed_unsettled = [], []
    for expiration in previous_expirations:
        if expiration in current:
            continue
        settlement = parse_expiry_settlement(expiration)
        if settlement is not None and settlement <= current_as_of:
            settled.append(expiration)
        else:
            removed_unsettled.append(expiration)
    return {
        "settled": settled,
        "removed_unsettled": removed_unsettled,
        "added": [expiration for expiration in current_expirations if expiration not in previous],
    }


def attribute_level_change(
    previous_value: Optional[float],
    without_settled_value: Optional[float],
    current_value: Optional[float],
) -> str:
    """
    Whether a level's day-over-day change is explained by an expiry settling.

    ``without_settled_value`` is the PREVIOUS day's level recomputed from the
    previous day's inputs with the settled expiries' rows removed (equal to
    ``previous_value`` when nothing settled).

    Returns:
        "not_computed" (a value is None), "unchanged", "expiry_settlement"
        (removing the settled expiries alone reproduces today's level),
        "positioning_or_repricing" (removing them does not move the level,
        so the change comes from new open interest, spot or IV), or "mixed".
    """
    if previous_value is None or without_settled_value is None or current_value is None:
        return CHANGE_NOT_COMPUTED
    if current_value == previous_value:
        return CHANGE_UNCHANGED
    if without_settled_value == current_value:
        return CHANGE_EXPIRY_SETTLEMENT
    if without_settled_value == previous_value:
        return CHANGE_POSITIONING_OR_REPRICING
    return CHANGE_MIXED


def settlement_share_of_change(
    previous_value: Optional[float],
    without_settled_value: Optional[float],
    current_value: Optional[float],
) -> Optional[float]:
    """
    Fraction of a change (e.g. of total net GEX) that removing the settled
    expiries reproduces: (without_settled - previous) / (current - previous).
    None when a value is missing or there is no change.
    """
    if previous_value is None or without_settled_value is None or current_value is None:
        return None
    change = current_value - previous_value
    if change == 0:
        return None
    return (without_settled_value - previous_value) / change


def lagged_change(values: Sequence[Optional[float]], lag: int) -> List[Optional[float]]:
    """values[i] - values[i - lag]; None for the first ``lag`` entries or a missing value."""
    changes: List[Optional[float]] = []
    for position, value in enumerate(values):
        earlier = values[position - lag] if position >= lag else None
        changes.append(value - earlier if value is not None and earlier is not None else None)
    return changes


def interpolated_percentile(sorted_values: Sequence[float], percentile: float) -> float:
    """Linear-interpolation percentile (numpy's default) of an ascending list."""
    if not sorted_values:
        raise ValueError("no values")
    position = (len(sorted_values) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def absolute_distribution(values: Iterable[Optional[float]]) -> Dict[str, Optional[float]]:
    """
    Distribution of |value| (None ignored): count, share of non-zero values,
    median, 90th / 95th / 99th percentile (linear interpolation) and maximum.
    Percentiles are None when there is no value.
    """
    absolute = sorted(abs(value) for value in values if value is not None)
    if not absolute:
        return {"count": 0, "share_non_zero": None, "median": None, "percentile_90": None,
                "percentile_95": None, "percentile_99": None, "maximum": None}
    return {
        "count": len(absolute),
        "share_non_zero": sum(1 for value in absolute if value > 0) / len(absolute),
        "median": interpolated_percentile(absolute, 50),
        "percentile_90": interpolated_percentile(absolute, 90),
        "percentile_95": interpolated_percentile(absolute, 95),
        "percentile_99": interpolated_percentile(absolute, 99),
        "maximum": absolute[-1],
    }


def signed_median(values: Iterable[Optional[float]]) -> Optional[float]:
    """
    Median of the values WITH their sign (None ignored; linear
    interpolation for an even count), e.g. [-0.8, -0.6, 0.1] -> -0.6.
    None when there is no value. ``absolute_distribution`` gives the
    median of |value| instead.
    """
    present = sorted(value for value in values if value is not None)
    if not present:
        return None
    return interpolated_percentile(present, 50)


def forward_price_outcomes(prices: Sequence[Optional[float]], step_hours: float) -> Dict[str, Optional[float]]:
    """
    Outcomes of the price path that STARTS at the evaluation instant
    (``prices[0]``) and is sampled every ``step_hours``.

    Returns:
        {"realized_volatility_annualized_percent": sqrt(mean(r^2) *
         periods per year) * 100 with r the log returns (zero-mean
         convention), "absolute_log_return_percent": |ln(last / first)| * 100,
         "maximum_price", "minimum_price" (over prices[1:], the future
         samples)}. All None when any sample is missing or non-positive.
    """
    present = [price for price in prices if price is not None and price > 0]
    if len(prices) < 2 or len(present) != len(prices):
        return {"realized_volatility_annualized_percent": None, "absolute_log_return_percent": None,
                "maximum_price": None, "minimum_price": None}
    returns = [math.log(present[position] / present[position - 1]) for position in range(1, len(present))]
    mean_square = sum(value * value for value in returns) / len(returns)
    return {
        "realized_volatility_annualized_percent":
            100.0 * math.sqrt(mean_square * HOURS_PER_YEAR / step_hours),
        "absolute_log_return_percent": 100.0 * abs(math.log(present[-1] / present[0])),
        "maximum_price": max(present[1:]),
        "minimum_price": min(present[1:]),
    }


def barrier_outcome(
    spot: Optional[float], level: Optional[float], maximum_price: Optional[float],
    minimum_price: Optional[float], final_price: Optional[float],
) -> Optional[Dict[str, bool]]:
    """
    Whether a level was touched over a window and whether price ended back on
    the starting side ("rejected").

    A level above spot is touched when ``maximum_price >= level`` and
    rejected when ``final_price < level``; a level below spot is touched when
    ``minimum_price <= level`` and rejected when ``final_price > level``.

    Returns:
        {"touched", "rejected"} (rejected is False when not touched), or None
        when an input is missing or the level equals spot.
    """
    if (spot is None or level is None or maximum_price is None or minimum_price is None
            or final_price is None or level == spot):
        return None
    if level > spot:
        touched = maximum_price >= level
        rejected = touched and final_price < level
    else:
        touched = minimum_price <= level
        rejected = touched and final_price > level
    return {"touched": touched, "rejected": rejected}


def state_entries(flags: Sequence[Optional[bool]]) -> List[bool]:
    """
    True on each day a condition becomes true after a day it was false
    (an "entry"); a missing day (None) breaks the run and is never an entry,
    and the first day can not be an entry (no previous day to compare).
    """
    entries: List[bool] = []
    for position, flag in enumerate(flags):
        previous = flags[position - 1] if position > 0 else None
        entries.append(bool(flag) and previous is False)
    return entries


def regime_flips(regimes: Sequence[Optional[str]]) -> List[bool]:
    """True on a day whose regime differs from the previous day's (both known, neither "at")."""
    flips: List[bool] = []
    for position, regime in enumerate(regimes):
        previous = regimes[position - 1] if position > 0 else None
        known = (REGIME_SPOT_ABOVE_HVL, REGIME_SPOT_BELOW_HVL)
        flips.append(regime in known and previous in known and regime != previous)
    return flips


def sign_changes(values: Sequence[Optional[float]]) -> List[bool]:
    """True on a day whose value has the opposite strict sign of the previous day's."""
    changes: List[bool] = []
    for position, value in enumerate(values):
        previous = values[position - 1] if position > 0 else None
        changes.append(value is not None and previous is not None and value * previous < 0)
    return changes


def alerts_per_month(flags: Sequence[bool], evaluated_day_count: int) -> Tuple[int, Optional[float]]:
    """(alert count, alerts per 30.44-day month over ``evaluated_day_count`` days)."""
    count = sum(1 for flag in flags if flag)
    if evaluated_day_count <= 0:
        return count, None
    return count, count / (evaluated_day_count / 30.44)
