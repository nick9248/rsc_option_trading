"""
The Strategy tab of the unified dashboard: the newest stored live pick of each expiry, one card per expiry (pure: no files, no network).

A pick is the answer of a language model that could only choose among candidate structures built and priced by code, from facts of an analysis at
``as_of``. It is sized by code from a loss budget and not yet scored against what happens. The card shows what was chosen, why, how it pays at
expiry, and the whole shortlist in the model's own order, so the choice can be judged against the alternatives.
"""

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.charts.payoff import PayoffLeg, payoff_figure, summarize_payoff
from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.report.figure_style import figure_json
from coding.core.analytics.report.report_page import format_utc, paragraph_fragment, table_fragment

STALE_AFTER_HOURS = 4.0
"""A pick older than this is marked: the market has moved. The check is by time only; the spot of the pick is shown next to the age."""
MAX_SELECTED_TRADES = 2
"""At most this many picks are marked as the trades to take: the expiries share one view of the market, so more of them is the same bet again."""
ASKED_NOTE = ("The model is asked about the expiries nearest 3, 7, 14 and 30 days only (each ask costs about 0.05 USD); every other expiry is not asked, "
              "which is not a no-trade answer.")
RAW_TEXT_LIMIT = 600
"""Characters of an unusable answer that are shown."""

VIEW_LABELS = {"bullish": "Bullish", "bearish": "Bearish", "range_bound": "Range-bound", "long_volatility": "Long volatility",
               "short_volatility": "Short volatility", "no_trade": "No trade"}


@dataclass(frozen=True)
class StoredPick:
    """One stored live pick, as read from ``strategy_picks``."""

    expiration: str
    as_of: datetime
    spot: float
    model: str
    status: str
    candidates: List[Dict[str, Any]]
    choice: Optional[str]
    ranking: Optional[List[str]]
    response: str
    quantity: Optional[float]
    max_loss_usd: Optional[float]
    risk_per_trade_usd: Optional[float]
    baseline_id: Optional[str]
    cost_usd: float


@dataclass(frozen=True)
class StrategyPanel:
    """The Strategy tab: its inner HTML and the figures (Plotly JSON by id) its cards draw."""

    html: str
    figures: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _e(text: object) -> str:
    return html.escape(str(text))


def _age_text(hours: float) -> str:
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.1f} h"


