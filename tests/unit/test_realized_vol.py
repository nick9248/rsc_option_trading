"""Tests for the shared realized-vol utility."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from coding.core.analytics.vrp_calculator import VRPCalculator
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


class TestParityWithVRPCalculator:
    """
    Wave H Task H-F, Fix 2: realized_vol.py's docstring claims its
    methodology "matches VRPCalculator's existing methodology". It didn't
    -- realized_vol.py used sample variance (ddof=1) while VRPCalculator
    uses population variance (np.std default, ddof=0), a real ~1.5-2%
    discrepancy on realistic sample sizes. This test feeds the SAME
    identical close-price series through both implementations and proves
    they now agree (after accounting for the documented unit difference:
    compute_realized_vol returns percent, VRPCalculator returns a decimal
    fraction).
    """

    def test_same_price_series_yields_same_annualized_rv(self):
        import random
        random.seed(7)

        window_days = 24  # n = 24 log returns from window_days + 1 = 25 closes
        as_of = datetime(2026, 7, 20)
        closes_by_offset = {
            offset: 50000.0 * (1 + random.uniform(-0.03, 0.03))
            for offset in range(window_days + 1)
        }

        # -- scanner side (realized_vol.compute_realized_vol) --
        repo = FakeRepo(closes_by_offset)
        scanner_rv_pct = compute_realized_vol(repo, "BTC", window_days=window_days, as_of=as_of)
        assert scanner_rv_pct is not None

        # -- VRPCalculator side, fed the EXACT SAME closes in the EXACT
        # SAME oldest-to-newest order, wide enough a window_days that the
        # single-sided cutoff filter (>= cutoff_time) admits every point,
        # so both implementations consume an identical log-return series.
        price_history = [
            {
                "timestamp": (as_of - timedelta(days=offset)).replace(tzinfo=timezone.utc).timestamp(),
                "close": closes_by_offset[offset],
            }
            for offset in sorted(closes_by_offset.keys(), reverse=True)  # oldest -> newest
        ]
        reference_time = as_of.replace(tzinfo=timezone.utc)
        vrp_calc = VRPCalculator(currency="BTC", lookback_days=window_days)
        vrp_rv_fraction = vrp_calc.calculate_realized_volatility(
            price_history, window_days=window_days + 50, reference_time=reference_time,
        )

        assert vrp_rv_fraction * 100.0 == pytest.approx(scanner_rv_pct, rel=1e-9)
