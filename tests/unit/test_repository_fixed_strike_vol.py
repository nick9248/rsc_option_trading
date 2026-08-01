"""
Tests for DatabaseRepository.get_chain_iv_at (institutional_metrics_spec.md
section 7 / Task C8).

Follows the FakeCursor pattern established by test_repository_delta_flow.py
-- captures the SQL/params passed to ``cursor.execute`` without touching a
real DB. Live-data coverage (which of daily_oi_snapshots/snapshots is
actually reliable today) was verified separately via read-only SELECT and is
recorded in task-C8-report.md, not re-verified here.
"""

from datetime import date
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    return repo


class FakeCursor:
    """Returns a queued list of fetchall() results, one per execute() call,
    so a test can control what each of get_chain_iv_at's two possible
    queries returns independently."""

    def __init__(self, fetchall_results):
        self.captured_queries = []
        self.captured_params = []
        self._fetchall_results = list(fetchall_results)

    def execute(self, query, params):
        self.captured_queries.append(query)
        self.captured_params.append(params)

    def fetchall(self):
        return self._fetchall_results.pop(0)


def _patched_cursor(repo, cursor):
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return patch.object(repo, "_db_cursor", return_value=mock_ctx)


class TestGetChainIvAtDailyOiSnapshotsPrimary:
    def test_returns_rows_from_daily_oi_snapshots_when_present(self):
        """daily_oi_snapshots has rows for the exact date -- used directly,
        snapshots (the fallback) is never even queried."""
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[
            [
                (65000.0, "C", 34.50, 64182.0),
                (65000.0, "P", 36.00, 64182.0),
            ],
        ])

        with _patched_cursor(repo, cursor):
            result = repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        assert result["source"] == "daily_oi_snapshots"
        assert result["underlying_price"] == 64182.0
        assert result["rows"] == [
            {"strike": 65000.0, "option_type": "C", "mark_iv": 34.50},
            {"strike": 65000.0, "option_type": "P", "mark_iv": 36.00},
        ]
        # Only one query executed -- the snapshots fallback was skipped.
        assert len(cursor.captured_queries) == 1
        assert "daily_oi_snapshots" in cursor.captured_queries[0]

    def test_query_filters_currency_expiration_exact_date_and_non_null_iv(self):
        repo = _make_repo()
        # daily_oi_snapshots empty -> falls through to the snapshots
        # fallback, which is also empty (asserted separately below).
        cursor = FakeCursor(fetchall_results=[[], []])

        with _patched_cursor(repo, cursor):
            repo.get_chain_iv_at(
                currency="ETH", expiration="26MAR27", snapshot_date=date(2026, 7, 24),
            )

        query = cursor.captured_queries[0]
        assert "mark_iv IS NOT NULL" in query
        assert "snapshot_date = %s" in query
        # Two queries: daily_oi_snapshots (empty) then the snapshots fallback.
        assert cursor.captured_params[0] == ("ETH", "26MAR27", date(2026, 7, 24))

    def test_averages_underlying_price_across_rows_when_it_varies(self):
        """Defensive: the table has no CHECK constraint enforcing one
        underlying_price per snapshot_date -- average rather than
        arbitrarily picking the first row."""
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[
            [
                (65000.0, "C", 34.50, 64000.0),
                (66000.0, "C", 33.00, 64200.0),
            ],
        ])

        with _patched_cursor(repo, cursor):
            result = repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        assert result["underlying_price"] == 64100.0

    def test_null_underlying_price_rows_excluded_from_average(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[
            [
                (65000.0, "C", 34.50, None),
                (66000.0, "C", 33.00, 64200.0),
            ],
        ])

        with _patched_cursor(repo, cursor):
            result = repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        assert result["underlying_price"] == 64200.0

    def test_all_null_underlying_price_returns_none(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[
            [(65000.0, "C", 34.50, None)],
        ])

        with _patched_cursor(repo, cursor):
            result = repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        assert result["underlying_price"] is None


class TestGetChainIvAtSnapshotsFallback:
    def test_falls_back_to_snapshots_when_daily_oi_snapshots_empty(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[
            [],  # daily_oi_snapshots: nothing for this exact date
            [(65000.0, "C", 34.50, 64182.0)],  # snapshots fallback
        ])

        with _patched_cursor(repo, cursor):
            result = repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        assert result["source"] == "snapshots"
        assert result["rows"] == [{"strike": 65000.0, "option_type": "C", "mark_iv": 34.50}]
        assert len(cursor.captured_queries) == 2
        assert "FROM snapshots" in cursor.captured_queries[1]

    def test_returns_empty_when_neither_table_has_data(self):
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[[], []])

        with _patched_cursor(repo, cursor):
            result = repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        assert result == {"rows": [], "underlying_price": None, "source": None}

    def test_snapshots_fallback_anchors_to_08_00_utc(self):
        """institutional_metrics_spec.md section 7(b): the snapshots
        fallback anchors to 08:00 UTC on the requested date, matching
        Deribit settlement -- verified via the subquery's target timestamp
        parameter rather than a live query (mark_iv is 100%% NULL in
        snapshots today, see task-C8-report.md)."""
        repo = _make_repo()
        cursor = FakeCursor(fetchall_results=[[], []])

        with _patched_cursor(repo, cursor):
            repo.get_chain_iv_at(
                currency="BTC", expiration="31JUL26", snapshot_date=date(2026, 7, 30),
            )

        fallback_params = cursor.captured_params[1]
        from datetime import datetime
        assert datetime(2026, 7, 30, 8, 0, 0) in fallback_params
