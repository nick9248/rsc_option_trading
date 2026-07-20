"""Tests for the shared realized-vol utility."""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from coding.service.scanner.realized_vol import compute_realized_vol, dte_matched_window


class FakeRepo:
    def __init__(self, closes_by_offset_days: Dict[int, float]):
        """closes_by_offset_days: {days_before_today: close_price}."""
        self._closes = closes_by_offset_days

    def get_ohlcv_by_date_range(self, currency: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        today = datetime(2026, 7, 20)
        rows = []
        for offset, close in sorted(self._closes.items(), reverse=True):
            date = today - timedelta(days=offset)
            if start <= date <= end:
                rows.append({"date": date, "close": close})
        return sorted(rows, key=lambda r: r["date"])


class TestDteMatchedWindow:
    def test_floors_at_21(self):
        assert dte_matched_window(5) == 21
        assert dte_matched_window(21) == 21

    def test_rounds_to_dte_above_floor(self):
        assert dte_matched_window(39.5) == 40
        assert dte_matched_window(158) == 158


class TestComputeRealizedVol:
    def test_insufficient_rows_returns_none(self):
        repo = FakeRepo({i: 100.0 + i for i in range(5)})
        result = compute_realized_vol(repo, "BTC", window_days=21, as_of=datetime(2026, 7, 20))
        assert result is None

    def test_zero_variance_returns_zero(self):
        closes = {i: 100.0 for i in range(25)}  # constant price -> zero log returns
        repo = FakeRepo(closes)
        result = compute_realized_vol(repo, "BTC", window_days=21, as_of=datetime(2026, 7, 20))
        assert result == 0.0

    def test_sufficient_rows_returns_positive_float(self):
        import random
        random.seed(42)
        closes = {i: 60000.0 * (1 + random.uniform(-0.02, 0.02)) for i in range(40)}
        repo = FakeRepo(closes)
        result = compute_realized_vol(repo, "BTC", window_days=21, as_of=datetime(2026, 7, 20))
        assert result is not None
        assert result > 0
