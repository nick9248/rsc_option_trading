"""
From every candidate of an expiry to the few the model is shown, and the text and schema of its answer (pure).

The shortlist is built by code and is the only choice the model has: it cannot propose a structure that is not on it. Hard filters remove
candidates whose costs eat too much of their risk; the rest are ranked by one deterministic rule (expected PnL per unit of maximum loss under
the realised-volatility distribution) and the best few of each family are kept, so the model sees different kinds of structure, not ten
copies of one.
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.candidates.generator import LONG_CALL, LONG_PUT, Candidate
from coding.core.analytics.candidates.metrics import CandidateMetrics
from coding.core.analytics.candidates.scoring import REALISED, robust_expected_per_risk

BASELINE_RULE = "robust_expected_pnl_per_risk"
"""Name of the deterministic rule that orders the shortlist and defines the baseline pick the model is compared with (it is not in the scoring lab's RULES: it needs three models)."""
EXCLUDED_FAMILIES = (LONG_CALL, LONG_PUT)
"""A single long option is a lottery (a win can be 50 times the risk): its mean is dominated by a few outcomes that 35 expiries a year cannot validate."""
PER_FAMILY = 2
MAX_COST_FRACTION_OF_RISK = 0.15
"""A candidate whose spread and fees exceed this share of its maximum loss is dropped (an illiquid quote, not a trade)."""
NO_TRADE = "no_trade"
VIEWS = ("bullish", "bearish", "range_bound", "long_volatility", "short_volatility", "no_trade")

PICK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "choice": {"type": "string"},
        "ranking": {"type": "array", "items": {"type": "string"}},
        "view": {"type": "string", "enum": list(VIEWS)},
        "summary": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
    },
    "required": ["choice", "ranking", "view", "summary", "reasons", "risks", "invalidation"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an options strategist reviewing ONE expiry of crypto options for a human trader. Code gives you (1) facts about the market \
and the expiry and (2) a shortlist of candidate structures that code built from the live chain and priced at the bid and ask. You choose one \
candidate by its id, or "no_trade".

Rules:
- You may only choose an id that is on the shortlist. Never propose another structure or other strikes.
- All candidate numbers are for one coin per leg. A buy fills at the ask, a sell at the bid; the costs shown are real. Code sizes the position \
from a loss budget, so do not discuss size.
- Each candidate has two views. The market view is exact: at the chain's own prices a structure is worth minus its costs (the spread paid against
the mid and the fees), so its expected PnL there is that, for every structure; it carries no opinion. The realised-volatility view is a lognormal with the
recent realised volatility: it ignores skew and fat tails and flatters short-premium structures. A clearly positive expected PnL under it is a hypothesis
that implied volatility is too high, not a fact; it is the only place a candidate can look better than its costs.
- Use the facts to decide whether the situation favours a structure: say which facts you rely on, by label and value. Several facts are \
conventions, not measurements (assumed dealer positioning, GEX signs). A fact marked "not recorded" is unknown; do not guess it.
- "ranking" lists every shortlisted id in your order of preference, best first. "choice" must equal the first entry of the ranking, or be \
"no_trade" when none is worth taking (then the ranking is still your order if you had to).
- Risks must include what the chosen structure loses if the view is wrong and what its costs are. "invalidation" states the level or event \
that would make the choice wrong.
- This is research for a human, never an order. Answer in the JSON schema only."""


@dataclass(frozen=True)
class ShortlistItem:
    """One candidate shown to the model."""

    candidate_id: str
    candidate: Candidate
    metrics: CandidateMetrics
    score: float
    """The baseline rule's score (higher is better)."""


def build_shortlist(scored: Sequence[Tuple[Candidate, CandidateMetrics]], per_family: int = PER_FAMILY,
                    max_cost_fraction: float = MAX_COST_FRACTION_OF_RISK) -> List[ShortlistItem]:
    """
    The candidates the model is shown: filtered, ranked by the baseline rule, the best ``per_family`` of each family, numbered C1, C2, ...
    by family and strikes (the order and the ids carry no ranking: the baseline's preference is not shown to the model).
    """
    rule = robust_expected_per_risk
    by_family: Dict[str, List[ShortlistItem]] = {}
    for candidate, metrics in scored:
        if candidate.family in EXCLUDED_FAMILIES:
            continue
        if metrics.max_loss_usd <= 0 or (metrics.spread_cost_usd + metrics.trading_fees_usd) > max_cost_fraction * metrics.max_loss_usd:
            continue
        score = rule(metrics)
        if score is None:
            continue
        by_family.setdefault(candidate.family, []).append(ShortlistItem("", candidate, metrics, score))
    chosen: List[ShortlistItem] = []
    for family in sorted(by_family):
        chosen += sorted(by_family[family], key=lambda item: -item.score)[:per_family]
    # Grouped by family and ordered by strikes: the order must not reveal the baseline's ranking to the model.
    chosen.sort(key=lambda item: (item.candidate.family, sorted(leg.strike for leg in item.candidate.legs)))
    return [ShortlistItem(f"C{number}", item.candidate, item.metrics, item.score) for number, item in enumerate(chosen, start=1)]


