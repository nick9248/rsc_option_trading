from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck
from coding.service.health.registry import run_checks


class _PassingCheck(HealthCheck):
    category = "Passing"
    environment = CheckEnvironment.BOTH

    def run(self, repo):
        return [CheckResult(name="ok", status=CheckStatus.PASS, message="fine")]


class _VpsOnlyCheck(HealthCheck):
    category = "VpsOnly"
    environment = CheckEnvironment.VPS

    def run(self, repo):
        return [CheckResult(name="vps thing", status=CheckStatus.PASS, message="vps fine")]


class _RaisingCheck(HealthCheck):
    category = "Raising"
    environment = CheckEnvironment.BOTH

    def run(self, repo):
        raise RuntimeError("boom")


def test_run_checks_groups_by_category():
    grouped = run_checks(CheckEnvironment.LOCAL, repo=None, checkers=[_PassingCheck()])
    assert "Passing" in grouped
    assert grouped["Passing"][0].status == CheckStatus.PASS


def test_run_checks_filters_by_environment():
    grouped = run_checks(CheckEnvironment.LOCAL, repo=None, checkers=[_VpsOnlyCheck()])
    assert grouped == {}

    grouped_vps = run_checks(CheckEnvironment.VPS, repo=None, checkers=[_VpsOnlyCheck()])
    assert "VpsOnly" in grouped_vps


def test_run_checks_both_environment_runs_everywhere():
    checker = _PassingCheck()
    assert "Passing" in run_checks(CheckEnvironment.LOCAL, repo=None, checkers=[checker])
    assert "Passing" in run_checks(CheckEnvironment.VPS, repo=None, checkers=[checker])


def test_run_checks_contains_exceptions_as_fail_result():
    grouped = run_checks(CheckEnvironment.LOCAL, repo=None, checkers=[_RaisingCheck()])
    results = grouped["Raising"]
    assert len(results) == 1
    assert results[0].status == CheckStatus.FAIL
    assert "boom" in results[0].message
