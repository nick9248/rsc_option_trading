"""
Unit tests for VolatilitySurfaceCalculator.

T4 (refactor_design_spec.md): calculate() returns the typed VolSurfaceResult
(coding/core/analytics/results/vol_surface_results.py) instead of a dict —
assertions here use attribute access. ``iv_by_strike`` is now one row per
INSTRUMENT; use ``result.merged_iv_by_strike()`` for the legacy per-strike
{call_iv, put_iv} view.
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

    def test_calculate_returns_all_fields(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        assert result.iv_by_strike is not None
        assert result.skew_25d is not None
        assert result.pc_by_moneyness is not None
        assert result.second_order_greeks is not None
        assert result.atm_iv is not None

    def test_iv_by_strike(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        # One row per instrument: 5 calls + 5 puts
        assert len(result.iv_by_strike) == 10

        merged = result.merged_iv_by_strike()
        assert len(merged) == 5  # 5 unique strikes

        # Check ATM strike has both call and put IV
        atm_entry = merged[90000.0]
        assert atm_entry["call_iv"] == 65.0
        assert atm_entry["put_iv"] == 66.0

    def test_25_delta_skew(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        skew = result.skew_25d
        assert skew.skew is not None
        assert skew.put_25d_iv is not None
        assert skew.call_25d_iv is not None
        # Put 25d should be at strike 80000 (delta -0.25)
        assert skew.put_25d_strike == 80000
        # Call 25d should be at strike 95000 (delta 0.25)
        assert skew.call_25d_strike == 95000
        # Skew = put IV - call IV = 88 - 68 = 20
        assert skew.skew == pytest.approx(20.0)

    def test_25_delta_skew_insufficient_data(self):
        instruments = [
            _make_instrument(90000, "C", mark_iv=65.0, delta=0.50)
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        result = calc.calculate()

        skew = result.skew_25d
        assert skew.skew is None
        assert "Insufficient" in skew.interpretation

    def test_pc_by_moneyness(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        pc = result.pc_by_moneyness

        # ATM bucket (±5%) should contain 90000 strike
        atm = pc.atm
        assert atm.call_oi > 0
        assert atm.put_oi > 0
        assert atm.ratio is not None
        assert atm.bias is not None

    def test_second_order_greeks(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        greeks = result.second_order_greeks
        # Values should be non-zero with our test data
        assert greeks.net_vanna != 0
        assert greeks.net_charm != 0
        assert greeks.skipped_instruments >= 0

    def test_atm_iv(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        result = calc.calculate()

        atm_iv = result.atm_iv
        assert atm_iv is not None
        # ATM IV should be average of call (65) and put (66) at strike 90000
        assert atm_iv == pytest.approx(65.5)

    def test_atm_iv_no_data(self):
        instruments = [
            _make_instrument(90000, "C", mark_iv=None, delta=0.50)
        ]
        calc = VolatilitySurfaceCalculator(instruments, 90000, "28MAR26")
        result = calc.calculate()
        assert result.atm_iv is None

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
        calc.set_vwap_iv_data(vwap_iv=67.5, mark_iv_baseline=65.0)
        report = calc.generate_report_section()

        assert "VWAP IV:" in report
        assert "67.5%" in report

    def test_zero_spot_price(self, sample_instruments):
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        result = calc.calculate()
        # Should not crash
        assert result is not None

    def test_zero_spot_pc_by_moneyness_buckets_have_ratio_and_bias(self, sample_instruments):
        """
        bugfix_spec.md H2: the spot<=0 early return in _calculate_pc_by_moneyness()
        must populate ratio/bias on every bucket, not just call_oi/put_oi/range,
        otherwise generate_report_section() KeyErrors on bucket["ratio"].

        _calculate_pc_by_moneyness() stays a dict-returning internal helper
        (not part of the T4 public typed API) — calculate() wraps its output
        into PutCallByMoneyness/MoneynessBucket.
        """
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        buckets = calc._calculate_pc_by_moneyness()

        for bucket_name in ("atm", "near_otm", "far_otm"):
            bucket = buckets[bucket_name]
            assert bucket["ratio"] == 0.0
            assert bucket["bias"] == "N/A"

    def test_zero_spot_report_generation_does_not_raise(self, sample_instruments):
        """bugfix_spec.md H2: report path must not KeyError when spot_price == 0."""
        calc = VolatilitySurfaceCalculator(sample_instruments, 0, "28MAR26")
        report = calc.generate_report_section()

        assert isinstance(report, str)
        assert "P/C RATIO BY MONEYNESS" in report
        assert "N/A" in report

    def test_empty_instruments(self):
        calc = VolatilitySurfaceCalculator([], 90000, "28MAR26")
        result = calc.calculate()

        assert result.iv_by_strike == ()
        assert result.skew_25d.skew is None
        assert result.atm_iv is None

    def test_pc_by_moneyness_bias_uses_shared_interpreter(self, sample_instruments):
        """M4 (code_quality_review.md): _interpret_pc_ratio was deleted --
        bucket bias now comes from the shared
        coding.core.analytics.thresholds.interpret_put_call_ratio (see
        tests/unit/analytics/test_thresholds.py for the full boundary
        coverage). This just confirms the wiring."""
        calc = VolatilitySurfaceCalculator(sample_instruments, 90000, "28MAR26")
        buckets = calc._calculate_pc_by_moneyness()

        for bucket in buckets.values():
            assert bucket["bias"] in (
                "N/A", "Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish",
            )
