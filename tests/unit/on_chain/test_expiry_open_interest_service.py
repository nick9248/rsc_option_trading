"""Tests of the open interest per expiry service with a fake repository (no database)."""

from datetime import datetime, timezone

import pytest

from coding.service.on_chain.expiry_open_interest_service import ExpiryOpenInterestService

NOW = datetime(2026, 11, 14, 11, 48, tzinfo=timezone.utc)
CAPTURED_AT = datetime(2026, 11, 14, 11, 45, tzinfo=timezone.utc)


class FakeRepository:
    """Returns one stored snapshot; records the instants it was asked for."""

    def __init__(self, snapshot=True, index=True):
        self.snapshot = snapshot
        self.index = index
        self.asked = []

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        self.asked.append(as_of)
        if not self.snapshot:
            return None
        rows = [{"instrument_name": "BTC-25DEC26-70000-C", "expiration": "25DEC26", "strike": 70_000.0, "option_type": "C",
                 "open_interest": 10.0, "mark_iv": 50.0, "mark_price": 0.05, "underlying_price": 67_000.0},
                {"instrument_name": "BTC-25DEC26-70000-P", "expiration": "25DEC26", "strike": 70_000.0, "option_type": "P",
                 "open_interest": None, "mark_iv": 50.0, "mark_price": 0.05, "underlying_price": 67_000.0}]
        return CAPTURED_AT, rows

    def get_index_price_at_or_before(self, currency, as_of):
        return (67_000.0, CAPTURED_AT) if self.index else None


def test_report_has_the_snapshot_time_index_price_and_the_rows():
    repository = FakeRepository()
    report = ExpiryOpenInterestService(repository, lambda: NOW).open_interest("BTC")
    assert report.currency == "BTC" and report.captured_at == CAPTURED_AT and report.index_price == 67_000.0
    assert [row.expiry for row in report.rows] == ["25DEC26"]
    assert report.rows[0].call_open_interest == 10.0 and report.rows[0].put_open_interest is None
    assert report.rows[0].notional_usd == pytest.approx(670_000.0)
    assert repository.asked == [NOW]                       # as of the clock, no lookahead


def test_no_snapshot_raises_lookup_error():
    with pytest.raises(LookupError):
        ExpiryOpenInterestService(FakeRepository(snapshot=False), lambda: NOW).open_interest("BTC")


def test_no_index_price_raises_lookup_error():
    with pytest.raises(LookupError):
        ExpiryOpenInterestService(FakeRepository(index=False), lambda: NOW).open_interest("BTC")
