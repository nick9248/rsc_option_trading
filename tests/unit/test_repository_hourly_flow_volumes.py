"""Tests for get_hourly_flow_volumes and get_onchain_snapshot_history.

The flow-volume SQL previously lived inside chart_generator (reaching into
repository._db_cursor); these tests cover it at its new home in the
repository. get_onchain_snapshot_history replaces the legacy
max_pain/open_interest/volume history readers (frozen tables).
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _capture(repo, method, *args, **kwargs):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        getattr(repo, method)(*args, **kwargs)

    sql, params = mock_cursor.execute.call_args[0]
    return sql, params, mock_cursor


# ── get_hourly_flow_volumes ──────────────────────────────────────────────────

def test_block_filter_injects_clause():
    _, _, cur = _capture(
        _make_repo(), "get_hourly_flow_volumes",
        "BTC", 1000, 2000, expiration="27MAR26", trade_filter="block",
    )
    sql = cur.execute.call_args[0][0]
    assert "(amount * index_price) >= 100000" in sql


def test_non_block_filter_injects_clause():
    sql, _, _ = _capture(
        _make_repo(), "get_hourly_flow_volumes",
        "BTC", 1000, 2000, expiration="27MAR26", trade_filter="non_block",
    )
    assert "(amount * index_price) < 100000" in sql


def test_all_filter_has_no_clause():
    sql, _, _ = _capture(
        _make_repo(), "get_hourly_flow_volumes",
        "BTC", 1000, 2000, expiration="27MAR26", trade_filter="all",
    )
    assert "(amount * index_price)" not in sql


def test_expiration_adds_param():
    sql, params, _ = _capture(
        _make_repo(), "get_hourly_flow_volumes",
        "BTC", 1000, 2000, expiration="27MAR26",
    )
    assert "expiration = %s" in sql
    assert params == ("BTC", "27MAR26", 1000, 2000)


def test_no_expiration_three_params():
    sql, params, _ = _capture(
        _make_repo(), "get_hourly_flow_volumes", "BTC", 1000, 2000,
    )
    assert "expiration = %s" not in sql
    assert params == ("BTC", 1000, 2000)


# ── get_onchain_snapshot_history ─────────────────────────────────────────────

def test_onchain_history_chronological_dicts():
    repo = _make_repo()
    rows = [
        (datetime(2026, 7, 14, 15), 62000.0, 100.0, 80.0, 0.8, 500.0, 0.9, 62100.0),
        (datetime(2026, 7, 14, 14), 61500.0, 95.0, 85.0, 0.89, 480.0, 1.1, 61900.0),
    ]
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_onchain_snapshot_history("BTC", "27MAR26", limit=2)

    # DB returns newest-first; method must return chronological (oldest first)
    assert result[0]["snapshot_hour"] == datetime(2026, 7, 14, 14)
    assert result[0]["max_pain_strike"] == 61500.0
    assert result[1]["put_call_ratio_volume"] == 0.9

    sql, params = mock_cursor.execute.call_args[0]
    assert "FROM onchain_analysis_snapshots" in sql
    assert params == ("BTC", "27MAR26", 2)
