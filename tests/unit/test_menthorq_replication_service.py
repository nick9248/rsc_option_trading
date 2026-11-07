"""
MenthorqReplicationService.load_inputs with an in-memory fake repository
(no database): point-in-time guards and the older-open-interest option.
"""

from datetime import datetime, timedelta, timezone

import pytest

from coding.service.on_chain.menthorq_replication_service import MenthorqReplicationService

AS_OF = datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)
EARLIER = datetime(2026, 11, 6, 8, 59, tzinfo=timezone.utc)


class FakeRepository:
    """Serves a 'current' snapshot at/before AS_OF and an older one at/before EARLIER."""

    def __init__(self, has_index=True):
        self.has_index = has_index

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        if as_of >= AS_OF:
            return AS_OF - timedelta(minutes=10), [
                {"instrument_name": "A", "expiration": "27NOV26", "strike": 1.0,
                 "option_type": "C", "open_interest": 500.0, "mark_iv": 40.0,
                 "underlying_price": 1.0},
                {"instrument_name": "B", "expiration": "27NOV26", "strike": 1.0,
                 "option_type": "P", "open_interest": 7.0, "mark_iv": 40.0,
                 "underlying_price": 1.0},
            ]
        if as_of >= EARLIER:
            return EARLIER - timedelta(minutes=10), [
                {"instrument_name": "A", "open_interest": 200.0},
            ]
        return None

    def get_index_price_at_or_before(self, currency, as_of):
        return (75000.0, as_of - timedelta(seconds=7)) if self.has_index else None


def test_open_interest_taken_from_older_snapshot_and_missing_stays_none():
    inputs = MenthorqReplicationService(FakeRepository()).load_inputs("BTC", AS_OF, EARLIER)
    by_name = {row["instrument_name"]: row for row in inputs["chain_rows"]}
    assert by_name["A"]["open_interest"] == 200.0
    assert by_name["A"]["mark_iv"] == 40.0          # IV still from AS_OF
    assert by_name["B"]["open_interest"] is None    # absent earlier -> None, not 0
    assert inputs["open_interest_captured_at_utc"] == EARLIER - timedelta(minutes=10)
    assert by_name["A"]["iv_source"] == "mark_iv"


class FakeRepositoryWithoutMarkIv(FakeRepository):
    """One row without mark_iv but with mark_price, one with neither."""

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        # 27NOV26 settles 2026-11-27 08:00; captured 2025-11-27 08:00 -> tau = 1 year.
        # Black-76 at F = K = 100, sigma 0.5: 19.7413 USD = 0.197413 BTC.
        captured = datetime(2025, 11, 27, 8, 0, tzinfo=timezone.utc)
        return captured, [
            {"instrument_name": "A", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "C", "open_interest": 1.0, "mark_iv": None,
             "mark_price": 0.197413, "underlying_price": 100.0},
            {"instrument_name": "B", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "P", "open_interest": 1.0, "mark_iv": None,
             "mark_price": None, "underlying_price": 100.0},
        ]


def test_missing_mark_iv_is_rebuilt_from_mark_price_and_labelled(caplog):
    with caplog.at_level("WARNING"):
        inputs = MenthorqReplicationService(FakeRepositoryWithoutMarkIv()).load_inputs("BTC", AS_OF)
    by_name = {row["instrument_name"]: row for row in inputs["chain_rows"]}
    assert by_name["A"]["mark_iv"] == pytest.approx(50.0, abs=1e-3)
    assert by_name["A"]["iv_source"] == "rebuilt_from_mark_price"
    assert by_name["B"]["mark_iv"] is None
    assert by_name["B"]["iv_source"] == "missing"
    assert inputs["iv_source_counts"] == {"mark_iv": 0, "rebuilt_from_mark_price": 1, "missing": 1}
    assert "rebuilt from mark_price" in caplog.text


def test_open_interest_after_as_of_is_rejected_as_lookahead():
    service = MenthorqReplicationService(FakeRepository())
    with pytest.raises(ValueError):
        service.load_inputs("BTC", EARLIER, AS_OF)


def test_missing_index_price_raises_instead_of_using_a_default():
    service = MenthorqReplicationService(FakeRepository(has_index=False))
    with pytest.raises(LookupError):
        service.load_inputs("BTC", AS_OF)


def test_missing_snapshot_raises():
    service = MenthorqReplicationService(FakeRepository())
    with pytest.raises(LookupError):
        service.load_inputs("BTC", EARLIER - timedelta(days=1))
