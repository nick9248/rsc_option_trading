"""
The Strategy tab of the unified dashboard: the stored suggestions of the model, one card per expiry (pure: no files, no network).

A suggestion is research output of a language model, built from the facts of an analysis at ``facts_as_of``; it is checked and priced
by code but not scored against what happened. The panel says so, shows how old its facts are and marks old or rejected ones.
"""

import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.report.report_page import format_utc, paragraph_fragment, table_fragment

STALE_AFTER_HOURS = 4.0
"""Facts older than this are marked: the market has moved."""
RAW_TEXT_LIMIT = 600
"""Characters of an unusable answer that are shown."""

VIEW_LABELS = {"bullish": "Bullish", "bearish": "Bearish", "range_bound": "Range-bound", "long_volatility": "Long volatility",
               "short_volatility": "Short volatility", "no_trade": "No trade"}


@dataclass(frozen=True)
class StoredSuggestion:
    """One stored answer of the model, as read from ``strategy_suggestions``."""

    expiration: str
    computed_at: datetime
    facts_as_of: Optional[datetime]
    reference_price: float
    model: str
    status: str
    response: str
    net_premium_usd: Optional[float]


def _e(text: object) -> str:
    return html.escape(str(text))


def _age_text(hours: float) -> str:
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.1f} h"


def _premium_text(net_premium_usd: Optional[float]) -> str:
    if net_premium_usd is None:
        return "not priceable"
    return (f"{abs(net_premium_usd):,.0f} USD paid" if net_premium_usd > 0 else
            f"{abs(net_premium_usd):,.0f} USD received" if net_premium_usd < 0 else "0 USD")


def _list(items: Sequence[str]) -> str:
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>" if items else ""


def _card(suggestion: StoredSuggestion, now_utc: datetime) -> str:
    facts_time = suggestion.facts_as_of or suggestion.computed_at
    age_hours = max((now_utc - facts_time).total_seconds() / 3600.0, 0.0)
    warnings: List[str] = []
    if age_hours > STALE_AFTER_HOURS:
        warnings.append(f"The facts are {_age_text(age_hours)} old: the market has moved since. Ask again before relying on this.")
    if suggestion.facts_as_of is None:
        warnings.append("The time of the facts was not recorded; the time of the call is shown.")
    header = (f'<div class="card-head"><div class="card-title"><h3>{_e(suggestion.expiration)}</h3></div><div class="card-meta">'
              f'<span class="card-sub">facts of {_e(format_utc(facts_time))} ({_e(_age_text(age_hours))} old), spot '
              f'{suggestion.reference_price:,.0f}</span></div></div>')

    body = ""
    if suggestion.status in ("ok", "legs_rejected"):
        try:
            data = json.loads(suggestion.response)
            view = VIEW_LABELS.get(data["view"], data["view"])
            legs = data["legs"]
            title = view if data["strategy_name"].strip().lower() == view.lower() else f"{view}: {data['strategy_name']}"
            body += f"<h4>{_e(title)}</h4>" + paragraph_fragment(data["summary"])
            if legs:
                body += table_fragment(["Action", "Type", "Strike", "Quantity (coins)"],
                                       [[leg["action"], leg["option_type"], f"{leg['strike']:,.0f}", f"{leg['quantity']:g}"] for leg in legs],
                                       numeric_columns=(2, 3))
                body += paragraph_fragment("Net at the quotes of that moment (buys at the ask, sells at the bid): "
                                           + _premium_text(suggestion.net_premium_usd), "note-s")
            body += ("<h4>Why</h4>" + _list(data["reasons"]) + "<h4>Risks</h4>" + _list(data["risks"])
                     + "<h4>What would make it wrong</h4>" + paragraph_fragment(data["invalidation"]))
        except (ValueError, KeyError, TypeError):
            warnings.append("The stored answer could not be read.")
            body += paragraph_fragment(suggestion.response[:RAW_TEXT_LIMIT], "note-s")
        if suggestion.status == "legs_rejected":
            warnings.insert(0, "REJECTED by the check: a leg is not among the quoted strikes or has no quote for its fill. Do not use.")
    elif suggestion.status == "refused":
        warnings.insert(0, "The model declined to answer this request.")
        body += paragraph_fragment(suggestion.response[:RAW_TEXT_LIMIT], "note-s")
    else:
        warnings.insert(0, "The answer of the model was not usable (not in the expected form).")
        body += paragraph_fragment(suggestion.response[:RAW_TEXT_LIMIT], "note-s")

    warning_html = "".join(f'<p class="note-s" style="color:var(--warn)"><strong>{_e(text)}</strong></p>' for text in warnings)
    return f'<article class="card">{header}{warning_html}{body}<p class="note-s">Model {_e(suggestion.model)}.</p></article>'


def build_strategy_panel(suggestions: Sequence[StoredSuggestion], now_utc: datetime, currency: str,
                         read_error: Optional[str] = None) -> str:
    """
    The inner HTML of the Strategy tab.

    Args:
        suggestions: The newest stored suggestion of each expiry (any order); expired expiries are left out.
        now_utc: The current instant, timezone-aware UTC (ages are measured against it).
        currency: "BTC" or "ETH".
        read_error: Text when the stored suggestions could not be read (shown instead of an empty list).

    Raises:
        ValueError: for a naive ``now_utc``.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (UTC)")
    now = now_utc.astimezone(timezone.utc)
    intro = (f'<div class="panel-head"><h2>Strategy suggestions, {_e(currency)}</h2></div>'
             + paragraph_fragment("Research output of a language model, written from the facts of an analysis and checked by code: every leg must be "
                                  "a quoted strike, and the price is the cost at the bid and ask of that moment. They are not scored against what "
                                  "happened yet, some inputs are conventions (assumed dealer positioning), and nothing here places an order. "
                                  "A new suggestion is made on request (scripts/suggest_strategies.py, about 0.06 USD per expiry); it is never "
                                  "made automatically.", "note-s"))
    if read_error:
        return intro + paragraph_fragment(f"The stored suggestions could not be read: {read_error}", "note-s")
    open_suggestions: Dict[str, StoredSuggestion] = {}
    for suggestion in suggestions:
        days = MarketWideCalculator.calculate_dte(suggestion.expiration, now)
        if days is not None and days > 0 and (suggestion.expiration not in open_suggestions
                                              or suggestion.computed_at > open_suggestions[suggestion.expiration].computed_at):
            open_suggestions[suggestion.expiration] = suggestion
    if not open_suggestions:
        return intro + paragraph_fragment("No stored suggestion for an expiry that is still open.", "note-s")
    ordered = sorted(open_suggestions.values(), key=lambda s: MarketWideCalculator.calculate_dte(s.expiration, now) or 0.0)
    return intro + '<div class="cards">' + "".join(_card(suggestion, now) for suggestion in ordered) + "</div>"
