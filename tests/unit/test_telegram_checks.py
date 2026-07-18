"""Unit tests for Telegram health checks."""

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.telegram_checker import TelegramConfigCheck, TelegramDeliveryCheck


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


def test_config_pass_when_token_valid(monkeypatch):
    monkeypatch.setenv("OSF_TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("OSF_TELEGRAM_CHAT_ID", "12345")
    check = TelegramConfigCheck(requests_module=_FakeRequests(_FakeResponse(ok=True)))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.PASS


def test_config_fail_when_missing_credentials(monkeypatch):
    monkeypatch.delenv("OSF_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OSF_TELEGRAM_CHAT_ID", raising=False)
    check = TelegramConfigCheck(requests_module=_FakeRequests(_FakeResponse(ok=True)))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL


def test_config_fail_when_token_rejected(monkeypatch):
    monkeypatch.setenv("OSF_TELEGRAM_BOT_TOKEN", "bad-token")
    monkeypatch.setenv("OSF_TELEGRAM_CHAT_ID", "12345")
    check = TelegramConfigCheck(requests_module=_FakeRequests(_FakeResponse(ok=False, status_code=401, text="Unauthorized")))
    results = check.run(repo=None)
    assert results[0].status == CheckStatus.FAIL


class _FakeCursor:
    def __init__(self, results_by_currency):
        self._results_by_currency = results_by_currency
        self._last_currency = None

    def execute(self, query, params):
        self._last_currency = params[0]

    def fetchone(self):
        return self._results_by_currency[self._last_currency]

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakeRepo:
    def __init__(self, results_by_currency):
        self._conn = _FakeConnection(_FakeCursor(results_by_currency))

    def _get_connection(self):
        return self._conn

    def _return_connection(self, conn):
        pass


def test_delivery_passes_when_no_triggers():
    repo = _FakeRepo({"BTC": (0, 0), "ETH": (0, 0)})
    results = TelegramDeliveryCheck().run(repo)
    assert all(r.status == CheckStatus.PASS for r in results)


def test_delivery_warns_when_triggered_but_never_sent():
    repo = _FakeRepo({"BTC": (3, 0), "ETH": (0, 0)})
    results = TelegramDeliveryCheck().run(repo)
    btc_result = next(r for r in results if "BTC" in r.name)
    assert btc_result.status == CheckStatus.WARN


def test_delivery_passes_when_triggered_and_sent():
    repo = _FakeRepo({"BTC": (3, 2), "ETH": (0, 0)})
    results = TelegramDeliveryCheck().run(repo)
    btc_result = next(r for r in results if "BTC" in r.name)
    assert btc_result.status == CheckStatus.PASS
