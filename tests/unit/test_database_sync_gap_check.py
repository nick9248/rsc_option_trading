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


def test_vps_row_count_unavailable_warns_without_crashing(tmp_path):
    """
    check_vps_health.py's _table_snapshot can now report {"rows": None,
    "error": ...} for a table it failed to count -- this must degrade to
    a WARN, not crash on `None - int` arithmetic.
    """
    health_json = tmp_path / "vps_health.json"
    health_json.write_text(json.dumps({
        "tables": {"onchain_volatility_snapshots": {"rows": None, "error": "relation does not exist"}},
    }))
    check = DatabaseSyncGapCheck(health_json_path=health_json)
    results = check.run(repo=_FakeRepo(local_rows=50))
    assert results[0].status == CheckStatus.WARN
    assert "unavailable" in results[0].message


def test_one_table_failure_does_not_block_others(tmp_path):
    class _PartiallyFailingCursor:
        def execute(self, query, params=None):
            if "onchain_volatility_snapshots" in query:
                raise Exception("relation does not exist")

        def fetchone(self):
            return (100,)

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

    class _FakeRepoWithRollback:
        def __init__(self, conn):
            self._conn = conn

        def _get_connection(self):
            return self._conn

        def _return_connection(self, conn):
            pass

    health_json = tmp_path / "vps_health.json"
    health_json.write_text(json.dumps({
        "tables": {
            "hourly_snapshots": {"rows": 100},
            "onchain_volatility_snapshots": {"rows": 200},
        },
    }))
    cursor = _PartiallyFailingCursor()
    conn = _FakeConnWithRollback(cursor)
    repo = _FakeRepoWithRollback(conn)

    check = DatabaseSyncGapCheck(health_json_path=health_json)
    results = check.run(repo)

    by_name = {r.name: r for r in results}
    assert by_name["hourly_snapshots sync"].status == CheckStatus.PASS
    assert by_name["onchain_volatility_snapshots sync"].status == CheckStatus.FAIL
    assert conn.rolled_back is True
