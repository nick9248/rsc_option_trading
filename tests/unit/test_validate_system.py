"""Unit tests for the rewritten SystemValidator (thin wrapper over the health registry)."""

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from scripts.validate_system import SystemValidator


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
