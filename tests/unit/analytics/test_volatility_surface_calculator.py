"""
Unit tests for VolatilitySurfaceCalculator.
"""

import pytest
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator


def _make_instrument(
    strike, option_type, mark_iv=None, delta=None, gamma=None,
    theta=None, vega=None, open_interest=100, volume=10
):
    """Helper to create instrument dicts for testing."""
    return {
        "instrument_name": f"BTC-28MAR26-{int(strike)}-{option_type}",
        "expiration": "28MAR26",
        "strike": strike,
        "option_type": option_type,
        "open_interest": open_interest,
        "volume": volume,
        "volume_usd": volume * 90000,
        "mark_price": 0.05,
        "mark_iv": mark_iv,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "underlying_price": 90000,
    }


@pytest.fixture
def sample_instruments():
    """Create a realistic set of instruments around BTC at 90000."""
    instruments = []
    spot = 90000

    # Calls
    for strike, iv, delta, gamma, theta, vega in [
        (80000, 85.0, 0.75, 0.00001, -50, 100),
        (85000, 72.0, 0.55, 0.00002, -60, 120),
        (90000, 65.0, 0.50, 0.00003, -70, 130),
        (95000, 68.0, 0.25, 0.00002, -55, 110),
        (100000, 75.0, 0.10, 0.00001, -40, 80),
    ]:
        instruments.append(_make_instrument(
            strike, "C", mark_iv=iv, delta=delta,
            gamma=gamma, theta=theta, vega=vega, open_interest=500
        ))

    # Puts
    for strike, iv, delta, gamma, theta, vega in [
        (80000, 88.0, -0.25, 0.00001, -45, 95),
        (85000, 74.0, -0.45, 0.00002, -55, 115),
        (90000, 66.0, -0.50, 0.00003, -65, 125),
        (95000, 70.0, -0.75, 0.00002, -50, 105),
        (100000, 78.0, -0.90, 0.00001, -35, 75),
    ]:
        instruments.append(_make_instrument(
            strike, "P", mark_iv=iv, delta=delta,
            gamma=gamma, theta=theta, vega=vega, open_interest=600
        ))

    return instruments


class TestVolatilitySurfaceCalculator:
    """Tests for VolatilitySurfaceCalculator."""

    def test_calculate_returns_all_keys(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        assert "iv_by_strike" in result
        assert "skew_25d" in result
        assert "pc_by_moneyness" in result
        assert "second_order_greeks" in result
        assert "atm_iv" in result

    def test_iv_by_strike(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        iv_data = result["iv_by_strike"]
        assert len(iv_data) == 5  # 5 unique strikes

        # Check ATM strike has both call and put IV
        atm_entry = next(e for e in iv_data if e["strike"] == 90000)
        assert atm_entry["call_iv"] == 65.0
        assert atm_entry["put_iv"] == 66.0

    def test_25_delta_skew(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        skew = result["skew_25d"]
        assert skew["skew"] is not None
        assert skew["put_25d_iv"] is not None
        assert skew["call_25d_iv"] is not None
        # Put 25d should be at strike 80000 (delta -0.25)
        assert skew["put_25d_strike"] == 80000
        # Call 25d should be at strike 95000 (delta 0.25)
        assert skew["call_25d_strike"] == 95000
        # Skew = put IV - call IV = 88 - 68 = 20
        assert skew["skew"] == pytest.approx(20.0)

    def test_25_delta_skew_insufficient_data(self):
        instruments = [
            _make_instrument(90000, "C", mark_iv=65.0, delta=0.50)
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        result = calc.calculate()

        skew = result["skew_25d"]
        assert skew["skew"] is None
        assert "Insufficient" in skew["interpretation"]

    def test_pc_by_moneyness(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        pc = result["pc_by_moneyness"]
        assert "atm" in pc
        assert "near_otm" in pc
        assert "far_otm" in pc

        # ATM bucket (±5%) should contain 90000 strike
        atm = pc["atm"]
        assert atm["call_oi"] > 0
        assert atm["put_oi"] > 0
        assert "ratio" in atm
        assert "bias" in atm

    def test_second_order_greeks(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        greeks = result["second_order_greeks"]
        assert "net_vanna" in greeks
        assert "net_charm" in greeks
        assert "vanna_signal" in greeks
        assert "charm_signal" in greeks
        # Values should be non-zero with our test data
        assert greeks["net_vanna"] != 0
        assert greeks["net_charm"] != 0

    def test_atm_iv(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        atm_iv = result["atm_iv"]
        assert atm_iv is not None
        # ATM IV should be average of call (65) and put (66) at strike 90000
        assert atm_iv == pytest.approx(65.5)

    def test_atm_iv_no_data(self):
        instruments = [
            _make_instrument(90000, "C", mark_iv=None, delta=0.50)
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        result = calc.calculate()
        assert result["atm_iv"] is None

    def test_generate_report_section(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        report = calc.generate_report_section()

        assert "VOLATILITY SURFACE ANALYSIS" in report
        assert "25-Delta Skew" in report
        assert "IV BY STRIKE" in report
        assert "P/C RATIO BY MONEYNESS" in report
        assert "SECOND-ORDER GREEKS" in report

    def test_vwap_iv_in_report(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        calc.set_vwap_iv_data(vwap_iv=67.5, mark_iv_avg=65.0)
        report = calc.generate_report_section()

        assert "VWAP IV:" in report
        assert "67.5%" in report

    def test_zero_spot_price(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        result = calc.calculate()
        # Should not crash
        assert result is not None

    def test_empty_instruments(self):
        calc = VolatilitySurfaceCalculator([], 90000, "28MAR26")
        result = calc.calculate()

        assert result["iv_by_strike"] == []
        assert result["skew_25d"]["skew"] is None
        assert result["atm_iv"] is None

    def test_pc_ratio_interpretation(self):
        assert VolatilitySurfaceCalculator._interpret_pc_ratio(0.5) == "Bullish"
        assert VolatilitySurfaceCalculator._interpret_pc_ratio(0.85) == "Slightly Bullish"
        assert VolatilitySurfaceCalculator._interpret_pc_ratio(1.1) == "Slightly Bearish"
        assert VolatilitySurfaceCalculator._interpret_pc_ratio(1.5) == "Bearish"
        assert VolatilitySurfaceCalculator._interpret_pc_ratio(float("inf")) == "N/A"
