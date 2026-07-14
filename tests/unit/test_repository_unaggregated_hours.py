"""Tests for get_unaggregated_hours lookback bounding.

Regression: the unbounded gap-finder rescanned the full historical_trades
table (with per-row to_timestamp) every collection cycle. At 1.28M rows this
took minutes on the VPS and stalled the pipeline behind it (observed
2026-07-14). The query must carry a sargable trade_timestamp lower bound.
"""
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _run(lookback_hours=None):
    repo = DatabaseRepository.__new__(DatabaseRepository)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [(datetime(2026, 7, 14, 15),)]
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        if lookback_hours is None:
            result = repo.get_unaggregated_hours("BTC")
        else:
            result = repo.get_unaggregated_hours("BTC", lookback_hours=lookback_hours)

    sql, params = mock_cursor.execute.call_args[0]
    return result, sql, params


def test_query_is_bounded_by_trade_timestamp():
    """The SQL must filter on raw trade_timestamp (sargable), not full history."""
    _, sql, params = _run()
    assert "trade_timestamp >= %s" in sql
    assert "NOT EXISTS" in sql

    currency, lookback_ms, currency2 = params
    assert currency == "BTC" and currency2 == "BTC"
    # default 168h lookback: bound must sit ~7 days behind now (epoch ms)
    now_ms = time.time() * 1000
    assert abs((now_ms - lookback_ms) - 168 * 3600 * 1000) < 60_000


def test_custom_lookback_respected():
    _, _, params = _run(lookback_hours=24)
    now_ms = time.time() * 1000
    assert abs((now_ms - params[1]) - 24 * 3600 * 1000) < 60_000


def test_returns_hour_buckets():
    result, _, _ = _run()
    assert result == [datetime(2026, 7, 14, 15)]
