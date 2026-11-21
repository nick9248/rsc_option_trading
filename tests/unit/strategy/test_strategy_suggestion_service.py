"""The service with a fake client and a fake repository: no network, no paid call."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.onchain_dashboard.expiry_section import ordered_bundles
from coding.core.analytics.strategy_suggestion.expiry_facts import build_expiry_facts
from coding.service.strategy.strategy_suggestion_service import (
    BudgetExceeded, StrategySuggestionService, call_cost_usd)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
FACTS_AS_OF = datetime(2026, 11, 21, 11, 30, tzinfo=timezone.utc)


def response(text, stop_reason="end_turn", input_tokens=3000, output_tokens=3000, cache_read=0, cache_write=0):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason, stop_details=None,
                           usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                                 cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write),
                           _request_id="req_1")


def make_service(real_result, reply, spent=0.0, budget=15.0):
    client = MagicMock()
    client.messages.create.return_value = reply
    repository = MagicMock()
    repository.get_strategy_suggestion_total_cost.return_value = spent
    repository.save_strategy_suggestion.return_value = 7
    return StrategySuggestionService(repository, client, budget_usd=budget), client, repository


def real_legs(real_result):
    """Two real quoted options of the first expiry, so the answer can be priced."""
    bundle = next(b for b in ordered_bundles(real_result) if b.enriched_instruments)
    facts = build_expiry_facts(real_result, bundle.expiration)
    priced = [(k, q) for k, q in facts.quotes.items() if q.bid_usd and q.ask_usd]
    return bundle.expiration, facts, priced[:2]


def answer_for(priced, view="range_bound"):
    (k1, _), (k2, _) = priced
    return json.dumps({"view": view, "strategy_name": "x", "summary": "s", "reasons": ["r"], "risks": ["k"], "invalidation": "i",
                       "legs": [{"action": "sell", "option_type": k1[1], "strike": k1[0], "quantity": 1},
                                {"action": "buy", "option_type": k2[1], "strike": k2[0], "quantity": 1}]})


def test_cost_is_computed_from_the_usage_at_list_prices():
    # 3000 in at 4, 3000 out at 20, 1000 cache read at 0.2, 500 cache write at 5 (USD per million tokens)
    assert call_cost_usd("claude-opus-5-5", 3000, 3000, 1000, 500) == pytest.approx((12000 + 60000 + 200 + 2500) / 1_000_000)
    with pytest.raises(ValueError):
        call_cost_usd("claude-unknown", 1, 1, 0, 0)


def test_a_good_answer_is_stored_priced_with_its_cost(real_result):
    expiration, facts, priced = real_legs(real_result)
    service, client, repository = make_service(real_result, response(answer_for(priced)))

    outcome = service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW)

    assert outcome.status == "ok" and outcome.row_id == 7 and outcome.pricing.net_premium_usd is not None
    row = repository.save_strategy_suggestion.call_args.args[0]
    assert row["status"] == "ok" and row["computed_at"] == NOW and row["facts_as_of"] == FACTS_AS_OF and row["expiration"] == expiration
    assert row["facts"] == facts.text and row["cost_usd"] == pytest.approx(outcome.cost_usd) and row["request_id"] == "req_1"
    call = client.messages.create.call_args.kwargs
    assert call["model"] == "claude-opus-5-5" and "tools" not in call and call["output_config"]["format"]["type"] == "json_schema"


def test_a_leg_that_is_not_in_the_chain_is_stored_as_rejected(real_result):
    expiration, _, _ = real_legs(real_result)
    bad = json.dumps({"view": "bullish", "strategy_name": "x", "summary": "s", "reasons": [], "risks": [], "invalidation": "i",
                      "legs": [{"action": "buy", "option_type": "call", "strike": 12345, "quantity": 1}]})
    service, _, repository = make_service(real_result, response(bad))

    outcome = service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW)

    assert outcome.status == "legs_rejected" and outcome.pricing.net_premium_usd is None
    assert repository.save_strategy_suggestion.call_args.args[0]["net_premium_usd"] is None


def test_an_answer_that_is_not_json_is_stored_as_invalid_with_its_cost(real_result):
    expiration, _, _ = real_legs(real_result)
    service, _, repository = make_service(real_result, response("I think you should buy calls"))

    outcome = service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW)

    assert outcome.status == "invalid_response" and outcome.cost_usd > 0
    assert repository.save_strategy_suggestion.call_args.args[0]["response"] == "I think you should buy calls"


def test_a_refusal_is_stored_as_refused(real_result):
    expiration, _, _ = real_legs(real_result)
    service, _, repository = make_service(real_result, response("", stop_reason="refusal"))
    assert service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW).status == "refused"


def test_the_budget_stops_the_call_before_it_is_made(real_result):
    expiration, _, _ = real_legs(real_result)
    service, client, repository = make_service(real_result, response("x"), spent=14.9, budget=15.0)

    with pytest.raises(BudgetExceeded):
        service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW)

    client.messages.create.assert_not_called()
    repository.save_strategy_suggestion.assert_not_called()


def test_a_response_of_an_unexpected_shape_is_still_stored_with_its_cost(real_result):
    expiration, _, _ = real_legs(real_result)
    odd = response("x")
    odd.content = [SimpleNamespace(type="text")]                         # a text block without text
    service, _, repository = make_service(real_result, odd)

    outcome = service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW)

    assert outcome.status == "invalid_response" and outcome.cost_usd > 0
    repository.save_strategy_suggestion.assert_called_once()


def test_an_answer_cut_at_the_token_limit_is_invalid_not_parsed(real_result):
    expiration, _, priced = real_legs(real_result)
    service, _, _ = make_service(real_result, response(answer_for(priced), stop_reason="max_tokens"))
    assert service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW).status == "invalid_response"


def test_a_naive_facts_time_is_refused(real_result):
    expiration, _, _ = real_legs(real_result)
    service, client, _ = make_service(real_result, response("x"))
    with pytest.raises(ValueError):
        service.suggest(real_result, expiration, datetime(2026, 11, 21, 11, 30))
    client.messages.create.assert_not_called()


def test_a_strike_that_exists_but_was_not_shown_is_rejected(real_result, monkeypatch):
    monkeypatch.setattr("coding.core.analytics.strategy_suggestion.expiry_facts.CHAIN_ROWS", 2)       # show only the 2 strikes nearest spot
    expiration, facts, _ = real_legs(real_result)
    bundle = real_result.bundle(expiration)
    hidden = next(float(i["strike"]) for i in bundle.enriched_instruments
                  if float(i["strike"]) not in {key[0] for key in facts.quotes} and i.get("ask_price"))
    bad = json.dumps({"view": "bullish", "strategy_name": "x", "summary": "s", "reasons": [], "risks": [], "invalidation": "i",
                      "legs": [{"action": "buy", "option_type": "call", "strike": hidden, "quantity": 1}]})
    service, _, _ = make_service(real_result, response(bad))
    assert service.suggest(real_result, expiration, FACTS_AS_OF, now_utc=NOW).status == "legs_rejected"


def test_a_model_without_a_known_price_is_refused():
    with pytest.raises(ValueError):
        StrategySuggestionService(MagicMock(), MagicMock(), model="claude-unknown")
