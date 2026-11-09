"""
Analysis of the daily MenthorQ-level rows built by
``MenthorqLevelsBacktestService``: level-change distributions with
expiry-settlement attribution, a fixed battery of predictive tests against
the NEXT 1-day / 3-day realized volatility and absolute return, and alert
frequencies for candidate thresholds.

Point-in-time: every feature in a row is known at that row's ``as_of``
(23:59 UTC); every outcome is measured strictly after it. Changes compare a
row with an EARLIER row only.

Each daily row is a flat dict. Per expiry universe ``u`` it carries
``{u}_call_resistance``, ``{u}_put_support``, ``{u}_hvl``,
``{u}_total_net_gex_gamma_open_interest_spot``, ``{u}_gamma_regime`` and
``{u}_{level}_change_attribution``; shared columns are ``date``, ``status``,
``index_price`` and the ``next_{1,3}d_*`` / ``trailing_1d_*`` outcomes.

Pure: no database.
"""

import math
from typing import Any, Dict, List, Optional, Sequence

from coding.core.analytics.level_signal_statistics import (
    group_mean_difference,
    moving_block_bootstrap_interval,
    select,
    spearman_rank_correlation,
    two_sided_bootstrap_p_value,
)
from coding.core.analytics.menthorq_levels_backtest_calculator import (
    CHANGE_EXPIRY_SETTLEMENT,
    LEVEL_NAMES,
    REGIME_SPOT_ABOVE_HVL,
    REGIME_SPOT_BELOW_HVL,
    absolute_distribution,
    alerts_per_month,
    barrier_outcome,
    lagged_change,
    percent_of,
    regime_flips,
    sign_changes,
    signed_median,
    state_entries,
)

TOTAL_NET_GEX_COLUMN = "total_net_gex_gamma_open_interest_spot"
DAYS_PER_MONTH = 30.44


def column(rows: Sequence[Dict[str, Any]], name: str) -> List[Any]:
    """One column of the daily rows (None where absent)."""
    return [row.get(name) for row in rows]


def level_change_series(
    rows: Sequence[Dict[str, Any]], universe: str, level: str, lag: int
) -> Dict[str, List[Optional[float]]]:
    """
    Change of a level (or of total net GEX) over ``lag`` rows, in its own
    unit and in percent of the current spot. Rows must be one per calendar
    day, in date order, including days that were not computed.
    """
    values = column(rows, f"{universe}_{level}")
    changes = lagged_change(values, lag)
    spots = column(rows, "index_price")
    return {
        "change": changes,
        "change_percent_of_spot": [percent_of(change, spot) for change, spot in zip(changes, spots)],
    }


def trend_summary(rows: Sequence[Dict[str, Any]], universe: str) -> Dict[str, Any]:
    """
    Distribution of 1-day and 7-day level moves (USD and % of spot) for
    Call Resistance, Put Support and HVL, split by whether the 1-day move is
    attributed to an expiry settling; plus total net GEX changes and the
    share of its change on settlement days that removing the settled expiry
    reproduces.
    """
    summary: Dict[str, Any] = {}
    for level in LEVEL_NAMES:
        one_day = level_change_series(rows, universe, level, 1)
        seven_day = level_change_series(rows, universe, level, 7)
        attributions = column(rows, f"{universe}_{level}_change_attribution")
        category_counts: Dict[str, int] = {}
        for attribution in attributions:
            if attribution is not None:
                category_counts[attribution] = category_counts.get(attribution, 0) + 1
        not_settlement = [change if attribution != CHANGE_EXPIRY_SETTLEMENT else None
                          for change, attribution in zip(one_day["change_percent_of_spot"], attributions)]
        summary[level] = {
            "one_day_usd": absolute_distribution(one_day["change"]),
            "one_day_percent_of_spot": absolute_distribution(one_day["change_percent_of_spot"]),
            "one_day_percent_of_spot_excluding_expiry_settlement": absolute_distribution(not_settlement),
            "seven_day_usd": absolute_distribution(seven_day["change"]),
            "seven_day_percent_of_spot": absolute_distribution(seven_day["change_percent_of_spot"]),
            "one_day_change_attribution_counts": category_counts,
        }
    total_one_day = lagged_change(column(rows, f"{universe}_{TOTAL_NET_GEX_COLUMN}"), 1)
    total_seven_day = lagged_change(column(rows, f"{universe}_{TOTAL_NET_GEX_COLUMN}"), 7)
    shares = [share for share in column(rows, f"{universe}_total_net_gex_settlement_share")
              if share is not None]
    summary["total_net_gex_gamma_open_interest_spot"] = {
        "one_day": absolute_distribution(total_one_day),
        "seven_day": absolute_distribution(total_seven_day),
        "settlement_day_count": len(shares),
        # Signed: a negative share means removing the settled expiry moves net GEX the other way.
        "settlement_share_of_change_signed_median": signed_median(shares),
        "settlement_share_of_change_absolute_median": absolute_distribution(shares)["median"],
        "settlement_share_of_change_values": shares,
    }
    return summary


