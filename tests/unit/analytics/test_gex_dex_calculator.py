"""
Unit tests for GEX/DEX Calculator.

Tests the industry standard formula:
Net GEX = (Call Gamma - Put Gamma) * Spot² * 0.01

T4 (refactor_design_spec.md): calculate() / aggregate_across_expirations()
return the typed GexDexResult (coding/core/analytics/results/gex_dex_results.py)
instead of a dict — assertions here use attribute access. Legacy dict
consumers use GexDexResult.to_dict().
"""

from datetime import datetime, timedelta, timezone

import pytest
from coding.core.analytics.gex_dex_calculator import GexDexCalculator


def _strike_row(result, strike):
    """Find the GexDexStrikeRow for a given strike."""
    return next(r for r in result.strike_rows if r.strike == strike)


class TestGexDexCalculator:
    """Test suite for GexDexCalculator."""

    def test_initialization(self):
        """Test calculator initialization with instruments and spot price."""
        instruments = [
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": 0.00001,
                "delta": 0.5,
                "open_interest": 100,
            }
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)

        assert calculator.spot_price == 70000
        assert len(calculator.instruments) == 1
        assert calculator.strike_data == {}

    def test_gex_formula_with_spot_squared(self):
        """Test that GEX formula uses Spot² * 0.01 (industry standard)."""
        instruments = [
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": 0.00005,  # Per contract
                "delta": 0.5,
                "open_interest": 1000,  # OI
            }
        ]
        spot_price = 70000

        calculator = GexDexCalculator(instruments, spot_price)
        result = calculator.calculate()

        # Expected calculation:
        # call_gamma_weighted = 0.00005 * 1000 = 0.05
        # put_gamma_weighted = 0
        # net_gamma = 0.05 - 0 = 0.05
        # net_gex = 0.05 * (70000^2) * 0.01 = 0.05 * 4,900,000,000 * 0.01 = 2,450,000

        expected_gex = 0.05 * (70000 ** 2) * 0.01
        assert _strike_row(result, 70000).net_gex == pytest.approx(expected_gex)
        assert _strike_row(result, 70000).net_gex == pytest.approx(2450000.0)

    def test_gex_aggregation_by_strike(self):
        """Test that gamma is correctly aggregated by strike and weighted by OI."""
        instruments = [
            # Two calls at same strike with different OI
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": 0.00003,
                "delta": 0.5,
                "open_interest": 500,
            },
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": 0.00002,
                "delta": 0.6,
                "open_interest": 300,
            },
            # One put at same strike
            {
                "strike": 70000,
                "option_type": "P",
                "gamma": 0.00004,
                "delta": -0.4,
                "open_interest": 200,
            },
        ]
        spot_price = 70000

        calculator = GexDexCalculator(instruments, spot_price)
        result = calculator.calculate()

        row = _strike_row(result, 70000)

        # Check aggregation
        # call_gamma = (0.00003 * 500) + (0.00002 * 300) = 0.015 + 0.006 = 0.021
        # put_gamma = 0.00004 * 200 = 0.008
        assert row.call_gamma == pytest.approx(0.021)
        assert row.put_gamma == pytest.approx(0.008)

        # net_gamma = 0.021 - 0.008 = 0.013
        # net_gex = 0.013 * (70000^2) * 0.01 = 637,000
        expected_net_gex = 0.013 * (70000 ** 2) * 0.01
        assert row.net_gex == pytest.approx(expected_net_gex)

    def test_dex_calculation(self):
        """Test DEX calculation (sum of call delta + put delta)."""
        instruments = [
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": 0.00001,
                "delta": 0.6,
                "open_interest": 100,
            },
            {
                "strike": 70000,
                "option_type": "P",
                "gamma": 0.00001,
                "delta": -0.4,
                "open_interest": 150,
            },
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        row = _strike_row(result, 70000)

        # call_delta_weighted = 0.6 * 100 = 60
        # put_delta_weighted = -0.4 * 150 = -60
        # net_dex = 60 + (-60) = 0
        assert row.call_delta == pytest.approx(60.0)
        assert row.put_delta == pytest.approx(-60.0)
        assert row.net_dex == pytest.approx(0.0)

    def test_call_resistance_detection(self):
        """Test Call Resistance is strike with maximum positive Net GEX."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 72000, "option_type": "C", "gamma": 0.00008, "delta": 0.4, "open_interest": 1000},  # Highest
            {"strike": 74000, "option_type": "C", "gamma": 0.00003, "delta": 0.3, "open_interest": 1000},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # Strike 72000 should have highest positive GEX
        assert result.key_levels.call_resistance.strike == 72000

    def test_put_support_detection(self):
        """Test Put Support is strike with maximum negative Net GEX."""
        instruments = [
            {"strike": 68000, "option_type": "P", "gamma": 0.00005, "delta": -0.5, "open_interest": 1000},
            {"strike": 66000, "option_type": "P", "gamma": 0.00008, "delta": -0.6, "open_interest": 1000},  # Most negative
            {"strike": 64000, "option_type": "P", "gamma": 0.00003, "delta": -0.7, "open_interest": 1000},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # Strike 66000 should have most negative GEX
        assert result.key_levels.put_support.strike == 66000

    def test_hvl_zero_crossing(self):
        """Test HVL detection at cumulative GEX zero crossing."""
        instruments = [
            # Below spot: net positive GEX (calls > puts)
            {"strike": 65000, "option_type": "C", "gamma": 0.00010, "delta": 0.7, "open_interest": 500},
            {"strike": 65000, "option_type": "P", "gamma": 0.00002, "delta": -0.3, "open_interest": 500},
            # At spot: balanced
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 500},
            {"strike": 70000, "option_type": "P", "gamma": 0.00005, "delta": -0.5, "open_interest": 500},
            # Above spot: net negative GEX (puts > calls)
            {"strike": 75000, "option_type": "C", "gamma": 0.00002, "delta": 0.3, "open_interest": 500},
            {"strike": 75000, "option_type": "P", "gamma": 0.00010, "delta": -0.7, "open_interest": 500},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # HVL should be detected where cumulative flips sign
        # In this case, 70k or 75k depending on cumulative sum
        assert result.key_levels.hvl in [70000, 75000]
        # Task Wave-J-B Fix 1: a genuine crossing means hvl and gamma_flip
        # (the field never touched by the deleted fabrication fallbacks)
        # must agree exactly -- restoring the historical invariant broken
        # by those fallbacks (gamma_flip stayed clean; hvl didn't).
        assert result.key_levels.hvl == result.key_levels.gamma_flip

    def test_hvl_is_none_when_cumulative_gex_never_crosses_zero(self):
        """
        Task Wave-J-B Fix 1 (BLOCKER repro): a book where cumulative net GEX
        is negative at EVERY strike -- one deep-ITM put with large gamma at
        a low strike, one small OTM call at a high strike, chosen so the
        running cumulative sum starts deeply negative and the small
        positive contribution from the call never pulls it back to zero.
        Before this fix, ``hvl`` (rendered as "Cumulative GEX Zero Strike",
        persisted into the live ``hvl_level`` DB column) was fabricated
        anyway -- first via a "nearest net-GEX flip to spot" fallback, then
        via a "smallest |cumulative GEX|" fallback that always finds SOME
        strike. Both are now deleted: hvl (and gamma_flip, which was never
        touched by the buggy fallbacks) must both be None.
        """
        instruments = [
            # Deep ITM put, large gamma, low strike -> large negative net GEX.
            {"strike": 50000, "option_type": "P", "gamma": 0.00050, "delta": -0.95, "open_interest": 1000},
            # Small OTM call, high strike -> small positive net GEX, nowhere
            # near enough to offset the put's contribution.
            {"strike": 90000, "option_type": "C", "gamma": 0.00001, "delta": 0.05, "open_interest": 100},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # Confirm the premise: cumulative net GEX is negative at every
        # strike -- never actually zero, never changes sign.
        assert result.cumulative_gex[50000] < 0
        assert result.cumulative_gex[90000] < 0

        assert result.key_levels.hvl is None
        assert result.key_levels.gamma_flip is None
        assert result.key_levels.cumulative_gex_zero_strike is None

    def test_cumulative_gex_calculation(self):
        """Test cumulative GEX is correctly computed as running sum."""
        instruments = [
            {"strike": 65000, "option_type": "C", "gamma": 0.00005, "delta": 0.7, "open_interest": 1000},
            {"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.5, "open_interest": 1000},
            {"strike": 75000, "option_type": "P", "gamma": 0.00004, "delta": -0.3, "open_interest": 1000},
        ]
        spot_price = 70000
        calculator = GexDexCalculator(instruments, spot_price)
        result = calculator.calculate()

        # Calculate expected cumulative values
        # net_gamma (weighted by OI) = gamma * OI
        # gex = net_gamma * spot^2 * 0.01
        gex_65k = (0.00005 * 1000) * (spot_price ** 2) * 0.01  # 0.05 * 4.9e9 * 0.01
        gex_70k = (0.00003 * 1000) * (spot_price ** 2) * 0.01  # 0.03 * 4.9e9 * 0.01
        gex_75k = -(0.00004 * 1000) * (spot_price ** 2) * 0.01  # -0.04 * 4.9e9 * 0.01

        cumulative_65k = gex_65k
        cumulative_70k = gex_65k + gex_70k
        cumulative_75k = gex_65k + gex_70k + gex_75k

        assert result.cumulative_gex[65000] == pytest.approx(cumulative_65k)
        assert result.cumulative_gex[70000] == pytest.approx(cumulative_70k)
        assert result.cumulative_gex[75000] == pytest.approx(cumulative_75k)

    def test_empty_instruments(self):
        """Test calculator handles empty instrument list gracefully."""
        calculator = GexDexCalculator([], spot_price=70000)
        result = calculator.calculate()

        assert result.strike_rows == ()
        assert result.key_levels.call_resistance is None
        assert result.key_levels.put_support is None
        assert result.key_levels.hvl is None
        assert result.total_net_gex == 0
        assert result.total_net_dex == 0

    def test_missing_greeks_handled(self):
        """Test that missing gamma/delta are treated as zero."""
        instruments = [
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": None,  # Missing
                "delta": None,  # Missing
                "open_interest": 100,
            }
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # Should default to zero
        row = _strike_row(result, 70000)
        assert row.call_gamma == 0.0
        assert row.call_delta == 0.0
        assert row.net_gex == 0.0
        assert row.net_dex == 0.0

    def test_missing_oi_handled(self):
        """Test that missing OI is treated as zero (no contribution to GEX)."""
        instruments = [
            {
                "strike": 70000,
                "option_type": "C",
                "gamma": 0.00005,
                "delta": 0.5,
                "open_interest": None,  # Missing
            }
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # With OI = 0, weighted gamma = 0
        row = _strike_row(result, 70000)
        assert row.call_gamma == 0.0
        assert row.net_gex == 0.0

    def test_all_positive_gex(self):
        """Test edge case where all strikes have positive GEX (no put support)."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 72000, "option_type": "C", "gamma": 0.00008, "delta": 0.4, "open_interest": 1000},
            {"strike": 74000, "option_type": "C", "gamma": 0.00003, "delta": 0.3, "open_interest": 1000},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # Should have call resistance but no put support
        assert result.key_levels.call_resistance is not None
        assert result.key_levels.put_support is None

    def test_all_negative_gex(self):
        """Test edge case where all strikes have negative GEX (no call resistance)."""
        instruments = [
            {"strike": 68000, "option_type": "P", "gamma": 0.00005, "delta": -0.5, "open_interest": 1000},
            {"strike": 66000, "option_type": "P", "gamma": 0.00008, "delta": -0.6, "open_interest": 1000},
            {"strike": 64000, "option_type": "P", "gamma": 0.00003, "delta": -0.7, "open_interest": 1000},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # Should have put support but no call resistance
        assert result.key_levels.call_resistance is None
        assert result.key_levels.put_support is not None

    def test_total_net_gex_calculation(self):
        """Test total net GEX is sum of all strikes."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 72000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 1000},
        ]
        spot_price = 70000
        calculator = GexDexCalculator(instruments, spot_price)
        result = calculator.calculate()

        # Manual calculation
        # net_gamma = gamma * OI
        # gex = net_gamma * spot^2 * 0.01
        gex_70k = (0.00005 * 1000) * (spot_price ** 2) * 0.01  # Call: positive
        gex_72k = -(0.00003 * 1000) * (spot_price ** 2) * 0.01  # Put: negative
        expected_total = gex_70k + gex_72k

        assert result.total_net_gex == pytest.approx(expected_total)

    def test_total_net_dex_calculation(self):
        """Test total net DEX is sum of all strikes."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 100},
            {"strike": 72000, "option_type": "P", "gamma": 0.00003, "delta": -0.3, "open_interest": 200},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        result = calculator.calculate()

        # call_delta = 0.5 * 100 = 50
        # put_delta = -0.3 * 200 = -60
        # total = 50 - 60 = -10
        expected_total_dex = (0.5 * 100) + (-0.3 * 200)
        assert result.total_net_dex == pytest.approx(expected_total_dex)

    # NOTE (task A7, carried finding #1): test_report_generation,
    # test_gex_report_shows_usd_unit, test_dex_report_shows_currency_unit_eth,
    # and test_dex_report_shows_currency_unit_btc used to live here, covering
    # GexDexCalculator.generate_report_section() (deleted -- zero production
    # callers, only test call sites). Equivalent (better) coverage now lives
    # in tests/unit/analytics/reporting/test_gex_dex_formatter.py, which
    # exercises the actual live render path (format_gex_dex_section) against
    # a typed GexDexResult: test_gex_dex_section_key_levels_and_totals (USD
    # label, key levels, totals), test_gex_dex_section_no_levels_found,
    # test_gex_dex_section_neutral_environment (currency-unit label via the
    # DEX line).

    def test_multiple_strikes_multiple_instruments(self):
        """Test realistic scenario with multiple strikes and instruments."""
        instruments = [
            # Strike 68000
            {"strike": 68000, "option_type": "C", "gamma": 0.00004, "delta": 0.6, "open_interest": 500},
            {"strike": 68000, "option_type": "P", "gamma": 0.00008, "delta": -0.4, "open_interest": 800},
            # Strike 70000
            {"strike": 70000, "option_type": "C", "gamma": 0.00006, "delta": 0.5, "open_interest": 1000},
            {"strike": 70000, "option_type": "P", "gamma": 0.00006, "delta": -0.5, "open_interest": 1000},
            # Strike 72000
            {"strike": 72000, "option_type": "C", "gamma": 0.00008, "delta": 0.4, "open_interest": 800},
            {"strike": 72000, "option_type": "P", "gamma": 0.00004, "delta": -0.6, "open_interest": 500},
        ]
        spot_price = 70000
        calculator = GexDexCalculator(instruments, spot_price)
        result = calculator.calculate()

        strikes = {row.strike for row in result.strike_rows}

        # Check all strikes are present
        assert 68000 in strikes
        assert 70000 in strikes
        assert 72000 in strikes

        # Verify calculations for 68000
        # call_gamma = 0.00004 * 500 = 0.02
        # put_gamma = 0.00008 * 800 = 0.064
        # net_gamma = 0.02 - 0.064 = -0.044
        # net_gex = -0.044 * (70000^2) * 0.01
        expected_gex_68k = -0.044 * (spot_price ** 2) * 0.01
        assert _strike_row(result, 68000).net_gex == pytest.approx(expected_gex_68k)

        # Verify calculations for 72000
        # call_gamma = 0.00008 * 800 = 0.064
        # put_gamma = 0.00004 * 500 = 0.02
        # net_gamma = 0.064 - 0.02 = 0.044
        # net_gex = 0.044 * (70000^2) * 0.01
        expected_gex_72k = 0.044 * (spot_price ** 2) * 0.01
        assert _strike_row(result, 72000).net_gex == pytest.approx(expected_gex_72k)

        # Call resistance should be 72000 (positive GEX)
        # Put support should be 68000 (negative GEX)
        assert result.key_levels.call_resistance.strike == 72000
        assert result.key_levels.put_support.strike == 68000


class TestIdempotentCalculateAndResultPassing:
    """
    Regression tests for the confirmed production bug (bugfix_spec.md Item 1):
    calculate() never reset strike_data, so calling generate_report_section()
    after calculate() doubled every stored total (and tripled it on a 2nd render).

    Fixture and expected numbers are verbatim from bugfix_spec.md section 1.1/1.5.
    spot = 100,000:
      K=100,000: net_gamma = 0.00002*100 - 0.00001*50 = 0.0015 -> net_gex = +150,000; net_dex = +25
      K=110,000: net_gamma = 0.00001*40 - 0.00003*80 = -0.0020 -> net_gex = -200,000; net_dex = -44
      total_net_gex = -50,000.00, total_net_dex = -19.0000
    """

    FIXTURE = [
        {"strike": 100_000, "option_type": "C", "gamma": 0.00002, "delta": 0.5, "open_interest": 100},
        {"strike": 100_000, "option_type": "P", "gamma": 0.00001, "delta": -0.5, "open_interest": 50},
        {"strike": 110_000, "option_type": "C", "gamma": 0.00001, "delta": 0.3, "open_interest": 40},
        {"strike": 110_000, "option_type": "P", "gamma": 0.00003, "delta": -0.7, "open_interest": 80},
    ]

    def test_calculate_is_idempotent(self):
        """T1.1 - calling calculate() twice must yield identical totals and strike_data."""
        calc = GexDexCalculator(self.FIXTURE, 100_000.0, "BTC")
        first = calc.calculate()
        second = calc.calculate()

        assert first.total_net_gex == second.total_net_gex == pytest.approx(-50_000.0)
        assert first.total_net_dex == second.total_net_dex == pytest.approx(-19.0)
        assert calc.strike_data[100_000.0]["call_oi"] == pytest.approx(100.0)

    def test_calculate_result_has_correct_undoubled_per_strike_values(self):
        """
        T1.2 (adapted, task A7 carried finding #1): the original test named
        this "generate_report_section with result= does not recompute" and
        exercised the now-deleted dead-code report method to prove it. The
        actual regression this guards -- calculate() itself must produce
        correct, un-doubled per-strike net_gex -- doesn't need the report
        path at all; keeping only that assertion here.
        """
        calc = GexDexCalculator(self.FIXTURE, 100_000.0, "BTC")
        stored = calc.calculate()

        assert _strike_row(stored, 100_000.0).net_gex == pytest.approx(150_000.0)
        assert _strike_row(stored, 110_000.0).net_gex == pytest.approx(-200_000.0)
        assert calc.strike_data[100_000.0]["call_oi"] == pytest.approx(100.0)

    # NOTE (task A7, carried finding #1): test_repeated_report_generation_
    # without_result_does_not_drift (T1.3) used to live here, calling the
    # now-deleted generate_report_section() 3 times with no result= to prove
    # repeated renders don't accumulate. That guard is now structurally
    # redundant with test_calculate_is_idempotent (T1.1) above -- there is
    # no report-rendering path left that can call calculate() a second time.

    def test_aggregate_across_expirations_matches_true_sum(self):
        """T1.4 - service-level regression: aggregate must not double-count."""
        a = GexDexCalculator(self.FIXTURE, 100_000.0, "BTC")
        ra = a.calculate()
        b = GexDexCalculator(self.FIXTURE, 100_000.0, "BTC")
        rb = b.calculate()

        agg = GexDexCalculator.aggregate_across_expirations({"A": ra, "B": rb}, 100_000.0, "BTC")

        assert agg.total_net_gex == pytest.approx(-100_000.0)  # was -200,000.00 before the fix
        assert agg.total_net_dex == pytest.approx(-38.0)  # was -76.0000 before the fix
        assert agg.expiration_count == 2


class TestAggregateAcrossExpirations:
    """Tests for GexDexCalculator.aggregate_across_expirations."""

    def _make_expiry_result(self, instruments, spot_price=70000, currency="BTC"):
        """Helper: run calculate() and return the typed result."""
        calc = GexDexCalculator(instruments, spot_price=spot_price, currency=currency)
        return calc.calculate()

    def test_aggregate_single_expiry_matches_original(self):
        """Aggregate of one expiry should match running that expiry alone."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 72000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 800},
        ]
        spot_price = 70000
        original = self._make_expiry_result(instruments, spot_price)

        agg = GexDexCalculator.aggregate_across_expirations(
            {"27DEC24": original}, spot_price, currency="BTC"
        )

        assert agg.total_net_gex == pytest.approx(original.total_net_gex)
        assert agg.total_net_dex == pytest.approx(original.total_net_dex)
        assert agg.expiration_count == 1
        assert {r.strike for r in agg.strike_rows} == {r.strike for r in original.strike_rows}

    def test_aggregate_overlapping_strikes_sums_correctly(self):
        """Overlapping strikes across two expirations should be summed."""
        inst_exp1 = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00002, "delta": 0.5, "open_interest": 500},
        ]
        inst_exp2 = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00003, "delta": 0.6, "open_interest": 400},
        ]
        spot_price = 70000

        result_exp1 = self._make_expiry_result(inst_exp1, spot_price)
        result_exp2 = self._make_expiry_result(inst_exp2, spot_price)

        agg = GexDexCalculator.aggregate_across_expirations(
            {"27DEC24": result_exp1, "28MAR25": result_exp2}, spot_price, "BTC"
        )

        # call_gamma at 70k should be sum of both: (0.00002*500) + (0.00003*400) = 0.01 + 0.012 = 0.022
        expected_call_gamma = (0.00002 * 500) + (0.00003 * 400)
        assert _strike_row(agg, 70000).call_gamma == pytest.approx(expected_call_gamma)

        expected_net_gamma = expected_call_gamma  # no puts
        expected_net_gex = expected_net_gamma * (spot_price ** 2) * 0.01
        assert _strike_row(agg, 70000).net_gex == pytest.approx(expected_net_gex)
        assert agg.expiration_count == 2

    def test_aggregate_non_overlapping_strikes_all_present(self):
        """Strikes unique to each expiry should all appear in aggregate."""
        inst_exp1 = [
            {"strike": 68000, "option_type": "C", "gamma": 0.00002, "delta": 0.5, "open_interest": 100},
        ]
        inst_exp2 = [
            {"strike": 75000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 200},
        ]
        result_exp1 = self._make_expiry_result(inst_exp1)
        result_exp2 = self._make_expiry_result(inst_exp2)

        agg = GexDexCalculator.aggregate_across_expirations(
            {"27DEC24": result_exp1, "28MAR25": result_exp2}, 70000, "BTC"
        )

        strikes = {r.strike for r in agg.strike_rows}
        assert 68000 in strikes
        assert 75000 in strikes
        assert agg.expiration_count == 2

    def test_aggregate_key_levels_differ_from_single_expiry(self):
        """After combining, dominant levels may shift versus any single expiry."""
        inst_exp1 = [
            # exp1: call resistance at 72000
            {"strike": 70000, "option_type": "C", "gamma": 0.00001, "delta": 0.5, "open_interest": 100},
            {"strike": 72000, "option_type": "C", "gamma": 0.00010, "delta": 0.4, "open_interest": 1000},
        ]
        inst_exp2 = [
            # exp2: much larger positive GEX at 74000
            {"strike": 74000, "option_type": "C", "gamma": 0.00010, "delta": 0.3, "open_interest": 5000},
        ]
        result_exp1 = self._make_expiry_result(inst_exp1)
        result_exp2 = self._make_expiry_result(inst_exp2)

        # exp1 alone: call resistance at 72000
        assert result_exp1.key_levels.call_resistance.strike == 72000

        agg = GexDexCalculator.aggregate_across_expirations(
            {"27DEC24": result_exp1, "28MAR25": result_exp2}, 70000, "BTC"
        )

        # Combined: 74000 has far more gamma (0.0001 * 5000 = 0.5 vs 0.0001 * 1000 = 0.1)
        assert agg.key_levels.call_resistance.strike == 74000

    def test_aggregate_empty_input_returns_empty_result(self):
        """aggregate_across_expirations with empty dict should return zero totals."""
        agg = GexDexCalculator.aggregate_across_expirations({}, spot_price=70000)

        assert agg.total_net_gex == 0.0
        assert agg.total_net_dex == 0.0
        assert agg.strike_rows == ()
        assert agg.key_levels.call_resistance is None
        assert agg.key_levels.put_support is None
        assert agg.key_levels.hvl is None
        assert agg.expiration_count == 0

    def test_aggregate_skips_existing_aggregate_key(self):
        """An "AGGREGATE" key already present should not be double-counted."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
        ]
        result = self._make_expiry_result(instruments)
        # Simulate a pre-existing AGGREGATE entry being passed in
        by_expiry = {"27DEC24": result, "AGGREGATE": result}

        agg = GexDexCalculator.aggregate_across_expirations(by_expiry, 70000, "BTC")

        # Should count only 1 real expiry
        assert agg.expiration_count == 1

    # NOTE (task A7, carried finding #1): test_aggregate_report_section_
    # contains_expected_content used to live here, covering
    # GexDexCalculator.generate_aggregate_report_section() (deleted -- zero
    # production callers). Equivalent coverage now lives in
    # tests/unit/analytics/reporting/test_gex_dex_formatter.py::
    # test_aggregate_gex_dex_section_has_expiration_count_and_no_strike_table,
    # exercising the actual live render path (format_aggregate_gex_dex_section).


