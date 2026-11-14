"""The extractors translate service outputs into evidence (fakes of the service outputs; no database, no network)."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.liquidation_heatmap.level_snapshot import LevelSnapshot
from coding.core.analytics.open_interest.expiry_open_interest import ExpiryOpenInterest
from coding.core.analytics.option_flow.block_structure import BlockTradeLeg
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels
from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike
from coding.service.decision import evidence_extractors as extractors

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
CHAIN_TIME = NOW - timedelta(minutes=40)


def dealer_levels(gate_passed=True, total_open_interest_gex=-5e6, total_flow_gex=2e6, trades_used=100):
    return DealerFlowLevels(
        currency="BTC", expiry="27NOV26", index_price=65_000.0, chain_captured_at=CHAIN_TIME, flow_start=NOW - timedelta(days=20),
        trades_used=trades_used, rows=[], flow_call_wall=64_000.0, flow_put_wall=65_000.0, flow_gamma_flip=None,
        open_interest_call_wall=90_000.0, open_interest_put_wall=68_000.0, open_interest_gamma_flip=70_000.0,
        total_flow_gex=total_flow_gex, total_open_interest_gex=total_open_interest_gex, coverage=0.99,
        violation_rate=0.0 if gate_passed else 10 / 92, violations=0 if gate_passed else 10, options_checked=92,
        options_without_open_interest=0, gate_passed=gate_passed,
        gate_reason=None if gate_passed else "10.9% of options exceed their open interest (limit 5%)")


def open_interest_row(share=0.125, expiry="27NOV26", settlement=datetime(2026, 11, 27, 8, tzinfo=timezone.utc), total=1000.0):
    return ExpiryOpenInterest(expiry, settlement, (settlement - NOW).total_seconds() / 86400, total / 2, total / 2, total,
                              total * 65_000, share, 0)


def by_id(items):
    return {item.id: item for item in items}


def test_dealer_evidence_with_a_failed_check_discounts_only_the_flow_based_items():
    items = by_id(extractors.dealer_evidence(dealer_levels(gate_passed=False), open_interest_row(share=0.125), NOW))
    flow_wall, oi_wall = items["dealer.27NOV26.flow.put_wall"], items["dealer.27NOV26.open_interest.put_wall"]
    assert flow_wall.quality == pytest.approx(0.6 * 0.2)       # FLOW_DEALER_LEVEL_QUALITY x FAILED_CHECK_QUALITY_FACTOR
    assert any(reason.startswith("plausibility check failed (10.9% of options") for reason in flow_wall.quality_reasons)
    assert flow_wall.input_group == "deribit_trades" and oi_wall.input_group == "deribit_open_interest"
    assert oi_wall.quality == pytest.approx(0.6) and not any("plausibility" in reason for reason in oi_wall.quality_reasons)
    # share 12.5 % of all open interest / 25 % for full strength = 0.5
    assert oi_wall.strength == pytest.approx(0.5)
    assert items["dealer.27NOV26.open_interest.gamma_flip"].strength == pytest.approx(0.5 * 0.5)
    assert oi_wall.horizon_days == pytest.approx(12 + 20 / 24)       # 27 Nov 08:00 minus 14 Nov 12:00
    assert oi_wall.as_of == CHAIN_TIME
    assert "dealer.27NOV26.flow.gamma_flip" not in items            # None stays absent, never invented


def test_dealer_evidence_turns_the_total_gex_sign_into_a_weak_regime_hint():
    items = by_id(extractors.dealer_evidence(dealer_levels(gate_passed=False), open_interest_row(share=0.25), NOW))
    oi_regime, flow_regime = items["dealer.27NOV26.open_interest.total_gex_sign"], items["dealer.27NOV26.flow.total_gex_sign"]
    assert oi_regime.regime == "expansion" and flow_regime.regime == "range_bound"
    assert oi_regime.strength == pytest.approx(0.5) and oi_regime.quality == pytest.approx(0.3)
    assert flow_regime.quality == pytest.approx(0.3 * 0.2)
    assert any(reason.startswith("assumption-based") for reason in oi_regime.quality_reasons)


def test_dealer_evidence_with_a_passed_check_and_unknown_share():
    items = by_id(extractors.dealer_evidence(dealer_levels(gate_passed=True, total_open_interest_gex=None, trades_used=0),
                                             None, NOW))
    assert items["dealer.27NOV26.flow.put_wall"].quality == pytest.approx(0.6)
    assert items["dealer.27NOV26.flow.put_wall"].strength == pytest.approx(1.0)     # not scaled, never a made-up share
    assert any("share of the expiry not computed: strength not scaled" in reason for reason in items["dealer.27NOV26.flow.put_wall"].quality_reasons)
    # no total computed (None) and no trades: no regime item at all
    assert not any(key.endswith("total_gex_sign") for key in items)


def trade(option_type, direction, amount, strike=65_000.0, block=False):
    return OptionTrade("27NOV26", strike, option_type, direction, amount, block)


def leg(block_id, option_type, direction, amount, declared=None, strike=65_000.0):
    return BlockTradeLeg(block_id, f"{block_id}-{option_type}{direction}{amount}", NOW - timedelta(hours=1), "27NOV26", strike,
                         option_type, direction, amount, declared, None)


def test_option_flow_splits_screen_single_leg_and_multi_leg_blocks():
    legs = [leg("single", "C", "buy", 10), leg("multi", "C", "buy", 5, declared=3), leg("multi", "C", "sell", 5, declared=3),
            leg("multi", "P", "buy", 10, declared=3)]
    flow = aggregate_by_strike([trade("C", "buy", 30), trade("P", "buy", 10)]
                               + [trade(item.option_type, item.direction, item.amount, block=True) for item in legs])
    items = by_id(extractors.option_flow_evidence(flow, legs, NOW - timedelta(minutes=5), NOW))
    screen = items["option_flow.screen_net"]
    # screen only (blocks excluded): (30 - 0 - 10 + 0) / 40 = +0.5 -> bullish, strength 0.5
    assert screen.direction == "bullish" and screen.strength == pytest.approx(0.5) and screen.quality == pytest.approx(0.5)
    single = items["option_flow.single_leg_blocks_net"]
    assert single.direction == "bullish" and single.strength == pytest.approx(1.0) and single.quality == pytest.approx(0.5)
    multi = items["option_flow.multi_leg_blocks_net"]
    # (5 - 5 - 10 + 0) / 20 = -0.5 -> bearish; quality 0.5 x 0.15 (MULTI_LEG_BLOCK_QUALITY_FACTOR)
    assert multi.direction == "bearish" and multi.strength == pytest.approx(0.5)
    assert multi.quality == pytest.approx(0.075)
    assert any(reason.startswith("multi-leg structures") for reason in multi.quality_reasons)
    assert all(item.input_group == "deribit_trades" for item in items.values())
    assert screen.as_of == NOW - timedelta(minutes=5)


def test_a_block_declared_with_more_legs_than_are_in_the_window_counts_as_multi_leg():
    legs = [leg("partial", "P", "buy", 4, declared=2)]
    items = by_id(extractors.option_flow_evidence(aggregate_by_strike([]), legs, NOW, NOW))
    assert set(items) == {"option_flow.multi_leg_blocks_net"}


def test_balanced_flow_is_neutral_and_its_strength_says_how_balanced():
    flow = aggregate_by_strike([trade("C", "buy", 11), trade("P", "buy", 9)])
    item = extractors.option_flow_evidence(flow, [], NOW, NOW)[0]
    # (11 - 9) / 20 = 0.1 < 0.2 -> neutral, strength 1 - 0.1 / 0.2 = 0.5
    assert item.direction == "neutral" and item.strength == pytest.approx(0.5)


def test_flow_time_is_clamped_to_the_window_end_and_no_trades_give_no_items():
    flow = aggregate_by_strike([trade("C", "buy", 1)])
    assert extractors.option_flow_evidence(flow, [], NOW + timedelta(hours=2), NOW)[0].as_of == NOW
    assert extractors.option_flow_evidence(flow, [], None, NOW) == []
    assert extractors.flow_strike_evidence(aggregate_by_strike([]), NOW, NOW) == []


def test_flow_strikes_are_weak_levels_scaled_by_the_largest():
    flow = aggregate_by_strike([trade("P", "buy", 2, strike=60_000.0), trade("C", "sell", 1, strike=70_000.0)])
    items = by_id(extractors.flow_strike_evidence(flow, NOW, NOW, hours=24, per_side=2))
    # notional = contracts x strike: 120,000 and 70,000 -> strengths 1 and 70/120
    assert items["option_flow.strike.P_buy.60000"].strength == pytest.approx(1.0)
    assert items["option_flow.strike.C_sell.70000"].strength == pytest.approx(70_000 / 120_000)
    assert all(item.kind == "price_level" and item.quality == pytest.approx(0.35) for item in items.values())


def test_taker_ratio_against_its_own_band():
    hours = [NOW - timedelta(hours=offset) for offset in range(300, 0, -1)]
    ratios = [0.9 if index % 2 == 0 else 1.1 for index in range(299)] + [1.4]
    item, reason = extractors.taker_ratio_evidence("BTCUSDT", list(zip(hours, ratios)), NOW)
    assert reason is None
    mean = sum(ratios) / 300                                            # (150 x 0.9 + 149 x 1.1 + 1.4) / 300
    deviation = math.sqrt(sum((ratio - mean) ** 2 for ratio in ratios) / 299)
    z = (1.4 - mean) / deviation
    assert z > 1                                                        # above the band
    assert item.direction == "bullish" and item.strength == pytest.approx(min(1.0, z / 3))
    assert item.details["z"] == pytest.approx(z) and item.horizon_days == 3.0
    assert item.as_of == hours[-1] + timedelta(hours=1)                 # the 24 h window ends one hour after its last start
    assert item.input_group == "binance_perpetual_candles"


def test_taker_ratio_needs_enough_points_and_a_band():
    few = [(NOW - timedelta(hours=offset), 1.0) for offset in range(10)]
    item, reason = extractors.taker_ratio_evidence("BTCUSDT", few, NOW)
    assert item is None and "only 9 rolling points" in reason      # the point at NOW covers an hour not yet closed
    flat = [(NOW - timedelta(hours=offset), 1.0) for offset in range(300)]
    item, reason = extractors.taker_ratio_evidence("BTCUSDT", flat, NOW)
    assert item is None and "no width" in reason


def test_liquidation_pools_become_magnets_scaled_by_the_largest():
    snapshot = LevelSnapshot("BTCUSDT", NOW - timedelta(minutes=50), NOW - timedelta(minutes=48), 100_000.0, 100.0,
                             [[96_000.0, 5e6, 0.0], [97_500.0, 4e6, 0.0], [98_000.0, 10e6, 0.0],
                              [103_000.0, 0.0, 20e6], [104_500.0, 0.0, 2e6]])
    items = by_id(extractors.liquidation_evidence(snapshot))
    # longs below: 98,000 (10M) then 96,000 (5M); 97,500 is within 1 % (1,000) of 98,000 and is skipped
    assert sorted(items) == ["liquidation.long.96000", "liquidation.long.98000", "liquidation.short.103000",
                             "liquidation.short.104500"]
    assert items["liquidation.long.98000"].strength == pytest.approx(0.5)      # 10M / 20M
    assert items["liquidation.short.104500"].strength == pytest.approx(0.1)    # 2M / 20M
    assert all(item.kind == "magnet" and item.direction is None and item.quality == pytest.approx(0.25)
               for item in items.values())
    assert items["liquidation.long.98000"].as_of == NOW - timedelta(minutes=50)


def test_menthorq_levels_are_open_interest_price_levels_and_none_is_skipped():
    items = by_id(extractors.menthorq_evidence({"call_resistance": 80_000.0, "put_support": 60_000.0, "hvl": None},
                                               ["27NOV26", "25DEC26"], CHAIN_TIME, NOW))
    assert sorted(items) == ["menthorq.call_resistance", "menthorq.put_support"]
    assert all(item.input_group == "deribit_open_interest" and item.quality == pytest.approx(0.7) for item in items.values())
    assert "not a regime boundary" in items["menthorq.put_support"].statement
    assert items["menthorq.put_support"].horizon_days == pytest.approx(12 + 20 / 24)


def test_chosen_expiries_are_the_nearest_monthly_and_the_largest():
    rows = [open_interest_row(0.4, "25DEC26", datetime(2026, 12, 25, 8, tzinfo=timezone.utc), 4000.0),
            open_interest_row(0.25, "27NOV26", datetime(2026, 11, 27, 8, tzinfo=timezone.utc), 2500.0),
            open_interest_row(0.1, "20NOV26", datetime(2026, 11, 20, 8, tzinfo=timezone.utc), 1000.0)]
    assert extractors.chosen_expiries(rows) == ["27NOV26", "25DEC26"]
    assert extractors.chosen_expiries(rows[1:]) == ["27NOV26"]          # the monthly is also the largest
    assert extractors.chosen_expiries([]) == []
