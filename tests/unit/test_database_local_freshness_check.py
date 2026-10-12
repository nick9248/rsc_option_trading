"""Unit tests for DatabaseLocalFreshnessCheck's freshness/completeness helpers."""

from datetime import date, datetime, timedelta, timezone

from coding.core.health.models import CheckEnvironment, CheckStatus
from coding.service.health.checkers.database_local_checker import DatabaseLocalFreshnessCheck


class _FakeCursor:
    def __init__(self, fetchone_result=None):
        self._fetchone_result = fetchone_result

    def execute(self, query, params=None):
        pass

    def fetchone(self):
        return self._fetchone_result


def test_freshness_pass_when_recent():
    cursor = _FakeCursor(fetchone_result=(datetime.now(timezone.utc) - timedelta(minutes=5), 10))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.PASS


def test_freshness_warn_when_stale():
    cursor = _FakeCursor(fetchone_result=(datetime.now(timezone.utc) - timedelta(hours=5), 10))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.WARN


def test_freshness_fail_when_very_stale():
    cursor = _FakeCursor(fetchone_result=(datetime.now(timezone.utc) - timedelta(hours=48), 10))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0)
    assert result.status == CheckStatus.FAIL


def test_freshness_fail_when_no_data():
    cursor = _FakeCursor(fetchone_result=(None, 0))
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
    five_minutes_ago_ms = int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp() * 1000)
    cursor = _FakeCursor(fetchone_result=(five_minutes_ago_ms, 10))
    check = DatabaseLocalFreshnessCheck()
    result = check._freshness_result(cursor, "historical_trades", "trade_timestamp", "ms_epoch", 2.0, 24.0)
    assert result.status == CheckStatus.PASS


def test_environment_is_both_so_it_runs_under_local_and_vps():
    """Task E3 fix: previously tagged CheckEnvironment.LOCAL, which meant
    this check (and its dvol_history staleness WARN/FAIL) only ever ran
    when a human manually ran scripts/validate_system.py -- the only
    automated/cron health run, scripts/check_vps_health.py, only runs
    CheckEnvironment.VPS-tagged checks and never saw it. Tagging BOTH makes
    it run under both entry points via registry.run_checks' existing
    ``checker.environment == CheckEnvironment.BOTH`` passthrough."""
    assert DatabaseLocalFreshnessCheck.environment == CheckEnvironment.BOTH


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


def test_naive_database_times_are_read_as_utc_whatever_the_host_clock():
    just_now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    cursor = _FakeCursor(fetchone_result=(just_now_utc_naive, 10))
    result = DatabaseLocalFreshnessCheck()._freshness_result(cursor, "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 6.0)
    assert result.status == CheckStatus.PASS


def test_timestamptz_values_are_compared_as_the_instant_they_are():
    four_hours_ago = datetime.now(timezone(timedelta(hours=2))) - timedelta(hours=4)  # +02:00, like the local server
    cursor = _FakeCursor(fetchone_result=(four_hours_ago, 10))
    result = DatabaseLocalFreshnessCheck()._freshness_result(cursor, "dvol_history", "timestamp", "datetime", 3.0, 6.0)
    assert result.status == CheckStatus.WARN


def test_date_column_is_read_as_midnight_utc():
    today = datetime.now(timezone.utc).date()
    cursor = _FakeCursor(fetchone_result=(today, 10))
    result = DatabaseLocalFreshnessCheck()._freshness_result(cursor, "daily_oi_snapshots", "snapshot_date", "date", 36.0, 72.0)
    assert result.status == CheckStatus.PASS


def test_event_table_reports_its_row_count_without_a_freshness_limit():
    long_ago = datetime(2024, 12, 6, tzinfo=timezone.utc)
    cursor = _FakeCursor(fetchone_result=(long_ago, 57))
    result = DatabaseLocalFreshnessCheck()._freshness_result(cursor, "user_trades", "trade_time", "datetime", None, None, True)
    assert result.status == CheckStatus.PASS
    assert "57 rows" in result.message


def test_empty_optional_table_is_a_warning_and_an_empty_required_table_a_failure():
    check = DatabaseLocalFreshnessCheck()
    optional = check._freshness_result(_FakeCursor((None, 0)), "account_snapshots", "snapshot_hour", "datetime", 2.0, 6.0, True)
    required = check._freshness_result(_FakeCursor((None, 0)), "hourly_snapshots", "snapshot_hour", "datetime", 2.0, 6.0)
    assert optional.status == CheckStatus.WARN
    assert required.status == CheckStatus.FAIL


def test_event_table_with_no_rows_is_fine():
    result = DatabaseLocalFreshnessCheck()._freshness_result(
        _FakeCursor((None, 0)), "position_snapshots", "snapshot_hour", "datetime", None, None, True)
    assert result.status == CheckStatus.PASS