class TestBuildGammaLegs:
    """
    Direct unit coverage for GexDexCalculator._build_gamma_legs
    (bugfix_spec.md Item 2 / task B1 review Important #2): previously only
    covered transitively through the golden master, which would not have
    caught a silent degrade (e.g. an upstream field rename causing zero legs
    to build -- just a mysteriously-UNKNOWN gamma profile, no failing test).
    """

    @staticmethod
    def _future_expiration(days: int = 30) -> str:
        """A Deribit-format expiration label ('%d%b%y') safely in the future
        relative to real wall-clock time, so these tests never depend on --
        or drift with -- today's date."""
        return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%d%b%y").upper()

    def test_leg_built_correctly_from_well_formed_instrument(self):
        instrument = {
            "strike": 70000, "option_type": "C", "open_interest": 150,
            "mark_iv": 55.0, "expiration": self._future_expiration(30),
        }
        legs = GexDexCalculator._build_gamma_legs([instrument])

        assert len(legs) == 1
        leg = legs[0]
        assert leg.strike == 70000.0
        assert leg.option_type == "C"
        assert leg.open_interest == 150.0
        assert leg.implied_volatility == pytest.approx(0.55)
        # ~30 days to expiry -> a small positive fraction of a year.
        assert 0.0 < leg.time_to_expiry_years < (45 / 365)

    def test_iv_scaling_mark_iv_50_gives_sigma_0_50(self):
        instrument = {
            "strike": 70000, "option_type": "P", "open_interest": 10,
            "mark_iv": 50.0, "expiration": self._future_expiration(30),
        }
        legs = GexDexCalculator._build_gamma_legs([instrument])

        assert legs[0].implied_volatility == pytest.approx(0.50)

    def test_skips_leg_with_missing_mark_iv(self):
        instrument = {
            "strike": 70000, "option_type": "C", "open_interest": 10,
            "mark_iv": None, "expiration": self._future_expiration(30),
        }
        assert GexDexCalculator._build_gamma_legs([instrument]) == []

    def test_skips_leg_with_zero_open_interest(self):
        instrument = {
            "strike": 70000, "option_type": "C", "open_interest": 0,
            "mark_iv": 50.0, "expiration": self._future_expiration(30),
        }
        assert GexDexCalculator._build_gamma_legs([instrument]) == []

    def test_skips_leg_with_unparseable_expiration(self):
        instrument = {
            "strike": 70000, "option_type": "C", "open_interest": 10,
            "mark_iv": 50.0, "expiration": "not-a-date",
        }
        assert GexDexCalculator._build_gamma_legs([instrument]) == []


