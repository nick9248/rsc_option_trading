"""Unit tests for the rewritten SystemValidator (thin wrapper over the health registry)."""

import pytest

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from scripts.validate_system import SystemValidator, main


class _FakeRepo:
    pass


def test_validate_all_returns_registry_grouping(monkeypatch):
    def _fake_run_checks(environment, repo):
        assert environment == CheckEnvironment.LOCAL
        return {"API Connectivity": [CheckResult(name="Deribit API", status=CheckStatus.PASS, message="ok")]}

    monkeypatch.setattr("scripts.validate_system.run_checks", _fake_run_checks)
    validator = SystemValidator(repository=_FakeRepo())
    grouped = validator.validate_all()

    assert "API Connectivity" in grouped
    assert grouped["API Connectivity"][0].status == CheckStatus.PASS


def test_validate_all_handles_empty_grouping(monkeypatch):
    monkeypatch.setattr("scripts.validate_system.run_checks", lambda environment, repo: {})
    validator = SystemValidator(repository=_FakeRepo())
    grouped = validator.validate_all()
    assert grouped == {}


def test_main_exits_1_when_any_check_fails(monkeypatch):
    monkeypatch.setattr(
        "scripts.validate_system.run_checks",
        lambda environment, repo: {"API Connectivity": [CheckResult(name="x", status=CheckStatus.FAIL, message="down")]},
    )
    monkeypatch.setattr("scripts.validate_system.DatabaseRepository", lambda: _FakeRepo())
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_main_does_not_exit_when_all_pass(monkeypatch):
    monkeypatch.setattr(
        "scripts.validate_system.run_checks",
        lambda environment, repo: {"API Connectivity": [CheckResult(name="x", status=CheckStatus.PASS, message="ok")]},
    )
    monkeypatch.setattr("scripts.validate_system.DatabaseRepository", lambda: _FakeRepo())
    main()
