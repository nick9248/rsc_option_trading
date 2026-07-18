"""Unit tests for DatabaseVpsGapCheck's per-table gap analysis."""

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.database_vps_gap_checker import DatabaseVpsGapCheck


class _FakeCursor:
    def __init__(self, fetchall_result):
        self._fetchall_result = fetchall_result

    def execute(self, query, params=None):
        pass

    def fetchall(self):
        return self._fetchall_result


def test_full_coverage_passes():
    cursor = _FakeCursor(fetchall_result=[("BTC", 48), ("ETH", 48)])
    results = DatabaseVpsGapCheck()._gap_results(cursor, "onchain_analysis_snapshots", "snapshot_hour", None)
    assert all(r.status == CheckStatus.PASS for r in results)


def test_partial_coverage_warns():
    cursor = _FakeCursor(fetchall_result=[("BTC", 30)])
    results = DatabaseVpsGapCheck()._gap_results(cursor, "onchain_analysis_snapshots", "snapshot_hour", None)
    assert results[0].status == CheckStatus.WARN


def test_low_coverage_fails():
    cursor = _FakeCursor(fetchall_result=[("BTC", 10)])
    results = DatabaseVpsGapCheck()._gap_results(cursor, "onchain_analysis_snapshots", "snapshot_hour", None)
    assert results[0].status == CheckStatus.FAIL


def test_no_rows_fails():
    cursor = _FakeCursor(fetchall_result=[])
    results = DatabaseVpsGapCheck()._gap_results(cursor, "onchain_analysis_snapshots", "snapshot_hour", None)
    assert len(results) == 1
    assert results[0].status == CheckStatus.FAIL


def test_run_isolates_one_table_failure_and_rolls_back():
    class _MultiTableCursor:
        def execute(self, query, params=None):
            if "straddle_scan_history" in query:
                raise Exception("relation does not exist")
            self._last_query = query

        def fetchall(self):
            if "onchain_analysis_snapshots" in self._last_query:
                return [("BTC", 48)]
            if "hourly_snapshots" in self._last_query:
                return [("BTC", 30)]
            return []

        def close(self):
            pass

    class _FakeConnWithRollback:
        def __init__(self, cursor):
            self._cursor = cursor
            self.rolled_back = False

        def cursor(self):
            return self._cursor

        def rollback(self):
            self.rolled_back = True

    class _FakeRepoForRun:
        def __init__(self, conn):
            self._conn = conn

        def _get_connection(self):
            return self._conn

        def _return_connection(self, conn):
            pass

    cursor = _MultiTableCursor()
    conn = _FakeConnWithRollback(cursor)
    repo = _FakeRepoForRun(conn)

    results = DatabaseVpsGapCheck().run(repo)

    by_name = {r.name: r for r in results}
    assert "straddle_scan_history continuity" in by_name
    assert by_name["straddle_scan_history continuity"].status == CheckStatus.FAIL
    assert "continuity check failed" in by_name["straddle_scan_history continuity"].message

    assert by_name["onchain_analysis_snapshots continuity (BTC)"].status == CheckStatus.PASS
    assert by_name["hourly_snapshots continuity (BTC)"].status == CheckStatus.WARN

    assert conn.rolled_back is True
