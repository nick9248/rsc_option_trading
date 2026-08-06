"""Tests for DatabaseRepository.save_dvol_history_row (infra_spec.md
section 1 / Task E3).

The daemon collector calls this repository method rather than reaching into
DVOLFetcher.save_to_db(rows, asset, conn) directly with a raw connection --
that would violate this project's layered architecture (every other daemon
writer goes through DatabaseRepository, not a service class's raw-conn
method). The insert SQL is intentionally re-declared here (not imported
from DVOLFetcher) because coding/core/database/repository.py is Core and
coding/service/deribit/dvol_fetcher.py is Service -- Core must not import
Service. Both call sites share the identical
``ON CONFLICT (asset, timestamp) DO NOTHING`` idempotency key by
convention, not by shared code.
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.logger = MagicMock()
    return repo


def test_save_dvol_history_row_inserts_idempotently():
    repo = _make_repo()
    ts = datetime(2026, 8, 6, 14, 0, 0, tzinfo=timezone.utc)
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1

    with patch.object(repo, "_db_cursor") as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.save_dvol_history_row("BTC", ts, 62.5)

    sql = mock_cursor.execute.call_args[0][0]
    params = mock_cursor.execute.call_args[0][1]
    assert "dvol_history" in sql
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert params == ("BTC", ts, 62.5)
    assert result == 1


def test_save_dvol_history_row_returns_zero_on_conflict():
    """Existing (asset, timestamp) row -> ON CONFLICT DO NOTHING -> rowcount 0."""
    repo = _make_repo()
    ts = datetime(2026, 8, 6, 14, 0, 0, tzinfo=timezone.utc)
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0

    with patch.object(repo, "_db_cursor") as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.save_dvol_history_row("ETH", ts, 40.0)

    assert result == 0


def test_save_dvol_history_row_uses_asset_column_name():
    """dvol_history's column is `asset` (not `currency`, unlike most other
    tables in this repository) -- confirm the SQL text matches the schema
    exactly (migrations/000_base_schema.sql)."""
    repo = _make_repo()
    ts = datetime(2026, 8, 6, 14, 0, 0, tzinfo=timezone.utc)
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1

    with patch.object(repo, "_db_cursor") as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        repo.save_dvol_history_row("BTC", ts, 62.5)

    sql = mock_cursor.execute.call_args[0][0]
    assert "INSERT INTO dvol_history (asset, timestamp, dvol_value)" in sql
    assert "ON CONFLICT (asset, timestamp)" in sql
