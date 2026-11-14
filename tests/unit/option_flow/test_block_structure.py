"""Pure tests of the block structure description and of the expiry shares of the biggest bars."""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.option_flow.block_structure import BlockTradeLeg, bar_expiry_shares, describe_blocks
from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike

START = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


def make_leg(block="BLOCK-1", expiration="25DEC26", strike=100000.0, option_type="C", direction="buy", amount=1.0,
             declared=None, offset=0, trade_id=None):
    return BlockTradeLeg(block, trade_id or f"{block}-{expiration}-{strike}-{option_type}-{direction}-{offset}",
                         START + timedelta(seconds=offset), expiration, strike, option_type, direction, amount,
                         declared, None)


def test_one_leg_is_a_single_leg():
    (structure,) = describe_blocks([make_leg(declared=1)])
    assert structure.label == "single leg"
    assert structure.legs_in_window == 1 and structure.declared_leg_count == 1


def test_two_legs_label():
    legs = [make_leg(direction="buy"), make_leg(direction="sell", strike=90000.0)]
    assert describe_blocks(legs)[0].label == "two legs"


def test_fifteen_legs_over_several_expiries():
    expiries = ["25DEC26", "26MAR27", "25SEP26"]
    legs = [make_leg(expiration=expiries[index % 3], strike=100000.0 + index * 1000, offset=index) for index in range(15)]
    structure = describe_blocks(legs)[0]
    assert structure.label == ("multi-leg structure over 3 expiries: the direction of one leg is not a view on its own")
    assert structure.expiries == ["25DEC26", "25SEP26", "26MAR27"]
    assert structure.legs_in_window == 15


def test_many_legs_over_one_expiry_is_singular():
    legs = [make_leg(strike=100000.0 + index * 1000) for index in range(3)]
    assert "over 1 expiry:" in describe_blocks(legs)[0].label


def test_partial_window_is_stated():
    legs = [make_leg(declared=15, strike=100000.0 + index * 1000) for index in range(4)]
    assert describe_blocks(legs)[0].label.endswith(" (only 4 of 15 legs in the window)")
    legs = [make_leg(declared=2), make_leg(declared=2, strike=90000.0)]
    assert describe_blocks(legs)[0].label == "two legs"           # complete: no suffix


def test_declared_count_unknown_gives_no_suffix():
    assert "only" not in describe_blocks([make_leg(declared=None)])[0].label


def test_notional_counts_and_biggest_legs():
    legs = [
        make_leg(strike=100000.0, amount=2.0, direction="buy", option_type="C", offset=5),     # 200,000
        make_leg(strike=50000.0, amount=1.0, direction="sell", option_type="P", offset=1),     # 50,000
        make_leg(strike=120000.0, amount=3.0, direction="buy", option_type="C", offset=3),     # 360,000
        make_leg(strike=80000.0, amount=0.5, direction="sell", option_type="P", offset=2),     # 40,000
    ]
    structure = describe_blocks(legs)[0]
    assert structure.notional_usd == pytest.approx(650000.0)
    assert (structure.legs_bought, structure.legs_sold, structure.calls, structure.puts) == (2, 2, 2, 2)
    assert [(leg.strike, leg.notional_usd) for leg in structure.biggest_legs] == [
        (120000.0, 360000.0), (100000.0, 200000.0), (50000.0, 50000.0)]
    assert structure.biggest_legs[0].side == "buy" and structure.biggest_legs[0].contracts == 3.0
    assert structure.first_time == START + timedelta(seconds=1)


def test_blocks_are_sorted_by_notional_and_limited():
    legs = [make_leg(block="BLOCK-A", amount=1.0), make_leg(block="BLOCK-B", amount=5.0), make_leg(block="BLOCK-C", amount=3.0)]
    assert [s.block_trade_id for s in describe_blocks(legs, top=2)] == ["BLOCK-B", "BLOCK-C"]


def test_block_ties_are_ordered_by_id():
    legs = [make_leg(block="BLOCK-B"), make_leg(block="BLOCK-A")]
    assert [s.block_trade_id for s in describe_blocks(legs)] == ["BLOCK-A", "BLOCK-B"]


def make_flows(trades_by_expiry):
    per_expiry = {expiry: aggregate_by_strike(trades) for expiry, trades in trades_by_expiry.items()}
    all_trades = [trade for trades in trades_by_expiry.values() for trade in trades]
    return per_expiry, aggregate_by_strike(all_trades)


def trade(expiration, strike, amount, is_block, option_type="C", direction="buy"):
    return OptionTrade(expiration, strike, option_type, direction, amount, is_block)


def test_bar_shares_sum_to_one_and_nothing_is_unexplained():
    per_expiry, all_flow = make_flows({
        "25DEC26": [trade("25DEC26", 120000.0, 3.0, True), trade("25DEC26", 120000.0, 1.0, False)],
        "26MAR27": [trade("26MAR27", 120000.0, 1.0, False)],
    })
    (bar,) = bar_expiry_shares(per_expiry, all_flow)
    assert bar.total_notional_usd == pytest.approx(600000.0)
    assert bar.blocked_notional_usd == pytest.approx(360000.0) and bar.blocked_share == pytest.approx(0.6)
    assert [(item.expiry, item.share) for item in bar.expiries] == [("25DEC26", pytest.approx(0.8)),
                                                                   ("26MAR27", pytest.approx(0.2))]
    assert sum(item.share for item in bar.expiries) == pytest.approx(1.0)
    assert bar.unexplained_notional == 0.0


def test_a_missing_expiry_shows_as_unexplained():
    per_expiry, all_flow = make_flows({
        "25DEC26": [trade("25DEC26", 120000.0, 3.0, True)],
        "26MAR27": [trade("26MAR27", 120000.0, 1.0, False)],
    })
    del per_expiry["26MAR27"]
    (bar,) = bar_expiry_shares(per_expiry, all_flow)
    assert bar.unexplained_notional == pytest.approx(120000.0)
    assert bar.expiries[0].share == pytest.approx(0.75)


def test_bars_are_ranked_per_type_and_side_and_limited():
    per_expiry, all_flow = make_flows({"25DEC26": [
        trade("25DEC26", 100000.0, 1.0, False, "C", "buy"),     # 100,000
        trade("25DEC26", 100000.0, 4.0, True, "P", "sell"),     # 400,000
        trade("25DEC26", 90000.0, 2.0, False, "C", "sell"),     # 180,000
    ]})
    bars = bar_expiry_shares(per_expiry, all_flow, top=2)
    assert [(bar.strike, bar.option_type, bar.direction) for bar in bars] == [(100000.0, "P", "sell"), (90000.0, "C", "sell")]


def test_ties_are_ordered_by_strike_type_side_and_expiry_by_name():
    per_expiry, all_flow = make_flows({
        "B": [trade("B", 100000.0, 1.0, False), trade("B", 90000.0, 1.0, False)],
        "A": [trade("A", 100000.0, 1.0, False)],
    })
    bars = bar_expiry_shares(per_expiry, all_flow)
    assert bars[0].strike == 100000.0 and bars[1].strike == 90000.0       # the larger total first
    assert [item.expiry for item in bars[0].expiries] == ["A", "B"]       # equal shares: by name
    per_expiry, all_flow = make_flows({"X": [trade("X", 80000.0, 1.0, False, "P"), trade("X", 80000.0, 1.0, False, "C")]})
    assert [bar.option_type for bar in bar_expiry_shares(per_expiry, all_flow)] == ["C", "P"]
