from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_signal import DisplacementSignal


def _make_mock_api(btc_prices=None, eth_prices=None, funding=-0.008, dvol=85.0):
    """Build a mock DeribitApiService."""
    api = MagicMock()

    def price_side_effect(asset, **kwargs):
        if asset == "BTC":
            prices = btc_prices or ([78000.0] + [80000.0] * 3 + [100000.0] * 163)
        else:
            prices = eth_prices or ([1200.0] + [1230.0] * 3 + [1540.0] * 163)
        return [{"timestamp": i, "close": p} for i, p in enumerate(prices)]

    api.get_price_ohlcv.side_effect = price_side_effect
    api.get_funding_chart_data.return_value = {"result": [{"interest_8h": funding}]}
    api.get_volatility_index_data.return_value = {"data": [[0, 50, 60, 45, dvol]]}
    api.get_book_summary_by_currency.return_value = [
        {
            "instrument_name": "BTC-25SEP26-70000-C", "option_type": "call",
            "strike": 70000.0, "dte": 153, "delta": 0.14,
            "bid_iv": 0.85, "ask_iv": 0.90, "mark_iv": 0.87,
            "open_interest": 500.0, "mark_price": 0.013,
            "underlying_price": 78000.0,
        }
    ]
    return api


def _make_mock_repo():
    repo = MagicMock()
    repo.get_dvol_history.return_value = [50.0] * 90
    repo.get_ohlcv_daily.return_value = [{"close": 100000.0}] * 500
    repo.get_funding_rate_history.return_value = [-0.008] * 100
    return repo


# Prices that produce a 22% 24h drop (triggers displacement)
BTC_DROP_PRICES = [78000.0] + [78500.0] * 3 + [80000.0] * 20 + [100000.0] * 143
# Prices that produce only a 5% 24h drop (no displacement)
BTC_FLAT_PRICES = [95000.0] * 168


class TestDisplacementScannerService:
    def _make_svc(self, api, repo):
        from coding.service.displacement.displacement_scanner_service import DisplacementScannerService
        cfg = DisplacementConfig()
        return DisplacementScannerService(config=cfg, api_service=api, repository=repo)

    def test_scan_returns_empty_when_no_displacement(self):
        api = _make_mock_api(btc_prices=BTC_FLAT_PRICES)
        svc = self._make_svc(api, _make_mock_repo())
        result = svc.scan(["BTC"])
        assert result == []

    def test_scan_returns_signal_when_displacement_detected(self):
        # BTC_DROP_PRICES produces 22% 24h drop → above 20% threshold
        # Force threshold to 0 so any conviction score triggers a signal
        api = _make_mock_api(btc_prices=BTC_DROP_PRICES)
        repo = _make_mock_repo()
        svc = self._make_svc(api, repo)
        svc._config = DisplacementConfig(alert_medium_threshold=0.0, alert_high_threshold=0.01)
        with patch.object(svc._telegram, "send", return_value=True):
            result = svc.scan(["BTC"])
        assert len(result) >= 1
        assert isinstance(result[0], DisplacementSignal)

    def test_scan_saves_signal_when_event_and_conviction_met(self):
        api = _make_mock_api(btc_prices=BTC_DROP_PRICES, funding=-0.01)
        repo = _make_mock_repo()
        svc = self._make_svc(api, repo)
        # Zero threshold ensures any displacement event triggers a save
        svc._config = DisplacementConfig(alert_medium_threshold=0.0, alert_high_threshold=0.01)
        with patch.object(svc._telegram, "send", return_value=True):
            svc.scan(["BTC"])
        assert repo.save_displacement_signal.call_count == 1

    def test_scan_handles_multiple_assets(self):
        api = _make_mock_api(
            btc_prices=BTC_FLAT_PRICES,
            eth_prices=BTC_FLAT_PRICES,
        )
        repo = _make_mock_repo()
        svc = self._make_svc(api, repo)
        result = svc.scan(["BTC", "ETH"])
        assert isinstance(result, list)

    def test_scan_continues_when_one_asset_fails(self):
        api = MagicMock()
        api.get_price_ohlcv.side_effect = Exception("API error")
        repo = _make_mock_repo()
        svc = self._make_svc(api, repo)
        # Should not raise — errors are caught per-asset
        result = svc.scan(["BTC", "ETH"])
        assert isinstance(result, list)

    def test_get_current_prices_returns_price_and_change(self):
        api = _make_mock_api()
        svc = self._make_svc(api, _make_mock_repo())
        result = svc.get_current_prices("BTC")
        assert "price" in result
        assert "change_24h_pct" in result
        assert isinstance(result["price"], float)

    def test_get_current_prices_returns_zeros_on_error(self):
        api = MagicMock()
        api.get_price_ohlcv.side_effect = Exception("fail")
        svc = self._make_svc(api, _make_mock_repo())
        result = svc.get_current_prices("BTC")
        assert result == {"price": 0.0, "change_24h_pct": 0.0}
