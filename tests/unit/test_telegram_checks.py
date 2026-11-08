"""Unit tests for the Telegram config health check."""

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.telegram_checker import TelegramConfigCheck


class _FakeResponse:
    def __init__(self, ok, status_code=200, text=""):
        self.ok = ok
        self.status_code = status_code
        self.text = text


class _FakeRequests:
    def __init__(self, response):
        self._response = response

    def get(self, url, timeout):
        return self._response


def test_config_pass_when_token_valid(monkeypatch, tmp_path):
    monkeypatch.setenv("OSF_TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("OSF_TELEGRAM_CHAT_ID", "12345")
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    check = TelegramConfigCheck(requests_module=_FakeRequests(_FakeResponse(ok=True)), env_path=empty_env)
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.PASS


def test_config_fail_when_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("OSF_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OSF_TELEGRAM_CHAT_ID", raising=False)
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    check = TelegramConfigCheck(requests_module=_FakeRequests(_FakeResponse(ok=True)), env_path=empty_env)
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL


def test_config_fail_when_token_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("OSF_TELEGRAM_BOT_TOKEN", "bad-token")
    monkeypatch.setenv("OSF_TELEGRAM_CHAT_ID", "12345")
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    check = TelegramConfigCheck(requests_module=_FakeRequests(_FakeResponse(ok=False, status_code=401, text="Unauthorized")), env_path=empty_env)
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL


class _FailingRequests:
    def get(self, url, timeout):
        raise ConnectionError(f"Max retries exceeded with url: {url}")


def test_unreachable_message_hides_the_token(monkeypatch, tmp_path):
    monkeypatch.setenv("OSF_TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("OSF_TELEGRAM_CHAT_ID", "12345")
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    check = TelegramConfigCheck(requests_module=_FailingRequests(), env_path=empty_env)
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL
    assert "secret-token" not in results[0].message
    assert "/bot<bot token>/getMe" in results[0].message
