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
    black76_price,
    build_net_dex_chart_data,
    build_priced_instruments,
    compare_with_reference,
    compute_expiry_iv_times_open_interest,
    compute_exposure,
    compute_gex_expiring_shares,
    compute_iv_times_open_interest,
    compute_leg_greeks,
    compute_net_delta_exposure,
    profile_rescale_factor,
    select_drawn_levels,
    cumulative_by_strike,
    fill_missing_implied_volatility,
    fit_scale_factor,
    implied_volatility_from_mark_price,
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


def _row(expiration, strike, option_type, open_interest, mark_iv, underlying_price=100.0,
         mark_price=None):
    return {
        "instrument_name": f"BTC-{expiration}-{int(strike)}-{option_type}",
        "expiration": expiration,
        "strike": float(strike),
        "option_type": option_type,
        "open_interest": open_interest,
        "mark_iv": mark_iv,
        "underlying_price": underlying_price,
        "mark_price": mark_price,
    }


# Hand calculation, Black-76 (r = 0), F = K = 100, sigma = 0.5, tau = 1:
#   d1 = 0.25, d2 = -0.25
#   C = 100 * (N(0.25) - N(-0.25)) = 100 * (0.598706 - 0.401294) = 19.7413
#   (the at-the-money put is equal by parity: P = C - F + K)
#   mark price in BTC = 19.7413 / F = 0.197413
AT_THE_MONEY_PRICE_USD = 19.7413
AT_THE_MONEY_MARK_PRICE_BTC = 0.197413


