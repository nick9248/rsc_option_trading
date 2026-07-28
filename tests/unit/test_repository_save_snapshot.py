"""
Unit tests for DatabaseRepository.save_snapshot's mark_iv persistence.

institutional_metrics_spec.md Migration M1 / Decision D11: the daemon's
hourly full-chain `snapshots` table capture (40/40 days complete) dropped
`mark_iv` on write even though get_book_summary already returns it. This is
the single highest-leverage fix in the institutional-metrics research --
verify the write path actually persists it, not just that the column exists.

Mocked cursor only -- no live database (matches the existing
test_repository_onchain_snapshot.py pattern for save_* methods).
"""
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _capture_executemany_rows(repo, data, captured_at="2026-07-28 12:00:00"):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_snapshot(currency="BTC", data=data, captured_at=captured_at)

    # save_snapshot calls cursor.executemany(insert_sql, rows)
    args, _ = mock_cursor.executemany.call_args
    insert_sql, rows = args
    return insert_sql, rows


def test_mark_iv_present_in_insert_column_list():
    """The INSERT statement must name mark_iv -- regression guard for the
    exact defect this migration fixes (column silently dropped on write)."""
    repo = _make_repo()
    insert_sql, _ = _capture_executemany_rows(
        repo,
        data=[{
            "instrument_name": "BTC-30SEP26-70000-C",
            "open_interest": 100.0,
            "volume": 5.0,
            "volume_usd": 1000.0,
            "underlying_price": 68000.0,
            "mark_price": 0.05,
            "bid_price": 0.049,
            "ask_price": 0.051,
            "mark_iv": 35.70,
        }],
    )
    assert "mark_iv" in insert_sql


def test_mark_iv_value_persisted_from_book_summary_item():
    """The mark_iv value from the book-summary item must land in the row
    tuple passed to executemany, not silently dropped."""
    repo = _make_repo()
    insert_sql, rows = _capture_executemany_rows(
        repo,
        data=[{
            "instrument_name": "BTC-30SEP26-70000-C",
            "open_interest": 100.0,
            "volume": 5.0,
            "volume_usd": 1000.0,
            "underlying_price": 68000.0,
            "mark_price": 0.05,
            "bid_price": 0.049,
            "ask_price": 0.051,
            "mark_iv": 35.70,
        }],
    )
    assert len(rows) == 1
    assert 35.70 in rows[0]


def test_mark_iv_missing_from_item_defaults_to_none_not_dropped():
    """A book-summary item without mark_iv (defensive -- live API always
    includes it for options) must still produce a row, with mark_iv=None,
    not raise and not silently omit the row."""
    repo = _make_repo()
    insert_sql, rows = _capture_executemany_rows(
        repo,
        data=[{
            "instrument_name": "BTC-30SEP26-70000-C",
            "open_interest": 100.0,
            "volume": 5.0,
            "volume_usd": 1000.0,
            "underlying_price": 68000.0,
            "mark_price": 0.05,
            "bid_price": 0.049,
            "ask_price": 0.051,
        }],
    )
    assert len(rows) == 1
    assert rows[0][-1] is None


def test_mark_iv_is_last_column_matching_insert_order():
    """Row tuple order must match the INSERT column list exactly (mark_iv
    appended last, after ask_price) -- a param-position mismatch would
    silently write mark_iv into the wrong column."""
    repo = _make_repo()
    insert_sql, rows = _capture_executemany_rows(
        repo,
        data=[{
            "instrument_name": "BTC-30SEP26-70000-C",
            "open_interest": 100.0,
            "volume": 5.0,
            "volume_usd": 1000.0,
            "underlying_price": 68000.0,
            "mark_price": 0.05,
            "bid_price": 0.049,
            "ask_price": 0.051,
            "mark_iv": 35.70,
        }],
    )
    # Column order in insert_sql: captured_at, currency, instrument_name,
    # expiration, strike, option_type, open_interest, volume, volume_usd,
    # underlying_price, mark_price, bid_price, ask_price, mark_iv
    row = rows[0]
    assert row[10] == 0.05    # mark_price
    assert row[11] == 0.049   # bid_price
    assert row[12] == 0.051   # ask_price
    assert row[13] == 35.70   # mark_iv
