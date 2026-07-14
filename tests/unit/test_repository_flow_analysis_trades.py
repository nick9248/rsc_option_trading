"""
Tests for DatabaseRepository.get_trades_for_flow_analysis.

Covers the block/non_block/all trade_filter SQL clause injection that used
to live inline in BuySellFlowAnalyzer._fetch_trades before it was moved into
the repository layer (architecture fix: analytics code should not build SQL
directly against repository internals).
"""
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    return repo


class FakeCursor:
    def __init__(self):
        self.captured_queries = []
        self.captured_params = []

    def execute(self, query, params):
        self.captured_queries.append(query)
        self.captured_params.append(params)

    def fetchall(self):
        return []


def _patched_cursor(repo, cursor):
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return patch.object(repo, "_db_cursor", return_value=mock_ctx)


def test_block_filter_injects_sql():
    """Block filter must inject 'AND (amount * index_price) >= 100000' into the SQL."""
    repo = _make_repo()
    cursor = FakeCursor()

    with _patched_cursor(repo, cursor):
        repo.get_trades_for_flow_analysis(
            currency="BTC", expiration="27MAR26",
            start_ts=1000, end_ts=2000, trade_filter="block",
        )

    assert len(cursor.captured_queries) == 1
    assert "(amount * index_price) >= 100000" in cursor.captured_queries[0]


def test_non_block_filter_injects_sql():
    """Non-block filter must inject 'AND (amount * index_price) < 100000' into SQL."""
    repo = _make_repo()
    cursor = FakeCursor()

    with _patched_cursor(repo, cursor):
        repo.get_trades_for_flow_analysis(
            currency="BTC", expiration="27MAR26",
            start_ts=1000, end_ts=2000, trade_filter="non_block",
        )

    assert "(amount * index_price) < 100000" in cursor.captured_queries[0]


def test_all_filter_no_block_clause():
    """'all' filter must NOT inject any block filter clause."""
    repo = _make_repo()
    cursor = FakeCursor()

    with _patched_cursor(repo, cursor):
        repo.get_trades_for_flow_analysis(
            currency="BTC", expiration="27MAR26",
            start_ts=1000, end_ts=2000, trade_filter="all",
        )

    assert "(amount * index_price)" not in cursor.captured_queries[0]


def test_query_params_passed_through():
    """Currency, expiration, and timestamp window must be passed as query params."""
    repo = _make_repo()
    cursor = FakeCursor()

    with _patched_cursor(repo, cursor):
        repo.get_trades_for_flow_analysis(
            currency="ETH", expiration="27JUN26",
            start_ts=111, end_ts=222, trade_filter="all",
        )

    assert cursor.captured_params[0] == ("ETH", "27JUN26", 111, 222)


def test_returns_list_of_dicts_with_expected_keys():
    """Rows must be mapped to dicts with the documented column names."""
    repo = _make_repo()
    cursor = FakeCursor()
    cursor.fetchall = lambda: [
        ("trade1", 1700000000000, "BTC-27MAR26-90000-C", 90000.0, "C", 0.05, 1.5, "buy", 85000.0)
    ]

    with _patched_cursor(repo, cursor):
        result = repo.get_trades_for_flow_analysis(
            currency="BTC", expiration="27MAR26",
            start_ts=1000, end_ts=2000, trade_filter="all",
        )

    assert result == [
        {
            "trade_id": "trade1",
            "trade_timestamp": 1700000000000,
            "instrument_name": "BTC-27MAR26-90000-C",
            "strike": 90000.0,
            "option_type": "C",
            "price": 0.05,
            "amount": 1.5,
            "direction": "buy",
            "index_price": 85000.0,
        }
    ]


def test_empty_result():
    """No matching trades returns an empty list."""
    repo = _make_repo()
    cursor = FakeCursor()

    with _patched_cursor(repo, cursor):
        result = repo.get_trades_for_flow_analysis(
            currency="BTC", expiration="27MAR26",
            start_ts=1000, end_ts=2000, trade_filter="all",
        )

    assert result == []
