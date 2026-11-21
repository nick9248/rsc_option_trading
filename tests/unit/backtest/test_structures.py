"""Structure selection from one chain: nearest delta, wings beyond the short strike, usable sides only."""

import pytest

from coding.core.analytics.backtest.structures import (
    IRON_CONDOR, LONG_CALL, PUT_CREDIT_SPREAD, SHORT_STRANGLE, StructureParameters, Quote, select_legs)

SPOT = 70000.0


def chain():
    quotes = []
    for strike, put_delta, call_delta in ((56000, -0.03, 0.97), (60000, -0.07, 0.93), (63000, -0.12, 0.88), (65000, -0.17, 0.83),
                                           (70000, -0.45, 0.55), (75000, -0.80, 0.20), (77000, -0.88, 0.15), (80000, -0.95, 0.09),
                                           (85000, -0.98, 0.04)):
        quotes.append(Quote(f"P{strike}", float(strike), "put", 100.0 + strike / 1000, 110.0 + strike / 1000, put_delta))
        quotes.append(Quote(f"C{strike}", float(strike), "call", 100.0 + (90000 - strike) / 1000, 110.0 + (90000 - strike) / 1000, call_delta))
    return quotes


def test_short_strangle_sells_the_put_and_call_nearest_the_target_delta():
    legs, reason = select_legs(SHORT_STRANGLE, chain(), SPOT, StructureParameters(short_delta=0.16))
    assert reason == "" and [(leg.action, leg.option_type, leg.strike) for leg in legs] == [("sell", "put", 65000.0), ("sell", "call", 77000.0)]
    assert legs[0].entry_price_usd == 100.0 + 65.0                        # sold at the bid


def test_put_credit_spread_buys_a_wing_beyond_the_short_strike():
    legs, _ = select_legs(PUT_CREDIT_SPREAD, chain(), SPOT, StructureParameters(short_delta=0.16, wing_fraction=0.04))
    assert [(leg.action, leg.strike) for leg in legs] == [("sell", 65000.0), ("buy", 63000.0)]      # wanted 65,000 - 2,800 = 62,200: nearest quoted is 63,000
    assert legs[1].entry_price_usd == 110.0 + 63.0                        # bought at the ask


def test_iron_condor_has_four_legs_with_wings_outside_the_shorts():
    legs, _ = select_legs(IRON_CONDOR, chain(), SPOT, StructureParameters(short_delta=0.16, wing_fraction=0.05))
    by = {(leg.action, leg.option_type): leg.strike for leg in legs}
    assert len(legs) == 4 and by[("buy", "put")] < by[("sell", "put")] < by[("sell", "call")] < by[("buy", "call")]


def test_long_call_buys_near_the_long_delta():
    legs, _ = select_legs(LONG_CALL, chain(), SPOT, StructureParameters(long_delta=0.20))
    assert [(leg.action, leg.option_type, leg.strike) for leg in legs] == [("buy", "call", 75000.0)]


def test_an_option_without_the_needed_side_is_not_used():
    quotes = [quote if quote.strike != 65000.0 or quote.option_type != "put" else Quote(quote.instrument_name, quote.strike, "put", None, quote.ask_usd, quote.delta)
              for quote in chain()]
    legs, _ = select_legs(SHORT_STRANGLE, quotes, SPOT, StructureParameters(short_delta=0.16))
    assert legs[0].strike == 63000.0                                      # the 65,000 put has no bid: the next nearest delta


def test_a_chain_without_a_wing_gives_a_reason_not_a_made_up_leg():
    quotes = [quote for quote in chain() if not (quote.option_type == "put" and quote.strike < 65000)]
    legs, reason = select_legs(PUT_CREDIT_SPREAD, quotes, SPOT)
    assert legs is None and "no quoted put wing" in reason


def test_an_empty_chain_and_an_unknown_structure():
    assert select_legs(SHORT_STRANGLE, [], SPOT)[0] is None
    with pytest.raises(ValueError):
        select_legs("butterfly", chain(), SPOT)


def test_an_option_far_from_the_wanted_delta_is_not_used_instead_the_structure_is_skipped():
    only_far = [quote for quote in chain() if abs(quote.delta) > 0.4]                 # nothing near 0.16
    legs, reason = select_legs(SHORT_STRANGLE, only_far, SPOT, StructureParameters(short_delta=0.16))
    assert legs is None and "near the wanted delta" in reason
    legs, reason = select_legs(LONG_CALL, only_far, SPOT, StructureParameters(long_delta=0.05))
    assert legs is None and "near the wanted delta" in reason


def test_a_tie_in_delta_goes_to_the_lower_strike_so_a_rerun_picks_the_same_option():
    tied = [Quote("A", 60000.0, "put", 10.0, 11.0, -0.15), Quote("B", 62000.0, "put", 10.0, 11.0, -0.17)]
    legs, _ = select_legs(PUT_CREDIT_SPREAD, tied + [Quote("W", 50000.0, "put", 1.0, 2.0, -0.02)],
                          SPOT, StructureParameters(short_delta=0.16, wing_fraction=0.1))
    assert legs[0].strike == 60000.0