class TestImpliedVolatilityFromMarkPrice:
    def test_black76_at_the_money_matches_hand_calculation(self):
        assert black76_price(100.0, 100.0, 1.0, 0.5, "C") == pytest.approx(
            AT_THE_MONEY_PRICE_USD, rel=1e-5)
        assert black76_price(100.0, 100.0, 1.0, 0.5, "P") == pytest.approx(
            AT_THE_MONEY_PRICE_USD, rel=1e-5)

    def test_black76_out_of_the_money_call_matches_hand_calculation(self):
        # F = 80000, K = 90000, sigma = 0.40, tau = 0.25: sigma*sqrt(tau) = 0.2
        #   d1 = ln(0.888889) / 0.2 + 0.1 = -0.488915, d2 = -0.688915
        #   N(d1) = 0.312451, N(d2) = 0.245438
        #   C = 80000 * 0.312451 - 90000 * 0.245438 = 2906.62
        #   P = C - F + K = 12906.62
        assert black76_price(80000.0, 90000.0, 0.25, 0.40, "C") == pytest.approx(2906.62, rel=1e-5)
        assert black76_price(80000.0, 90000.0, 0.25, 0.40, "P") == pytest.approx(12906.62, rel=1e-6)

    def test_inverts_the_hand_calculated_price_back_to_fifty_percent(self):
        assert implied_volatility_from_mark_price(
            AT_THE_MONEY_MARK_PRICE_BTC, 100.0, 100.0, 1.0, "C"
        ) == pytest.approx(50.0, abs=1e-3)

    @pytest.mark.parametrize("strike, option_type", [
        (90000.0, "C"), (90000.0, "P"), (60000.0, "P"), (70000.0, "C"),
    ])
    def test_round_trip_recovers_the_volatility(self, strike, option_type):
        # price from 40% vol, convert USD -> BTC (divide by F), invert -> 40%
        forward = 80000.0
        mark_price_btc = black76_price(forward, strike, 0.25, 0.40, option_type) / forward
        assert implied_volatility_from_mark_price(
            mark_price_btc, forward, strike, 0.25, option_type
        ) == pytest.approx(40.0, abs=1e-6)

    @pytest.mark.parametrize("arguments", [
        (None, 100.0, 100.0, 1.0, "C"),          # no mark price
        (0.197413, None, 100.0, 1.0, "C"),       # no forward
        (0.197413, 100.0, 100.0, 0.0, "C"),      # expired
        (0.05, 100.0, 50.0, 1.0, "C"),           # 5 USD < intrinsic 50
        (1.5, 100.0, 100.0, 1.0, "C"),           # 150 USD > forward 100
        (0.197413, 100.0, 100.0, 1.0, "X"),
    ])
    def test_impossible_or_missing_input_returns_none(self, arguments):
        assert implied_volatility_from_mark_price(*arguments) is None

    def test_fill_labels_every_row_and_only_rebuilds_missing_iv(self):
        rows = [
            _row("27NOV26", 100, "C", 1.0, 40.0, mark_price=0.5),       # mark_iv kept
            _row("27NOV26", 100, "P", 1.0, None, mark_price=AT_THE_MONEY_MARK_PRICE_BTC),
            _row("27NOV26", 110, "C", 1.0, None, mark_price=None),      # nothing to rebuild from
        ]
        filled, counts = fill_missing_implied_volatility(rows, ONE_YEAR_BEFORE)
        assert [row["iv_source"] for row in filled] == [
            "mark_iv", "rebuilt_from_mark_price", "missing"]
        assert filled[0]["mark_iv"] == 40.0
        # tau = 1 year from ONE_YEAR_BEFORE -> the at-the-money hand calculation
        assert filled[1]["mark_iv"] == pytest.approx(50.0, abs=1e-3)
        assert filled[2]["mark_iv"] is None
        assert counts == {"mark_iv": 1, "rebuilt_from_mark_price": 1, "missing": 1}
        assert rows[1]["mark_iv"] is None    # input rows are not modified


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

    def test_no_leg_with_gamma_gives_none_not_zero_or_a_strike(self):
        # mark_iv None on every leg (e.g. 2026-08-24 without the IV fallback).
        # Before the fix: call/put GEX 0.0, levels = lowest strike (90),
        # HVL candidates were strikes too. Now everything is "not computed".
        rows = [_row("27NOV26", 90, "P", 30.0, None), _row("27NOV26", 100, "C", 10.0, None),
                _row("27NOV26", 110, "C", 5.0, None)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 100.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_INDEX
        )
        exposure = compute_exposure(instruments, 100.0, ONE_YEAR_BEFORE)
        assert exposure["total_net_gex"] is None
        assert exposure["total_gross_gex"] is None
        assert exposure["instruments_missing_gamma"] == 3
        assert [row["strike"] for row in exposure["strikes"]] == [90.0, 100.0, 110.0]
        assert exposure["strikes"][0]["put_open_interest"] == 30.0   # observed OI is kept
        for row in exposure["strikes"]:
            assert (row["call_gex"], row["put_gex"], row["net_gex"],
                    row["cumulative_net_gex"]) == (None, None, None, None)
        for candidates in exposure["levels"].values():
            assert all(value is None for value in candidates.values())
        assert exposure["spot_sweep_profile"] == []

    def test_no_leg_with_open_interest_gives_none_not_the_lowest_strike(self):
        # 10NOV26 on 2026-11-06: listed after the 00:00 UTC open-interest
        # snapshot, so every row has gamma but open interest None. Before the
        # fix net GEX was 0 everywhere and HVL became the lowest strike (90).
        rows = [_row("27NOV26", 90, "P", None, 50.0), _row("27NOV26", 100, "C", None, 50.0)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 100.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_INDEX
        )
        exposure = compute_exposure(instruments, 100.0, ONE_YEAR_BEFORE)
        assert exposure["total_net_gex"] is None
        for candidates in exposure["levels"].values():
            assert all(value is None for value in candidates.values())

    def test_missing_forward_gives_none_gamma(self):
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0, underlying_price=None)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 100.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_FORWARD
        )
        assert instruments[0]["gamma"] is None


