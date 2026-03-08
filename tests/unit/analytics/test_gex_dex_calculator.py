"""
Unit tests for GEX/DEX Calculator.

Tests the industry standard formula:
Net GEX = (Call Gamma - Put Gamma) * Spot² * 0.01
"""

import pytest
from coding.core.analytics.gex_dex_calculator import GexDexCalculator


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
        assert result["strike_data"][70000]["net_gex"] == pytest.approx(expected_gex)
        assert result["strike_data"][70000]["net_gex"] == pytest.approx(2450000.0)

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

        strike_data = result["strike_data"][70000]

        # Check aggregation
        # call_gamma = (0.00003 * 500) + (0.00002 * 300) = 0.015 + 0.006 = 0.021
        # put_gamma = 0.00004 * 200 = 0.008
        assert strike_data["call_gamma"] == pytest.approx(0.021)
        assert strike_data["put_gamma"] == pytest.approx(0.008)

        # net_gamma = 0.021 - 0.008 = 0.013
        # net_gex = 0.013 * (70000^2) * 0.01 = 637,000
        expected_net_gex = 0.013 * (70000 ** 2) * 0.01
        assert strike_data["net_gex"] == pytest.approx(expected_net_gex)

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

        strike_data = result["strike_data"][70000]

        # call_delta_weighted = 0.6 * 100 = 60
        # put_delta_weighted = -0.4 * 150 = -60
        # net_dex = 60 + (-60) = 0
        assert strike_data["call_delta"] == pytest.approx(60.0)
        assert strike_data["put_delta"] == pytest.approx(-60.0)
        assert strike_data["net_dex"] == pytest.approx(0.0)

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
        assert result["key_levels"]["call_resistance"]["strike"] == 72000

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
        assert result["key_levels"]["put_support"]["strike"] == 66000

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
        assert result["key_levels"]["hvl"] in [70000, 75000]

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

        assert result["cumulative_gex"][65000] == pytest.approx(cumulative_65k)
        assert result["cumulative_gex"][70000] == pytest.approx(cumulative_70k)
        assert result["cumulative_gex"][75000] == pytest.approx(cumulative_75k)

    def test_empty_instruments(self):
        """Test calculator handles empty instrument list gracefully."""
        calculator = GexDexCalculator([], spot_price=70000)
        result = calculator.calculate()

        assert result["strike_data"] == {}
        assert result["key_levels"]["call_resistance"] is None
        assert result["key_levels"]["put_support"] is None
        assert result["key_levels"]["hvl"] is None
        assert result["total_net_gex"] == 0
        assert result["total_net_dex"] == 0

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
        assert result["strike_data"][70000]["call_gamma"] == 0.0
        assert result["strike_data"][70000]["call_delta"] == 0.0
        assert result["strike_data"][70000]["net_gex"] == 0.0
        assert result["strike_data"][70000]["net_dex"] == 0.0

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
        assert result["strike_data"][70000]["call_gamma"] == 0.0
        assert result["strike_data"][70000]["net_gex"] == 0.0

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
        assert result["key_levels"]["call_resistance"] is not None
        assert result["key_levels"]["put_support"] is None

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
        assert result["key_levels"]["call_resistance"] is None
        assert result["key_levels"]["put_support"] is not None

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

        assert result["total_net_gex"] == pytest.approx(expected_total)

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
        assert result["total_net_dex"] == pytest.approx(expected_total_dex)

    def test_report_generation(self):
        """Test that report generation doesn't crash and includes key sections."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 72000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 1000},
        ]
        calculator = GexDexCalculator(instruments, spot_price=70000)
        report = calculator.generate_report_section()

        # Check report contains expected sections
        assert "GEX/DEX ANALYSIS" in report
        assert "Spot Price:" in report
        assert "KEY LEVELS:" in report
        assert "TOTALS:" in report
        assert "Call Resistance" in report or "None found" in report
        assert "Put Support" in report or "None found" in report
        assert "HVL" in report

    def test_gex_report_shows_usd_unit(self):
        """GEX values are labeled USD in the report."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 72000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 1000},
        ]
        calc = GexDexCalculator(instruments, spot_price=70000, currency="BTC")
        report = calc.generate_report_section()
        assert "USD" in report

    def test_dex_report_shows_currency_unit_eth(self):
        """DEX values are labeled with currency (ETH)."""
        instruments = [
            {"strike": 2000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 100},
            {"strike": 2000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 100},
        ]
        calc = GexDexCalculator(instruments, spot_price=2000.0, currency="ETH")
        report = calc.generate_report_section()
        assert "ETH" in report

    def test_dex_report_shows_currency_unit_btc(self):
        """DEX values are labeled with currency (BTC)."""
        instruments = [
            {"strike": 50000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 100},
            {"strike": 50000, "option_type": "P", "gamma": 0.00003, "delta": -0.5, "open_interest": 100},
        ]
        calc = GexDexCalculator(instruments, spot_price=50000.0, currency="BTC")
        report = calc.generate_report_section()
        assert "BTC" in report

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

        # Check all strikes are present
        assert 68000 in result["strike_data"]
        assert 70000 in result["strike_data"]
        assert 72000 in result["strike_data"]

        # Verify calculations for 68000
        # call_gamma = 0.00004 * 500 = 0.02
        # put_gamma = 0.00008 * 800 = 0.064
        # net_gamma = 0.02 - 0.064 = -0.044
        # net_gex = -0.044 * (70000^2) * 0.01
        expected_gex_68k = -0.044 * (spot_price ** 2) * 0.01
        assert result["strike_data"][68000]["net_gex"] == pytest.approx(expected_gex_68k)

        # Verify calculations for 72000
        # call_gamma = 0.00008 * 800 = 0.064
        # put_gamma = 0.00004 * 500 = 0.02
        # net_gamma = 0.064 - 0.02 = 0.044
        # net_gex = 0.044 * (70000^2) * 0.01
        expected_gex_72k = 0.044 * (spot_price ** 2) * 0.01
        assert result["strike_data"][72000]["net_gex"] == pytest.approx(expected_gex_72k)

        # Call resistance should be 72000 (positive GEX)
        # Put support should be 68000 (negative GEX)
        assert result["key_levels"]["call_resistance"]["strike"] == 72000
        assert result["key_levels"]["put_support"]["strike"] == 68000


