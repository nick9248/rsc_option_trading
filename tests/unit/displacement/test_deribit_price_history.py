# tests/unit/displacement/test_deribit_price_history.py
from unittest.mock import MagicMock, patch
import pytest
from coding.service.deribit.deribit_api_service import DeribitApiService


class TestGetPriceOhlcv:
    @patch("coding.service.deribit.deribit_api_service.ApiConnection")
    def test_returns_list_of_candles(self, mock_conn_cls):
        now_ms = 1745000000000
        mock_response = {
            "result": {
                "ticks": [now_ms - 3600000, now_ms],
                "open": [90000.0, 91000.0],
                "high": [91500.0, 91800.0],
                "low": [89500.0, 90500.0],
                "close": [91000.0, 91500.0],
                "volume": [100.0, 120.0],
            }
        }
        mock_conn = MagicMock()
        mock_conn.fetch.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        svc = DeribitApiService()
        result = svc.get_price_ohlcv("BTC", resolution_hours=1, lookback_hours=168)

        assert isinstance(result, list)
        assert len(result) == 2
        assert "timestamp" in result[0]
        assert "close" in result[0]
        assert "open" in result[0]
        assert "high" in result[0]
        assert "low" in result[0]
        assert "volume" in result[0]
        # Newest first
        assert result[0]["timestamp"] >= result[1]["timestamp"]

    @patch("coding.service.deribit.deribit_api_service.ApiConnection")
    def test_empty_response_returns_empty_list(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn.fetch.return_value = {"result": {"ticks": [], "close": [], "open": [], "high": [], "low": [], "volume": []}}
        mock_conn_cls.return_value = mock_conn

        svc = DeribitApiService()
        result = svc.get_price_ohlcv("ETH", resolution_hours=1, lookback_hours=24)
        assert result == []

    @patch("coding.service.deribit.deribit_api_service.ApiConnection")
    def test_api_exception_returns_empty_list(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn.fetch.side_effect = Exception("Connection refused")
        mock_conn_cls.return_value = mock_conn

        svc = DeribitApiService()
        result = svc.get_price_ohlcv("BTC", resolution_hours=1, lookback_hours=24)
        assert result == []

    @patch("coding.service.deribit.deribit_api_service.ApiConnection")
    def test_uses_perpetual_instrument(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn.fetch.return_value = {"result": {"ticks": [], "close": [], "open": [], "high": [], "low": [], "volume": []}}
        mock_conn_cls.return_value = mock_conn

        svc = DeribitApiService()
        svc.get_price_ohlcv("BTC", resolution_hours=1, lookback_hours=24)

        call_args = mock_conn.fetch.call_args
        params = call_args[1].get("parameters") or (call_args[0][1] if len(call_args[0]) > 1 else {})
        assert params.get("instrument_name") == "BTC-PERPETUAL"