# --------------------------------------------------------------------------
# Predictive tests
# --------------------------------------------------------------------------

def _difference_test(
    name: str, outcome: Sequence[Optional[float]], in_group: Sequence[Optional[bool]],
    block_length: int, resample_count: int, seed: int, group_description: str,
) -> Dict[str, Any]:
    pairs = [(value, group) for value, group in zip(outcome, in_group)
             if value is not None and group is not None]
    values = [value for value, _ in pairs]
    groups = [group for _, group in pairs]
    estimate = group_mean_difference(values, groups)
    in_values = [value for value, group in pairs if group]
    out_values = [value for value, group in pairs if not group]
    interval = moving_block_bootstrap_interval(
        len(pairs),
        lambda positions: group_mean_difference(select(values, positions), select(groups, positions)),
        block_length, resample_count, seed,
    ) if pairs else {"low": None, "high": None, "share_at_or_below_zero": None, "valid_resamples": 0}
    return {
        "test": name,
        "kind": "difference_of_means",
        "group": group_description,
        "count_in_group": len(in_values),
        "count_out_of_group": len(out_values),
        "mean_in_group": sum(in_values) / len(in_values) if in_values else None,
        "mean_out_of_group": sum(out_values) / len(out_values) if out_values else None,
        "estimate": estimate,
        "confidence_interval_95": [interval["low"], interval["high"]],
        "bootstrap_p_value": two_sided_bootstrap_p_value(interval["share_at_or_below_zero"]),
    }


def _correlation_test(
    name: str, feature: Sequence[Optional[float]], outcome: Sequence[Optional[float]],
    block_length: int, resample_count: int, seed: int,
) -> Dict[str, Any]:
    pairs = [(feature_value, outcome_value) for feature_value, outcome_value in zip(feature, outcome)
             if feature_value is not None and outcome_value is not None]
    features = [feature_value for feature_value, _ in pairs]
    outcomes = [outcome_value for _, outcome_value in pairs]
    interval = moving_block_bootstrap_interval(
        len(pairs),
        lambda positions: spearman_rank_correlation(select(features, positions), select(outcomes, positions)),
        block_length, resample_count, seed,
    ) if pairs else {"low": None, "high": None, "share_at_or_below_zero": None, "valid_resamples": 0}
    return {
        "test": name,
        "kind": "spearman_rank_correlation",
        "count": len(pairs),
        "estimate": spearman_rank_correlation(features, outcomes),
        "confidence_interval_95": [interval["low"], interval["high"]],
        "bootstrap_p_value": two_sided_bootstrap_p_value(interval["share_at_or_below_zero"]),
    }


def barrier_rejection_rate(
    spots: Sequence[Optional[float]], levels: Sequence[Optional[float]],
    maxima: Sequence[Optional[float]], minima: Sequence[Optional[float]],
    finals: Sequence[Optional[float]],
) -> Dict[str, Optional[float]]:
    """Touch count and share of touches that ended back on the starting side."""
    touched = rejected = 0
    for spot, level, maximum, minimum, final in zip(spots, levels, maxima, minima, finals):
        outcome = barrier_outcome(spot, level, maximum, minimum, final)
        if outcome is None or not outcome["touched"]:
            continue
        touched += 1
        rejected += outcome["rejected"]
    return {"touched": touched, "rejected": rejected,
            "rejection_rate": rejected / touched if touched else None}


