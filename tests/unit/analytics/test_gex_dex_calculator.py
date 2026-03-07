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