def _usd(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _legs_text(candidate: Dict[str, Any]) -> str:
    return " / ".join(f"{leg['action']} {leg['strike']:,.0f} {leg['option_type']}" for leg in candidate["legs"])


def _list(items: Sequence[str]) -> str:
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>" if items else ""


def _entry_text(entry_cash_usd: float, quantity: float) -> str:
    cash = entry_cash_usd * quantity
    return f"credit {_usd(cash)} USD" if cash >= 0 else f"debit {_usd(-cash)} USD"


def _shortlist_table(pick: StoredPick) -> str:
    """The shortlist in the model's own order (best first), with the numbers the model was shown."""
    by_id = {candidate["id"]: candidate for candidate in pick.candidates}
    order = [identifier for identifier in (pick.ranking or []) if identifier in by_id] or list(by_id)
    rows = []
    for rank, identifier in enumerate(order, start=1):
        candidate = by_id[identifier]
        realised = candidate.get("distributions", {}).get("realised", {})
        probability = realised.get("probability_of_profit")
        expected = realised.get("expected_pnl_usd")
        marks = " (chosen)" if identifier == pick.choice else " (baseline)" if identifier == pick.baseline_id else ""
        rows.append([str(rank), identifier + marks, candidate["family"].replace("_", " "), _legs_text(candidate), _usd(candidate["entry_cash_usd"]),
                     _usd(candidate["max_loss_usd"]), "n/a" if probability is None else f"{probability:.0%}",
                     "n/a" if expected is None else f"{expected:+,.0f}"])
    return table_fragment(["Rank", "Candidate", "Structure", "Legs", "Entry cash", "Max loss", "P(profit), realised view", "Expected PnL, realised view"],
                          rows, numeric_columns=(0, 4, 5, 6, 7), caption="Shortlist in the model's order, one coin per leg, USD")


def _chart(pick: StoredPick, chosen: Dict[str, Any]) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """The PnL-at-expiry chart of the chosen candidate at the stored size, and the numbers under it."""
    quantity = pick.quantity or 0.0
    legs = [PayoffLeg(leg["action"], leg["option_type"], float(leg["strike"]), quantity) for leg in chosen["legs"]]
    entry_cash = chosen["entry_cash_usd"] * quantity
    summary = summarize_payoff(legs, entry_cash)
    figure_id = f"strategy-{pick.expiration}"
    profit = "unlimited" if summary.max_profit_usd is None else f"{summary.max_profit_usd:,.0f} USD"
    loss = "unlimited" if summary.max_loss_usd is None else f"{summary.max_loss_usd:,.0f} USD"
    breakevens = ", ".join(f"{value:,.0f}" for value in summary.breakevens) or "none"
    text = (f"At expiry, before fees, at this size: maximum profit {profit}, maximum loss {loss}, breakeven {breakevens}. "
            "Entry at the quotes of that moment (buys at the ask, sells at the bid).")
    chart_html = f'<div class="switcher-plot" data-stale="{_e(figure_id)}"></div>' + paragraph_fragment(text, "note-s")
    return chart_html, {figure_id: figure_json(payoff_figure(legs, entry_cash, pick.spot))}


def _score(pick: StoredPick) -> Optional[float]:
    """Expected PnL per unit of maximum loss of the chosen candidate under the realised-volatility view; None when there is no usable choice."""
    if pick.status != "ok" or not pick.quantity:
        return None
    chosen = next((candidate for candidate in pick.candidates if candidate["id"] == pick.choice), None)
    if chosen is None or not chosen.get("max_loss_usd"):
        return None
    expected = chosen.get("distributions", {}).get("realised", {}).get("expected_pnl_usd")
    return None if expected is None else expected / chosen["max_loss_usd"]


def select_trades(picks: Sequence[StoredPick]) -> List[StoredPick]:
    """The picks to take: the best ones by expected PnL per risk, at most ``MAX_SELECTED_TRADES`` (a pick without a usable choice never)."""
    scored = [(score, pick) for pick in picks for score in [_score(pick)] if score is not None and score > 0]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [pick for _, pick in scored[:MAX_SELECTED_TRADES]]


def _selection_text(selected: Sequence[StoredPick]) -> str:
    """The plain statement of what to trade and how much is at risk."""
    if not selected:
        return paragraph_fragment("No pick is selected as a trade: none has a positive expected PnL under the realised-volatility view.", "note-s")
    names = ", ".join(f"{pick.expiration} (max loss {_usd(pick.max_loss_usd)} USD)" for pick in selected)
    total = sum(pick.max_loss_usd or 0.0 for pick in selected)
    families = {next((c["family"] for c in pick.candidates if c["id"] == pick.choice), "?") for pick in selected}
    text = (f"Selected trades (best expected PnL per risk, at most {MAX_SELECTED_TRADES}): {names}. If all of them lose the whole maximum, "
            f"{_usd(total)} USD is lost. Other expiries' picks are alternatives, not additional trades.")
    if len(selected) > 1 and len(families) == 1:
        text += " The selected trades are the same structure on the same view of the market: treat them as one bet, not as diversification."
    return paragraph_fragment(text)


def _card(pick: StoredPick, now_utc: datetime, role: str = "") -> Tuple[str, Dict[str, Dict[str, Any]]]:
    age_hours = max((now_utc - pick.as_of).total_seconds() / 3600.0, 0.0)
    warnings: List[str] = []
    if age_hours > STALE_AFTER_HOURS:
        warnings.append(f"This pick is {_age_text(age_hours)} old: the market has moved since. Ask again before relying on it.")
    header = (f'<div class="card-head"><div class="card-title"><h3>{_e(pick.expiration)}{(" - " + _e(role)) if role else ""}</h3></div><div class="card-meta">'
              f'<span class="card-sub">facts of {_e(format_utc(pick.as_of))} ({_e(_age_text(age_hours))} old), spot {pick.spot:,.0f}</span></div></div>')
    body = ""
    figures: Dict[str, Dict[str, Any]] = {}
    chosen = next((candidate for candidate in pick.candidates if candidate["id"] == pick.choice), None)
    if pick.status in ("ok", "no_trade"):
        try:
            data = json.loads(pick.response)
            view = VIEW_LABELS.get(data["view"], data["view"])
            parsed = f"<h4>{_e(view)}</h4>" + paragraph_fragment(data["summary"])
            chart_figures: Dict[str, Dict[str, Any]] = {}
            if pick.status == "no_trade":
                parsed += paragraph_fragment("The model chose no trade for this expiry.", "note-s")
            elif chosen is not None and pick.quantity:
                parsed += (f"<h4>Chosen: {_e(pick.choice)}, {_e(chosen['family'].replace('_', ' '))}</h4>"
                           + table_fragment(["Action", "Type", "Strike", "Fill price (USD per coin)"],
                                            [[leg["action"], leg["option_type"], f"{leg['strike']:,.0f}", f"{leg['entry_price_usd']:,.0f}"]
                                             for leg in chosen["legs"]], numeric_columns=(2, 3)))
                parsed += paragraph_fragment(f"Size from the loss budget: {pick.quantity:.4f} coins per leg; entry {_entry_text(chosen['entry_cash_usd'], pick.quantity)}; "
                                             f"maximum loss {_usd(pick.max_loss_usd)} USD (risk per trade {_usd(pick.risk_per_trade_usd)} USD).", "note-s")
                chart_html, chart_figures = _chart(pick, chosen)
                parsed += chart_html
                if pick.baseline_id:
                    same = pick.baseline_id == pick.choice
                    parsed += paragraph_fragment("The deterministic baseline (best expected PnL per risk under the realised volatility) " +
                                                 ("chose the same candidate." if same else f"preferred {pick.baseline_id}."), "note-s")
            else:
                raise ValueError("the chosen candidate is missing")
            parsed += ("<h4>Why</h4>" + _list(data["reasons"]) + "<h4>Risks</h4>" + _list(data["risks"])
                       + "<h4>What would make it wrong</h4>" + paragraph_fragment(data["invalidation"]) + _shortlist_table(pick))
            body += parsed                                  # only a fully read pick is shown
            figures = chart_figures
        except (ValueError, KeyError, TypeError):
            warnings.append("The stored pick could not be read.")
            body += paragraph_fragment(pick.response[:RAW_TEXT_LIMIT], "note-s")
    elif pick.status == "refused":
        warnings.insert(0, "The model declined to answer this request.")
        body += paragraph_fragment(pick.response[:RAW_TEXT_LIMIT], "note-s")
    elif pick.status == "no_candidates":
        warnings.insert(0, "The chain offered no usable candidate for this expiry.")
    else:
        warnings.insert(0, "The answer of the model was not usable (not in the expected form, or not a candidate of the shortlist).")
        body += paragraph_fragment(pick.response[:RAW_TEXT_LIMIT], "note-s")
    warning_html = "".join(f'<p class="note-s" style="color:var(--warn)"><strong>{_e(text)}</strong></p>' for text in warnings)
    return (f'<article class="card">{header}{warning_html}{body}<p class="note-s">Model {_e(pick.model)}, cost {pick.cost_usd:.3f} USD.</p></article>',
            figures)


def build_strategy_panel(picks: Sequence[StoredPick], now_utc: datetime, currency: str, read_error: Optional[str] = None) -> StrategyPanel:
    """
    The Strategy tab: inner HTML and figures.

    Args:
        picks: The newest stored live pick of each expiry (any order); expired expiries are left out.
        now_utc: The current instant, timezone-aware UTC (ages are measured against it).
        currency: "BTC" or "ETH".
        read_error: Text when the stored picks could not be read (shown instead of an empty list).

    Raises:
        ValueError: for a naive ``now_utc``.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (UTC)")
    now = now_utc.astimezone(timezone.utc)
    intro = (f'<div class="panel-head"><h2>Strategy picks, {_e(currency)}</h2></div>'
             + paragraph_fragment("The answer of a language model that could only choose among candidate structures built and priced by code (a buy at the "
                                  "ask, a sell at the bid), from the facts of an analysis. The position is sized by code from a loss budget. These picks are "
                                  "not scored against what happened yet, some inputs are conventions (assumed dealer positioning), and nothing here "
                                  "places an order. Picks are made when 'Ask Claude for strategies' is ticked in the Analysis tab (about 0.05 USD per "
                                  "expiry); never otherwise.", "note-s"))
    if read_error:
        return StrategyPanel(intro + paragraph_fragment(f"The stored picks could not be read: {read_error}", "note-s"))
    open_picks: Dict[str, StoredPick] = {}
    days_left: Dict[str, float] = {}
    for pick in picks:
        days = MarketWideCalculator.calculate_dte(pick.expiration, now)          # fractional days to the 08:00 UTC settlement
        if days is not None and days > 0 and (pick.expiration not in open_picks or pick.as_of > open_picks[pick.expiration].as_of):
            open_picks[pick.expiration] = pick
            days_left[pick.expiration] = days
    if not open_picks:
        return StrategyPanel(intro + paragraph_fragment("No stored pick for an expiry that is still open.", "note-s"))
    ordered = sorted(open_picks.values(), key=lambda stored: days_left[stored.expiration])
    selected = select_trades(ordered)
    chosen_ids = {id(pick) for pick in selected}
    cards = [_card(pick, now, "SELECTED TRADE" if id(pick) in chosen_ids else "alternative, not selected") for pick in ordered]
    figures: Dict[str, Dict[str, Any]] = {}
    for _, card_figures in cards:
        figures.update(card_figures)
    coverage = paragraph_fragment(ASKED_NOTE + " Asked in this view: " + ", ".join(pick.expiration for pick in ordered) + ".", "note-s")
    return StrategyPanel(intro + _selection_text(selected) + coverage + '<div class="cards">' + "".join(card for card, _ in cards) + "</div>", figures)
