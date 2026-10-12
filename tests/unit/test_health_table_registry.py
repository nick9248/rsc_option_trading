"""The health-check table registry must cover every table the sync copies."""
from coding.service.health.table_registry import TABLE_SPECS
from scripts.sync_from_vps import SYNC_TABLES


def test_every_synced_table_is_watched_by_the_health_checks():
    watched = {spec.table for spec in TABLE_SPECS}
    unwatched = [entry["name"] for entry in SYNC_TABLES if entry["name"] not in watched]
    assert unwatched == [], f"add these tables to coding/service/health/table_registry.py: {unwatched}"


def test_every_watched_table_is_synced():
    synced = {entry["name"] for entry in SYNC_TABLES}
    assert [spec.table for spec in TABLE_SPECS if spec.table not in synced] == []


def test_each_table_is_listed_once():
    names = [spec.table for spec in TABLE_SPECS]
    assert len(names) == len(set(names))


def test_freshness_limits_are_consistent():
    for spec in TABLE_SPECS:
        assert (spec.warn_hours is None) == (spec.fail_hours is None), spec.table
        if spec.warn_hours is not None:
            assert 0 < spec.warn_hours < spec.fail_hours, spec.table


def test_dvol_history_is_judged_in_hours_not_days():
    dvol = next(spec for spec in TABLE_SPECS if spec.table == "dvol_history")
    assert dvol.fail_hours <= 6  # it used to be 7 / 30 days


def test_flow_delta_hourly_does_not_warn_at_its_normal_two_hour_lag_but_does_when_a_cycle_is_missed():
    """It stores the just-closed hour one cycle later, so its newest row is ~2 h old at the top of
    the hour (the first real health run on the server warned at exactly 2.0 h)."""
    from datetime import datetime, timedelta, timezone

    from coding.core.health.models import CheckStatus
    from coding.service.health.checkers.database_local_checker import DatabaseLocalFreshnessCheck

    spec = next(spec for spec in TABLE_SPECS if spec.table == "flow_delta_hourly")

    class _Cursor:
        def __init__(self, age_hours):
            self._row = (datetime.now(timezone.utc) - timedelta(hours=age_hours), 10)

        def execute(self, query, params=None):
            pass

        def fetchone(self):
            return self._row

    def status_at(age_hours):
        return DatabaseLocalFreshnessCheck()._freshness_result(
            _Cursor(age_hours), spec.table, spec.time_column, spec.time_kind,
            spec.warn_hours, spec.fail_hours, spec.optional).status

    assert status_at(2.2) == CheckStatus.PASS      # normal, just before the :09 cycle
    assert status_at(2.7) == CheckStatus.PASS      # one 30-minute cycle late
    assert status_at(3.5) == CheckStatus.WARN
    assert status_at(7.0) == CheckStatus.FAIL
