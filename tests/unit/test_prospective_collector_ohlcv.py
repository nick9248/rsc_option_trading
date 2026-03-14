"""Tests for OHLCV collection in ProspectiveCollector."""
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest


def _make_collector():
    """Build a ProspectiveCollector with mocked dependencies."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    return collector


def test_fetch_ohlcv_saves_candles():
    """_fetch_ohlcv calls save_ohlcv for each candle returned by API."""
    collector = _make_collector()

    collector.api.get_tradingview_chart_data.return_value = {
        "ticks": [1700000000000, 1700086400000],
        "open":  [37000.0, 37500.0],
        "high":  [38000.0, 38200.0],
        "low":   [36500.0, 37100.0],
        "close": [37500.0, 37900.0],
        "volume": [100.0, 120.0],
        "status": "ok"
    }

    collector._fetch_ohlcv("BTC")

    assert collector.repo.save_ohlcv.call_count == 2
    first_call_args = collector.repo.save_ohlcv.call_args_list[0][1]
    assert first_call_args["currency"] == "BTC"
    assert first_call_args["instrument_name"] == "BTC-PERPETUAL"
    assert first_call_args["close"] == 37500.0


def test_fetch_ohlcv_empty_response_does_not_crash():
    """_fetch_ohlcv handles empty API response gracefully."""
    collector = _make_collector()
    collector.api.get_tradingview_chart_data.return_value = {}

    collector._fetch_ohlcv("ETH")  # should not raise

    collector.repo.save_ohlcv.assert_not_called()


def test_fetch_ohlcv_none_response_does_not_crash():
    """_fetch_ohlcv handles None API response gracefully."""
    collector = _make_collector()
    collector.api.get_tradingview_chart_data.return_value = None

    collector._fetch_ohlcv("BTC")  # should not raise

    collector.repo.save_ohlcv.assert_not_called()


def test_collect_currency_calls_fetch_ohlcv():
    """_collect_currency calls _fetch_ohlcv as part of collection."""
    collector = _make_collector()
    collector._fetch_trades = MagicMock(return_value={"count": 5})
    collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
    collector._run_onchain_analysis = MagicMock()
    collector._fetch_dvol = MagicMock()
    collector._fetch_funding_rate = MagicMock()
    collector._fetch_ohlcv = MagicMock()

    collector._collect_currency("BTC", datetime(2026, 3, 14))

    collector._fetch_ohlcv.assert_called_once_with("BTC")
