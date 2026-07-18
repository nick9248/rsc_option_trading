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
