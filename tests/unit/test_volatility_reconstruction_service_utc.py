"""
Unit tests for VolatilityReconstructionService._reconstruct_vrp's UTC
handling (institutional_metrics_spec.md Wave G Task G2-C).

Two bugs, both instances of the same naive-local-vs-UTC mismatch class:

1. ``row["date"].timestamp()`` on a naive-UTC value (DatabaseRepository.
   get_ohlcv_by_date_range's own convention: naive datetimes that already
   represent UTC, per its docstring) silently reinterprets that value in
   the calling machine's LOCAL timezone -- wrong everywhere except UTC+00:00
   machines. Fixed via ``.replace(tzinfo=timezone.utc)`` before ``.timestamp()``.

2. ``VRPCalculator.calculate_realized_volatility`` now requires an explicit,
   timezone-aware ``reference_time`` (it no longer silently defaults to a
   naive ``datetime.now()``) -- ``snapshot_hour`` (also naive-UTC per the
   same DB convention) must be given the same ``.replace(tzinfo=timezone.
   utc)`` treatment before being passed through, not reinterpreted.
"""
import calendar
from datetime import datetime, timezone
from unittest.mock import MagicMock

from coding.core.analytics.vrp_calculator import VRPCalculator
from coding.service.on_chain.volatility_reconstruction_service import (
    VolatilityReconstructionService,
)


def _make_service():
    service = VolatilityReconstructionService.__new__(VolatilityReconstructionService)
    service.repo = MagicMock()
    return service


class TestReconstructVrpUtcHandling:
    def test_price_history_epoch_is_true_utc_not_local(self, monkeypatch):
        """
        row["date"] values are naive-UTC (per get_ohlcv_by_date_range's
        contract) -- each price_history "timestamp" must equal the true UTC
        epoch of that naive value (calendar.timegm, timezone-independent),
        never the machine-local-timezone-shifted epoch the old
        ``row["date"].timestamp()`` call silently produced.
        """
        service = _make_service()
        naive_dates = [datetime(2026, 6, d, 0, 0, 0) for d in range(1, 31)]
        closes = [60000.0 + i * 37.0 for i in range(len(naive_dates))]
        service.repo.get_ohlcv_by_date_range.return_value = [
            {"date": d, "close": c} for d, c in zip(naive_dates, closes)
        ]

        captured = {}

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            captured["price_history"] = price_history
            captured["reference_time"] = reference_time
            return 0.5  # arbitrary positive RV so _reconstruct_vrp proceeds past its gate

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )

        snapshot_hour = datetime(2026, 7, 1, 8, 0, 0)  # naive, per DB convention
        result = service._reconstruct_vrp("BTC", snapshot_hour, instruments=[], underlying_price=65000.0)

        assert result["realized_vol"] == 0.5

        price_history = captured["price_history"]
        assert len(price_history) == len(naive_dates)
        for entry, expected_date in zip(price_history, naive_dates):
            expected_epoch = calendar.timegm(expected_date.timetuple())
            assert entry["timestamp"] == expected_epoch

    def test_reference_time_passed_through_is_timezone_aware_utc(self, monkeypatch):
        """
        snapshot_hour (naive-UTC per DB convention) must reach
        VRPCalculator.calculate_realized_volatility as a timezone-aware UTC
        instant carrying the SAME wall-clock value -- not reinterpreted
        (e.g. via a bare `.timestamp()`-and-back round trip through local
        time), and not left naive (which would now raise inside the
        Core calculator's UTC-aware comparison).
        """
        service = _make_service()
        service.repo.get_ohlcv_by_date_range.return_value = [
            {"date": datetime(2026, 6, 1), "close": 60000.0},
            {"date": datetime(2026, 6, 2), "close": 60100.0},
        ]

        captured = {}

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            captured["reference_time"] = reference_time
            return 0.5

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )

        snapshot_hour = datetime(2026, 7, 1, 8, 0, 0)
        service._reconstruct_vrp("BTC", snapshot_hour, instruments=[], underlying_price=65000.0)

        reference_time = captured["reference_time"]
        assert reference_time.tzinfo is not None
        assert reference_time == snapshot_hour.replace(tzinfo=timezone.utc)

    def test_already_aware_snapshot_hour_is_not_double_converted(self, monkeypatch):
        """If a caller ever passes an already timezone-aware snapshot_hour,
        it must be forwarded as-is (not shifted again)."""
        service = _make_service()
        service.repo.get_ohlcv_by_date_range.return_value = [
            {"date": datetime(2026, 6, 1), "close": 60000.0},
            {"date": datetime(2026, 6, 2), "close": 60100.0},
        ]

        captured = {}

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            captured["reference_time"] = reference_time
            return 0.5

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )

        snapshot_hour_aware = datetime(2026, 7, 1, 8, 0, 0, tzinfo=timezone.utc)
        service._reconstruct_vrp("BTC", snapshot_hour_aware, instruments=[], underlying_price=65000.0)

        assert captured["reference_time"] == snapshot_hour_aware
