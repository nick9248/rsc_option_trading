"""Tests for the DVOL step of ProspectiveCollector's cycle.

dvol_history is the single DVOL table. Every cycle the collector re-checks the last
48 hours of hourly candles and stores the final close of each completed candle,
correcting rows that were captured while a candle was still forming.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _make_collector():
    """Build a ProspectiveCollector with mocked dependencies."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    collector._dvol_fetcher = MagicMock()
    return collector


def _candles():
    return [
        (datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc), 36.9),
        (datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc), 36.76),
    ]


def test_sync_dvol_history_stores_completed_candles_via_the_repository():
    collector = _make_collector()
    collector._dvol_fetcher.fetch_recent_completed_candles.return_value = _candles()
    collector.repo.save_dvol_candles.return_value = 2

    collector._sync_dvol_history("BTC")

    collector._dvol_fetcher.fetch_recent_completed_candles.assert_called_once_with("BTC", hours=48)
    collector.repo.save_dvol_candles.assert_called_once_with("BTC", _candles())


def test_sync_dvol_history_raises_when_the_fetch_fails():
    """None means the Deribit fetch failed: surface it so the cycle logs the error."""
    collector = _make_collector()
    collector._dvol_fetcher.fetch_recent_completed_candles.return_value = None

    with pytest.raises(RuntimeError, match="DVOL candle fetch failed for ETH"):
        collector._sync_dvol_history("ETH")

    collector.repo.save_dvol_candles.assert_not_called()


def _collect_with_stubbed_steps(collector):
    collector._fetch_trades = MagicMock(return_value={"count": 5})
    collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
    collector._run_onchain_analysis = MagicMock()
    collector._fetch_funding_rate = MagicMock()
    collector._fetch_ohlcv = MagicMock()
    collector._persist_delta_flow = MagicMock()
    collector._collect_currency("BTC", datetime(2026, 3, 14))


def test_collect_currency_runs_the_dvol_step_once_per_currency():
    collector = _make_collector()
    collector._sync_dvol_history = MagicMock()

    _collect_with_stubbed_steps(collector)

    collector._sync_dvol_history.assert_called_once_with("BTC")


def test_collect_currency_isolates_a_dvol_failure_from_the_other_steps():
    """A DVOL failure must not stop the funding-rate step or propagate out of
    _collect_currency (every step has its own try/except)."""
    collector = _make_collector()
    collector._sync_dvol_history = MagicMock(side_effect=RuntimeError("dvol down"))

    _collect_with_stubbed_steps(collector)  # must not raise

    collector._fetch_funding_rate.assert_called_once_with("BTC")
