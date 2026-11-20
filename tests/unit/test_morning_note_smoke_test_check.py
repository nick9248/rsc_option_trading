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


def test_default_synthesis_runner_analyzes_then_synthesizes_with_the_injected_repo():
    """
    The health check still proves the synthesis engine runs: analyze with the framework-injected repository (not a fresh
    one), then ``MorningNoteService.generate`` on the typed result. Nothing is saved.
    """
    from coding.service.health.checkers import morning_note_checker as checker_module

    injected_repo = MagicMock(name="injected_repo")
    fake_result = MagicMock(name="result")

    with patch.object(checker_module, "DeribitApiService"),             patch.object(checker_module, "OnChainAnalysisService") as mock_service_cls,             patch.object(checker_module, "MorningNoteService") as mock_morning_cls:
        mock_service_cls.return_value.fetch_and_analyze.return_value = ("REPORT", fake_result)
        mock_morning_cls.return_value.generate.return_value = "BTC synthesis text"

        text = checker_module.MorningNoteSmokeTestCheck._default_synthesis_runner("BTC", injected_repo)

    assert text == "BTC synthesis text"
    _, kwargs = mock_service_cls.call_args
    assert kwargs["repository"] is injected_repo
    mock_morning_cls.return_value.generate.assert_called_once_with(fake_result)
