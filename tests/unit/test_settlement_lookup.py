"""Tests for the shared settlement-price lookup utility."""
from datetime import datetime, timezone
from typing import Any, Dict, List

from coding.service.scanner.settlement_lookup import lookup_settlement_price, parse_expiry_settlement


class FakeRepo:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def get_ohlcv_by_date_range(self, currency: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        return [r for r in self._rows if start <= r["date"] <= end]


class TestParseExpirySettlement:
    def test_valid_expiry_returns_0800_utc(self):
        result = parse_expiry_settlement("28AUG26")
        assert result == datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)

    def test_invalid_expiry_returns_none(self):
        assert parse_expiry_settlement("not-a-date") is None


class TestLookupSettlementPrice:
    def test_no_rows_returns_none(self):
        repo = FakeRepo([])
        result = lookup_settlement_price(repo, "BTC", datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc))
        assert result is None

    def test_returns_close_from_matching_day(self):
        repo = FakeRepo([{"date": datetime(2026, 8, 28, 8, 0), "close": 65000.0}])
        result = lookup_settlement_price(repo, "BTC", datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc))
        assert result == 65000.0
