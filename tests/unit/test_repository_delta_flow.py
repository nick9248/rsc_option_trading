"""
Tests for DatabaseRepository.get_trades_for_delta_flow,
save_delta_flow_hourly, and get_delta_flow_summary
(institutional_metrics_spec.md section 6 / infra_spec.md section 2 --
task C7).

Follows the FakeCursor pattern established by
test_repository_flow_analysis_trades.py / test_repository_dealer_inventory.py
-- captures the SQL/params passed to ``cursor.execute`` without touching a
real DB. Schema (columns, unique constraint) verified separately via a
read-only SELECT against the live table (migration 021 / dbf6803) --
recorded in task-C7-report.md, not re-verified here.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository
from coding.core.analytics.results.delta_flow_results import FlowBucket


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


class TestGetTradesForDeltaFlow:
    def test_query_filters_currency_window_and_excludes_null_direction_strike(self):
        """Mirrors the established filter at this same table
        (get_trades_for_flow_analysis / get_signed_taker_flow_by_strike) --
        defensive, current data verified 0-null."""
        repo = _make_repo()
        cursor = FakeCursor()

        with _patched_cursor(repo, cursor):
            repo.get_trades_for_delta_flow(currency="BTC", start_ts=1000, end_ts=2000)

        assert len(cursor.captured_queries) == 1
        query = cursor.captured_queries[0]
        assert "direction IS NOT NULL" in query
        assert "strike IS NOT NULL" in query
        assert cursor.captured_params[0] == ("BTC", 1000, 2000)

    def test_returns_list_of_dicts_with_expected_keys(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_result=[
            ("t1", 1700000000000, "BTC-27MAR26-90000-C", "27MAR26", 90000.0, "C",
             "buy", 1.5, 0.05, 85000.0, 65.0)
        ])

        with _patched_cursor(repo, cursor):
            result = repo.get_trades_for_delta_flow(currency="BTC", start_ts=1000, end_ts=2000)

        assert result == [
            {
                "trade_id": "t1",
                "trade_timestamp": 1700000000000,
                "instrument_name": "BTC-27MAR26-90000-C",
                "expiration": "27MAR26",
                "strike": 90000.0,
                "option_type": "C",
                "direction": "buy",
                "amount": 1.5,
                "price": 0.05,
                "index_price": 85000.0,
                "iv": 65.0,
            }
        ]

    def test_empty_result(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_result=[])

        with _patched_cursor(repo, cursor):
            result = repo.get_trades_for_delta_flow(currency="BTC", start_ts=1000, end_ts=2000)

        assert result == []


class TestSaveDeltaFlowHourly:
    def _bucket(self, expiration="27MAR26"):
        return FlowBucket(
            expiration=expiration, hiro_usd=1234.5, premium_usd=67.8, gross_delta_usd=999.9,
            net_contracts=10.0, gross_contracts=20.0, trade_count=7, buy_count=4,
            sell_count=3, skipped_count=2,
        )

    def test_upsert_on_snapshot_hour_currency_expiration(self):
        """ON CONFLICT DO UPDATE (not DO NOTHING) mirrors the
        onchain_analysis_snapshots/hourly_snapshots convention at this same
        (snapshot_hour, currency, expiration) key -- a daemon re-run must
        refresh the aggregate, not freeze at a stale first attempt."""
        repo = _make_repo()
        cursor = FakeCursor()
        hour = datetime(2026, 7, 31, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            repo.save_delta_flow_hourly(currency="BTC", snapshot_hour=hour, bucket=self._bucket())

        query = cursor.captured_queries[0]
        assert "ON CONFLICT (snapshot_hour, currency, expiration) DO UPDATE SET" in query
        assert "INSERT INTO flow_delta_hourly" in query

    def test_params_include_all_bucket_fields(self):
        repo = _make_repo()
        cursor = FakeCursor()
        hour = datetime(2026, 7, 31, 14, 0, 0)
        bucket = self._bucket()

        with _patched_cursor(repo, cursor):
            repo.save_delta_flow_hourly(currency="BTC", snapshot_hour=hour, bucket=bucket)

        params = cursor.captured_params[0]
        assert params == (
            hour, "BTC", "27MAR26",
            bucket.hiro_usd, bucket.premium_usd, bucket.gross_delta_usd,
            bucket.net_contracts, bucket.gross_contracts,
            bucket.trade_count, bucket.buy_count, bucket.sell_count, bucket.skipped_count,
        )

    def test_all_rollup_expiration_persisted_as_is(self):
        repo = _make_repo()
        cursor = FakeCursor()
        hour = datetime(2026, 7, 31, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            repo.save_delta_flow_hourly(currency="BTC", snapshot_hour=hour, bucket=self._bucket(expiration="ALL"))

        assert cursor.captured_params[0][2] == "ALL"


class TestGetDeltaFlowSummary:
    def test_query_filters_currency_and_since(self):
        repo = _make_repo()
        cursor = FakeCursor()
        since = datetime(2026, 7, 30, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            repo.get_delta_flow_summary(currency="BTC", since=since)

        assert len(cursor.captured_queries) == 1
        assert cursor.captured_params[0] == ("BTC", since)
        assert "GROUP BY expiration" in cursor.captured_queries[0]

    def test_returns_list_of_flow_bucket_shaped_dicts(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_result=[
            ("ALL", 1000.0, 50.0, 2000.0, 5.0, 10.0, 7, 4, 3, 2),
            ("27MAR26", 1000.0, 50.0, 2000.0, 5.0, 10.0, 7, 4, 3, 2),
        ])
        since = datetime(2026, 7, 30, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            result = repo.get_delta_flow_summary(currency="BTC", since=since)

        assert result == [
            {
                "expiration": "ALL", "hiro_usd": 1000.0, "premium_usd": 50.0,
                "gross_delta_usd": 2000.0, "net_contracts": 5.0, "gross_contracts": 10.0,
                "trade_count": 7, "buy_count": 4, "sell_count": 3, "skipped_count": 2,
            },
            {
                "expiration": "27MAR26", "hiro_usd": 1000.0, "premium_usd": 50.0,
                "gross_delta_usd": 2000.0, "net_contracts": 5.0, "gross_contracts": 10.0,
                "trade_count": 7, "buy_count": 4, "sell_count": 3, "skipped_count": 2,
            },
        ]

    def test_empty_result_when_no_rows_in_window(self):
        """No flow_delta_hourly rows yet (e.g. feature just shipped, or the
        daemon hasn't run in this window) -> empty list, never a fabricated
        zero-valued summary row."""
        repo = _make_repo()
        cursor = FakeCursor(fetchall_result=[])
        since = datetime(2026, 7, 30, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            result = repo.get_delta_flow_summary(currency="BTC", since=since)

        assert result == []


class TestGetDeltaFlowCoverage:
    """Review fix, Important #4 -- coverage/recency signal so the report
    can disclose a stale/lagging daemon instead of a confident-looking
    total over an incomplete window."""

    def test_query_filters_currency_all_expiration_and_since(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(0, None))
        since = datetime(2026, 7, 30, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            repo.get_delta_flow_coverage(currency="BTC", since=since)

        assert len(cursor.captured_queries) == 1
        query = cursor.captured_queries[0]
        assert "expiration = 'ALL'" in query
        assert cursor.captured_params[0] == ("BTC", since)

    def test_returns_hours_present_and_max_snapshot_hour(self):
        repo = _make_repo()
        max_hour = datetime(2026, 7, 31, 10, 0, 0)
        cursor = FakeCursor(fetchone_result=(24, max_hour))
        since = datetime(2026, 7, 30, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            result = repo.get_delta_flow_coverage(currency="BTC", since=since)

        assert result == {"hours_present": 24, "max_snapshot_hour": max_hour}

    def test_no_rows_returns_zero_hours_and_none_max(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchone_result=(0, None))
        since = datetime(2026, 7, 30, 14, 0, 0)

        with _patched_cursor(repo, cursor):
            result = repo.get_delta_flow_coverage(currency="BTC", since=since)

        assert result == {"hours_present": 0, "max_snapshot_hour": None}