def _barrier_test(
    name: str, rows: Sequence[Dict[str, Any]], level_column: str, horizon: str,
    minimum_shift: int = 7,
) -> Dict[str, Any]:
    """
    Rejection rate at the real level vs placebo levels at the same relative
    distances taken from other days (circular shifts of the distance series,
    shifts of at least ``minimum_shift`` days, so the level's positioning
    information is removed but the distance distribution is kept).
    """
    kept = [row for row in rows if row.get(level_column) is not None
            and row.get(f"next_{horizon}_final_price") is not None]
    spots = column(kept, "index_price")
    levels = column(kept, level_column)
    maxima = column(kept, f"next_{horizon}_maximum_price")
    minima = column(kept, f"next_{horizon}_minimum_price")
    finals = column(kept, f"next_{horizon}_final_price")
    real = barrier_rejection_rate(spots, levels, maxima, minima, finals)
    distances = [(level - spot) / spot for level, spot in zip(levels, spots)]
    placebo_rates = []
    count = len(kept)
    for shift in range(minimum_shift, count - minimum_shift + 1):
        placebo_levels = [spot * (1.0 + distances[(position + shift) % count])
                          for position, spot in enumerate(spots)]
        rate = barrier_rejection_rate(spots, placebo_levels, maxima, minima, finals)["rejection_rate"]
        if rate is not None:
            placebo_rates.append(rate)
    placebo_rates.sort()
    p_value = (sum(1 for rate in placebo_rates if rate >= real["rejection_rate"]) / len(placebo_rates)
               if placebo_rates and real["rejection_rate"] is not None else None)
    return {
        "test": name,
        "kind": "barrier_rejection_vs_placebo",
        "days": count,
        "touched": real["touched"],
        "rejected": real["rejected"],
        "estimate": real["rejection_rate"],
        "placebo_mean": sum(placebo_rates) / len(placebo_rates) if placebo_rates else None,
        "placebo_range_95": ([placebo_rates[int(0.025 * (len(placebo_rates) - 1))],
                              placebo_rates[int(math.ceil(0.975 * (len(placebo_rates) - 1)))]]
                             if placebo_rates else [None, None]),
        "one_sided_placebo_p_value": p_value,
    }


