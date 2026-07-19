"""Unit tests for ScannerActivityCheck."""

from datetime import datetime, timedelta, timezone

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.scanner_checker import ScannerActivityCheck


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


def test_active_scanner_passes():
    now = datetime.now(timezone.utc)
    repo = _FakeRepo({"BTC": (5, now - timedelta(minutes=10)), "ETH": (5, now - timedelta(minutes=10))})
    results = ScannerActivityCheck().run(repo)
    assert all(r.status == CheckStatus.PASS for r in results)


def test_silent_currency_fails():
    now = datetime.now(timezone.utc)
    repo = _FakeRepo({"BTC": (0, None), "ETH": (5, now - timedelta(minutes=10))})
    results = ScannerActivityCheck().run(repo)
    btc_result = next(r for r in results if "BTC" in r.name)
    assert btc_result.status == CheckStatus.FAIL
