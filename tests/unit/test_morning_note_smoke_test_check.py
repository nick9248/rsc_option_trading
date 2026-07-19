"""Unit tests for MorningNoteSmokeTestCheck."""

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
