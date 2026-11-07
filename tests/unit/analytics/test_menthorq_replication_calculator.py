"""
Unit tests for the MenthorQ replication calculator. Pure: no database.

Expected values are hand-calculated in the comments next to each assertion.
"""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH,
    GAMMA_UNDERLYING_FORWARD,
    GAMMA_UNDERLYING_INDEX,
    IMPLIED_VOLATILITY_UNIT_DECIMAL,
    IMPLIED_VOLATILITY_UNIT_PERCENT,
    IV_OI_AGGREGATION_MAXIMUM,
    IV_OI_AGGREGATION_SUM,
    PANEL_RANKING_ABSOLUTE_NET,
    PANEL_RANKING_GROSS,
    build_priced_instruments,
    compare_with_reference,
    compute_exposure,
    compute_gex_expiring_shares,
    compute_iv_times_open_interest,
    compute_leg_greeks,
    cumulative_by_strike,
    fit_scale_factor,
    is_monthly_expiry,
    is_quarterly_expiry,
    nearest_strike,
    parse_expiry_settlement,
    replicate_menthorq,
    select_expiry_universe,
    select_panel_expirations,
    start_of_utc_day,
    strike_of_cumulative_minimum,
    strike_of_maximum,
    strike_of_minimum,
)

# 27NOV26 settles 2026-11-27 08:00 UTC; exactly 365 days earlier tau = 1 year.
SETTLEMENT_27NOV26 = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
ONE_YEAR_BEFORE = SETTLEMENT_27NOV26 - timedelta(days=365)

# Hand calculation, S = K = 100, sigma = 0.5, tau = 1, r = q = 0:
#   d1 = (ln(1) + 0.5 * 0.25 * 1) / (0.5 * 1) = 0.25
#   phi(0.25) = exp(-0.03125) / sqrt(2 pi) = 0.969233 / 2.506628 = 0.386668
#   gamma = phi(d1) / (S sigma sqrt(tau)) = 0.386668 / 50 = 0.00773336
#   call delta = N(0.25) = 0.598706
AT_THE_MONEY_GAMMA = 0.00773336


def _row(expiration, strike, option_type, open_interest, mark_iv, underlying_price=100.0):
    return {
        "instrument_name": f"BTC-{expiration}-{int(strike)}-{option_type}",
        "expiration": expiration,
        "strike": float(strike),
        "option_type": option_type,
        "open_interest": open_interest,
        "mark_iv": mark_iv,
        "underlying_price": underlying_price,
    }


class TestBlackScholesGamma:
    def test_at_the_money_gamma_and_delta_match_hand_calculation(self):
        greeks = compute_leg_greeks(100.0, 100.0, 1.0, 0.5, "C")
        assert greeks["gamma"] == pytest.approx(AT_THE_MONEY_GAMMA, rel=1e-5)
        assert greeks["delta"] == pytest.approx(0.598706, rel=1e-5)

    def test_out_of_the_money_gamma_matches_hand_calculation(self):
        # S = 75000, K = 80000, sigma = 0.40, tau = 0.25:
        #   d1 = (ln(0.9375) + 0.5 * 0.16 * 0.25) / (0.4 * 0.5)
        #      = (-0.0645385 + 0.02) / 0.2 = -0.2226926
        #   phi(d1) = exp(-0.0247964) / 2.506628 = 0.389172
        #   gamma = 0.389172 / (75000 * 0.4 * 0.5) = 2.59448e-5
        greeks = compute_leg_greeks(75000.0, 80000.0, 0.25, 0.40, "P")
        assert greeks["gamma"] == pytest.approx(2.59448e-5, rel=1e-4)

    @pytest.mark.parametrize("arguments", [
        (None, 100.0, 1.0, 0.5, "C"),
        (100.0, 100.0, 0.0, 0.5, "C"),
        (100.0, 100.0, 1.0, None, "C"),
        (100.0, 100.0, 1.0, 0.0, "C"),
        (100.0, 100.0, 1.0, 0.5, "X"),
    ])
    def test_missing_or_invalid_input_returns_none_not_zero(self, arguments):
        assert compute_leg_greeks(*arguments) is None


