"""
Unit tests for the GEX/DEX history backfill script's pure row-building logic.

Covers:
- _apply_bs_fallback: Black-Scholes fallback for instruments missing delta/gamma
  in hourly_snapshots, using a historical reference time for time-to-expiry.
- _extract_update_values: flattening GexDexCalculator.calculate() output into
  the scalar columns onchain_analysis_snapshots stores.

No database access — these are pure-function tests against synthetic instrument
dicts shaped like DatabaseRepository.get_hourly_snapshots_for_hour's return value.
"""
from datetime import datetime, timedelta

import pytest

from scripts.backfill_gex_dex_history import _apply_bs_fallback, _extract_update_values
from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.gex_dex_calculator import GexDexCalculator


@pytest.fixture
def bs_calculator():
    return BlackScholesCalculator()


class TestApplyBsFallback:
    """Tests for the Black-Scholes fallback pass."""

    def test_fills_missing_greeks_from_mark_iv(self, bs_calculator):
        """Instrument with mark_iv but no delta/gamma gets BS-computed values."""
        reference_time = datetime(2026, 6, 1, 0, 0, 0)
        instruments = [{
            "instrument_name": "BTC-1JUN26-70000-C",
            "strike": 70000.0,
            "option_type": "C",
            "mark_iv": 60.0,  # percent
            "delta": None,
            "gamma": None,
            "open_interest": 100.0,
        }]

        result = _apply_bs_fallback(instruments, underlying_price=70000.0,
                                     reference_time=reference_time, bs_calculator=bs_calculator)

        assert len(result) == 1
        # ATM call: delta should be roughly 0.5, gamma strictly positive.
        assert 0.3 < result[0]["delta"] < 0.7
        assert result[0]["gamma"] > 0

    def test_put_delta_is_negative(self, bs_calculator):
        """BS fallback for a put option should yield negative delta."""
        reference_time = datetime(2026, 6, 1, 0, 0, 0)
        instruments = [{
            "instrument_name": "BTC-1JUN26-70000-P",
            "strike": 70000.0,
            "option_type": "P",
            "mark_iv": 60.0,
            "delta": None,
            "gamma": None,
            "open_interest": 50.0,
        }]

        result = _apply_bs_fallback(instruments, underlying_price=70000.0,
                                     reference_time=reference_time, bs_calculator=bs_calculator)

        assert result[0]["delta"] < 0
        assert result[0]["gamma"] > 0

    def test_preserves_existing_greeks_unchanged(self, bs_calculator):
        """Instrument that already has delta/gamma is passed through unmodified."""
        reference_time = datetime(2026, 6, 1, 0, 0, 0)
        instruments = [{
            "instrument_name": "BTC-1JUN26-70000-C",
            "strike": 70000.0,
            "option_type": "C",
            "mark_iv": 60.0,
            "delta": 0.55,
            "gamma": 0.00002,
            "open_interest": 100.0,
        }]

        result = _apply_bs_fallback(instruments, underlying_price=70000.0,
                                     reference_time=reference_time, bs_calculator=bs_calculator)

        assert result[0]["delta"] == 0.55
        assert result[0]["gamma"] == 0.00002

    def test_no_mark_iv_defaults_to_zero(self, bs_calculator):
        """Instrument with no mark_iv and no existing greeks defaults to 0, not None."""
        reference_time = datetime(2026, 6, 1, 0, 0, 0)
        instruments = [{
            "instrument_name": "BTC-1JUN26-70000-C",
            "strike": 70000.0,
            "option_type": "C",
            "mark_iv": None,
            "delta": None,
            "gamma": None,
            "open_interest": 100.0,
        }]

        result = _apply_bs_fallback(instruments, underlying_price=70000.0,
                                     reference_time=reference_time, bs_calculator=bs_calculator)

        assert result[0]["delta"] == 0
        assert result[0]["gamma"] == 0

    def test_expired_option_at_reference_time_defaults_to_zero(self, bs_calculator):
        """Option already past expiry at the historical reference time stays at 0/0
        (time_to_expiry <= 0 guard), matching the aggregation-time behavior that
        produced the NULL greeks in the first place."""
        reference_time = datetime(2026, 6, 5, 0, 0, 0)  # after expiry
        instruments = [{
            "instrument_name": "BTC-1JUN26-70000-C",
            "strike": 70000.0,
            "option_type": "C",
            "mark_iv": 60.0,
            "delta": None,
            "gamma": None,
            "open_interest": 100.0,
        }]

        result = _apply_bs_fallback(instruments, underlying_price=70000.0,
                                     reference_time=reference_time, bs_calculator=bs_calculator)

        assert result[0]["delta"] == 0
        assert result[0]["gamma"] == 0

    def test_zero_underlying_price_skips_fallback(self, bs_calculator):
        """Guards against division-by-zero-style errors in BS math."""
        reference_time = datetime(2026, 6, 1, 0, 0, 0)
        instruments = [{
            "instrument_name": "BTC-1JUN26-70000-C",
            "strike": 70000.0,
            "option_type": "C",
            "mark_iv": 60.0,
            "delta": None,
            "gamma": None,
            "open_interest": 100.0,
        }]

        result = _apply_bs_fallback(instruments, underlying_price=0.0,
                                     reference_time=reference_time, bs_calculator=bs_calculator)

        assert result[0]["delta"] == 0
        assert result[0]["gamma"] == 0

    def test_output_feeds_gex_dex_calculator_without_error(self, bs_calculator):
        """End-to-end sanity: fallback output is directly consumable by GexDexCalculator."""
        reference_time = datetime(2026, 6, 1, 0, 0, 0)
        instruments = [
            {
                "instrument_name": "BTC-1JUN26-70000-C",
                "strike": 70000.0,
                "option_type": "C",
                "mark_iv": 60.0,
                "delta": None,
                "gamma": None,
                "open_interest": 100.0,
            },
            {
                "instrument_name": "BTC-1JUN26-70000-P",
                "strike": 70000.0,
                "option_type": "P",
                "mark_iv": 62.0,
                "delta": None,
                "gamma": None,
                "open_interest": 80.0,
            },
        ]

        enriched = _apply_bs_fallback(instruments, underlying_price=70000.0,
                                       reference_time=reference_time, bs_calculator=bs_calculator)

        calc = GexDexCalculator(instruments=enriched, spot_price=70000.0, currency="BTC")
        result = calc.calculate()

        assert "total_net_gex" in result
        assert "total_net_dex" in result
        assert isinstance(result["total_net_gex"], float)


