"""
Unit tests for GEX/DEX Calculator.

Tests the industry standard formula:
Net GEX = (Call Gamma - Put Gamma) * Spot² * 0.01

T4 (refactor_design_spec.md): calculate() / aggregate_across_expirations()
return the typed GexDexResult (coding/core/analytics/results/gex_dex_results.py)
instead of a dict — assertions here use attribute access. Legacy dict
consumers use GexDexResult.to_dict().
"""

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
