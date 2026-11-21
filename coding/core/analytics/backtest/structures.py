"""
Simple option structures chosen by rule from one chain snapshot (nothing but that snapshot is looked at: no lookahead).

Strikes are chosen by Black-Scholes delta (the usual way a short-premium trader names a strike) and by a wing distance as a fraction of
spot. A buy is filled at the ask and a sell at the bid; an option without the needed side is not usable.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

SHORT_STRANGLE = "short_strangle"
PUT_CREDIT_SPREAD = "put_credit_spread"
IRON_CONDOR = "iron_condor"
LONG_CALL = "long_call"
LONG_PUT = "long_put"
STRUCTURES = (SHORT_STRANGLE, PUT_CREDIT_SPREAD, IRON_CONDOR, LONG_CALL, LONG_PUT)


@dataclass(frozen=True)
class StructureParameters:
    """The rule: how far out the short strikes are, how wide the wings, how far out a bought option is."""

    short_delta: float = 0.16
    """Absolute delta of the sold options (about a 16% chance of finishing in the money, roughly one standard deviation out)."""
    wing_fraction: float = 0.05
    """Distance of a protective wing from its short strike, as a fraction of spot."""
    long_delta: float = 0.30
    """Absolute delta of a bought call or put (long_call, long_put)."""


@dataclass(frozen=True)
class Quote:
    """One option of the chain at the moment of entry, in USD per coin."""

    instrument_name: str
    strike: float
    option_type: str
    """"call" or "put"."""
    bid_usd: Optional[float]
    ask_usd: Optional[float]
    delta: float


@dataclass(frozen=True)
class Leg:
    """One filled leg: buy at the ask or sell at the bid, one coin."""

    action: str
    option_type: str
    strike: float
    instrument_name: str
    entry_price_usd: float


def _nearest_delta(quotes: Sequence[Quote], option_type: str, target: float, action: str,
                   beyond: Optional[float] = None) -> Optional[Quote]:
    """The quote whose absolute delta is nearest the target and that has the side the fill needs (bid to sell, ask to buy)."""
    usable = [q for q in quotes if q.option_type == option_type and (q.bid_usd if action == "sell" else q.ask_usd)]
    if option_type == "call" and beyond is not None:
        usable = [q for q in usable if q.strike > beyond]
    if option_type == "put" and beyond is not None:
        usable = [q for q in usable if q.strike < beyond]
    return min(usable, key=lambda q: abs(abs(q.delta) - target)) if usable else None


def _wing(quotes: Sequence[Quote], short: Quote, spot: float, wing_fraction: float) -> Optional[Quote]:
    """The bought protection: the quoted strike nearest the wanted distance beyond the short strike, never at or inside it."""
    direction = -1 if short.option_type == "put" else 1
    wanted = short.strike + direction * wing_fraction * spot
    usable = [q for q in quotes if q.option_type == short.option_type and q.ask_usd and q.strike * direction > short.strike * direction]
    return min(usable, key=lambda q: abs(q.strike - wanted)) if usable else None


def _leg(action: str, quote: Quote) -> Leg:
    price = quote.bid_usd if action == "sell" else quote.ask_usd
    if not price:
        raise ValueError(f"{quote.instrument_name} has no {'bid' if action == 'sell' else 'ask'} to {action} at")
    return Leg(action, quote.option_type, quote.strike, quote.instrument_name, price)


def select_legs(structure: str, quotes: Sequence[Quote], spot: float,
                parameters: StructureParameters = StructureParameters()) -> Tuple[Optional[List[Leg]], str]:
    """
    The legs of a structure from the quotes of one expiry.

    Args:
        structure: One of ``STRUCTURES``.
        quotes: Every option of the expiry at the moment of entry (USD per coin).
        spot: Index price at that moment.
        parameters: The rule.

    Returns:
        (legs, "") or (None, reason) when the chain does not offer the structure (no quoted option near the wanted delta, no wing).

    Raises:
        ValueError: for an unknown structure.
    """
    if structure not in STRUCTURES:
        raise ValueError(f"unknown structure {structure!r}; choose from {STRUCTURES}")
    if structure in (LONG_CALL, LONG_PUT):
        option_type = "call" if structure == LONG_CALL else "put"
        bought = _nearest_delta(quotes, option_type, parameters.long_delta, "buy")
        return ([_leg("buy", bought)], "") if bought else (None, f"no quoted {option_type} to buy")

    legs: List[Leg] = []
    short_put = short_call = None
    if structure in (SHORT_STRANGLE, PUT_CREDIT_SPREAD, IRON_CONDOR):
        short_put = _nearest_delta(quotes, "put", parameters.short_delta, "sell")
        if short_put is None:
            return None, "no quoted put to sell"
    if structure in (SHORT_STRANGLE, IRON_CONDOR):
        short_call = _nearest_delta(quotes, "call", parameters.short_delta, "sell", beyond=short_put.strike if short_put else None)
        if short_call is None:
            return None, "no quoted call to sell above the put"
    for short in (short_put, short_call):
        if short is None:
            continue
        legs.append(_leg("sell", short))
        if structure != SHORT_STRANGLE:
            wing = _wing(quotes, short, spot, parameters.wing_fraction)
            if wing is None:
                return None, f"no quoted {short.option_type} wing beyond {short.strike:,.0f}"
            legs.append(_leg("buy", wing))
    return legs, ""
