"""Unit tests for MorningNoteSmokeTestCheck."""

from unittest.mock import MagicMock, patch

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.morning_note_checker import MorningNoteSmokeTestCheck


def test_pass_when_synthesis_returns_text():
    check = MorningNoteSmokeTestCheck(synthesis_runner=lambda currency, repo: f"{currency} synthesis text")
    results = check.run(repo=None)
    assert all(r.status == CheckStatus.PASS for r in results)


def test_fail_when_synthesis_empty():
    check = MorningNoteSmokeTestCheck(synthesis_runner=lambda currency, repo: "   ")
    results = check.run(repo=None)
    assert all(r.status == CheckStatus.FAIL for r in results)


def test_fail_when_synthesis_raises():
    def _raise(currency, repo):
        raise RuntimeError("no data")
    check = MorningNoteSmokeTestCheck(synthesis_runner=_raise)
    results = check.run(repo=None)
    assert all(r.status == CheckStatus.FAIL for r in results)
    assert "no data" in results[0].message


def test_default_synthesis_runner_uses_one_call_workflow_service_with_injected_repo():
    """
    T9 (refactor_design_spec.md): _default_synthesis_runner delegates to
    OnChainWorkflowService.run(currency) -> .synthesis_text, and passes
    through the framework-injected repo (not a fresh one) -- this check
    must keep querying the exact repository instance the health-check
    framework gave it.
    """
    from coding.service.health.checkers import morning_note_checker as checker_module

    fake_output = MagicMock()
    fake_output.synthesis_text = "BTC synthesis text"
    injected_repo = MagicMock(name="injected_repo")

    with patch.object(checker_module, "OnChainWorkflowService") as mock_workflow_cls:
        mock_workflow_cls.return_value.run.return_value = fake_output

        result = checker_module.MorningNoteSmokeTestCheck._default_synthesis_runner(
            "BTC", injected_repo
        )

    assert result == "BTC synthesis text"
    mock_workflow_cls.assert_called_once_with("BTC", repository=injected_repo)
    mock_workflow_cls.return_value.run.assert_called_once_with()