class TestGammaProfileSafetyNet:
    """
    bugfix_spec.md Item 2 / task B1 review Important #1: the gamma-profile
    computation is ADDITIVE only (zero_gamma_level etc.) and must never be
    able to abort calculate() -- the same method that produces the value
    persisted into the live hvl_level DB column. A malformed field feeding
    only the new computation (e.g. a non-numeric mark_iv) must degrade to
    the "insufficient data" shape rather than raising TypeError/ValueError
    uncaught, which would otherwise abort the whole currency's on-chain
    analysis (task A6 made the daemon re-raise TypeError/AttributeError
    precisely so genuine shape-mismatch bugs are loud -- this guards against
    an unrelated data-quality issue in the new field accidentally triggering
    that same loud path).
    """

    def test_malformed_mark_iv_does_not_raise_and_hvl_path_is_unaffected(self):
        instruments = [
            {"strike": 65000, "option_type": "C", "gamma": 0.00010, "delta": 0.7,
             "open_interest": 500, "mark_iv": "not-a-number", "expiration": "27DEC24"},
            {"strike": 65000, "option_type": "P", "gamma": 0.00002, "delta": -0.3,
             "open_interest": 500, "mark_iv": "not-a-number", "expiration": "27DEC24"},
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5,
             "open_interest": 500, "mark_iv": "not-a-number", "expiration": "27DEC24"},
            {"strike": 70000, "option_type": "P", "gamma": 0.00005, "delta": -0.5,
             "open_interest": 500, "mark_iv": "not-a-number", "expiration": "27DEC24"},
            {"strike": 75000, "option_type": "C", "gamma": 0.00002, "delta": 0.3,
             "open_interest": 500, "mark_iv": "not-a-number", "expiration": "27DEC24"},
            {"strike": 75000, "option_type": "P", "gamma": 0.00010, "delta": -0.7,
             "open_interest": 500, "mark_iv": "not-a-number", "expiration": "27DEC24"},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)

        result = calculator.calculate()  # must not raise

        # The strike-axis hvl/key_levels path is entirely independent of
        # mark_iv and must be computed exactly as it always was.
        assert result.key_levels.hvl in [70000, 75000]
        # The new, additive gamma-profile field degrades safely instead of
        # propagating the TypeError ("not-a-number" <= 0.0) raised while
        # building legs.
        assert result.key_levels.zero_gamma_level is None
        assert result.key_levels.gamma_regime == "UNKNOWN"

    def test_non_string_expiration_does_not_raise(self):
        instrument = {
            "strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5,
            "open_interest": 100, "mark_iv": 50.0, "expiration": 12345,
        }
        calculator = GexDexCalculator([instrument], spot_price=70000)

        result = calculator.calculate()  # must not raise

        assert result.key_levels.zero_gamma_level is None
        assert result.key_levels.gamma_regime == "UNKNOWN"


