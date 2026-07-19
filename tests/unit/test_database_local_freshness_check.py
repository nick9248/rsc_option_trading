"""Unit tests for DatabaseLocalFreshnessCheck's freshness/completeness helpers."""

from datetime import datetime, timedelta

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.database_local_checker import DatabaseLocalFreshnessCheck


class _FakeCursor:
    def __init__(self, fetchone_result=None):
        self._fetchone_result = fetchone_result

    def execute(self, query, params=None):
        pass

    def fetchone(self):
        return self._fetchone_result


def test_freshness_pass_when_recent():
    cursor = _FakeCursor(fetchone_result=(datetime.now() - timedelta(minutes=5),))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.PASS


def test_freshness_warn_when_stale():
    cursor = _FakeCursor(fetchone_result=(datetime.now() - timedelta(hours=5),))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.WARN


def test_freshness_fail_when_very_stale():
    cursor = _FakeCursor(fetchone_result=(datetime.now() - timedelta(hours=48),))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.FAIL


def test_freshness_fail_when_no_data():
    cursor = _FakeCursor(fetchone_result=(None,))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.FAIL
    assert "NO DATA" in result.message


def test_completeness_pass_when_fully_populated():
    cursor = _FakeCursor(fetchone_result=(100, 100, 100))
    check = DatabaseLocalFreshnessCheck()
    result = check._completeness_result(cursor, "historical_trades", "trade_timestamp", ["direction", "iv"])
    assert result.status == CheckStatus.PASS


def test_completeness_warn_when_below_threshold():
    cursor = _FakeCursor(fetchone_result=(100, 100, 50))
    check = DatabaseLocalFreshnessCheck()
    result = check._completeness_result(cursor, "historical_trades", "trade_timestamp", ["direction", "iv"])
    assert result.status == CheckStatus.WARN
    assert "iv" in result.message


def test_completeness_fail_when_no_rows():
    cursor = _FakeCursor(fetchone_result=(0, 0, 0))
    check = DatabaseLocalFreshnessCheck()
    result = check._completeness_result(cursor, "historical_trades", "trade_timestamp", ["direction", "iv"])
    assert result.status == CheckStatus.FAIL


def test_freshness_handles_ms_epoch_conversion():
    five_minutes_ago_ms = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)
    cursor = _FakeCursor(fetchone_result=(five_minutes_ago_ms,))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "historical_trades", "trade_timestamp", "ms_epoch", 2.0, 24.0)
    assert result.status == CheckStatus.PASS


def test_safe_table_checks_isolates_failure_and_rolls_back():
    class _FailingCursor:
        def execute(self, query, params=None):
            raise Exception("relation does not exist")

    class _FakeConnWithRollback:
        def __init__(self):
            self.rolled_back = False

        def rollback(self):
            self.rolled_back = True

    conn = _FakeConnWithRollback()
    cursor = _FailingCursor()
    check = DatabaseLocalFreshnessCheck()
    results = check._safe_table_checks(conn, cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0, [])
    assert results[0].status == CheckStatus.FAIL
    assert "check failed" in results[0].message
    assert conn.rolled_back is True