class TestIvTimesOpenInterest:
    ROWS = [
        _row("27NOV26", 80000, "C", 100.0, 50.0),
        _row("25DEC26", 80000, "C", 50.0, 60.0),
        _row("27NOV26", 80000, "P", 10.0, None),     # no IV -> skipped
        _row("20NOV26", 80000, "C", 1000.0, 40.0),   # expiry not selected
        _row("27NOV26", 75000, "P", 20.0, 55.0),
    ]
    EXPIRATIONS = ["27NOV26", "25DEC26"]

    def test_decimal_sum(self):
        # 80000 C: 0.50*100 + 0.60*50 = 80; 75000 P: 0.55*20 = 11
        result = compute_iv_times_open_interest(self.ROWS, self.EXPIRATIONS)
        assert result[80000.0]["call"] == pytest.approx(80.0)
        assert result[80000.0]["put"] is None
        assert result[75000.0] == {"call": None, "put": pytest.approx(11.0)}

    def test_percent_is_one_hundred_times_decimal(self):
        result = compute_iv_times_open_interest(
            self.ROWS, self.EXPIRATIONS, IMPLIED_VOLATILITY_UNIT_PERCENT
        )
        assert result[80000.0]["call"] == pytest.approx(8000.0)

    def test_maximum_keeps_largest_single_expiry(self):
        # max(50, 30) = 50
        result = compute_iv_times_open_interest(
            self.ROWS, self.EXPIRATIONS, IMPLIED_VOLATILITY_UNIT_DECIMAL,
            IV_OI_AGGREGATION_MAXIMUM,
        )
        assert result[80000.0]["call"] == pytest.approx(50.0)

    def test_unknown_unit_raises(self):
        with pytest.raises(ValueError):
            compute_iv_times_open_interest(self.ROWS, self.EXPIRATIONS, "basis_points")


class TestLevelPickers:
    def test_strike_of_maximum_ignores_none_and_breaks_ties_low(self):
        assert strike_of_maximum({70.0: 3.0, 80.0: None, 90.0: 3.0}) == 70.0

    def test_strike_of_maximum_requires_positive(self):
        assert strike_of_maximum({70.0: -1.0, 80.0: -2.0}, require_positive=True) is None
        assert strike_of_maximum({70.0: -1.0, 80.0: -2.0}) == 70.0

    def test_strike_of_minimum_requires_negative(self):
        assert strike_of_minimum({70.0: -1.0, 80.0: -4.0}, require_negative=True) == 80.0
        assert strike_of_minimum({70.0: 1.0}, require_negative=True) is None

    def test_cumulative_minimum(self):
        # cumulative over 70, 75, 80, 85: -5, -15, -11, 9 -> minimum at 75
        net = {80.0: 4.0, 70.0: -5.0, 85.0: 20.0, 75.0: -10.0}
        assert cumulative_by_strike(net) == {70.0: -5.0, 75.0: -15.0, 80.0: -11.0, 85.0: 9.0}
        assert strike_of_cumulative_minimum(net) == 75.0
        assert strike_of_cumulative_minimum({}) is None

    def test_nearest_strike(self):
        assert nearest_strike(76400.0, [75000.0, 76000.0, 78000.0]) == 76000.0
        assert nearest_strike(None, [75000.0]) is None


class TestExpiryHandling:
    def test_parse_single_digit_day(self):
        assert parse_expiry_settlement("7NOV26") == datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
        assert parse_expiry_settlement("BAD") is None

    def test_monthly_and_quarterly_detection(self):
        assert is_monthly_expiry(parse_expiry_settlement("27NOV26"))
        assert not is_monthly_expiry(parse_expiry_settlement("20NOV26"))
        assert is_quarterly_expiry(parse_expiry_settlement("25DEC26"))
        assert is_monthly_expiry(parse_expiry_settlement("29JAN27"))
        assert not is_quarterly_expiry(parse_expiry_settlement("29JAN27"))

    def test_universes_drop_expired_and_sort_chronologically(self):
        as_of = datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)
        labels = ["29JAN27", "6NOV26", "20NOV26", "27NOV26", "25DEC26", "26MAR27"]
        assert select_expiry_universe(labels, EXPIRY_UNIVERSE_ALL, as_of) == [
            "20NOV26", "27NOV26", "25DEC26", "29JAN27", "26MAR27"]
        assert select_expiry_universe(labels, EXPIRY_UNIVERSE_MONTHLY, as_of) == [
            "27NOV26", "25DEC26", "29JAN27", "26MAR27"]
        assert select_expiry_universe(
            labels, EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH, as_of
        ) == ["27NOV26", "25DEC26", "26MAR27"]

    def test_start_of_utc_day_floors_to_midnight_utc(self):
        # 23:59 UTC on Nov 6 -> 00:00 UTC on Nov 6.
        assert start_of_utc_day(datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)) == datetime(
            2026, 11, 6, 0, 0, tzinfo=timezone.utc)

    def test_start_of_utc_day_uses_the_utc_date_not_the_local_date(self):
        # 2026-11-06 20:30 at UTC-5 is 2026-11-07 01:30 UTC -> 2026-11-07 00:00 UTC.
        new_york_winter = timezone(timedelta(hours=-5))
        assert start_of_utc_day(datetime(2026, 11, 6, 20, 30, tzinfo=new_york_winter)) == datetime(
            2026, 11, 7, 0, 0, tzinfo=timezone.utc)

    def test_start_of_utc_day_rejects_naive_datetime(self):
        with pytest.raises(ValueError):
            start_of_utc_day(datetime(2026, 11, 6, 23, 59))


