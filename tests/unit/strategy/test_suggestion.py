"""Parsing, checking and pricing of the model's suggestion (pure code, hand-computed prices)."""

import json

import pytest

from coding.core.analytics.strategy_suggestion.expiry_facts import LegQuote
from coding.core.analytics.strategy_suggestion.suggestion import StrategyLeg, parse_suggestion, price_legs


def answer(**overrides):
    data = {"view": "range_bound", "strategy_name": "put credit spread", "summary": "s",
            "legs": [{"action": "sell", "option_type": "put", "strike": 55000, "quantity": 1},
                     {"action": "buy", "option_type": "put", "strike": 53000, "quantity": 1}],
            "reasons": ["r"], "risks": ["k"], "invalidation": "below 53000", **overrides}
    return json.dumps(data)


QUOTES = {(55000.0, "put"): LegQuote(813.0, 842.0, 827.0, 70.7, 1417.0),
          (53000.0, "put"): LegQuote(465.0, 523.0, 494.0, 76.7, 1304.0),
          (60000.0, "call"): LegQuote(None, 959.0, 930.0, 58.4, 842.0)}


def test_a_valid_answer_is_parsed():
    suggestion = parse_suggestion(answer())
    assert suggestion.view == "range_bound" and len(suggestion.legs) == 2
    assert suggestion.legs[0] == StrategyLeg("sell", "put", 55000.0, 1.0)


@pytest.mark.parametrize("text", ["not json", "{}", answer(view="moon"), answer(legs=[]),
                                  answer(legs=[{"action": "hold", "option_type": "put", "strike": 1, "quantity": 1}]),
                                  answer(legs=[{"action": "buy", "option_type": "put", "strike": 55000, "quantity": 0}])])
def test_a_bad_answer_is_refused(text):
    with pytest.raises(ValueError):
        parse_suggestion(text)


def test_no_trade_may_have_no_legs():
    assert parse_suggestion(answer(view="no_trade", legs=[])).legs == ()


def test_a_buy_pays_the_ask_and_a_sell_receives_the_bid():
    pricing = price_legs(parse_suggestion(answer()), QUOTES)
    # sell 55,000 put at the bid 813, buy 53,000 put at the ask 523: receive 290
    assert pricing.net_premium_usd == -290.0 and pricing.problems == ()


def test_quantity_scales_the_premium():
    legs = [{"action": "sell", "option_type": "put", "strike": 55000, "quantity": 2.5}]
    assert price_legs(parse_suggestion(answer(legs=legs)), QUOTES).net_premium_usd == -813.0 * 2.5


def test_a_leg_not_in_the_chain_is_reported_and_nothing_is_priced():
    legs = [{"action": "sell", "option_type": "put", "strike": 54500, "quantity": 1}]
    pricing = price_legs(parse_suggestion(answer(legs=legs)), QUOTES)
    assert pricing.net_premium_usd is None and "not in the chain" in pricing.problems[0]


def test_a_missing_side_is_reported_not_taken_as_zero():
    legs = [{"action": "sell", "option_type": "call", "strike": 60000, "quantity": 1}]            # no bid
    pricing = price_legs(parse_suggestion(answer(legs=legs)), QUOTES)
    assert pricing.net_premium_usd is None and "no bid" in pricing.problems[0]