def baseline_id(shortlist: Sequence[ShortlistItem]) -> Optional[str]:
    """The id of the shortlisted candidate the baseline rule likes best (the pick the model must beat)."""
    return max(shortlist, key=lambda item: item.score).candidate_id if shortlist else None


def _usd(value: float) -> str:
    return f"{value:,.0f}"


def render_shortlist(shortlist: Sequence[ShortlistItem]) -> str:
    """The shortlist as text for the model."""
    lines = ["## Shortlist (USD; numbers are per coin of each leg; where a size is given, the fees and the maximum loss of that whole position are given too)"]
    for item in shortlist:
        metrics, candidate = item.metrics, item.candidate
        legs = ", ".join(f"{leg.action} {leg.strike:,.0f} {leg.option_type} @ {_usd(leg.entry_price_usd)}" for leg in candidate.legs)
        entry = f"credit {_usd(metrics.entry_cash_usd)}" if metrics.entry_cash_usd >= 0 else f"debit {_usd(-metrics.entry_cash_usd)}"
        profit = "unlimited" if metrics.max_profit_usd is None else _usd(metrics.max_profit_usd)
        breakevens = ", ".join(_usd(value) for value in metrics.breakevens) or "none"
        views = [f"market view: expected PnL {metrics.market_expected_pnl_usd:+,.0f} (minus its costs)"]
        result = metrics.by_distribution.get(REALISED)
        if result is not None:
            views.append(f"realised-vol view: P(profit) {result.probability_of_profit:.0%}, expected PnL {result.expected_pnl_usd:+,.0f}, "
                         f"tail loss {_usd(result.tail_loss_usd)}")
        lines.append(f"{item.candidate_id} {candidate.family}: {legs} | {entry} | max loss {_usd(metrics.max_loss_usd)}, max profit {profit}, "
                     f"breakeven {breakevens} | costs: spread {_usd(metrics.spread_cost_usd)} + fees {_usd(metrics.trading_fees_usd)} | "
                     + " | ".join(views)
                     + ("" if metrics.quantity is None else
                        f" | size {metrics.quantity:g} coins: max loss {metrics.quantity * metrics.max_loss_usd:,.2f} USD, "
                        f"fees {metrics.quantity * metrics.trading_fees_usd:,.2f} USD"))
    return "\n".join(lines)


def _finite(value: Optional[float]) -> Optional[float]:
    """A number as is; None for NaN or infinity (not valid JSON, and a stored record must never fail on one)."""
    return value if value is not None and math.isfinite(value) else None


def candidate_record(item: ShortlistItem) -> Dict[str, Any]:
    """The shortlist item as plain data for storage (non-finite numbers become None)."""
    metrics = item.metrics
    return {"id": item.candidate_id, "family": item.candidate.family,
            "legs": [{"action": leg.action, "option_type": leg.option_type, "strike": leg.strike, "instrument": leg.instrument_name,
                      "entry_price_usd": leg.entry_price_usd} for leg in item.candidate.legs],
            "entry_cash_usd": _finite(metrics.entry_cash_usd), "mid_cash_usd": _finite(item.candidate.mid_cash_usd),
            "max_loss_usd": _finite(metrics.max_loss_usd), "max_profit_usd": _finite(metrics.max_profit_usd),
            "breakevens": [_finite(value) for value in metrics.breakevens], "spread_cost_usd": _finite(metrics.spread_cost_usd),
            "trading_fees_usd": _finite(metrics.trading_fees_usd), "baseline_score": _finite(item.score), "quantity": _finite(metrics.quantity),
            "market_expected_pnl_usd": _finite(metrics.market_expected_pnl_usd),
            "distributions": {name: {"probability_of_profit": _finite(result.probability_of_profit),
                                     "expected_pnl_usd": _finite(result.expected_pnl_usd), "tail_loss_usd": _finite(result.tail_loss_usd)}
                              for name, result in metrics.by_distribution.items()}}


@dataclass(frozen=True)
class Pick:
    """The model's parsed answer."""

    choice: str
    ranking: Tuple[str, ...]
    view: str
    summary: str
    reasons: Tuple[str, ...]
    risks: Tuple[str, ...]
    invalidation: str


def parse_pick(text: str, valid_ids: Sequence[str]) -> Pick:
    """
    The model's answer.

    Raises:
        ValueError: when it is not JSON of the schema, the choice is neither a shortlisted id nor no_trade, the ranking is not exactly the
            shortlisted ids, or the choice is not the first of the ranking (unless it is no_trade).
    """
    try:
        data = json.loads(text)
        pick = Pick(data["choice"], tuple(data["ranking"]), data["view"], data["summary"], tuple(data["reasons"]), tuple(data["risks"]),
                    data["invalidation"])
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"the answer does not follow the schema: {error!r}") from error
    if not all(isinstance(value, str) for value in (pick.choice, pick.view, pick.summary, pick.invalidation)) or pick.view not in VIEWS:
        raise ValueError("choice, view, summary and invalidation must be text, and the view one of the listed")
    if sorted(pick.ranking) != sorted(valid_ids):
        raise ValueError("the ranking must list every shortlisted id exactly once")
    if pick.choice != NO_TRADE and (pick.choice not in valid_ids or pick.choice != pick.ranking[0]):
        raise ValueError(f"choice {pick.choice!r} is not no_trade or the first of the ranking")
    return pick
