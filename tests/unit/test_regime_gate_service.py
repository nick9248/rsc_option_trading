"""
Tests for RegimeGateService. gate_pass is the ORIGINAL theoretically-
motivated definition (net_gex>0 AND rv_ratio<1) -- do not change this
definition based on the 2026-07-20 16-sample backtest, which found it
backwards; see docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
