"""Hand-calculated tests of the MenthorQ-level backtest helpers (pure, no database)."""

from datetime import datetime, timezone

import pytest

from coding.core.analytics.menthorq_levels_backtest_calculator import (
    CHANGE_EXPIRY_SETTLEMENT,
    CHANGE_MIXED,
    CHANGE_NOT_COMPUTED,
    CHANGE_POSITIONING_OR_REPRICING,
    CHANGE_UNCHANGED,
    REGIME_SPOT_ABOVE_HVL,
    REGIME_SPOT_AT_HVL,
    REGIME_SPOT_BELOW_HVL,
    absolute_distribution,
    alerts_per_month,
    attribute_level_change,
    barrier_outcome,
    expiry_list_changes,
    forward_price_outcomes,
    gamma_regime,
    lagged_change,
    percent_of,
    regime_flips,
    settlement_share_of_change,
    sign_changes,
    signed_median,
    state_entries,
    summarize_replication,
)
from coding.core.analytics.menthorq_monitor.level_record import all_expiries_levels


def test_gamma_regime_compares_spot_with_hvl():
    assert gamma_regime(100.0, 90.0) == REGIME_SPOT_ABOVE_HVL
    assert gamma_regime(80.0, 90.0) == REGIME_SPOT_BELOW_HVL
    assert gamma_regime(90.0, 90.0) == REGIME_SPOT_AT_HVL
    assert gamma_regime(None, 90.0) is None
    assert gamma_regime(90.0, None) is None


def test_percent_of_and_missing_reference():
    assert percent_of(50.0, 1000.0) == 5.0
    assert percent_of(50.0, 0.0) is None
    assert percent_of(None, 1000.0) is None


def test_expiry_list_changes_separates_settled_from_vanished_and_added():
    # 27NOV26 settles 2026-11-27 08:00 UTC, before as_of; 4DEC26 has not settled.
    as_of = datetime(2026, 11, 27, 23, 59, tzinfo=timezone.utc)
    changes = expiry_list_changes(["27NOV26", "4DEC26", "25DEC26"], ["25DEC26", "26MAR27"], as_of)
    assert changes == {"settled": ["27NOV26"], "removed_unsettled": ["4DEC26"], "added": ["26MAR27"]}


@pytest.mark.parametrize("previous, without, current, expected", [
    (80000.0, 80000.0, 80000.0, CHANGE_UNCHANGED),
    (80000.0, 85000.0, 85000.0, CHANGE_EXPIRY_SETTLEMENT),
    (80000.0, 80000.0, 85000.0, CHANGE_POSITIONING_OR_REPRICING),
    (80000.0, 82000.0, 85000.0, CHANGE_MIXED),
    (None, 82000.0, 85000.0, CHANGE_NOT_COMPUTED),
    (80000.0, None, 85000.0, CHANGE_NOT_COMPUTED),
])
def test_attribute_level_change(previous, without, current, expected):
    assert attribute_level_change(previous, without, current) == expected


def test_settlement_share_of_change():
    # change 10 -> 1 is -9; removing the settled expiry alone gives 4, i.e. -6 of -9.
    assert settlement_share_of_change(10.0, 4.0, 1.0) == pytest.approx(2.0 / 3.0)
    assert settlement_share_of_change(10.0, 4.0, 10.0) is None
    assert settlement_share_of_change(None, 4.0, 1.0) is None


def test_lagged_change_keeps_missing_as_none():
    assert lagged_change([1.0, None, 4.0, 7.0], 1) == [None, None, None, 3.0]
    assert lagged_change([1.0, None, 4.0, 7.0], 2) == [None, None, 3.0, None]


def test_absolute_distribution_percentiles_by_linear_interpolation():
    # |values| sorted: 0, 1, 2, 3. Position of p is 3 * p / 100.
    distribution = absolute_distribution([-1.0, 2.0, None, 3.0, 0.0])
    assert distribution["count"] == 4
    assert distribution["share_non_zero"] == 0.75
    assert distribution["median"] == pytest.approx(1.5)          # position 1.5
    assert distribution["percentile_90"] == pytest.approx(2.7)   # position 2.7
    assert distribution["percentile_95"] == pytest.approx(2.85)
    assert distribution["percentile_99"] == pytest.approx(2.97)
    assert distribution["maximum"] == 3.0
    assert absolute_distribution([None])["median"] is None


def test_forward_price_outcomes_hand_calculated():
    # r = ln(1.1) = 0.0953102, ln(0.9) = -0.1053605; mean r^2 = 0.0100924;
    # hourly -> * 8760 = 88.410; sqrt = 9.40264 -> 940.26 %.
    outcome = forward_price_outcomes([100.0, 110.0, 99.0], step_hours=1)
    assert outcome["realized_volatility_annualized_percent"] == pytest.approx(940.2645, abs=1e-3)
    assert outcome["absolute_log_return_percent"] == pytest.approx(1.005034, abs=1e-5)  # |ln 0.99|
    assert outcome["maximum_price"] == 110.0
    assert outcome["minimum_price"] == 99.0
    # two-hour steps halve the periods per year: 940.26 / sqrt(2) = 664.87
    assert forward_price_outcomes([100.0, 110.0, 99.0], step_hours=2)[
        "realized_volatility_annualized_percent"] == pytest.approx(664.867, abs=1e-3)


def test_forward_price_outcomes_with_missing_sample_is_none():
    outcome = forward_price_outcomes([100.0, None, 99.0], step_hours=1)
    assert outcome["realized_volatility_annualized_percent"] is None
    assert outcome["maximum_price"] is None


