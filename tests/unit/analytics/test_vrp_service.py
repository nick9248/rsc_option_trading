"""
Unit tests for VRPService (Wave H Task H-D).

institutional_metrics_spec.md's audit found VRPService has zero live
callers anywhere in the codebase (grep-confirmed again here) -- it is dead
code in the sense that nothing in the production pipeline reaches it, but
it is still public API and inherits VRPCalculator's fabricated-default
fix, so it needs the same "insufficient input -> None, never a fabricated
0.0/NEUTRAL" contract as VRPCalculator itself. These tests exercise
VRPService end-to-end (through a fake DeribitApiService) to prove the
Optional propagation holds all the way from the API boundary through
calculate_vrp() and generate_report().
"""
import math
import time
from unittest.mock import MagicMock

from coding.service.analytics.vrp_service import VRPService


def _tradingview_response(days: int, base_price: float = 60000.0, flat: bool = False):
    """Synthetic get_tradingview_chart_data-shaped response, `days` daily
    candles ending "now", real epoch ms timestamps."""
    now_ms = int(time.time() * 1000)
    ticks = [now_ms - (days - i) * 86400_000 for i in range(days)]
    if flat:
        closes = [base_price] * days
    else:
        closes = [base_price * (1 + 0.02 * math.sin(i * 0.7)) for i in range(days)]
    return {"status": "ok", "ticks": ticks, "close": closes}


def _atm_options(expiration: str = "28MAR26", n: int = 2):
    """Options priced ATM (moneyness ~1.0) so they pass the default
    (0.9, 1.1) filter."""
    return [
        {
            "instrument_name": f"BTC-{expiration}-{60000 + i * 500}-C",
            "mark_iv": 60.0 + i,  # percentage, as the raw API returns it
            "underlying_price": 60000.0,
            "open_interest": 100,
            "volume": 10,
        }
        for i in range(n)
    ]


def _otm_options(expiration: str = "28MAR26", n: int = 2):
    """Options priced far OTM (moneyness way outside (0.9, 1.1))."""
    return [
        {
            "instrument_name": f"BTC-{expiration}-200000-C",
            "mark_iv": 80.0,
            "underlying_price": 60000.0,
            "open_interest": 100,
            "volume": 10,
        }
        for _ in range(n)
    ]


class TestVrpServiceInsufficientData:
    def test_price_history_api_failure_returns_empty_result(self):
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = {"status": "error"}
        service = VRPService(api_service=api)

        result = service.calculate_vrp("BTC", "28MAR26")

        assert result["vrp_absolute"] is None
        assert result["vrp_percentage"] is None
        assert result["implied_volatility"] is None
        assert result["realized_volatility"] is None
        assert result["iv_percentile"] is None
        assert result["signal"] == "NO_DATA"

    def test_insufficient_price_history_returns_empty_result(self):
        """Only 1 candle -- calculate_realized_volatility now returns None
        (not a fabricated 0.0) and VRPService must propagate that as an
        insufficient-data result, not crash on `None * 100`."""
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = _tradingview_response(1)
        service = VRPService(api_service=api)

        result = service.calculate_vrp("BTC", "28MAR26")

        assert result["realized_volatility"] is None
        assert result["signal"] == "NO_DATA"

    def test_no_options_for_expiration_returns_empty_result(self):
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = _tradingview_response(40)
        api.get_book_summary.return_value = []
        service = VRPService(api_service=api)

        result = service.calculate_vrp("BTC", "28MAR26")

        assert result["implied_volatility"] is None
        assert result["signal"] == "NO_DATA"

    def test_nothing_passes_moneyness_filter_returns_empty_result(self):
        """calculate_average_iv now returns None (not a fabricated 0.0)
        when nothing is within the ATM band -- must propagate cleanly."""
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = _tradingview_response(40)
        api.get_book_summary.return_value = _otm_options()
        service = VRPService(api_service=api)

        result = service.calculate_vrp("BTC", "28MAR26")

        assert result["implied_volatility"] is None
        assert result["signal"] == "NO_DATA"

    def test_flat_price_series_self_contradiction_case_is_closed(self):
        """A perfectly flat price series gives a real (not None) but exactly
        zero realized_vol. Before this task, calculate_vrp would force
        vrp_percentage=0.0/signal=NEUTRAL while vrp_absolute stayed a real,
        large, non-zero number. It must now propagate as a full
        insufficient-data result instead."""
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = _tradingview_response(40, flat=True)
        api.get_book_summary.return_value = _atm_options()
        service = VRPService(api_service=api)

        result = service.calculate_vrp("BTC", "28MAR26")

        assert result["vrp_absolute"] is None
        assert result["vrp_percentage"] is None
        assert result["signal"] == "NO_DATA"

    def test_generate_report_on_insufficient_data_is_honest_not_a_crash(self):
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = {"status": "error"}
        service = VRPService(api_service=api)

        report = service.generate_report("BTC", "28MAR26")

        assert "Insufficient data" in report
        assert "NEUTRAL" not in report
        assert "50.0%" not in report


class TestVrpServiceHappyPath:
    def test_sufficient_data_computes_a_real_vrp(self):
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = _tradingview_response(40)
        api.get_book_summary.return_value = _atm_options()
        service = VRPService(api_service=api)

        result = service.calculate_vrp("BTC", "28MAR26")

        assert result["vrp_absolute"] is not None
        assert result["vrp_percentage"] is not None
        assert result["implied_volatility"] is not None
        assert result["realized_volatility"] is not None
        assert result["signal"] in (
            "VERY_EXPENSIVE", "EXPENSIVE", "NEUTRAL", "CHEAP", "VERY_CHEAP",
        )
        # Fewer than MIN_OBS (30) IV observations from a 2-option proxy
        # history -- iv_percentile is honestly None, not a fabricated 50.0.
        assert result["iv_percentile"] is None

    def test_generate_report_on_sufficient_data_renders_real_numbers(self):
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = _tradingview_response(40)
        api.get_book_summary.return_value = _atm_options()
        service = VRPService(api_service=api)

        report = service.generate_report("BTC", "28MAR26")

        assert "Insufficient data" not in report
        assert "Implied Volatility (IV):" in report
        assert "Data Quality:" in report
