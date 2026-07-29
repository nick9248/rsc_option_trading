"""
Tests for DatabaseRepository.get_signed_taker_flow_by_strike,
get_trade_hour_coverage, and get_first_trade_timestamp
(institutional_metrics_spec.md section 2 / task C3).

Follows the FakeCursor pattern established by
test_repository_flow_analysis_trades.py -- captures the SQL/params passed to
``cursor.execute`` without touching a real DB.
"""
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    return repo


class FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.captured_queries = []
        self.captured_params = []
        self._fetchall_result = fetchall_result if fetchall_result is not None else []
        self._fetchone_result = fetchone_result

    def execute(self, query, params):
        self.captured_queries.append(query)
        self.captured_params.append(params)

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result


def _patched_cursor(repo, cursor):
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return patch.object(repo, "_db_cursor", return_value=mock_ctx)


class TestGetSignedTakerFlowByStrike:
    def test_query_filters_currency_expiration_since_and_excludes_null_direction(self):
        """direction IS NOT NULL guards the signed-sum CASE expression from
        silently treating a future null as 'sell' (current data has 0
        nulls [verified] -- defensive, matches the established
        get_trades_for_flow_analysis filter at this same table)."""
        repo = _make_repo()
        cursor = FakeCursor()

        with _patched_cursor(repo, cursor):
            repo.get_signed_taker_flow_by_strike(currency="BTC", expiration="27MAR26", since_ts=1000)

        assert len(cursor.captured_queries) == 1
        query = cursor.captured_queries[0]
        assert "direction IS NOT NULL" in query
        assert "GROUP BY strike, option_type" in query
        assert cursor.captured_params[0] == ("BTC", "27MAR26", 1000)

    def test_returns_list_of_dicts_with_expected_keys(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_result=[(70000.0, "C", 20.0, 100.0, 5, 1700000000000)])

        with _patched_cursor(repo, cursor):
            result = repo.get_signed_taker_flow_by_strike(currency="BTC", expiration="27MAR26", since_ts=0)

        assert result == [
            {
                "strike": 70000.0,
                "option_type": "C",
                "taker_net": 20.0,
                "gross_volume": 100.0,
                "trade_count": 5,
                "first_ts": 1700000000000,
            }
        ]

    def test_empty_result(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_result=[])

        with _patched_cursor(repo, cursor):
            result = repo.get_signed_taker_flow_by_strike(currency="BTC", expiration="27MAR26", since_ts=0)

        assert result == []


class TestGetTradeHourCoverage:
    def test_query_params_passed_through(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(10,))

        with _patched_cursor(repo, cursor):
            repo.get_trade_hour_coverage(currency="ETH", expiration="27JUN26", since_ts=111)

        assert cursor.captured_params[0] == ("ETH", "27JUN26", 111)

    def test_present_hours_from_query_and_expected_hours_from_wall_clock(self):
        import time as time_module

        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(5,))

        now_ms = int(time_module.time() * 1000)
        since_ts = now_ms - (10 * 3600 * 1000)  # 10 hours ago

        with _patched_cursor(repo, cursor):
            present_hours, expected_hours = repo.get_trade_hour_coverage(
                currency="BTC", expiration="27MAR26", since_ts=since_ts
            )

        assert present_hours == 5
        assert expected_hours in (9, 10)  # tolerate test-execution jitter

    def test_null_present_hours_returns_zero(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(None,))

        with _patched_cursor(repo, cursor):
            present_hours, _expected_hours = repo.get_trade_hour_coverage(
                currency="BTC", expiration="27MAR26", since_ts=0
            )

        assert present_hours == 0

    def test_since_ts_equal_to_now_gives_zero_expected_hours(self):
        import time as time_module

        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(0,))
        now_ms = int(time_module.time() * 1000)

        with _patched_cursor(repo, cursor):
            _present, expected_hours = repo.get_trade_hour_coverage(
                currency="BTC", expiration="27MAR26", since_ts=now_ms
            )

        assert expected_hours == 0


class TestGetFirstTradeTimestamp:
    def test_query_params_passed_through(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(1700000000000,))

        with _patched_cursor(repo, cursor):
            repo.get_first_trade_timestamp(currency="BTC", expiration="27MAR26")

        assert cursor.captured_params[0] == ("BTC", "27MAR26")

    def test_returns_int_timestamp(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(1700000000000,))

        with _patched_cursor(repo, cursor):
            result = repo.get_first_trade_timestamp(currency="BTC", expiration="27MAR26")

        assert result == 1700000000000
        assert isinstance(result, int)

    def test_no_trades_returns_none(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(None,))

        with _patched_cursor(repo, cursor):
            result = repo.get_first_trade_timestamp(currency="BTC", expiration="27MAR26")

        assert result is None