def test_forward_price_outcomes_with_non_positive_or_too_few_samples_is_none():
    for prices in ([100.0, 0.0, 99.0], [100.0, -1.0], [100.0], [None, 100.0]):
        outcome = forward_price_outcomes(prices, step_hours=1)
        assert set(outcome.values()) == {None}


def test_barrier_outcome_is_none_when_any_window_input_is_missing():
    assert barrier_outcome(None, 105.0, 106.0, 98.0, 104.0) is None
    assert barrier_outcome(100.0, 105.0, None, 98.0, 104.0) is None
    assert barrier_outcome(100.0, 105.0, 106.0, None, 104.0) is None
    assert barrier_outcome(100.0, 105.0, 106.0, 98.0, None) is None


def test_barrier_outcome_above_and_below_spot():
    assert barrier_outcome(100.0, 105.0, 106.0, 98.0, 104.0) == {"touched": True, "rejected": True}
    assert barrier_outcome(100.0, 105.0, 106.0, 98.0, 107.0) == {"touched": True, "rejected": False}
    assert barrier_outcome(100.0, 105.0, 104.0, 98.0, 104.0) == {"touched": False, "rejected": False}
    assert barrier_outcome(100.0, 95.0, 101.0, 94.0, 96.0) == {"touched": True, "rejected": True}
    assert barrier_outcome(100.0, 100.0, 101.0, 94.0, 96.0) is None
    assert barrier_outcome(100.0, None, 101.0, 94.0, 96.0) is None


def test_state_entries_regime_flips_and_sign_changes():
    assert state_entries([False, True, True, None, True, False, True]) == [
        False, True, False, False, False, False, True]
    above, below = REGIME_SPOT_ABOVE_HVL, REGIME_SPOT_BELOW_HVL
    assert regime_flips([above, below, below, None, above, REGIME_SPOT_AT_HVL, below]) == [
        False, True, False, False, False, False, False]
    assert sign_changes([1.0, -2.0, -3.0, None, 4.0, 0.0, 5.0]) == [
        False, True, False, False, False, False, False]


def test_alerts_per_month():
    count, per_month = alerts_per_month([True, False, True], 61)
    assert count == 2
    assert per_month == pytest.approx(2 / (61 / 30.44))


def _summary_replication(hvl: float) -> dict:
    """A replication over strikes 48,000 / 49,000 / 52,000 with the given HVL."""
    return {
        "settings": {"index_price": 50000.0, "expirations": ["27NOV26"]},
        "iv_times_open_interest": {
            "levels": {
                "call_resistance": {"net_gex_argmax_positive": 52000.0},
                "put_support": {"net_gex_argmin_negative": 48000.0},
                "hvl": {"cumulative_net_gex_argmin": hvl},
            },
            # 1000 USD per 1% move / (50000 * 0.01) = 2.0 in gamma * OI * S
            "all_expiries_exposure": {"total_net_gex": 1000.0, "instruments_missing_gamma": 3,
                                      "strikes": [{"strike": 48000.0}, {"strike": 49000.0},
                                                  {"strike": 52000.0}]},
        },
        "gex_by_expiration": {"27NOV26": {"total_net_gex": None, "levels": {
            "call_resistance": {"net_gex_argmax_positive": None},
            "put_support": {"net_gex_argmin_negative": None},
            "hvl": {"cumulative_net_gex_argmin": None}}}},
        "panels": {"absolute_net_gex": {"first_expiration": None, "next_expiration": None,
                                        "highest_gex": None, "second_highest_gex": None}},
    }


def test_summarize_replication_converts_units_and_keeps_missing_panels_none():
    summary = summarize_replication(_summary_replication(hvl=49000.0))
    # 49,000 is an inner strike: HVL kept, the same numbers as before the edge rule.
    assert summary["levels"] == {"call_resistance": 52000.0, "put_support": 48000.0, "hvl": 49000.0}
    assert summary["total_net_gex_gamma_open_interest_spot"] == pytest.approx(2.0)
    assert summary["expirations_not_computed"] == ["27NOV26"]
    assert summary["panels"]["first_expiration"] == {
        "expiration": None, "call_resistance": None, "put_support": None, "hvl": None}


@pytest.mark.parametrize("edge_strike", [48000.0, 52000.0])
def test_summarize_replication_drops_hvl_at_the_chain_edge_like_the_monitor(edge_strike):
    replication = _summary_replication(hvl=edge_strike)
    summary = summarize_replication(replication)
    # lowest (48,000) or highest (52,000) strike -> no HVL; the other numbers unchanged
    assert summary["levels"] == {"call_resistance": 52000.0, "put_support": 48000.0, "hvl": None}
    assert summary["total_net_gex_gamma_open_interest_spot"] == pytest.approx(2.0)
    levels, hvl_at_chain_edge = all_expiries_levels(replication)
    assert levels == summary["levels"] and hvl_at_chain_edge is True


def test_signed_median_keeps_the_sign_unlike_the_absolute_median():
    # sorted [-0.8, -0.6, 0.1] -> middle -0.6; |values| sorted [0.1, 0.6, 0.8] -> 0.6
    assert signed_median([-0.8, -0.6, 0.1]) == pytest.approx(-0.6)
    assert absolute_distribution([-0.8, -0.6, 0.1])["median"] == pytest.approx(0.6)
    # even count: midpoint of -1.0 and 3.0 = 1.0; None ignored
    assert signed_median([3.0, None, -1.0]) == pytest.approx(1.0)
    assert signed_median([]) is None and signed_median([None]) is None