def predictive_tests(
    rows: Sequence[Dict[str, Any]], universe: str, block_length: int = 5,
    resample_count: int = 2000, seed: int = 20261107,
) -> List[Dict[str, Any]]:
    """
    The fixed battery of tests (decided before looking at results; every
    test run is returned, none dropped).

    Features (known at 23:59 UTC of the day): gamma regime (spot below HVL),
    |1-day HVL / Call Resistance / Put Support move| in % of spot, signed
    (spot - HVL) / spot, total net GEX and its sign. Outcomes: next 1-day /
    3-day annualized realized volatility (hourly index samples) and
    absolute log return. Also the trailing 1-day realized volatility, as a
    confounder check (volatility clusters) and a benchmark predictor.
    """
    tests: List[Dict[str, Any]] = []
    regimes = column(rows, f"{universe}_gamma_regime")
    below = [None if regime not in (REGIME_SPOT_ABOVE_HVL, REGIME_SPOT_BELOW_HVL)
             else regime == REGIME_SPOT_BELOW_HVL for regime in regimes]
    outcomes = {
        "next_1d_realized_volatility": column(rows, "next_1d_realized_volatility_annualized_percent"),
        "next_3d_realized_volatility": column(rows, "next_3d_realized_volatility_annualized_percent"),
        "next_1d_absolute_return": column(rows, "next_1d_absolute_log_return_percent"),
        "next_3d_absolute_return": column(rows, "next_3d_absolute_log_return_percent"),
    }
    trailing = column(rows, "trailing_1d_realized_volatility_annualized_percent")

    def add_difference(name: str, outcome: Sequence[Optional[float]],
                       group: Sequence[Optional[bool]], description: str) -> None:
        tests.append(_difference_test(name, outcome, group, block_length, resample_count,
                                      seed + len(tests), description))

    def add_correlation(name: str, feature: Sequence[Optional[float]],
                        outcome: Sequence[Optional[float]]) -> None:
        tests.append(_correlation_test(name, feature, outcome, block_length, resample_count,
                                       seed + len(tests)))

    for outcome_name, outcome in outcomes.items():
        add_difference(f"spot_below_hvl__{outcome_name}", outcome, below,
                       "spot below HVL (in group) vs above")
    add_difference("spot_below_hvl__trailing_1d_realized_volatility", trailing, below,
                   "confounder check: was volatility already higher below HVL?")

    absolute_hvl_move = [abs(value) if value is not None else None
                         for value in level_change_series(rows, universe, "hvl", 1)["change_percent_of_spot"]]
    for outcome_name, outcome in outcomes.items():
        add_correlation(f"absolute_hvl_move_1d__{outcome_name}", absolute_hvl_move, outcome)
    present_moves = sorted(value for value in absolute_hvl_move if value is not None)
    top_decile_cut = present_moves[int(0.9 * (len(present_moves) - 1))] if present_moves else None
    large_hvl_move = [None if value is None or top_decile_cut is None else value > top_decile_cut
                      for value in absolute_hvl_move]
    add_difference("large_hvl_move_top_decile__next_1d_realized_volatility",
                   outcomes["next_1d_realized_volatility"], large_hvl_move,
                   f"|1-day HVL move| > {top_decile_cut}% of spot (top decile) vs the rest")

    spots = column(rows, "index_price")
    hvls = column(rows, f"{universe}_hvl")
    signed_distance = [percent_of(spot - hvl, spot) if spot is not None and hvl is not None else None
                       for spot, hvl in zip(spots, hvls)]
    add_correlation("spot_minus_hvl_percent__next_1d_realized_volatility", signed_distance,
                    outcomes["next_1d_realized_volatility"])
    add_correlation("spot_minus_hvl_percent__next_3d_realized_volatility", signed_distance,
                    outcomes["next_3d_realized_volatility"])

    total_gex = column(rows, f"{universe}_{TOTAL_NET_GEX_COLUMN}")
    add_correlation("total_net_gex__next_1d_realized_volatility", total_gex,
                    outcomes["next_1d_realized_volatility"])
    add_correlation("total_net_gex__next_3d_realized_volatility", total_gex,
                    outcomes["next_3d_realized_volatility"])
    negative_gex = [None if value is None or value == 0 else value < 0 for value in total_gex]
    add_difference("net_gex_negative__next_1d_realized_volatility",
                   outcomes["next_1d_realized_volatility"], negative_gex,
                   "total net GEX < 0 (in group) vs > 0")
    # Added after the first run (post hoc, disclosed): the same confounder
    # check as for the HVL regime.
    add_difference("net_gex_negative__trailing_1d_realized_volatility", trailing, negative_gex,
                   "confounder check (added after the first run): was volatility already higher?")

    for level in ("call_resistance", "put_support"):
        absolute_move = [abs(value) if value is not None else None for value in
                         level_change_series(rows, universe, level, 1)["change_percent_of_spot"]]
        add_correlation(f"absolute_{level}_move_1d__next_1d_realized_volatility", absolute_move,
                        outcomes["next_1d_realized_volatility"])

    add_correlation("benchmark_trailing_1d_realized_volatility__next_1d_realized_volatility",
                    trailing, outcomes["next_1d_realized_volatility"])

    for level in ("call_resistance", "put_support"):
        for horizon in ("1d", "3d"):
            tests.append(_barrier_test(f"barrier_{level}__{horizon}", rows,
                                       f"{universe}_{level}", horizon))
    return tests


# --------------------------------------------------------------------------
# Alert thresholds
# --------------------------------------------------------------------------

def threshold_for_target_count(values: Sequence[Optional[float]], target_count: int) -> Optional[float]:
    """
    Smallest threshold T (taken from the data) such that at most about
    ``target_count`` values satisfy |value| >= T: the target_count-th
    largest |value|. Ties can make the realized count larger; the caller
    reports the realized count. None when there are too few non-zero values.
    """
    absolute = sorted((abs(value) for value in values if value is not None and value != 0), reverse=True)
    if target_count <= 0 or len(absolute) < target_count:
        return None
    return absolute[target_count - 1]