class TestHolderDealerSignConvention:
    """
    bugfix_spec.md Item 8 acceptance tests (section 8.5), adapted to this
    codebase's typed GexDexResult (attribute access, not the spec's
    illustrative dict subscripting -- Wave A already replaced calculate()'s
    return type).
    """

    FIXTURE = [
        # K=100,000: call gamma*OI = 0.002, put gamma*OI = 0.0005;
        #            call delta*OI = 50,    put delta*OI = -25
        {"strike": 100_000.0, "option_type": "C", "gamma": 0.002, "delta": 50.0, "open_interest": 1.0},
        {"strike": 100_000.0, "option_type": "P", "gamma": 0.0005, "delta": -25.0, "open_interest": 1.0},
        # K=110,000: call gamma*OI = 0.0004, put gamma*OI = 0.0024;
        #            call delta*OI = 12,     put delta*OI = -56
        {"strike": 110_000.0, "option_type": "C", "gamma": 0.0004, "delta": 12.0, "open_interest": 1.0},
        {"strike": 110_000.0, "option_type": "P", "gamma": 0.0024, "delta": -56.0, "open_interest": 1.0},
    ]

    def test_t8_1_both_families_hand_computed(self):
        """
        Sigma call g*OI = 0.0024; Sigma put g*OI = 0.0029; S^2*0.01 = 1e8
          gamma_exposure_holder = (0.0024+0.0029)*1e8 = 530,000.00
          dealer_gamma_exposure = (0.0024-0.0029)*1e8 = -50,000.00
          delta_exposure_holder = (50-25)+(12-56) = -19.0

        Wave-H-A (reverting Task G2-D fix 2 / commit cb1770a):
          dealer_delta_exposure is -delta_exposure_holder (dealers short
          whatever holders hold), per GexDexCalculator's canonical SIGN
          CONVENTION -- the long-calls/short-puts SPLIT is gamma-only, NOT
          a pattern to extend to delta:
            dealer_delta_exposure_total = -(-19.0) = 19.0
          NOT call_delta - put_delta (= 143.0) -- that was cb1770a's
          regression, which is algebraically guaranteed non-negative for
          any real book (call_delta >= 0, put_delta <= 0) and so could
          never represent a dealer net-short-delta book.
        """
        r = GexDexCalculator(self.FIXTURE, 100_000.0, "BTC").calculate()

        assert r.gamma_exposure_holder_total == pytest.approx(530_000.0)
        assert r.dealer_gamma_exposure_total == pytest.approx(-50_000.0)
        assert r.delta_exposure_holder_total == pytest.approx(-19.0)
        assert r.dealer_delta_exposure_total == pytest.approx(19.0)
        # legacy aliases unchanged
        assert r.total_net_gex == pytest.approx(r.dealer_gamma_exposure_total)
        assert r.total_net_dex == pytest.approx(r.delta_exposure_holder_total)

    def test_t8_2_holder_gamma_exposure_never_negative(self):
        r = GexDexCalculator(self.FIXTURE, 100_000.0, "BTC").calculate()
        assert r.gamma_exposure_holder_total >= 0.0

    def test_holder_gamma_exposure_zero_when_no_oi(self):
        """8.4 edge case: holder gamma exposure is 0 (no OI) -- no division,
        both totals print 0.0."""
        r = GexDexCalculator([], 100_000.0, "BTC").calculate()
        assert r.gamma_exposure_holder_total == 0.0
        assert r.dealer_gamma_exposure_total == 0.0


