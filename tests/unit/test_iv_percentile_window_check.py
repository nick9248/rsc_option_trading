"""Unit tests for IvPercentileWindowCheck."""

from datetime import datetime, timedelta, timezone

from coding.core.health.models import CheckStatus
from coding.service.health.checkers.iv_percentile_checker import IvPercentileWindowCheck


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query, params):
        pass

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakeRepo:
    def __init__(self, rows, window_info):
        self._conn = _FakeConnection(_FakeCursor(rows))
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
        rows=[("BTC", "25SEP26", now - timedelta(hours=1))],
        window_info={"percentile": 12.0, "n_obs": 200, "window_days": 90.0, "latest_atm_iv": 0.55},
    )
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.PASS


def test_stale_expiry_warns():
    now = datetime.now(timezone.utc)
    repo = _FakeRepo(
        rows=[("BTC", "26MAR27", now - timedelta(days=10))],
        window_info={"percentile": 12.0, "n_obs": 40, "window_days": 112.0, "latest_atm_iv": 0.55},
    )
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.WARN
    assert "112" in results[0].message


def test_no_active_expiries_warns():
    repo = _FakeRepo(rows=[], window_info={})
    results = IvPercentileWindowCheck().run(repo)
    assert results[0].status == CheckStatus.WARN