def _threshold_rows(
    name: str, values: Sequence[Optional[float]], attributions: Optional[Sequence[Optional[str]]],
    fixed_thresholds: Sequence[float], target_alerts_per_month: Sequence[float],
    evaluated_day_count: int, unit: str,
) -> List[Dict[str, Any]]:
    months = evaluated_day_count / DAYS_PER_MONTH
    candidates = [("fixed", threshold) for threshold in fixed_thresholds]
    for target in target_alerts_per_month:
        threshold = threshold_for_target_count(values, max(1, round(target * months)))
        if threshold is not None:
            candidates.append((f"target_{target}_per_month", threshold))
    results = []
    for origin, threshold in candidates:
        flags = [value is not None and abs(value) >= threshold for value in values]
        count, per_month = alerts_per_month(flags, evaluated_day_count)
        entry = {"alert": name, "threshold": threshold, "unit": unit, "threshold_origin": origin,
                 "alert_count": count, "alerts_per_month": per_month}
        if attributions is not None:
            non_settlement = [flag and attribution != CHANGE_EXPIRY_SETTLEMENT
                              for flag, attribution in zip(flags, attributions)]
            entry["alert_count_excluding_expiry_settlement"] = sum(non_settlement)
            entry["alerts_per_month_excluding_expiry_settlement"] = alerts_per_month(
                non_settlement, evaluated_day_count)[1]
        results.append(entry)
    return results


def alert_threshold_table(
    rows: Sequence[Dict[str, Any]], universe: str,
    target_alerts_per_month: Sequence[float] = (1.0, 2.0, 4.0),
) -> Dict[str, Any]:
    """
    Historical alert counts for candidate urgent-alert rules, evaluated once
    per day at 23:59 UTC (intraday crossings are not seen). Frequencies are
    in-sample: the data-derived thresholds are fitted to this same history.
    """
    evaluated = sum(1 for row in rows if row.get(f"{universe}_hvl") is not None)
    table: List[Dict[str, Any]] = []
    for level, fixed in (("hvl", (1.0, 2.0, 3.0, 5.0)),
                         ("call_resistance", (2.0, 5.0, 10.0)),
                         ("put_support", (2.0, 5.0, 10.0))):
        table.extend(_threshold_rows(
            f"{level}_1d_move", level_change_series(rows, universe, level, 1)["change_percent_of_spot"],
            column(rows, f"{universe}_{level}_change_attribution"), fixed, target_alerts_per_month,
            evaluated, "% of spot"))
    settlement_day = [bool(value) for value in column(rows, f"{universe}_settled_expirations")]
    for name, unit, flags in (
            ("spot_crosses_hvl", "daily close vs HVL", regime_flips(column(rows, f"{universe}_gamma_regime"))),
            ("total_net_gex_sign_change", "sign",
             sign_changes(column(rows, f"{universe}_{TOTAL_NET_GEX_COLUMN}")))):
        count, per_month = alerts_per_month(flags, evaluated)
        table.append({"alert": name, "threshold": None, "unit": unit, "threshold_origin": "rule",
                      "alert_count": count, "alerts_per_month": per_month,
                      "alert_count_on_settlement_days": sum(
                          1 for flag, settled in zip(flags, settlement_day) if flag and settled)})
    spots = column(rows, "index_price")
    for level in ("call_resistance", "put_support"):
        levels = column(rows, f"{universe}_{level}")
        signed_distances = [percent_of(level_value - spot, spot) if level_value is not None and spot is not None else None
                            for level_value, spot in zip(levels, spots)]
        distances = [None if distance is None else abs(distance) for distance in signed_distances]
        for within in (0.5, 1.0, 2.0):
            inside = [None if distance is None else distance <= within for distance in distances]
            count, per_month = alerts_per_month(state_entries(inside), evaluated)
            table.append({"alert": f"spot_enters_within_n_percent_of_{level}", "threshold": within,
                          "unit": "% of spot", "threshold_origin": "fixed", "alert_count": count,
                          "alerts_per_month": per_month,
                          "days_inside": sum(1 for flag in inside if flag)})
    return {"evaluated_days": evaluated, "months": evaluated / DAYS_PER_MONTH, "alerts": table}