class TestDealerDeltaIsNegatedHolderSum:
    """
    Wave-H-A: reverts Task G2-D fix 2 (commit cb1770a), which changed
    ``dealer_delta_exposure`` from ``-net_dex`` to ``call_delta -
    put_delta`` (the gamma-style call/put SPLIT), reasoning it should
    match ``dealer_gamma_exposure``'s own convention. That reasoning
    contradicts ``GexDexCalculator``'s own class docstring (the ONE place
    the SIGN CONVENTION is stated): the long-calls/short-puts SPLIT applies
    to GAMMA ONLY -- delta/vanna/charm are each "short whatever customers
    (i.e. holders) hold", i.e. the negated holder-side sum.

    The split formula is also PROVABLY WRONG, not merely inconsistent:
    ``call_delta`` (holder-side call aggregate) is always >= 0 and
    ``put_delta`` is always <= 0, so ``call_delta - put_delta`` =
    ``call_delta + abs(put_delta)`` is ALGEBRAICALLY GUARANTEED
    non-negative for any real book -- the report's "dealers net short
    delta" branch could never fire, for any book, ever. This class proves
    that with an all-puts book (negative dealer delta) and an all-calls
    book (positive dealer delta) -- genuinely two-sided, unlike the split.

    Fixture reproduces the original audit's own worked numbers: a single
    strike with call_delta*OI = +137.53, put_delta*OI = -78.53 (OI folded
    into a delta of exactly +-1.0 for a clean product).
    """

    FIXTURE = [
        {"strike": 65_000.0, "option_type": "C", "gamma": 0.0, "delta": 1.0, "open_interest": 137.53},
        {"strike": 65_000.0, "option_type": "P", "gamma": 0.0, "delta": -1.0, "open_interest": 78.53},
    ]

    def test_dealer_delta_is_negated_holder_sum_not_call_minus_put(self):
        r = GexDexCalculator(self.FIXTURE, 65_000.0, "BTC").calculate()

        row = r.strike_rows[0]
        assert row.call_delta == pytest.approx(137.53)
        assert row.put_delta == pytest.approx(-78.53)

        # Holder-side (unaffected by this fix): the raw sum a holder holds.
        assert row.delta_exposure_holder == pytest.approx(59.0, abs=1e-6)
        assert r.delta_exposure_holder_total == pytest.approx(59.0, abs=1e-6)

        # cb1770a's regression: call_delta - put_delta = +216.06 -- "dealers
        # net long delta, hedge by selling". Must NOT be this.
        wrong_split = row.call_delta - row.put_delta
        assert row.dealer_delta_exposure != pytest.approx(wrong_split)
        assert r.dealer_delta_exposure_total != pytest.approx(wrong_split)

        # The CORRECT convention: -(call_delta + put_delta) = -59.0 --
        # "dealers net short delta, hedge by buying".
        assert row.dealer_delta_exposure == pytest.approx(-59.0, abs=1e-6)
        assert r.dealer_delta_exposure_total == pytest.approx(-59.0, abs=1e-6)

    def test_all_puts_book_produces_negative_dealer_delta(self):
        """
        Genuinely two-sided proof (task Wave-H-A verification requirement):
        an all-puts book must be able to produce a NEGATIVE dealer delta.
        Under the retired call/put-SPLIT formula (call_delta - put_delta),
        an all-puts book gives call_delta=0, so the result is
        -put_delta >= 0 -- ALWAYS non-negative, even for a maximally
        bearish, 100%-put book. The reverted negation formula does not
        have this defect.
        """
        all_puts = [
            {"strike": 65_000.0, "option_type": "P", "gamma": 0.001, "delta": -0.5, "open_interest": 100.0},
        ]
        r = GexDexCalculator(all_puts, 65_000.0, "BTC").calculate()

        row = r.strike_rows[0]
        assert row.call_delta == pytest.approx(0.0)
        assert row.put_delta == pytest.approx(-50.0)  # -0.5 * 100

        # Negation formula: -(0 + -50) = +50 -- dealers short the puts
        # holders hold, i.e. net LONG delta (correct: writing puts to a
        # bearish holder base means the dealer is short puts, which is a
        # long-delta position).
        assert row.dealer_delta_exposure == pytest.approx(50.0, abs=1e-6)
        assert r.dealer_delta_exposure_total == pytest.approx(50.0, abs=1e-6)

        # The retired split formula (call_delta - put_delta = 0 - (-50) =
        # +50) happens to agree here only because there are zero calls --
        # confirm the split would have been NON-NEGATIVE regardless of how
        # bearish the put book gets, by construction (call_delta is always
        # 0 in an all-puts book, so split = -put_delta >= 0 always).
        wrong_split = row.call_delta - row.put_delta
        assert wrong_split >= 0.0

    def test_all_calls_book_produces_positive_dealer_delta(self):
        """Mirror of the all-puts case: an all-calls (holder-side net long
        delta) book must produce a NEGATIVE dealer delta under the correct
        negation convention -- dealers short whatever the (bullish) holder
        base holds."""
        all_calls = [
            {"strike": 65_000.0, "option_type": "C", "gamma": 0.001, "delta": 0.5, "open_interest": 100.0},
        ]
        r = GexDexCalculator(all_calls, 65_000.0, "BTC").calculate()

        row = r.strike_rows[0]
        assert row.call_delta == pytest.approx(50.0)
        assert row.put_delta == pytest.approx(0.0)

        # Negation formula: -(50 + 0) = -50 -- dealers short the calls
        # holders hold, i.e. net SHORT delta.
        assert row.dealer_delta_exposure == pytest.approx(-50.0, abs=1e-6)
        assert r.dealer_delta_exposure_total == pytest.approx(-50.0, abs=1e-6)


