"""Tests for wiring dvol_history collection into ProspectiveCollector's
hourly cycle (infra_spec.md section 1 / Task E3).

dvol_history is a SEPARATE table from volatility_index_history (already
written every hour by ``_fetch_dvol``). Prior to this task, dvol_history was
only written by a one-time backfill script and had been stale for weeks.
"""
from unittest.mock import MagicMock
from datetime import datetime, timezone

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


def test_fetch_dvol_history_row_persists_via_repository():
    """_fetch_dvol_history_row calls DVOLFetcher.fetch_latest and persists
    the result via the repository -- never a raw connection directly."""
    collector = _make_collector()
    collector._dvol_fetcher.fetch_latest.return_value = 62.5

    collector._fetch_dvol_history_row("BTC")

    collector._dvol_fetcher.fetch_latest.assert_called_once_with("BTC")
    collector.repo.save_dvol_history_row.assert_called_once()
    call_kwargs = collector.repo.save_dvol_history_row.call_args[1]
    assert call_kwargs["currency"] == "BTC"
    assert call_kwargs["dvol_value"] == 62.5
    assert isinstance(call_kwargs["timestamp"], datetime)
    assert call_kwargs["timestamp"].tzinfo == timezone.utc


def test_fetch_dvol_history_row_hour_aligned_timestamp():
    """The persisted timestamp is truncated to the top of the UTC hour --
    matches the ON CONFLICT (asset, timestamp) dedup key's intended
    granularity (one row per asset per hour, not per collection cycle)."""
    collector = _make_collector()
    collector._dvol_fetcher.fetch_latest.return_value = 50.0

    collector._fetch_dvol_history_row("ETH")

    ts = collector.repo.save_dvol_history_row.call_args[1]["timestamp"]
    assert ts.minute == 0
    assert ts.second == 0
    assert ts.microsecond == 0


def test_fetch_dvol_history_row_skips_persist_when_none():
    """When DVOLFetcher.fetch_latest returns None (API failure, per its own
    documented contract), no row is persisted and no exception is raised."""
    collector = _make_collector()
    collector._dvol_fetcher.fetch_latest.return_value = None

    collector._fetch_dvol_history_row("BTC")  # must not raise

    collector.repo.save_dvol_history_row.assert_not_called()


def test_fetch_dvol_history_row_raises_on_fetch_error():
    """Isolation contract: an exception raised while fetching/persisting
    propagates out of _fetch_dvol_history_row (matches _fetch_dvol's
    existing raise-on-error convention) -- _collect_currency's own
    try/except decides whether it's fatal to the cycle, not this method."""
    collector = _make_collector()
    collector._dvol_fetcher.fetch_latest.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        collector._fetch_dvol_history_row("BTC")


def test_collect_currency_calls_fetch_dvol_history_row_once_per_currency():
    """_collect_currency calls the new step exactly once per currency,
    independently of _fetch_dvol (the volatility_index_history writer)."""
    collector = _make_collector()
    collector._fetch_trades = MagicMock(return_value={"count": 5})
    collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
    collector._run_onchain_analysis = MagicMock()
    collector._fetch_dvol = MagicMock()
    collector._fetch_dvol_history_row = MagicMock()
    collector._fetch_funding_rate = MagicMock()
    collector._fetch_ohlcv = MagicMock()
    collector._persist_delta_flow = MagicMock()

    collector._collect_currency("BTC", datetime(2026, 3, 14))

    collector._fetch_dvol_history_row.assert_called_once_with("BTC")


def test_collect_currency_isolates_dvol_history_failure_from_other_steps():
    """A failure in _fetch_dvol_history_row must not prevent _fetch_dvol (or
    any other per-currency step) from running, and must not propagate out
    of _collect_currency -- same per-step isolation pattern used throughout
    this collector (each step has its own try/except)."""
    collector = _make_collector()
    collector._fetch_trades = MagicMock(return_value={"count": 5})
    collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
    collector._run_onchain_analysis = MagicMock()
    collector._fetch_dvol = MagicMock()
    collector._fetch_dvol_history_row = MagicMock(side_effect=RuntimeError("dvol_history down"))
    collector._fetch_funding_rate = MagicMock()
    collector._fetch_ohlcv = MagicMock()
    collector._persist_delta_flow = MagicMock()

    collector._collect_currency("BTC", datetime(2026, 3, 14))  # must not raise

    collector._fetch_dvol.assert_called_once_with("BTC")
    collector._fetch_funding_rate.assert_called_once_with("BTC")


def test_collect_currency_isolates_dvol_failure_from_dvol_history():
    """Symmetric isolation check: a failure in _fetch_dvol (the existing,
    unrelated writer) must not prevent the new _fetch_dvol_history_row step
    from running."""
    collector = _make_collector()
    collector._fetch_trades = MagicMock(return_value={"count": 5})
    collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
    collector._run_onchain_analysis = MagicMock()
    collector._fetch_dvol = MagicMock(side_effect=RuntimeError("dvol down"))
    collector._fetch_dvol_history_row = MagicMock()
    collector._fetch_funding_rate = MagicMock()
    collector._fetch_ohlcv = MagicMock()
    collector._persist_delta_flow = MagicMock()

    collector._collect_currency("BTC", datetime(2026, 3, 14))  # must not raise

    collector._fetch_dvol_history_row.assert_called_once_with("BTC")
