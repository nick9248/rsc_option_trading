"""
Unit tests for DatabaseRepository.save_hourly_snapshots's ON CONFLICT DO
UPDATE clause (Task E2, Wave E data/infra repairs).

Bug: futures_price and basis are present in the INSERT column list but
missing from the DO UPDATE SET clause, so a re-aggregation of an
(instrument_name, snapshot_hour) pair that already has a row (daemon
re-run, backfill, or late-arriving trades within a still-open hour)
refreshes every other column but freezes futures_price/basis at whatever
was written on the first pass -- silently going stale within a row that
otherwise looks live.

Mocked cursor only -- no live database (matches the established
mocked-cursor pattern at test_repository_save_snapshot.py /
test_repository_delta_flow.py for this repository's save_* methods).
"""
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _capture_query(repo, snapshots):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_hourly_snapshots(snapshots)

    args, _ = mock_cursor.execute.call_args
    insert_sql, params = args
    return insert_sql, params


def _snapshot(**overrides):
    base = {
        "snapshot_hour": "2026-08-06 14:00:00",
        "captured_at": "2026-08-06 14:05:00",
        "instrument_name": "BTC-30SEP26-70000-C",
        "currency": "BTC",
        "strike": 70000.0,
        "expiration": "30SEP26",
        "option_type": "C",
        "trade_count": 3,
        "total_volume": 1.5,
        "vwap": 0.05,
        "bid_price": 0.049,
        "ask_price": 0.051,
        "mark_price": 0.05,
        "mark_iv": 65.0,
        "open_interest": 100.0,
        "index_price": 68000.0,
        "futures_price": 68050.0,
        "basis": 50.0,
        "avg_delta": 0.4,
        "avg_gamma": 0.001,
        "avg_theta": -10.0,
        "avg_vega": 20.0,
    }
    base.update(overrides)
    return base


def test_do_update_set_refreshes_futures_price_and_basis():
    """Regression guard for the exact defect this task fixes: both columns
    are already present in the INSERT column list -- the DO UPDATE SET
    clause must refresh them too, or a re-aggregation silently freezes
    them at their first-write value while every other column refreshes.

    Currently FAILS: the DO UPDATE SET clause omits both columns.
    """
    repo = _make_repo()
    insert_sql, _ = _capture_query(repo, [_snapshot()])

    do_update_clause = insert_sql.split("DO UPDATE SET", 1)[1]
    assert "futures_price = EXCLUDED.futures_price" in do_update_clause
    assert "basis = EXCLUDED.basis" in do_update_clause


def test_do_update_set_still_refreshes_existing_neighbor_columns():
    """Isolation guard: fixing futures_price/basis must not disturb the
    neighboring columns (open_interest, index_price, avg_delta) that
    already refresh correctly."""
    repo = _make_repo()
    insert_sql, _ = _capture_query(repo, [_snapshot()])

    do_update_clause = insert_sql.split("DO UPDATE SET", 1)[1]
    assert "open_interest = EXCLUDED.open_interest" in do_update_clause
    assert "index_price = EXCLUDED.index_price" in do_update_clause
    assert "avg_delta = EXCLUDED.avg_delta" in do_update_clause


def test_insert_column_list_unchanged_futures_price_and_basis_still_present():
    """The INSERT column list already names futures_price/basis (that part
    was never buggy) -- this fix must not touch it."""
    repo = _make_repo()
    insert_sql, _ = _capture_query(repo, [_snapshot()])

    insert_clause = insert_sql.split("ON CONFLICT", 1)[0]
    assert "futures_price" in insert_clause
    assert "basis" in insert_clause
