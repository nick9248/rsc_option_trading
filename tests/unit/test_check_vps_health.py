"""Unit tests for the rewritten VPS health check entry point."""

import json

from coding.core.health.models import CheckResult, CheckStatus
import scripts.check_vps_health as check_vps_health


class _FakeCursor:
    def execute(self, query, params=None):
        pass

    def fetchone(self):
        return (42,)

    def close(self):
        pass


class _FakeConnection:
    def cursor(self):
        return _FakeCursor()


class _FakeRepo:
    def _get_connection(self):
        return _FakeConnection()

    def _return_connection(self, conn):
        pass


def test_run_writes_tables_and_problems(tmp_path, monkeypatch):
    monkeypatch.setattr(check_vps_health, "DatabaseRepository", lambda: _FakeRepo())
    monkeypatch.setattr(
        check_vps_health, "run_checks",
        lambda environment, repo: {
            "API Connectivity": [CheckResult(name="Deribit API", status=CheckStatus.PASS, message="ok")],
            "Daemon Service": [CheckResult(name="Daemon service", status=CheckStatus.FAIL, message="not running")],
        },
    )

    exit_code = check_vps_health.run(log_dir=tmp_path)
    assert exit_code == 1

    data = json.loads((tmp_path / "vps_health.json").read_text())
    assert data["passed"] == 1
    assert data["total"] == 2
    assert data["problems"] == ["not running"]
    assert data["tables"]["historical_trades"]["rows"] == 42


def test_run_returns_zero_when_all_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(check_vps_health, "DatabaseRepository", lambda: _FakeRepo())
    monkeypatch.setattr(
        check_vps_health, "run_checks",
        lambda environment, repo: {
            "API Connectivity": [CheckResult(name="Deribit API", status=CheckStatus.PASS, message="ok")],
        },
    )
    assert check_vps_health.run(log_dir=tmp_path) == 0


def test_run_still_writes_json_when_table_snapshot_fails(tmp_path, monkeypatch):
    class _RaisingRepo:
        def _get_connection(self):
            raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(check_vps_health, "DatabaseRepository", lambda: _RaisingRepo())
    monkeypatch.setattr(
        check_vps_health, "run_checks",
        lambda environment, repo: {
            "API Connectivity": [CheckResult(name="Deribit API", status=CheckStatus.PASS, message="ok")],
        },
    )

    exit_code = check_vps_health.run(log_dir=tmp_path)
    assert exit_code == 1

    data = json.loads((tmp_path / "vps_health.json").read_text())
    assert data["tables"] == {}
    assert any("Table row-count snapshot failed" in p for p in data["problems"])


def test_run_writes_json_when_repo_construction_fails(tmp_path, monkeypatch):
    def _raise():
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr(check_vps_health, "DatabaseRepository", _raise)

    exit_code = check_vps_health.run(log_dir=tmp_path)
    assert exit_code == 1

    data = json.loads((tmp_path / "vps_health.json").read_text())
    assert data["tables"] == {}
    assert data["total"] == 0
    assert any("Health check run failed entirely" in p for p in data["problems"])


def test_table_snapshot_isolates_one_table_failure():
    class _FailingThenOkCursor:
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

    class _FakeRepoForSnapshot:
        def __init__(self, conn):
            self._conn = conn

        def _get_connection(self):
            return self._conn

        def _return_connection(self, conn):
            pass

    cursor = _FailingThenOkCursor()
    conn = _FakeConnWithRollback(cursor)
    repo = _FakeRepoForSnapshot(conn)

    snapshot = check_vps_health._table_snapshot(repo)
    assert snapshot["onchain_volatility_snapshots"]["rows"] is None
    assert "error" in snapshot["onchain_volatility_snapshots"]
    assert snapshot["historical_trades"]["rows"] == 100
    assert conn.rolled_back is True
