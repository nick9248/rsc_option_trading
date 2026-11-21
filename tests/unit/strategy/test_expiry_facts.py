"""Facts of one expiry: from the real offline analysis result; every quote comes from the chain with the index price."""

import pytest

from coding.core.analytics.onchain_dashboard.expiry_section import ordered_bundles
from coding.core.analytics.strategy_suggestion.expiry_facts import build_expiry_facts


def test_the_facts_hold_the_sections_the_model_needs(real_result):
    bundle = next(b for b in ordered_bundles(real_result) if b.enriched_instruments)
    facts = build_expiry_facts(real_result, bundle.expiration)

    for title in ("## Market", "## Key levels of this expiry", "## Statistics of this expiry", "## Chain"):
        assert title in facts.text
    assert facts.expiration == bundle.expiration and facts.spot == real_result.underlying_price
    assert facts.text.count("\nDVOL |") == 1                           # a label is written once


def test_quotes_are_usd_per_coin_from_the_chain(real_result):
    bundle = next(b for b in ordered_bundles(real_result) if b.enriched_instruments)
    facts = build_expiry_facts(real_result, bundle.expiration)
    instrument = next(i for i in bundle.enriched_instruments if i.get("ask_price"))
    kind = "call" if instrument["option_type"] == "C" else "put"

    quote = facts.quotes[(float(instrument["strike"]), kind)]

    assert quote.ask_usd == pytest.approx(instrument["ask_price"] * real_result.underlying_price)
    assert all(q.bid_usd != 0 and q.ask_usd != 0 for q in facts.quotes.values())       # missing is None, never 0


def test_an_unknown_expiry_is_an_error(real_result):
    with pytest.raises(LookupError):
        build_expiry_facts(real_result, "01JAN99")