class TestExposure:
    def test_net_gex_and_levels_match_hand_calculation(self):
        # One expiry, S = 100, tau = 1, sigma = 0.5, gamma(K=100) = 0.00773336.
        #   call_gex(100) = 0.00773336 * 10 * 100^2 * 0.01 = 7.73336
        #   put_gex(100)  = 0.00773336 *  4 * 100   = 3.093344
        #   net(100)      = 4.640016
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0), _row("27NOV26", 100, "P", 4.0, 50.0)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 100.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_INDEX
        )
        exposure = compute_exposure(instruments, 100.0, ONE_YEAR_BEFORE)
        (strike_row,) = exposure["strikes"]
        assert strike_row["call_gex"] == pytest.approx(7.73336, rel=1e-5)
        assert strike_row["put_gex"] == pytest.approx(3.093344, rel=1e-5)
        assert strike_row["net_gex"] == pytest.approx(4.640016, rel=1e-5)
        assert exposure["total_gross_gex"] == pytest.approx(10.826704, rel=1e-5)
        assert exposure["levels"]["call_resistance"]["call_gex_argmax"] == 100.0
        assert exposure["levels"]["call_resistance"]["net_gex_argmax_positive"] == 100.0
        assert exposure["levels"]["put_support"]["net_gex_argmin_negative"] is None

    def test_forward_underlying_uses_row_forward(self):
        # gamma at the forward (100) with index 80: same gamma as the
        # at-the-money case; scaled by the index: 0.00773336 * 10 * 80^2 * 0.01
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0, underlying_price=100.0)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 80.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_FORWARD
        )
        assert instruments[0]["gamma"] == pytest.approx(AT_THE_MONEY_GAMMA, rel=1e-5)
        exposure = compute_exposure(instruments, 80.0, ONE_YEAR_BEFORE)
        assert exposure["strikes"][0]["call_gex"] == pytest.approx(4.94935, rel=1e-5)

    def test_missing_forward_gives_none_gamma(self):
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0, underlying_price=None)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 100.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_FORWARD
        )
        assert instruments[0]["gamma"] is None


class TestGexExpiringShares:
    def test_candidates_match_hand_calculation(self):
        # net sum = -30 + 50 = 20; |net| sum = 80; gross sum = 120
        shares = compute_gex_expiring_shares({
            "A": {"net": -30.0, "gross": 50.0},
            "B": {"net": 50.0, "gross": 70.0},
        })
        assert shares["A"]["net_over_net_sum"] == pytest.approx(-150.0)
        assert shares["A"]["net_over_absolute_net_sum"] == pytest.approx(-37.5)
        assert shares["B"]["net_over_absolute_net_sum"] == pytest.approx(62.5)
        assert shares["A"]["gross_over_gross_sum"] == pytest.approx(41.6666667)

    def test_zero_denominator_is_none(self):
        shares = compute_gex_expiring_shares({"A": {"net": 0.0, "gross": 0.0}})
        assert shares["A"] == {
            "net_over_net_sum": None,
            "net_over_absolute_net_sum": None,
            "gross_over_gross_sum": None,
        }


class TestPanelSelection:
    def test_first_next_then_ranked_remaining(self):
        totals = {
            "E1": {"net": -50.0, "gross": 100.0},
            "E2": {"net": 60.0, "gross": 120.0},
            "E3": {"net": 1.0, "gross": 30.0},
            "E4": {"net": -9.0, "gross": 10.0},
            "E5": {"net": 5.0, "gross": 20.0},
        }
        order = ["E1", "E2", "E3", "E4", "E5"]
        by_net = select_panel_expirations(order, totals, PANEL_RANKING_ABSOLUTE_NET)
        assert by_net == {"first_expiration": "E1", "next_expiration": "E2",
                          "highest_gex": "E4", "second_highest_gex": "E5"}
        by_gross = select_panel_expirations(order, totals, PANEL_RANKING_GROSS)
        assert by_gross["highest_gex"] == "E3"
        assert by_gross["second_highest_gex"] == "E5"