class TestPerExpiryLevels:
    def test_select_drawn_levels_uses_net_gex_rules(self):
        candidates = {
            "call_resistance": {"call_gex_argmax": 1.0, "net_gex_argmax_positive": 2.0},
            "put_support": {"put_gex_argmax": 3.0, "net_gex_argmin_negative": None},
            "hvl": {"cumulative_net_gex_argmin": 5.0, "spot_sweep_gex_argmin": 6.0},
        }
        assert select_drawn_levels(candidates) == {
            "call_resistance": 2.0, "put_support": None, "hvl": 5.0}

    def test_each_expiry_gets_its_own_levels_and_iv_times_open_interest(self):
        # 27NOV26: call K=100 OI 10 -> net GEX +7.73 at 100 (only strike):
        #   Call Resistance 100, no negative strike -> Put Support None,
        #   cumulative = [+7.73] -> HVL 100. IV*OI: call 0.5 * 10 = 5.
        # 25DEC26: put K=100 OI 4 -> net GEX < 0 at 100:
        #   Call Resistance None, Put Support 100, HVL 100. IV*OI: put 0.5 * 4 = 2.
        # Together the strike is net positive (Put Support None), so the
        # per-expiry levels differ from the all-expiry ones.
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0), _row("25DEC26", 100, "P", 4.0, 50.0)]
        replication = replicate_menthorq(rows, 100.0, ONE_YEAR_BEFORE, EXPIRY_UNIVERSE_ALL)
        november = compute_expiry_iv_times_open_interest(
            rows, "27NOV26", replication["gex_by_expiration"]["27NOV26"])
        december = compute_expiry_iv_times_open_interest(
            rows, "25DEC26", replication["gex_by_expiration"]["25DEC26"])
        assert november["levels"] == {"call_resistance": 100.0, "put_support": None, "hvl": 100.0}
        assert december["levels"] == {"call_resistance": None, "put_support": 100.0, "hvl": 100.0}
        assert november["by_strike"] == {100.0: {"call": pytest.approx(5.0), "put": None}}
        assert december["by_strike"] == {100.0: {"call": None, "put": pytest.approx(2.0)}}
        assert "own net GEX" in november["level_rule"]
        assert replication["net_dex_all_expiries"]["levels"]["put_support"] is None

    def test_expiry_without_gamma_has_no_levels(self):
        rows = [_row("27NOV26", 100, "C", 10.0, None)]
        replication = replicate_menthorq(rows, 100.0, ONE_YEAR_BEFORE, EXPIRY_UNIVERSE_ALL)
        result = compute_expiry_iv_times_open_interest(
            rows, "27NOV26", replication["gex_by_expiration"]["27NOV26"])
        assert result["levels"] == {"call_resistance": None, "put_support": None, "hvl": None}
        assert result["by_strike"] == {}


