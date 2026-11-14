"""The block structure method of the option flow service, with a fake repository (no database)."""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.option_flow.block_structure import BlockTradeLeg
from coding.service.option_flow.option_flow_service import OptionFlowService

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self, legs):
        self.legs = legs
        self.calls = []

    def get_block_trade_legs_between(self, currency, start_ms, end_ms):
        self.calls.append((currency, start_ms, end_ms))
        return self.legs


def make_leg(block, amount, strike=100000.0):
    return BlockTradeLeg(block, f"{block}-{strike}", NOW - timedelta(hours=1), "25DEC26", strike, "C", "buy", amount, None, None)


def test_the_window_is_the_one_of_strike_flow_and_the_largest_blocks_come_first():
    repository = FakeRepository([make_leg("BLOCK-1", 1.0), make_leg("BLOCK-2", 5.0), make_leg("BLOCK-2", 1.0, 90000.0)])
    service = OptionFlowService(repository, clock=lambda: NOW)
    report = service.block_structures("BTC", 24, top=1)
    assert repository.calls == [("BTC", int((NOW - timedelta(hours=24)).timestamp() * 1000), int(NOW.timestamp() * 1000))]
    assert report.window_start == NOW - timedelta(hours=24) and report.window_end == NOW
    assert [block.block_trade_id for block in report.blocks] == ["BLOCK-2"]
    assert report.legs_in_window == 3                  # all legs of the window, not only those of the blocks listed


def test_end_is_converted_to_utc_and_must_be_timezone_aware():
    repository = FakeRepository([])
    service = OptionFlowService(repository, clock=lambda: NOW)
    end = datetime(2026, 11, 14, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    report = service.block_structures("BTC", 24, end=end)
    assert report.window_end == NOW and report.window_end.utcoffset() == timedelta(0)
    assert report.blocks == [] and report.legs_in_window == 0
    with pytest.raises(ValueError):
        service.block_structures("BTC", 24, end=datetime(2026, 11, 14, 10, 0))