class TestScaleFit:
    def test_exact_proportional_fit(self):
        fit = fit_scale_factor([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])
        assert fit["scale"] == pytest.approx(2.0)
        assert fit["r_squared"] == pytest.approx(1.0)
        assert fit["root_mean_square_error"] == pytest.approx(0.0)

    def test_least_squares_through_origin(self):
        # scale = (1*1 + 2*3) / (1 + 4) = 1.4
        fit = fit_scale_factor([1.0, 2.0], [1.0, 3.0])
        assert fit["scale"] == pytest.approx(1.4)

    def test_degenerate_inputs_return_none(self):
        assert fit_scale_factor([1.0], [2.0]) is None
        assert fit_scale_factor([0.0, 0.0], [1.0, 2.0]) is None


class TestReplicateMenthorq:
    def test_rejects_naive_as_of(self):
        with pytest.raises(ValueError):
            replicate_menthorq([], 100.0, datetime(2026, 11, 6, 20, 59))

    def test_end_to_end_and_comparison(self):
        rows = [
            _row("27NOV26", 100, "C", 10.0, 50.0),
            _row("27NOV26", 90, "P", 30.0, 50.0),
            _row("20NOV26", 100, "C", 999.0, 50.0),   # weekly: outside monthly universe
        ]
        replication = replicate_menthorq(rows, 100.0, ONE_YEAR_BEFORE)
        assert replication["settings"]["expirations"] == ["27NOV26"]
        levels = replication["iv_times_open_interest"]["levels"]
        assert levels["call_resistance"]["call_iv_oi_argmax"] == 100.0
        assert levels["put_support"]["put_iv_oi_argmax"] == 90.0
        reference = {
            "iv_times_open_interest": {
                "levels": {"call_resistance": 100.0, "put_support": 90.0},
                "bars": {"100": {"call": 10.0, "put": None}, "90": {"call": None, "put": 30.0}},
            },
            "gex_panels": {"first_expiration": {
                "expiration_date": "2026-11-27", "call_resistance": 100.0,
                "put_support": 90.0, "hvl": 90.0, "gex_expiring_percent": 100.0,
            }},
        }
        comparison = compare_with_reference(replication, reference)
        assert comparison["iv_times_open_interest"]["levels"]["call_resistance"][
            "candidates"]["call_gex_argmax"]["matches"]
        # IV*OI: 0.5*10 = 5 vs 10 and 0.5*30 = 15 vs 30 -> scale exactly 2
        assert comparison["iv_times_open_interest"]["scale_fits"]["decimal_sum"][
            "fit"]["scale"] == pytest.approx(2.0)
        panel = comparison["gex_panels"]["first_expiration"]
        assert panel["panel_choice_matches"]
        assert panel["hvl"]["candidates"]["cumulative_net_gex_argmin"]["matches"]


class TestGexDexCalculatorAsOf:
    """The replay fix: _build_gamma_legs measures tau from an explicit as_of."""

    INSTRUMENT = {"strike": 100.0, "option_type": "C", "open_interest": 1.0,
                  "mark_iv": 50.0, "expiration": "27NOV26"}

    def test_time_to_expiry_measured_from_as_of(self):
        (leg,) = GexDexCalculator._build_gamma_legs([self.INSTRUMENT], as_of=ONE_YEAR_BEFORE)
        assert leg.time_to_expiry_years == pytest.approx(1.0)

    def test_as_of_after_settlement_skips_leg(self):
        after = SETTLEMENT_27NOV26 + timedelta(minutes=1)
        assert GexDexCalculator._build_gamma_legs([self.INSTRUMENT], as_of=after) == []

    def test_calculate_uses_constructor_as_of(self):
        instrument = dict(self.INSTRUMENT, gamma=AT_THE_MONEY_GAMMA, delta=0.6)
        before = GexDexCalculator([instrument], 100.0, as_of=ONE_YEAR_BEFORE).calculate()
        # A single live call: re-priced net gamma is positive at spot.
        assert before.key_levels.gamma_regime == "POSITIVE"
        after = GexDexCalculator(
            [instrument], 100.0, as_of=SETTLEMENT_27NOV26 + timedelta(minutes=1)
        ).calculate()
        # Replayed after settlement the leg is gone: nothing to re-price,
        # independent of the wall clock.
        assert after.key_levels.gamma_regime == "UNKNOWN"
