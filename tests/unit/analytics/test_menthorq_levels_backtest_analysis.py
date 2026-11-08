"""Hand-calculated tests of the MenthorQ-level backtest analysis (pure, no database)."""

import pytest

from coding.core.analytics.menthorq_levels_backtest_analysis import (
    alert_threshold_table,
    barrier_rejection_rate,
    level_change_series,
    predictive_tests,
    threshold_for_target_count,
    trend_summary,
)
from coding.core.analytics.menthorq_levels_backtest_calculator import (
    CHANGE_EXPIRY_SETTLEMENT,
    CHANGE_POSITIONING_OR_REPRICING,
    REGIME_SPOT_ABOVE_HVL,
    REGIME_SPOT_BELOW_HVL,
)


def make_rows():
    """Four days; HVL moves +2000 (settlement) then -1000; spot 100000 -> 1% = 1000."""
    hvls = [80000.0, 82000.0, 81000.0, 81000.0]
    attributions = [None, CHANGE_EXPIRY_SETTLEMENT, CHANGE_POSITIONING_OR_REPRICING, "unchanged"]
    regimes = [REGIME_SPOT_ABOVE_HVL, REGIME_SPOT_ABOVE_HVL, REGIME_SPOT_BELOW_HVL, REGIME_SPOT_ABOVE_HVL]
    gex = [5.0, -1.0, -2.0, 3.0]
    return [
        {"date": f"2026-01-0{day + 1}", "index_price": 100000.0, "u_hvl": hvl,
         "u_hvl_change_attribution": attribution, "u_gamma_regime": regime,
         "u_call_resistance": 101000.0, "u_put_support": 90000.0,
         "u_total_net_gex_gamma_open_interest_spot": total}
        for day, (hvl, attribution, regime, total) in enumerate(zip(hvls, attributions, regimes, gex))
    ]


def test_level_change_series_in_usd_and_percent_of_spot():
    series = level_change_series(make_rows(), "u", "hvl", 1)
    assert series["change"] == [None, 2000.0, -1000.0, 0.0]
    assert series["change_percent_of_spot"] == [None, 2.0, -1.0, 0.0]


def test_trend_summary_excludes_settlement_moves_on_request():
    summary = trend_summary(make_rows(), "u")["hvl"]
    assert summary["one_day_usd"]["maximum"] == 2000.0
    # without the settlement day: |moves| 1.0 and 0.0 -> maximum 1.0
    assert summary["one_day_percent_of_spot_excluding_expiry_settlement"]["maximum"] == 1.0
    assert summary["one_day_change_attribution_counts"][CHANGE_EXPIRY_SETTLEMENT] == 1


def test_threshold_for_target_count_takes_the_nth_largest_absolute_value():
    assert threshold_for_target_count([1.0, -5.0, 3.0, None, 0.0, 2.0], 2) == 3.0
    assert threshold_for_target_count([1.0], 2) is None


def test_barrier_rejection_rate():
    # day 1: level 105 touched (max 106), ends 104 -> rejected;
    # day 2: touched, ends 107 -> broken; day 3: not touched.
    result = barrier_rejection_rate([100.0, 100.0, 100.0], [105.0, 105.0, 105.0],
                                    [106.0, 106.0, 101.0], [99.0, 99.0, 99.0], [104.0, 107.0, 100.0])
    assert result == {"touched": 2, "rejected": 1, "rejection_rate": 0.5}


def test_alert_table_counts_flips_sign_changes_and_moves():
    table = alert_threshold_table(make_rows(), "u", target_alerts_per_month=())
    by_rule = {(entry["alert"], entry["threshold"]): entry for entry in table["alerts"]}
    assert table["evaluated_days"] == 4
    # HVL moves of 2% and 1%: threshold 1% -> 2 alerts, 1 of them not settlement-driven
    assert by_rule[("hvl_1d_move", 1.0)]["alert_count"] == 2
    assert by_rule[("hvl_1d_move", 1.0)]["alert_count_excluding_expiry_settlement"] == 1
    assert by_rule[("hvl_1d_move", 2.0)]["alert_count"] == 1
    # regimes above, above, below, above -> 2 flips; GEX 5, -1, -2, 3 -> 2 sign changes
    assert by_rule[("spot_crosses_hvl", None)]["alert_count"] == 2
    assert by_rule[("total_net_gex_sign_change", None)]["alert_count"] == 2
    # Call Resistance always 1% above spot: inside "within 1%" every day, never an entry
    entry = by_rule[("spot_enters_within_n_percent_of_call_resistance", 1.0)]
    assert entry["alert_count"] == 0 and entry["days_inside"] == 4


