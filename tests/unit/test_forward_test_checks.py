"""Unit tests for forward-test harness health checks."""

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.forward_test_checker import (
    OnChainForwardTestCheck, StraddleForwardTestCheck,
)


class _FakeOnChainHarness:
    def __init__(self, repository):
        pass

    def get_track_record(self, currency):
        if currency == "BTC":
            return {"n_total": 60, "n_signals": 50, "hit_rate": 0.58, "information_ratio": 0.35, "criteria_met": True}
        return {"n_total": 0, "n_signals": 0, "hit_rate": None, "information_ratio": None, "criteria_met": False}


def test_onchain_warns_when_no_predictions():
    check = OnChainForwardTestCheck(harness_factory=_FakeOnChainHarness)
    results = check.run(repo=None)
    eth_result = next(r for r in results if "ETH" in r.name)
    assert eth_result.status == CheckStatus.WARN


def test_onchain_passes_when_recording():
    check = OnChainForwardTestCheck(harness_factory=_FakeOnChainHarness)
    results = check.run(repo=None)
    btc_result = next(r for r in results if "BTC" in r.name)
    assert btc_result.status == CheckStatus.PASS


class _FakePartiallyFailingOnChainHarness:
    def __init__(self, repository):
        pass

    def get_track_record(self, currency):
        if currency == "BTC":
            raise RuntimeError("db connection lost")
        return {"n_total": 60, "n_signals": 50, "hit_rate": 0.58, "information_ratio": 0.35, "criteria_met": True}


def test_onchain_one_currency_failure_does_not_block_other():
    check = OnChainForwardTestCheck(harness_factory=_FakePartiallyFailingOnChainHarness)
    results = check.run(repo=None)
    btc_result = next(r for r in results if "BTC" in r.name)
    eth_result = next(r for r in results if "ETH" in r.name)
    assert btc_result.status == CheckStatus.FAIL
    assert eth_result.status == CheckStatus.PASS


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


def test_straddle_warns_when_no_history():
    repo = _FakeRepo({"BTC": (0, 0, None), "ETH": (0, 0, None)})
    results = StraddleForwardTestCheck().run(repo)
    assert all(r.status == CheckStatus.WARN for r in results)


def test_straddle_warns_when_none_settled():
    repo = _FakeRepo({"BTC": (10, 0, None), "ETH": (0, 0, None)})
    results = StraddleForwardTestCheck().run(repo)
    btc_result = next(r for r in results if "BTC" in r.name)
    assert btc_result.status == CheckStatus.WARN


def test_straddle_passes_when_settled():
    repo = _FakeRepo({"BTC": (10, 5, 12.5), "ETH": (0, 0, None)})
    results = StraddleForwardTestCheck().run(repo)
    btc_result = next(r for r in results if "BTC" in r.name)
    assert btc_result.status == CheckStatus.PASS
    assert "12.5" in btc_result.message


def test_straddle_one_currency_failure_does_not_block_other():
    class _PartiallyFailingCursor:
        def execute(self, query, params):
            if params[0] == "BTC":
                raise Exception("relation does not exist")

        def fetchone(self):
            return (0, 0, None)

        def close(self):
            pass

    class _FakeConnWithRollback:
        def __init__(self, cursor):
            self._cursor = cursor
            self.rolled_back = False

        def cursor(self):
            return self._cursor

        def rollback(self):
            self.rolled_back = True

    class _FakeRepoWithRollback:
        def __init__(self, conn):
            self._conn = conn

        def _get_connection(self):
            return self._conn

        def _return_connection(self, conn):
            pass

    cursor = _PartiallyFailingCursor()
    conn = _FakeConnWithRollback(cursor)
    repo = _FakeRepoWithRollback(conn)

    results = StraddleForwardTestCheck().run(repo)
    btc_result = next(r for r in results if "BTC" in r.name)
    eth_result = next(r for r in results if "ETH" in r.name)
    assert btc_result.status == CheckStatus.FAIL
    assert eth_result.status == CheckStatus.WARN
    assert conn.rolled_back is True