class TestCalculateRolloffProfile:
    """
    institutional_metrics_spec.md section 5 / Task C6 acceptance tests
    (T5.1-T5.3), verbatim numeric cases from the spec.

    ``now_utc`` is constructed so ``calculate_days_to_expiry``'s exact
    fractional-day math reproduces the spec's own illustrative DTEs
    (0.6d / 6.6d / 34.6d) exactly: 25JUL26, 31JUL26, and 28AUG26 are all
    08:00 UTC settlements 0, 6, and 34 calendar days apart respectively, so
    anchoring ``now_utc`` 0.6 days before 25JUL26's settlement reproduces
    all three DTEs from one offset.
    """

    _EXPIRY_25JUL26 = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
    NOW_UTC = _EXPIRY_25JUL26 - timedelta(days=0.6)

    def test_t5_1_shares_and_cliff_flag(self):
        per_expiry_net_gex = {
            "25JUL26": 30_000_000.0,
            "31JUL26": 50_000_000.0,
            "28AUG26": 20_000_000.0,
        }

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        rows = {r["expiration"]: r for r in result["rows"]}
        assert rows["25JUL26"]["dte_days"] == pytest.approx(0.6, abs=1e-6)
        assert rows["31JUL26"]["dte_days"] == pytest.approx(6.6, abs=1e-6)
        assert rows["28AUG26"]["dte_days"] == pytest.approx(34.6, abs=1e-6)

        assert rows["25JUL26"]["share_pct"] == pytest.approx(30.0)
        assert rows["31JUL26"]["share_pct"] == pytest.approx(50.0)
        assert rows["28AUG26"]["share_pct"] == pytest.approx(20.0)

        assert rows["25JUL26"]["cum_share_pct"] == pytest.approx(30.0)
        assert rows["31JUL26"]["cum_share_pct"] == pytest.approx(80.0)
        assert rows["28AUG26"]["cum_share_pct"] == pytest.approx(100.0)

        assert result["cum_share_7d"] == pytest.approx(80.0)
        assert result["gamma_cliff_7d"] is True

    def test_t5_2_mixed_signs(self):
        """
        Shares are computed on |net_gex| (still sum to 100%); cum_net_gex is
        signed and non-monotone -- that is correct, not a bug (spec 5(c)
        edge case "Mixed signs").
        """
        per_expiry_net_gex = {
            "25JUL26": 30_000_000.0,
            "31JUL26": -50_000_000.0,
            "28AUG26": 20_000_000.0,
        }

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        assert result["gross_total"] == pytest.approx(100_000_000.0)

        rows = {r["expiration"]: r for r in result["rows"]}
        assert rows["25JUL26"]["share_pct"] == pytest.approx(30.0)
        assert rows["31JUL26"]["share_pct"] == pytest.approx(50.0)
        assert rows["28AUG26"]["share_pct"] == pytest.approx(20.0)

        assert result["cum_share_7d"] == pytest.approx(80.0)
        assert result["gamma_cliff_7d"] is True

        # Net total across all expiries is 0; the 7d bucket's own signed
        # contribution is -20,000,000 (the spec's exact worked value).
        assert rows["28AUG26"]["cum_net_gex"] == pytest.approx(0.0)
        assert rows["31JUL26"]["cum_net_gex"] == pytest.approx(-20_000_000.0)

    def test_t5_3_empty_book_no_zero_division(self):
        """All net_gex == 0 -> gross_total == 0 -> shares None, no flag, no
        ZeroDivisionError (spec 5(c) edge case)."""
        per_expiry_net_gex = {
            "25JUL26": 0.0,
            "31JUL26": 0.0,
            "28AUG26": 0.0,
        }

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        assert result["gross_total"] == 0.0
        assert result["gamma_cliff_7d"] is False
        assert result["cum_share_7d"] is None
        assert result["cum_share_30d"] is None
        for row in result["rows"]:
            assert row["share_pct"] is None
            assert row["cum_share_pct"] is None

    def test_zero_expiries(self):
        """No expiries at all -- degenerate form of the empty-book case."""
        result = GexDexCalculator.calculate_rolloff_profile({}, self.NOW_UTC)

        assert result["rows"] == []
        assert result["gross_total"] == 0.0
        assert result["gamma_cliff_7d"] is False
        assert result["cum_share_7d"] is None
        assert result["cum_share_30d"] is None

    def test_one_expiry_only_within_7d_flags(self):
        result = GexDexCalculator.calculate_rolloff_profile(
            {"25JUL26": 10_000_000.0}, self.NOW_UTC,
        )

        assert result["rows"][0]["share_pct"] == pytest.approx(100.0)
        assert result["cum_share_7d"] == pytest.approx(100.0)
        assert result["gamma_cliff_7d"] is True

    def test_one_expiry_only_beyond_7d_does_not_flag(self):
        result = GexDexCalculator.calculate_rolloff_profile(
            {"28AUG26": 10_000_000.0}, self.NOW_UTC,
        )

        assert result["rows"][0]["share_pct"] == pytest.approx(100.0)
        assert result["cum_share_7d"] == pytest.approx(0.0)
        assert result["gamma_cliff_7d"] is False

    def test_all_expiries_beyond_7_days_no_flag(self):
        """Both expiries land >7 days but <=30 days from NOW_UTC (day
        offsets +10/+20 from 25JUL26, i.e. dte ~10.6d/20.6d)."""
        far_1 = (self._EXPIRY_25JUL26 + timedelta(days=10)).strftime("%d%b%y").upper()
        far_2 = (self._EXPIRY_25JUL26 + timedelta(days=20)).strftime("%d%b%y").upper()
        per_expiry_net_gex = {far_1: 40_000_000.0, far_2: 60_000_000.0}

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        assert result["cum_share_7d"] == pytest.approx(0.0)
        assert result["gamma_cliff_7d"] is False
        assert result["cum_share_30d"] == pytest.approx(100.0)

    def test_all_expiries_within_7_days_flags(self):
        """25JUL26 (dte 0.6d) and 31JUL26 (dte 6.6d) both sit inside the 7d
        window."""
        per_expiry_net_gex = {"25JUL26": 40_000_000.0, "31JUL26": 60_000_000.0}

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        assert result["cum_share_7d"] == pytest.approx(100.0)
        assert result["gamma_cliff_7d"] is True

    def test_boundary_exactly_30_percent_does_not_flag(self):
        """Spec: FLAG when cum_share(<=7d) > 30.0 -- strictly greater than,
        exactly 30.0 must not flag."""
        per_expiry_net_gex = {"25JUL26": 30_000_000.0, "28AUG26": 70_000_000.0}

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        assert result["cum_share_7d"] == pytest.approx(30.0)
        assert result["gamma_cliff_7d"] is False

    def test_expiry_past_settlement_still_in_chain_clamps_to_zero_dte(self):
        """Spec 5(c) edge case: an expiry already past its 08:00 UTC
        settlement but still present in the chain gets dte=0.0 and is
        included in the 7d bucket (never a negative DTE)."""
        past_expiry_net_gex = {"20JUL26": 5_000_000.0, "28AUG26": 5_000_000.0}

        result = GexDexCalculator.calculate_rolloff_profile(past_expiry_net_gex, self.NOW_UTC)

        rows = {r["expiration"]: r for r in result["rows"]}
        assert rows["20JUL26"]["dte_days"] == 0.0
        assert result["cum_share_7d"] == pytest.approx(50.0)
        assert result["gamma_cliff_7d"] is True

    def test_unparseable_expiration_label_is_skipped_not_raised(self):
        """Defensive: an unparseable expiration string must not raise --
        it is skipped (mirrors _build_gamma_legs' own parse-failure
        handling), never surfacing a crash from a presentation-only
        aggregation."""
        per_expiry_net_gex = {"NOT-A-DATE": 10_000_000.0, "25JUL26": 30_000_000.0}

        result = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)  # must not raise

        expirations = {r["expiration"] for r in result["rows"]}
        assert expirations == {"25JUL26"}
        assert result["gross_total"] == pytest.approx(30_000_000.0)

    def test_idempotent_pure_function(self):
        """Pure: calling twice with the same inputs returns identical output
        (no shared mutable state, unlike the pre-fix GexDexCalculator.calculate()
        double-counting bug this spec explicitly warns about)."""
        per_expiry_net_gex = {"25JUL26": 30_000_000.0, "31JUL26": -50_000_000.0, "28AUG26": 20_000_000.0}

        first = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)
        second = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, self.NOW_UTC)

        assert first == second
