"""Tests for get_onchain_snapshot_history, which replaces the legacy max_pain/open_interest/volume history readers (frozen tables)."""
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
