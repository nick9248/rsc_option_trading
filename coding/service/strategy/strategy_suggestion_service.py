"""
Asks the model for a strategy per expiry from the facts the code prepared, checks the answer against the chain, prices its legs and
stores everything (point in time) for a later evaluation. Orchestration only: the facts, the schema and the pricing are core code.

Safety: the service never places an order (there is no code path to one); every call is paid, so the total cost of all stored calls is
checked against a budget before each call and the cost of each call is stored.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from coding.core.analytics.results.analysis_result import OnChainAnalysisResult
from coding.core.analytics.strategy_suggestion.expiry_facts import ExpiryFacts, build_expiry_facts
from coding.core.analytics.strategy_suggestion.suggestion import (
    SUGGESTION_SCHEMA, LegPricing, StrategySuggestion, parse_suggestion, price_legs)
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_BUDGET_USD = 15.0
"""Stop asking when the stored calls have cost this much (the account holds 18 USD; the rest is a margin)."""
MAX_TOKENS = 12000
"""Output limit of one call (the thinking of the model counts as output)."""
WORST_CASE_CALL_USD = 0.60
"""Reserved before a call: the total plus this must stay within the budget."""
EFFORT = "medium"

PRICES_USD_PER_MILLION_TOKENS: Dict[str, Tuple[float, float, float, float]] = {
    # model: (input, output, cache write, cache read)
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.2),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.1),
}

SYSTEM_PROMPT = """You are an options strategist reviewing one expiry of Deribit crypto options for a human trader. You are given facts \
computed by code: market state, key levels, statistics, flow, skew and the chain with USD quotes.

Rules:
- Use only the facts given. Do not use outside knowledge of today's market. If the facts are thin or contradict each other, say so; \
"no_trade" with empty legs is a valid answer.
- Every leg must be a strike listed in the chain table, of this expiry, with the quote needed to fill it (a buy needs an ask, a sell a bid). \
Quantities are in coins. Prefer structures with a defined maximum loss; if you propose an undefined-risk structure, say so in the risks.
- Reasons must cite the facts by their label and value. Risks must include what the structure loses if the view is wrong, and the cost of the \
spread (bid/ask) where it matters. "invalidation" states the level or event that would make the view wrong.
- Several facts are conventions and not measurements (assumed dealer positioning, GEX signs). Treat them as such.
- This is research for a human, never an order. Answer in the JSON schema only."""


@dataclass(frozen=True)
class SuggestionOutcome:
    """The result of one call."""

    row_id: int
    status: str
    suggestion: Optional[StrategySuggestion]
    pricing: Optional[LegPricing]
    response_text: str
    cost_usd: float
    facts: ExpiryFacts


class BudgetExceeded(RuntimeError):
    """The stored calls have used the budget; no further call is made."""


def call_cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_write_tokens: int) -> float:
    """
    What one call cost at list prices.

    Raises:
        ValueError: for a model without a known price (a cost is never guessed).
    """
    if model not in PRICES_USD_PER_MILLION_TOKENS:
        raise ValueError(f"no price known for model {model!r}")
    price_input, price_output, price_write, price_read = PRICES_USD_PER_MILLION_TOKENS[model]
    return (input_tokens * price_input + output_tokens * price_output + cache_write_tokens * price_write
            + cache_read_tokens * price_read) / 1_000_000


class StrategySuggestionService:
    """One call per expiry: facts in, checked and priced suggestion out, all stored."""

    def __init__(self, repository: DatabaseRepository, client: Any, model: str = DEFAULT_MODEL, budget_usd: float = DEFAULT_BUDGET_USD):
        """
        Args:
            repository: Local database (suggestions and the running cost are stored there).
            client: An ``anthropic.Anthropic`` client (a fake in tests).
            model: Model id; must have a price in ``PRICES_USD_PER_MILLION_TOKENS``.
            budget_usd: Stop when the stored calls have cost this much.

        Raises:
            ValueError: for a model without a known price.
        """
        if model not in PRICES_USD_PER_MILLION_TOKENS:
            raise ValueError(f"no price known for model {model!r}")
        self.repository = repository
        self.client = client
        self.model = model
        self.budget_usd = budget_usd

    def suggest(self, result: OnChainAnalysisResult, expiration: str, now_utc: Optional[datetime] = None) -> SuggestionOutcome:
        """
        Ask for a strategy for one expiry, check it, price it and store it.

        Raises:
            BudgetExceeded: before the call, when the stored cost plus the reserved worst case would pass the budget.
            LookupError: when the expiry has no facts (not in the result, or no quotes).
        """
        facts = build_expiry_facts(result, expiration)
        spent = self.repository.get_strategy_suggestion_total_cost()
        if spent + WORST_CASE_CALL_USD > self.budget_usd:
            raise BudgetExceeded(f"stored calls cost {spent:.2f} USD; budget {self.budget_usd:.2f} USD, "
                                 f"{WORST_CASE_CALL_USD:.2f} USD reserved per call")
        now = now_utc or datetime.now(timezone.utc)
        logger.info("Asking %s for %s %s (spent so far %.2f of %.2f USD)", self.model, result.currency, expiration, spent, self.budget_usd)
        response = self.client.messages.create(
            model=self.model, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": facts.text + "\n\nPropose the best strategy for this expiry, or no_trade."}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SUGGESTION_SCHEMA}})
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = call_cost_usd(self.model, usage.input_tokens, usage.output_tokens, cache_read, cache_write)
        text = "".join(block.text for block in response.content if block.type == "text")

        suggestion: Optional[StrategySuggestion] = None
        pricing: Optional[LegPricing] = None
        if response.stop_reason == "refusal":
            status = "refused"
            category = getattr(getattr(response, "stop_details", None), "category", None)
            text = text or f"refused ({category})"
        else:
            try:
                suggestion = parse_suggestion(text)
                pricing = price_legs(suggestion, facts.quotes)
                status = "ok" if not pricing.problems else "legs_rejected"
            except ValueError as error:
                status = "invalid_response"
                logger.warning("The answer of the model is not usable: %s", error)
        if pricing is not None and pricing.problems:
            logger.warning("Legs rejected: %s", "; ".join(pricing.problems))

        row_id = self.repository.save_strategy_suggestion({
            "currency": result.currency, "expiration": expiration, "computed_at": now, "reference_price": facts.spot,
            "model": self.model, "status": status, "facts": facts.text, "response": text,
            "net_premium_usd": None if pricing is None else pricing.net_premium_usd,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write, "cost_usd": cost, "request_id": getattr(response, "_request_id", None)})
        logger.info("Suggestion %d stored: %s, cost %.4f USD", row_id, status, cost)
        return SuggestionOutcome(row_id, status, suggestion, pricing, text, cost, facts)