class TestAggregateAcrossExpirations:
    """Tests for GexDexCalculator.aggregate_across_expirations."""

    def _make_expiry_result(self, instruments, spot_price=70000, currency="BTC"):
        """Helper: run calculate() and return the result dict."""
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

        assert agg["total_net_gex"] == pytest.approx(original["total_net_gex"])
        assert agg["total_net_dex"] == pytest.approx(original["total_net_dex"])
        assert agg["expiration_count"] == 1
        assert set(agg["strike_data"].keys()) == set(original["strike_data"].keys())

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
        assert agg["strike_data"][70000]["call_gamma"] == pytest.approx(expected_call_gamma)

        expected_net_gamma = expected_call_gamma  # no puts
        expected_net_gex = expected_net_gamma * (spot_price ** 2) * 0.01
        assert agg["strike_data"][70000]["net_gex"] == pytest.approx(expected_net_gex)
        assert agg["expiration_count"] == 2

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

        assert 68000 in agg["strike_data"]
        assert 75000 in agg["strike_data"]
        assert agg["expiration_count"] == 2

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
        assert result_exp1["key_levels"]["call_resistance"]["strike"] == 72000

        agg = GexDexCalculator.aggregate_across_expirations(
            {"27DEC24": result_exp1, "28MAR25": result_exp2}, 70000, "BTC"
        )

        # Combined: 74000 has far more gamma (0.0001 * 5000 = 0.5 vs 0.0001 * 1000 = 0.1)
        assert agg["key_levels"]["call_resistance"]["strike"] == 74000

    def test_aggregate_empty_input_returns_empty_result(self):
        """aggregate_across_expirations with empty dict should return zero totals."""
        agg = GexDexCalculator.aggregate_across_expirations({}, spot_price=70000)

        assert agg["total_net_gex"] == 0.0
        assert agg["total_net_dex"] == 0.0
        assert agg["strike_data"] == {}
        assert agg["key_levels"]["call_resistance"] is None
        assert agg["key_levels"]["put_support"] is None
        assert agg["key_levels"]["hvl"] is None
        assert agg["expiration_count"] == 0

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
        assert agg["expiration_count"] == 1

    def test_aggregate_report_section_contains_expected_content(self):
        """Report should mention aggregation count and standard sections."""
        instruments = [
            {"strike": 70000, "option_type": "C", "gamma": 0.00005, "delta": 0.5, "open_interest": 1000},
            {"strike": 68000, "option_type": "P", "gamma": 0.00004, "delta": -0.5, "open_interest": 800},
        ]
        spot_price = 70000
        result = self._make_expiry_result(instruments, spot_price)
        agg_result = GexDexCalculator.aggregate_across_expirations(
            {"27DEC24": result}, spot_price, "BTC"
        )

        report = GexDexCalculator.generate_aggregate_report_section(
            agg_result, spot_price, "BTC"
        )

        assert "MARKET-WIDE GEX/DEX LEVELS" in report
        assert "Aggregated" in report
        assert "KEY LEVELS:" in report
        assert "TOTALS:" in report
        assert "USD" in report
        assert "BTC" in report
        # No per-strike table
        assert "GEX/DEX BY STRIKE:" not in report
