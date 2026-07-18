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
