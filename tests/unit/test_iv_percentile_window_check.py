"""Unit tests for IvPercentileWindowCheck."""

from datetime import datetime, timedelta, timezone

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.iv_percentile_checker import IvPercentileWindowCheck


class _FakeCursor:
    def __init__(self, candidates, reconstructed_at_by_pair):
        self._candidates = candidates
        self._reconstructed_at_by_pair = reconstructed_at_by_pair
        self._last_pair = None

    def execute(self, query, params=None):
        if "onchain_analysis_snapshots" in query:
            self._last_pair = None
        else:
            self._last_pair = params

    def fetchall(self):
        return self._candidates

    def fetchone(self):
        return (self._reconstructed_at_by_pair.get(self._last_pair),)

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakeRepo:
    def __init__(self, candidates, reconstructed_at_by_pair, window_info):
        self._conn = _FakeConnection(_FakeCursor(candidates, reconstructed_at_by_pair))
        self._window_info = window_info

    def _get_connection(self):
        return self._conn

    def _return_connection(self, conn):
        pass

    def get_iv_percentile_with_window(self, currency, expiration):
        return self._window_info


def test_fresh_expiry_passes():
    now = datetime.now(timezone.utc)
    repo = _FakeRepo(
        candidates=[("BTC", "25SEP26")],
        reconstructed_at_by_pair={("BTC", "25SEP26"): now - timedelta(hours=1)},
        window_info={"percentile": 12.0, "n_obs": 200, "window_days": 90.0, "latest_atm_iv": 0.55},
    )
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.PASS


def test_stale_expiry_warns():
    now = datetime.now(timezone.utc)
    repo = _FakeRepo(
        candidates=[("BTC", "26MAR27")],
        reconstructed_at_by_pair={("BTC", "26MAR27"): now - timedelta(days=10)},
        window_info={"percentile": 12.0, "n_obs": 40, "window_days": 112.0, "latest_atm_iv": 0.55},
    )
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.WARN
    assert "112" in results[0].message


def test_no_active_expiries_warns():
    repo = _FakeRepo(candidates=[], reconstructed_at_by_pair={}, window_info={})
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.WARN


def test_expiry_active_but_never_reconstructed_fails():
    """
    The exact incident this checker exists to catch: an expiry is active
    (has rows in onchain_analysis_snapshots) but has zero rows in
    onchain_volatility_snapshots -- reconstruction is stuck or never ran.
    Must be a FAIL, and must NOT be silently dropped from the report.
    """
    repo = _FakeRepo(
        candidates=[("BTC", "26MAR27")],
        reconstructed_at_by_pair={},
        window_info={"percentile": None, "n_obs": 0, "window_days": 0, "latest_atm_iv": None},
    )
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.FAIL
    assert "never run" in results[0].message


def test_one_candidate_failure_does_not_block_others():
    class _PartiallyFailingCursor:
        def execute(self, query, params=None):
            if "onchain_analysis_snapshots" in query:
                return
            if params == ("ETH", "26MAR27"):
                raise Exception("connection reset")

        def fetchall(self):
            return [("BTC", "25SEP26"), ("ETH", "26MAR27")]

        def fetchone(self):
            return (datetime.now(timezone.utc) - timedelta(hours=1),)

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

        def get_iv_percentile_with_window(self, currency, expiration):
            return {"percentile": 12.0, "n_obs": 100, "window_days": 50.0, "latest_atm_iv": 0.5}

    cursor = _PartiallyFailingCursor()
    conn = _FakeConnWithRollback(cursor)
    repo = _FakeRepoWithRollback(conn)

    results = IvPercentileWindowCheck().run(repo)
    by_name = {r.name: r for r in results}
    assert by_name["IV-percentile window (BTC 25SEP26)"].status == CheckStatus.PASS
    assert by_name["IV-percentile window (ETH 26MAR27)"].status == CheckStatus.FAIL
    assert conn.rolled_back is True
