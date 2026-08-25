"""
Tests for RegimeGateService. gate_pass is the ORIGINAL theoretically-
motivated definition (net_gex>0 AND rv_ratio<1) -- do not change this
definition based on the 2026-07-20 16-sample backtest, which found it
backwards; see docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import coding.service.scanner.regime_gate_service as regime_gate_service_module
from coding.service.scanner.regime_gate_service import RegimeGateService


class FakeCursor:
    def __init__(self, gex_hour_row, gex_sum_row):
        self._gex_hour_row = gex_hour_row
        self._gex_sum_row = gex_sum_row
        self._last_query = ""

    def execute(self, query, params=None):
        self._last_query = query

    def fetchone(self):
        if "snapshot_hour FROM" in self._last_query:
            return self._gex_hour_row
        return self._gex_sum_row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeRepo:
    def __init__(self, closes_by_offset_days: Dict[int, float], gex_hour_row=None, gex_sum_row=None):
        self._closes = closes_by_offset_days
        self._gex_hour_row = gex_hour_row
        self._gex_sum_row = gex_sum_row

    def get_ohlcv_by_date_range(self, currency: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        today = datetime(2026, 7, 20)
        rows = []
        for offset, close in sorted(self._closes.items(), reverse=True):
            date = today - timedelta(days=offset)
            if start <= date <= end:
                rows.append({"date": date, "close": close})
        return sorted(rows, key=lambda r: r["date"])

    def _db_cursor(self):
        return FakeCursor(self._gex_hour_row, self._gex_sum_row)


def _closes(n_days: int, rising: bool) -> Dict[int, float]:
    """n_days of closes; 'rising' controls whether recent (low offset) closes move more than older ones,
    to deterministically produce rv_10d < rv_30d (contracting) or > (expanding)."""
    closes = {}
    price = 60000.0
    for offset in range(n_days, -1, -1):
        # Older half moves a lot, recent half barely moves -> contracting vol (rv_10d < rv_30d).
        # Flip the condition for 'rising' to get expanding vol instead.
        move = 0.03 if (offset > 10) != rising else 0.001
        price *= (1 + move if offset % 2 == 0 else 1 - move)
        closes[offset] = price
    return closes


class TestCompute:
    def test_no_gex_data_returns_none_gex_and_gate_fails(self):
        repo = FakeRepo(_closes(45, rising=False), gex_hour_row=None, gex_sum_row=None)
        service = RegimeGateService(repository=repo)
        result = service.compute("BTC", as_of=datetime(2026, 7, 20))
        assert result["net_gex"] is None
        assert result["gate_pass"] is False

    def test_positive_gex_and_contracting_rv_passes_gate(self):
        repo = FakeRepo(_closes(45, rising=False), gex_hour_row=(datetime(2026, 7, 20),), gex_sum_row=(1_000_000.0,))
        service = RegimeGateService(repository=repo)
        result = service.compute("BTC", as_of=datetime(2026, 7, 20))
        assert result["net_gex"] == 1_000_000.0
        assert result["rv_ratio"] is not None
        assert result["gate_pass"] == (result["net_gex"] > 0 and result["rv_ratio"] < 1)

    def test_negative_gex_fails_gate_regardless_of_rv(self):
        repo = FakeRepo(_closes(45, rising=False), gex_hour_row=(datetime(2026, 7, 20),), gex_sum_row=(-500_000.0,))
        service = RegimeGateService(repository=repo)
        result = service.compute("BTC", as_of=datetime(2026, 7, 20))
        assert result["net_gex"] == -500_000.0
        assert result["gate_pass"] is False

    def test_insufficient_ohlcv_returns_none_rv_ratio(self):
        repo = FakeRepo({i: 60000.0 for i in range(5)}, gex_hour_row=(datetime(2026, 7, 20),), gex_sum_row=(1.0,))
        service = RegimeGateService(repository=repo)
        result = service.compute("BTC", as_of=datetime(2026, 7, 20))
        assert result["rv_10d"] is None
        assert result["rv_30d"] is None
        assert result["rv_ratio"] is None
        assert result["gate_pass"] is False

    def test_default_as_of_is_utc_correct_not_local_and_naive(self, monkeypatch):
        """
        Task G2-C: datetime.utcnow() (deprecated, naive) is replaced with
        datetime.now(timezone.utc).replace(tzinfo=None) -- must produce the
        exact same naive-but-UTC-valued datetime datetime.utcnow() used to
        (this module's DB-facing convention: onchain_analysis_snapshots.
        snapshot_hour is naive-UTC, and _compute_net_gex compares as_of
        against it directly, so a tz-aware as_of would silently mis-compare
        or error). Proven with a fake `datetime` whose now(tz) return value
        depends on tz, the same failure mode a naive-local
        datetime.now()-without-tz call would have produced.
        """
        fixed_utc_instant = datetime(2026, 7, 20, 14, 30, 0, tzinfo=timezone.utc)

        class _FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return fixed_utc_instant
                # A naive-local call (the pre-fix code path) would return a
                # DIFFERENT wall-clock value than the correct UTC instant --
                # simulated here as a deliberately wrong sentinel so the
                # test fails loudly if production code ever reverts to
                # calling datetime.now() without an explicit tz again.
                return datetime(1999, 1, 1)

        monkeypatch.setattr(regime_gate_service_module, "datetime", _FakeDatetime)

        repo = FakeRepo({}, gex_hour_row=None, gex_sum_row=None)
        service = RegimeGateService(repository=repo)
        captured_as_of = {}
        original_compute_net_gex = RegimeGateService._compute_net_gex

        def _spy(self, currency, as_of):
            captured_as_of["value"] = as_of
            return original_compute_net_gex(self, currency, as_of)

        monkeypatch.setattr(RegimeGateService, "_compute_net_gex", _spy)

        service.compute("BTC")

        resolved_as_of = captured_as_of["value"]
        assert resolved_as_of.tzinfo is None
        assert resolved_as_of == fixed_utc_instant.replace(tzinfo=None)
