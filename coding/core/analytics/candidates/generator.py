"""
Every defined-risk structure a chain offers (pure: no database, no network).

Families: long call, long put, call and put debit spreads, call and put credit spreads, iron condors. Strikes are chosen among the quoted
options (a buy needs an ask, a sell a bid) by delta range for the main leg and by distance (fractions of spot) for the other leg. The
result is a list of candidates the scoring step ranks; nothing here decides which one is good.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from coding.core.analytics.backtest.structures import Leg, Quote

LONG_CALL = "long_call"
LONG_PUT = "long_put"
CALL_DEBIT_SPREAD = "call_debit_spread"
PUT_DEBIT_SPREAD = "put_debit_spread"
CALL_CREDIT_SPREAD = "call_credit_spread"
PUT_CREDIT_SPREAD = "put_credit_spread"
IRON_CONDOR = "iron_condor"
FAMILIES = (LONG_CALL, LONG_PUT, CALL_DEBIT_SPREAD, PUT_DEBIT_SPREAD, CALL_CREDIT_SPREAD, PUT_CREDIT_SPREAD, IRON_CONDOR)


@dataclass(frozen=True)
class CandidateLimits:
    """Which strikes and widths are tried."""

    short_delta_range: Tuple[float, float] = (0.05, 0.45)
    """Absolute delta of a sold option."""
    long_delta_range: Tuple[float, float] = (0.15, 0.60)
    """Absolute delta of the main bought option (long options and debit spreads)."""
    width_fractions: Tuple[float, ...] = (0.02, 0.04, 0.06, 0.10)
    """Distance between the two strikes of a spread, as fractions of spot (snapped to the nearest quoted strike)."""


@dataclass(frozen=True)
class Candidate:
    """One structure, one coin per leg, at the touch (buys at the ask, sells at the bid)."""

    family: str
    legs: Tuple[Leg, ...]
    entry_cash_usd: float
    """Cash at entry at the touch: positive = credit received, negative = debit paid."""
    mid_cash_usd: float
    """The same at the mid of each leg's bid and ask (the touch where one side is missing)."""

    @property
    def key(self) -> frozenset:
        """The legs, for removing duplicates."""
        return frozenset((leg.action, leg.option_type, leg.strike) for leg in self.legs)


def _in_range(quote: Quote, bounds: Tuple[float, float]) -> bool:
    return bounds[0] <= abs(quote.delta) <= bounds[1]


def _price(quote: Quote, action: str) -> Optional[float]:
    return (quote.bid_usd if action == "sell" else quote.ask_usd) or None


def _mid(quote: Quote, touch_price: float) -> float:
    """The mid of bid and ask; the touch price where one side is missing (no spread cost can be known then)."""
    return (quote.bid_usd + quote.ask_usd) / 2.0 if quote.bid_usd and quote.ask_usd else touch_price


def _build(family: str, legs: Sequence[Tuple[str, Quote]]) -> Optional[Candidate]:
    built: List[Leg] = []
    entry = mid = 0.0
    for action, quote in legs:
        price = _price(quote, action)
        if price is None:
            return None
        sign = 1.0 if action == "sell" else -1.0
        entry += sign * price
        mid += sign * _mid(quote, price)
        built.append(Leg(action, quote.option_type, quote.strike, quote.instrument_name, price))
    return Candidate(family, tuple(built), entry, mid)


def _nearest(quotes: Sequence[Quote], strike: float, direction: int, base: float) -> Optional[Quote]:
    """The quote nearest to ``strike`` that lies strictly beyond ``base`` in the given direction (+1 above, -1 below)."""
    beyond = [quote for quote in quotes if (quote.strike - base) * direction > 0]
    return min(beyond, key=lambda quote: (abs(quote.strike - strike), quote.strike)) if beyond else None


def generate_candidates(quotes: Iterable[Quote], spot: float, limits: CandidateLimits = CandidateLimits()) -> List[Candidate]:
    """
    All candidates of the families above that the quotes allow, without duplicates.

    Args:
        quotes: Every option of one expiry (USD per coin, with delta).
        spot: Index price.
        limits: Delta ranges and widths.
    """
    by_type: Dict[str, List[Quote]] = {"call": [], "put": []}
    for quote in quotes:
        by_type[quote.option_type].append(quote)
    for series in by_type.values():
        series.sort(key=lambda quote: quote.strike)
    found: Dict[frozenset, Candidate] = {}

    def add(candidate: Optional[Candidate]) -> None:
        if candidate is not None and candidate.key not in found:
            found[candidate.key] = candidate

    for kind, family in (("call", LONG_CALL), ("put", LONG_PUT)):
        for quote in by_type[kind]:
            if _in_range(quote, limits.long_delta_range):
                add(_build(family, [("buy", quote)]))

    spreads: Dict[Tuple[str, float], List[Candidate]] = {}
    for kind in ("call", "put"):
        direction = 1 if kind == "call" else -1             # the second leg lies above a call's first strike, below a put's
        for width_fraction in limits.width_fractions:
            for quote in by_type[kind]:
                other = _nearest(by_type[kind], quote.strike + direction * width_fraction * spot, direction, quote.strike)
                if other is None:
                    continue
                if _in_range(quote, limits.short_delta_range):                       # sell the nearer strike, buy the farther: credit
                    credit = _build(f"{kind}_credit_spread", [("sell", quote), ("buy", other)])
                    add(credit)
                    if credit is not None:
                        spreads.setdefault((kind, width_fraction), []).append(credit)
                if _in_range(quote, limits.long_delta_range):                        # buy the nearer strike, sell the farther: debit
                    add(_build(f"{kind}_debit_spread", [("buy", quote), ("sell", other)]))

    for width_fraction in limits.width_fractions:                                    # iron condor: a put and a call credit spread
        for put_spread in spreads.get(("put", width_fraction), []):
            for call_spread in spreads.get(("call", width_fraction), []):
                put_short = next(leg for leg in put_spread.legs if leg.action == "sell")
                call_short = next(leg for leg in call_spread.legs if leg.action == "sell")
                if put_short.strike < call_short.strike:
                    found.setdefault(frozenset(put_spread.key | call_spread.key), Candidate(
                        IRON_CONDOR, put_spread.legs + call_spread.legs, put_spread.entry_cash_usd + call_spread.entry_cash_usd,
                        put_spread.mid_cash_usd + call_spread.mid_cash_usd))
    return list(found.values())