class TestNetDex:
    def test_net_dex_matches_hand_calculation(self):
        # Delta at the forward 100 (K = 100, sigma 0.5, tau 1): call
        # N(0.25) = 0.598706, put 0.598706 - 1 = -0.401294. Index 80.
        #   net DEX(100) = (0.598706 * 10 - 0.401294 * 4) * 80
        #                = (5.98706 - 1.605176) * 80 = 350.55072
        # K = 110 has no IV: strike None (not 0), one leg missing.
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0), _row("27NOV26", 100, "P", 4.0, 50.0),
                _row("27NOV26", 110, "C", 7.0, None)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 80.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_FORWARD)
        result = compute_net_delta_exposure(instruments, 80.0)
        assert result["net_dex_by_strike"][100.0] == pytest.approx(350.55072, rel=1e-5)
        assert result["net_dex_by_strike"][110.0] is None
        assert result["total_net_dex"] == pytest.approx(350.55072, rel=1e-5)
        assert result["instruments_missing_delta"] == 1

    def test_no_delta_anywhere_gives_none_total(self):
        rows = [_row("27NOV26", 100, "C", 10.0, None)]
        instruments = build_priced_instruments(
            rows, ["27NOV26"], 100.0, ONE_YEAR_BEFORE, GAMMA_UNDERLYING_FORWARD)
        result = compute_net_delta_exposure(instruments, 100.0)
        assert result == {"net_dex_by_strike": {100.0: None}, "total_net_dex": None,
                          "instruments_missing_delta": 1}

    def test_replication_uses_delta_at_forward_even_with_gamma_at_index(self):
        # forward 100, index 80: DEX = 0.598706 * 10 * 80 = 478.9648
        # (delta at the index 80 would be N(ln(0.8)/0.5 + 0.25) = N(-0.196) = 0.422).
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0, underlying_price=100.0)]
        replication = replicate_menthorq(rows, 80.0, ONE_YEAR_BEFORE,
                                         gamma_underlying=GAMMA_UNDERLYING_INDEX)
        (row,) = replication["net_dex_all_expiries"]["strikes"]
        assert row["net_dex"] == pytest.approx(478.9648, rel=1e-5)
        assert replication["net_dex_all_expiries"]["delta_underlying"] == "forward"

    def test_profile_rescale_factor(self):
        # largest |profile| 4, largest |bar| 6 -> 1.5
        assert profile_rescale_factor([1.0, -4.0, 2.0], [3.0, -6.0, None]) == pytest.approx(1.5)
        assert profile_rescale_factor([0.0], [1.0]) is None
        assert profile_rescale_factor([1.0], [None]) is None

    def test_chart_rows_rescale_both_profiles_to_the_largest_dex_bar(self):
        # DEX bars 90: -2, 100: 6, 110: None. Largest |bar| = 6.
        #   cumulative DEX = -2, 4, 4 -> factor 6/4 = 1.5 -> -3, 6, 6
        #   cumulative GEX (90: -3, 100: 1) = -3, -2, -2 -> factor 6/3 = 2 -> -6, -4, -4
        rows = build_net_dex_chart_data({90.0: -2.0, 100.0: 6.0, 110.0: None},
                                        {90.0: -3.0, 100.0: 1.0})
        assert [row["strike"] for row in rows] == [90.0, 100.0, 110.0]
        assert [row["net_dex"] for row in rows] == [-2.0, 6.0, None]
        assert [row["dex_profile"] for row in rows] == pytest.approx([-3.0, 6.0, 6.0])
        assert [row["gex_profile"] for row in rows] == pytest.approx([-6.0, -4.0, -4.0])

    def test_chart_rows_without_gex_have_no_gex_profile(self):
        rows = build_net_dex_chart_data({100.0: 5.0}, {100.0: None})
        assert rows[0]["gex_profile"] is None
        assert rows[0]["dex_profile"] == pytest.approx(5.0)


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

    def test_not_computed_expiry_is_none_and_left_out_of_denominators(self):
        # computed: A -30, B 50 -> |net| sum 80 (C excluded, not counted as 0)
        shares = compute_gex_expiring_shares({
            "A": {"net": -30.0, "gross": 50.0},
            "B": {"net": 50.0, "gross": 70.0},
            "C": {"net": None, "gross": None},
        })
        assert shares["B"]["net_over_absolute_net_sum"] == pytest.approx(62.5)
        assert shares["A"]["gross_over_gross_sum"] == pytest.approx(41.6666667)
        assert shares["C"] == {"net_over_net_sum": None, "net_over_absolute_net_sum": None,
                               "gross_over_gross_sum": None}

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

    def test_not_computed_expiries_are_skipped_for_every_panel(self):
        # D1, D2 (e.g. dailies without gamma) are skipped: first/next become
        # E1/E2, ranking of the rest by |net|: E4 (9) then E3 (1).
        not_computed = {"net": None, "gross": None}
        totals = {
            "D1": not_computed, "E1": {"net": -50.0, "gross": 100.0},
            "D2": not_computed, "E2": {"net": 60.0, "gross": 120.0},
            "E3": {"net": 1.0, "gross": 30.0}, "E4": {"net": -9.0, "gross": 10.0},
        }
        order = ["D1", "E1", "D2", "E2", "E3", "E4"]
        assert select_panel_expirations(order, totals, PANEL_RANKING_ABSOLUTE_NET) == {
            "first_expiration": "E1", "next_expiration": "E2",
            "highest_gex": "E4", "second_highest_gex": "E3"}

    def test_all_not_computed_gives_no_panels(self):
        totals = {"D1": {"net": None, "gross": None}, "D2": {"net": None, "gross": None}}
        assert set(select_panel_expirations(["D1", "D2"], totals).values()) == {None}


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

    def test_expiry_without_gamma_is_not_computed_end_to_end(self):
        # 27NOV26 priced: one call, net = 0.00773336 * 10 * 100^2 * 0.01 = 7.73336.
        # 25DEC26 has no mark_iv: totals None, share None, not a panel.
        rows = [_row("27NOV26", 100, "C", 10.0, 50.0), _row("25DEC26", 100, "C", 10.0, None)]
        replication = replicate_menthorq(rows, 100.0, ONE_YEAR_BEFORE)
        assert replication["settings"]["expirations"] == ["27NOV26", "25DEC26"]
        assert replication["gex_by_expiration"]["27NOV26"]["total_net_gex"] == pytest.approx(
            7.73336, rel=1e-5)
        assert replication["gex_by_expiration"]["25DEC26"]["total_net_gex"] is None
        assert replication["gex_expiring_percent"]["27NOV26"][
            "net_over_absolute_net_sum"] == pytest.approx(100.0)
        assert replication["gex_expiring_percent"]["25DEC26"]["net_over_absolute_net_sum"] is None
        assert replication["panels"][PANEL_RANKING_ABSOLUTE_NET] == {
            "first_expiration": "27NOV26", "next_expiration": None,
            "highest_gex": None, "second_highest_gex": None}

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
