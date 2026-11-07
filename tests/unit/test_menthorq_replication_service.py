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
