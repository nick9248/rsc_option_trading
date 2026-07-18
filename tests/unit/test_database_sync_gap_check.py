"""Unit tests for DatabaseSyncGapCheck."""

import json

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.database_sync_gap_checker import DatabaseSyncGapCheck


class _FakeCursor:
    def __init__(self, local_rows):
        self._local_rows = local_rows

    def execute(self, query, params=None):
        pass

    def fetchone(self):
        return (self._local_rows,)

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakeRepo:
    def __init__(self, local_rows):
        self._conn = _FakeConnection(_FakeCursor(local_rows))

    def _get_connection(self):
        return self._conn

    def _return_connection(self, conn):
        pass


def test_no_health_json_warns(tmp_path):
    check = DatabaseSyncGapCheck(health_json_path=tmp_path / "missing.json")
    results = check.run(repo=_FakeRepo(local_rows=0))
    assert results[0].status == CheckStatus.WARN
    assert "never synced" in results[0].message


def test_local_caught_up_passes(tmp_path):
    health_json = tmp_path / "vps_health.json"
    health_json.write_text(json.dumps({"tables": {"hourly_snapshots": {"rows": 100}}}))
    check = DatabaseSyncGapCheck(health_json_path=health_json)
    results = check.run(repo=_FakeRepo(local_rows=100))
    assert results[0].status == CheckStatus.PASS


def test_local_behind_warns(tmp_path):
    health_json = tmp_path / "vps_health.json"
    health_json.write_text(json.dumps({"tables": {"hourly_snapshots": {"rows": 100}}}))
    check = DatabaseSyncGapCheck(health_json_path=health_json)
    results = check.run(repo=_FakeRepo(local_rows=50))
    assert results[0].status == CheckStatus.WARN
    assert "Sync from VPS" in results[0].message
