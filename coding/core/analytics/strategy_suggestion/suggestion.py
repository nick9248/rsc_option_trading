"""
A strategy suggestion of the model: its schema, parsing, checking against the chain, and pricing of its legs in code.

The model proposes; code checks. Every leg must exist in the chain of the facts it was shown and be priced from that chain's quotes (a
buy at the ask, a sell at the bid). Nothing here places an order.
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.strategy_suggestion.expiry_facts import LegQuote

VIEWS = ("bullish", "bearish", "range_bound", "long_volatility", "short_volatility", "no_trade")
ACTIONS = ("buy", "sell")
OPTION_TYPES = ("call", "put")

SUGGESTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "view": {"type": "string", "enum": list(VIEWS)},
        "strategy_name": {"type": "string"},
        "summary": {"type": "string"},
        "legs": {"type": "array", "items": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": list(ACTIONS)},
                           "option_type": {"type": "string", "enum": list(OPTION_TYPES)},
                           "strike": {"type": "number"}, "quantity": {"type": "number"}},
            "required": ["action", "option_type", "strike", "quantity"], "additionalProperties": False}},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
    },
    "required": ["view", "strategy_name", "summary", "legs", "reasons", "risks", "invalidation"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class StrategyLeg:
    """One leg: buy or sell ``quantity`` coins of one option of the expiry."""

    action: str
    option_type: str
    strike: float
    quantity: float


@dataclass(frozen=True)
class StrategySuggestion:
    """What the model proposed for one expiry."""

    view: str
    strategy_name: str
    summary: str
    legs: Tuple[StrategyLeg, ...]
    reasons: Tuple[str, ...]
    risks: Tuple[str, ...]
    invalidation: str


@dataclass(frozen=True)
class LegPricing:
    """The legs priced from the chain: what is paid (positive) or received (negative) at the quotes."""

    net_premium_usd: Optional[float]
    """None when any leg could not be priced, or when there are no legs (no trade)."""
    problems: Tuple[str, ...]
    """Legs that are not in the chain or have no quote on the side the fill needs; empty when everything is priced."""


def parse_suggestion(text: str) -> StrategySuggestion:
    """
    The suggestion in the model's JSON answer.

    Raises:
        ValueError: when the text is not JSON of the schema, or a view, action or option type is unknown, a quantity is not positive,
            or a trade has no legs.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"the answer is not JSON: {error}") from error
    try:
        legs = tuple(StrategyLeg(leg["action"], leg["option_type"], float(leg["strike"]), float(leg["quantity"]))
                     for leg in data["legs"])
        suggestion = StrategySuggestion(data["view"], data["strategy_name"], data["summary"], legs,
                                        tuple(data["reasons"]), tuple(data["risks"]), data["invalidation"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"the answer does not follow the schema: {error!r}") from error
    if not isinstance(data["legs"], list) or not all(isinstance(data[key], list) and all(isinstance(item, str) for item in data[key])
                                                       for key in ("reasons", "risks")):
        raise ValueError("legs, reasons and risks must be lists (reasons and risks of text)")
    if not all(isinstance(data[key], str) for key in ("view", "strategy_name", "summary", "invalidation")):
        raise ValueError("view, strategy_name, summary and invalidation must be text")
    if suggestion.view not in VIEWS:
        raise ValueError(f"unknown view {suggestion.view!r}")
    for leg in suggestion.legs:
        if leg.action not in ACTIONS or leg.option_type not in OPTION_TYPES:
            raise ValueError(f"unknown leg {leg}")
        if not (math.isfinite(leg.quantity) and math.isfinite(leg.strike)) or leg.quantity <= 0 or leg.strike <= 0:
            raise ValueError(f"leg without a finite, positive quantity and strike: {leg}")
    options = [(leg.strike, leg.option_type) for leg in suggestion.legs]
    if len(set(options)) != len(options):
        raise ValueError("the same option appears in two legs: merge them or drop the offsetting one")
    if suggestion.view != "no_trade" and not suggestion.legs:
        raise ValueError("a trade needs at least one leg")
    return suggestion


def price_legs(suggestion: StrategySuggestion, quotes: Dict[Tuple[float, str], LegQuote]) -> LegPricing:
    """Price the legs: a buy pays the ask, a sell receives the bid; a missing option or side is reported, never taken as 0."""
    if not suggestion.legs:
        return LegPricing(None, ())                   # no trade: nothing to price, not a premium of 0
    total = 0.0
    problems: List[str] = []
    for leg in suggestion.legs:
        label = f"{leg.action} {leg.quantity:g} {leg.option_type} {leg.strike:,.0f}"
        quote = quotes.get((leg.strike, leg.option_type))
        if quote is None:
            problems.append(f"{label}: not among the strikes shown")
            continue
        price = quote.ask_usd if leg.action == "buy" else quote.bid_usd
        if price is None:
            problems.append(f"{label}: no {'ask' if leg.action == 'buy' else 'bid'}")
            continue
        total += price * leg.quantity * (1 if leg.action == "buy" else -1)
    return LegPricing(None if problems else total, tuple(problems))
