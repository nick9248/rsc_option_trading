"""
Unit tests for HourlyAggregationService._aggregate_instrument's bid/ask
estimation disclosure (Task Wave-J-E Fix 2).

hourly_snapshots.bid_price/ask_price have never held a real order-book
quote -- they are always derived from this hour's trades: ask_price = the
max buy-trade price (or vwap*1.005 if no buy trade occurred), bid_price =
the min sell-trade price (or vwap*0.995 if no sell trade occurred).
bid_is_estimated/ask_is_estimated disclose, per side, whether that side's
value came from a genuine trade this hour (False) or the vwap+/-0.5%
fallback with zero market evidence behind it (True). These flags are what
let VolatilitySurfaceCalculator's "quoted" filter stay meaningful now that
DatabaseRepository.get_hourly_snapshots_for_hour actually returns bid_price/
ask_price (Task Wave-J-E Fix 1).

Also covers the captured_at UTC fix: this machine's local timezone is not
UTC, and captured_at is a naive-UTC-valued `timestamp without time zone`
column (matching the day-boundary bug class this campaign fixed elsewhere,
e.g. save_daily_oi_snapshot).
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from coding.service.data_collection.hourly_aggregation_service import HourlyAggregationService


def _trade(price, amount, direction, iv=60.0, index_price=65000.0, mark_price=None):
    """(instrument_name, price, amount, direction, iv, index_price, mark_price)
    -- matches DatabaseRepository.get_trades_for_hour's documented tuple shape."""
    return ("BTC-10FEB26-65000-C", price, amount, direction, iv, index_price,
            mark_price if mark_price is not None else price)


@pytest.fixture
def service():
    svc = HourlyAggregationService.__new__(HourlyAggregationService)
    svc.repo = MagicMock()
    svc.bs_calculator = MagicMock()
    svc.bs_calculator.parse_instrument_name.return_value = None  # skip Greeks calc, not under test
    return svc


class TestPerSideEstimationFlags:
    def test_both_sides_traded_neither_flagged_estimated(self, service):
        trades = [
            _trade(0.050, 1.0, "buy"),
            _trade(0.049, 1.0, "sell"),
        ]
        snapshot = service._aggregate_instrument(
            "BTC", "BTC-10FEB26-65000-C", trades, datetime(2026, 2, 6, 22, 0),
        )
        assert snapshot["bid_is_estimated"] is False
        assert snapshot["ask_is_estimated"] is False

    def test_buy_only_ask_real_bid_estimated(self, service):
        """Only buy trades this hour: ask_price is a real trade price, but
        bid_price has zero sell-side evidence and falls back to
        vwap*0.995 -- bid_is_estimated must be True."""
        trades = [_trade(0.050, 1.0, "buy")]
        snapshot = service._aggregate_instrument(
            "BTC", "BTC-10FEB26-65000-C", trades, datetime(2026, 2, 6, 22, 0),
        )
        assert snapshot["bid_is_estimated"] is True
        assert snapshot["ask_is_estimated"] is False

    def test_sell_only_bid_real_ask_estimated(self, service):
        trades = [_trade(0.050, 1.0, "sell")]
        snapshot = service._aggregate_instrument(
            "BTC", "BTC-10FEB26-65000-C", trades, datetime(2026, 2, 6, 22, 0),
        )
        assert snapshot["bid_is_estimated"] is False
        assert snapshot["ask_is_estimated"] is True

    def test_estimated_flags_survive_the_bid_ask_crossed_swap(self, service):
        """A crossed/noisy hour (real buy-derived ask below real sell-derived
        bid) makes _aggregate_instrument swap bid_estimate/ask_estimate to
        keep bid <= ask. The is_estimated flags must be swapped along with
        the prices, not stay pinned to their pre-swap slot -- otherwise a
        genuinely-traded price could end up mislabeled estimated (or a
        fallback mislabeled real) purely as a side effect of the swap.
        (The fallback tracks vwap tightly enough that crossing only occurs
        when BOTH sides are real trades, so this case can't independently
        distinguish real-swapped-into-estimated -- it guards that the swap
        itself never corrupts the flag-price pairing.)"""
        trades = [
            _trade(0.030, 1.0, "buy"),   # ask candidate: 0.030 (real)
            _trade(0.060, 1.0, "sell"),  # bid candidate: 0.060 (real) -- crossed, triggers the swap
        ]
        snapshot = service._aggregate_instrument(
            "BTC", "BTC-10FEB26-65000-C", trades, datetime(2026, 2, 6, 22, 0),
        )
        # Both sides had real trades -- neither should be estimated,
        # regardless of which ends up in bid_price vs ask_price after the swap.
        assert snapshot["bid_is_estimated"] is False
        assert snapshot["ask_is_estimated"] is False
        assert snapshot["bid_price"] <= snapshot["ask_price"]


class TestCapturedAtIsUtc:
    def test_captured_at_is_naive_utc_not_local(self, service, monkeypatch):
        fixed_utc = datetime(2026, 2, 6, 23, 30, 0, tzinfo=timezone.utc)

        class _FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return fixed_utc
                # Simulate a non-UTC local clock (e.g. UTC+2) to prove the
                # naive datetime.now() bug class would have produced a
                # different (wrong) value here.
                return datetime(2026, 2, 7, 1, 30, 0)

        monkeypatch.setattr(
            "coding.service.data_collection.hourly_aggregation_service.datetime",
            _FixedDateTime,
        )

        trades = [_trade(0.050, 1.0, "buy"), _trade(0.049, 1.0, "sell")]
        snapshot = service._aggregate_instrument(
            "BTC", "BTC-10FEB26-65000-C", trades, datetime(2026, 2, 6, 22, 0),
        )

        assert snapshot["captured_at"] == datetime(2026, 2, 6, 23, 30, 0)
        assert snapshot["captured_at"].tzinfo is None