def test_alert_table_distance_is_not_computed_for_a_missing_level_or_zero_spot():
    rows = make_rows()
    # Call Resistance 3%, 0.5%, missing, 0.5% from spot; day 4's spot is 0 (not computed)
    for row, level in zip(rows, [103000.0, 100500.0, None, 100500.0]):
        row["u_call_resistance"] = level
    rows[3]["index_price"] = 0.0
    table = alert_threshold_table(rows, "u", target_alerts_per_month=())
    by_rule = {(entry["alert"], entry["threshold"]): entry for entry in table["alerts"]}
    # inside within 1%: False, True, None, None -> one entry (day 2), one day inside
    entry = by_rule[("spot_enters_within_n_percent_of_call_resistance", 1.0)]
    assert entry["alert_count"] == 1 and entry["days_inside"] == 1


def test_predictive_tests_run_the_full_fixed_battery_and_find_a_planted_effect():
    rows = []
    for day in range(120):
        below = (day // 10) % 2 == 1                       # alternating 10-day regimes
        spot = 100000.0
        rows.append({
            "date": str(day), "index_price": spot,
            "u_hvl": spot + (1000.0 if below else -1000.0) + (day % 3) * 100.0,
            "u_gamma_regime": REGIME_SPOT_BELOW_HVL if below else REGIME_SPOT_ABOVE_HVL,
            "u_call_resistance": spot * 1.03, "u_put_support": spot * 0.97,
            "u_total_net_gex_gamma_open_interest_spot": -1.0 if below else 1.0,
            "next_1d_realized_volatility_annualized_percent": (80.0 if below else 40.0) + day % 5,
            "next_3d_realized_volatility_annualized_percent": (80.0 if below else 40.0) + day % 7,
            "next_1d_absolute_log_return_percent": 1.0 + day % 4,
            "next_3d_absolute_log_return_percent": 2.0 + day % 4,
            "trailing_1d_realized_volatility_annualized_percent": 50.0 + day % 6,
            "next_1d_maximum_price": spot * 1.01, "next_1d_minimum_price": spot * 0.99,
            "next_1d_final_price": spot, "next_3d_maximum_price": spot * 1.02,
            "next_3d_minimum_price": spot * 0.98, "next_3d_final_price": spot,
        })
    tests = predictive_tests(rows, "u", resample_count=300)
    assert len(tests) == 23
    regime_test = next(test for test in tests if test["test"] == "spot_below_hvl__next_1d_realized_volatility")
    assert regime_test["estimate"] == pytest.approx(40.0, abs=1.0)
    assert regime_test["confidence_interval_95"][0] > 30.0


def test_settlement_share_is_reported_as_signed_and_absolute_median():
    rows = make_rows()
    for row, share in zip(rows, [None, -0.8, -0.6, 0.1]):
        row["u_total_net_gex_settlement_share"] = share
    summary = trend_summary(rows, "u")["total_net_gex_gamma_open_interest_spot"]
    assert summary["settlement_day_count"] == 3
    assert summary["settlement_share_of_change_signed_median"] == pytest.approx(-0.6)
    assert summary["settlement_share_of_change_absolute_median"] == pytest.approx(0.6)
    assert "settlement_share_of_change_median" not in summary      # the ambiguous name is gone


def test_settlement_share_medians_without_settlement_days_are_none():
    summary = trend_summary(make_rows(), "u")["total_net_gex_gamma_open_interest_spot"]
    assert summary["settlement_share_of_change_signed_median"] is None
    assert summary["settlement_share_of_change_absolute_median"] is None