class TestExtractUpdateValues:
    """Tests for flattening GexDexCalculator output into DB column values."""

    def test_extracts_all_five_columns(self):
        gex_dex_data = {
            "total_net_gex": 1234567.89,
            "total_net_dex": 12.3456,
            "key_levels": {
                "call_resistance": {"strike": 75000.0, "net_gex": 500000.0},
                "put_support": {"strike": 65000.0, "net_gex": -300000.0},
                "hvl": 70000.0,
                "gamma_flip": 70000.0,
            },
        }

        values = _extract_update_values(gex_dex_data)

        assert values == (1234567.89, 12.3456, 75000.0, 65000.0, 70000.0)

    def test_handles_none_call_resistance_and_put_support(self):
        """Empty/degenerate strike books should not raise — strikes come back None."""
        gex_dex_data = {
            "total_net_gex": 0.0,
            "total_net_dex": 0.0,
            "key_levels": {
                "call_resistance": None,
                "put_support": None,
                "hvl": None,
                "gamma_flip": None,
            },
        }

        values = _extract_update_values(gex_dex_data)

        assert values == (0.0, 0.0, None, None, None)

    def test_handles_missing_key_levels_key(self):
        """Defensive: key_levels missing entirely should not raise."""
        gex_dex_data = {"total_net_gex": 0.0, "total_net_dex": 0.0}

        values = _extract_update_values(gex_dex_data)

        assert values == (0.0, 0.0, None, None, None)
