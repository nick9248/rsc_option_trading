"""
Small HTML fragments of the report sections built from analysis results (pure; all text is escaped through the helpers of
``report_page``). Missing values are written as "not computed", never as zero.
"""

import html
import re
from datetime import datetime
from typing import Collection, Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.open_interest.expiry_open_interest import ExpiryOpenInterest
from coding.core.analytics.option_flow.block_structure import BarExpiryShare, BiggestLeg, BlockStructure
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, violating_gamma_share
from coding.core.analytics.option_flow.strike_flow import SERIES, StrikeFlow
from coding.core.analytics.report.report_page import format_utc, paragraph_fragment, table_fragment

NOT_COMPUTED = "not computed"


def level_text(strike: Optional[float]) -> str:
    """A price level as text; "not computed" when there is none."""
    return f"{strike:,.0f}" if strike is not None else NOT_COMPUTED


def millions_text(value: Optional[float]) -> str:
    """USD as millions with the sign in front, e.g. "-3.4M USD"; "not computed" when missing."""
    if value is None:
        return NOT_COMPUTED
    return f"{'-' if value < 0 else ''}{abs(value) / 1e6:,.1f}M USD"


def usd_text(value: Optional[float]) -> str:
    """USD as billions with 2 decimals from 1e9 up ("9.65B USD"), else millions with 1 decimal; "not computed" when missing."""
    if value is None:
        return NOT_COMPUTED
    if abs(value) >= 1e9:
        return f"{value / 1e9:,.2f}B USD"
    return f"{value / 1e6:,.1f}M USD"


def days_text(days_left: Optional[float]) -> str:
    """Days to settlement: whole days from 10 up, one decimal below; "not computed" when missing."""
    if days_left is None:
        return NOT_COMPUTED
    return f"{days_left:.0f}" if days_left >= 10 else f"{days_left:.1f}"


def coins_text(value: Optional[float]) -> str:
    """Open interest in coins with thousands separators; "not computed" when missing."""
    return f"{value:,.0f}" if value is not None else NOT_COMPUTED


def share_text(share: Optional[float]) -> str:
    """A fraction as a percentage with one decimal; "not computed" when missing."""
    return f"{share * 100:.1f}%" if share is not None else NOT_COMPUTED


def expiry_choice_label(row: ExpiryOpenInterest) -> str:
    """
    Label of an expiry choice, e.g. "25DEC26, 41 days, 9.65B USD OI, 40%". Parts that are missing are left out or named
    honestly ("25DEC26, 41 days, OI not computed"); without an index price the coins are shown instead of the USD value.
    """
    parts = [row.expiry]
    if row.days_left is not None:
        parts.append(f"{days_text(row.days_left)} days")
    if row.total_open_interest is None:
        parts.append("OI not computed")
        return ", ".join(parts)
    parts.append(f"{usd_text(row.notional_usd)} OI" if row.notional_usd is not None
                 else f"{coins_text(row.total_open_interest)} OI")
    if row.share is not None:
        parts.append(f"{row.share * 100:.0f}%")
    return ", ".join(parts)


def open_interest_table(report_rows: Sequence[ExpiryOpenInterest], captured_at: datetime, index_price: Optional[float],
                        limit: Optional[int] = None) -> str:
    """
    The open interest per expiry as a compact table (the first ``limit`` rows when given) with a small bar for each share.
    """
    price = f"{index_price:,.0f}" if index_price is not None else NOT_COMPUTED
    caption = (f"Open interest per expiry, chain snapshot {format_utc(captured_at)}; "
               f"notional = open interest x index price {price}")
    headers = ["Expiry", "Days left", "Calls", "Puts", "Total OI (coins)", "Notional", "Share"]
    head = "".join(f'<th scope="col"{" class=\"num\"" if index else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    body = []
    for row in report_rows if limit is None else report_rows[:limit]:
        if row.share is not None:
            width = min(max(row.share * 100, 0.0), 100.0)
            share_cell = (f'<span class="share-track"><span class="share-bar" style="width: {width:.1f}%"></span></span>'
                          f'<span class="share-value">{html.escape(share_text(row.share))}</span>')
        else:
            share_cell = html.escape(NOT_COMPUTED)
        cells = [days_text(row.days_left), coins_text(row.call_open_interest), coins_text(row.put_open_interest),
                 coins_text(row.total_open_interest), usd_text(row.notional_usd)]
        body.append(f'<tr><th scope="row">{html.escape(row.expiry)}</th>'
                    + "".join(f'<td class="num">{html.escape(cell)}</td>' for cell in cells)
                    + f'<td class="num share-cell">{share_cell}</td></tr>')
    return (f'<div class="table-wrap"><table class="open-interest"><caption>{html.escape(caption)}</caption>'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


UNRELIABLE_GAMMA_SHARE = 0.25       # display text only: above this the failing strikes carry a large part of the gamma
_OPTION_TYPE_TEXT = {"C": "Calls", "P": "Puts"}
_VIOLATION_REASON = re.compile(r"^[\d.]+% of options exceed their open interest \(limit (\d+)%\)$")
_COVERAGE_REASON = re.compile(r"^trade coverage ([\d.]+)% is below (\d+)%$")
_NO_OPEN_INTEREST_REASON = re.compile(r"^(\d+) of (\d+) traded options have no open interest to check against$")


def _plain_reason(reason: str, levels: DealerFlowLevels) -> str:
    """One gate reason of ``DealerFlowLevelsService`` in plain words; an unknown reason is passed through unchanged."""
    if match := _VIOLATION_REASON.match(reason):
        return (f"{levels.violations} of {levels.options_checked} options ({levels.violation_rate * 100:.1f} %) show a dealer "
                f"position larger than their open interest; the limit is {match.group(1)} %")
    if match := _COVERAGE_REASON.match(reason):
        return (f"trade coverage {match.group(1)} % is below {match.group(2)} %: hours without stored trades, "
                "so the dealer position is incomplete")
    if match := _NO_OPEN_INTEREST_REASON.match(reason):
        return (f"{match.group(1)} of {match.group(2)} traded options have no open interest value, "
                "so there is nothing to check them against")
    return reason


def check_sentence(levels: DealerFlowLevels) -> str:
    """What the plausibility check found, in one plain sentence (built from ``gate_reason``)."""
    if levels.gate_passed:
        return "Check passed: no rule of the plausibility check is broken beyond its limits."
    reasons = (levels.gate_reason or "no reason given").split("; ")
    return "Check failed: " + "; ".join(_plain_reason(reason, levels) for reason in reasons) + "."


def violating_options_table(levels: DealerFlowLevels) -> str:
    """All options whose dealer position exceeds their open interest, largest excess first (as stored in ``levels``)."""
    rows = []
    for option in levels.violating_options:
        ratio = option.position_to_open_interest
        rows.append([strike_text(option.strike), _OPTION_TYPE_TEXT.get(option.option_type, option.option_type),
                     f"{option.dealer_position:,.1f}", f"{option.open_interest:,.1f}", f"{option.excess:,.1f}",
                     f"{ratio:,.1f} x" if ratio is not None else "open interest is 0"])
    return table_fragment(["Strike", "Type", "Dealer position (contracts)", "Open interest (contracts)",
                           "Excess = |position| - open interest", "|position| / open interest"], rows,
                          caption="Every option that breaks the rule, largest excess first", numeric_columns=(2, 3, 4, 5))


def violation_explanation(levels: DealerFlowLevels) -> str:
    """The gamma share of the violating strikes, why they fail, and what to do with the flow-based view (paragraphs)."""
    share = violating_gamma_share(levels)
    strike_count = len({option.strike for option in levels.violating_options})
    share_sentence = (f"The {strike_count} strikes with a failing option carry "
                      f"{share * 100:.1f} % of the flow-based gamma of this expiry" if share is not None else
                      "The share of the flow-based gamma on the failing strikes is not computed")
    definition = (" (sum of |flow GEX| at those strikes divided by the sum of |flow GEX| at all strikes; "
                  "call and put of a strike together).")
    why = ("Why: the flow method assumes every taker trade opens a new position and the dealer holds the other side. "
           "Many trades instead close positions (for example a customer closing against a dealer), so the cumulative "
           "taker flow at a strike can be far larger than what is still open. Then the flow-based dealer position at "
           "that strike is wrong.")
    if share is not None and share > UNRELIABLE_GAMMA_SHARE:
        verdict = ("Treat the flow-based view of this expiry as unreliable; use the open-interest based view.")
    else:
        verdict = ("The flow-based levels are less affected by these strikes, but still read them with care.")
    return (paragraph_fragment(share_sentence + definition) + paragraph_fragment(why)
            + paragraph_fragment(verdict, "verdict-failed"))


def dealer_levels_caption(levels: DealerFlowLevels) -> str:
    """The comparison table of both views of one expiry, what the plausibility check found, and the check line."""
    rows = [
        ["Flow-based", level_text(levels.flow_call_wall), level_text(levels.flow_put_wall),
         level_text(levels.flow_gamma_flip), millions_text(levels.total_flow_gex)],
        ["Open-interest based", level_text(levels.open_interest_call_wall), level_text(levels.open_interest_put_wall),
         level_text(levels.open_interest_gamma_flip), millions_text(levels.total_open_interest_gex)],
    ]
    table = table_fragment(["", "Call wall", "Put wall", "Gamma flip", "Total GEX per 1 % move"], rows,
                           numeric_columns=(1, 2, 3, 4))
    verdict = "passed" if levels.gate_passed else f"not passed: {levels.gate_reason or 'no reason given'}"
    check = (f"Spot {levels.index_price:,.0f}. Plausibility check of the flow-based view (a dealer cannot hold more than "
             f"the open interest): {verdict}. {levels.violations} of {levels.options_checked} options exceed their open "
             f"interest; trade coverage {levels.coverage:.1%}. Flow since {levels.flow_start:%Y-%m-%d}, "
             f"{levels.trades_used:,} trades; chain snapshot {format_utc(levels.chain_captured_at)}.")
    notes = "".join(paragraph_fragment(note[:1].upper() + note[1:] + ".", "note") for note in levels.notes)
    details = paragraph_fragment(check_sentence(levels), "" if levels.gate_passed else "verdict-failed")
    if not levels.gate_passed and levels.violating_options:
        details += violating_options_table(levels) + violation_explanation(levels)
    return table + details + paragraph_fragment(check, "" if levels.gate_passed else "verdict-failed") + notes


def flow_totals(flow: StrikeFlow) -> Tuple[float, float, float, float]:
    """Notional (USD) of calls bought, calls sold, puts bought and puts sold, blocked and screen together."""
    totals = {("C", "buy"): 0.0, ("C", "sell"): 0.0, ("P", "buy"): 0.0, ("P", "sell"): 0.0}
    kind_of = {series.key: (series.option_type, series.direction) for series in SERIES}
    for cells in flow.cells.values():
        for key, cell in cells.items():
            totals[kind_of[key]] += cell.notional_usd
    return totals[("C", "buy")], totals[("C", "sell")], totals[("P", "buy")], totals[("P", "sell")]


def option_flow_caption(flow: StrikeFlow, scope: str, window_start: datetime, window_end: datetime,
                        warnings: Sequence[str] = ()) -> str:
    """The numbers behind one option-flow view: trades, strikes and the notional per type and side."""
    if flow.trades_used == 0:
        return paragraph_fragment(f"{scope}: no trades between {format_utc(window_start)} and {format_utc(window_end)}.")
    calls_bought, calls_sold, puts_bought, puts_sold = flow_totals(flow)
    summary = (f"{scope}: {flow.trades_used:,} trades on {len(flow.strikes)} strikes "
               f"({flow.trades_skipped:,} skipped: unknown side or size). Notional = contracts x strike.")
    table = table_fragment(["", "Bought by takers", "Sold by takers"],
                           [["Calls", millions_text(calls_bought), millions_text(calls_sold)],
                            ["Puts", millions_text(puts_bought), millions_text(puts_sold)]],
                           numeric_columns=(1, 2))
    warning_html = "".join(paragraph_fragment(f"Warning: {text}", "verdict-failed") for text in warnings)
    return paragraph_fragment(summary) + table + warning_html


BAR_EXPIRIES_SHOWN = 3        # expiries named per bar; the rest is "others"
UNEXPLAINED_TOLERANCE_USD = 1.0
SIDE_TEXT = {"buy": "bought", "sell": "sold"}
TYPE_TEXT = {"C": "Call", "P": "Put"}


def strike_text(strike: float) -> str:
    """A strike with thousands separators, decimals only when it has some."""
    return f"{strike:,.0f}" if float(strike).is_integer() else f"{strike:,.2f}"


def percent_text(share: float) -> str:
    """A share as a whole percentage; "<1%" when it is above zero but rounds to nothing."""
    return f"{share * 100:.0f}%" if share >= 0.01 or share <= 0 else "<1%"


def _expiry_button(expiry: str) -> str:
    """A small button that makes the option-flow switcher show ``expiry`` (handled by the report page script)."""
    escaped = html.escape(expiry)
    return (f'<button type="button" class="jump" data-switcher-target="option-flow" data-control="expiry" '
            f'data-value="{escaped}">{escaped}</button>')


def bar_expiry_table(bars: Sequence[BarExpiryShare], days_left_by_expiry: Dict[str, Optional[float]],
                     precomputed_expiries: Collection[str]) -> str:
    """
    One row per bar of the all-expirations chart: strike, type, taker side, notional, blocked share and the expiries that
    carry the bar (the top ``BAR_EXPIRIES_SHOWN`` with share and days left, then "others"). An expiry that has its own view in
    the switcher is a button that jumps there; the others are plain text. A bar the per-expiry numbers do not fully explain
    says how much is missing.
    """
    headers = ["Strike", "Type", "Taker side", "Notional", "Blocked", "Carried by expiry"]
    head = "".join(f'<th scope="col"{" class=\"num\"" if index in (0, 3, 4) else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    body = []
    for bar in bars:
        parts = []
        for item in bar.expiries[:BAR_EXPIRIES_SHOWN]:
            name = _expiry_button(item.expiry) if item.expiry in precomputed_expiries else html.escape(item.expiry)
            days = days_left_by_expiry.get(item.expiry)
            days_part = f"{days_text(days)} days" if days is not None else "days left not computed"
            parts.append(f"{name} {html.escape(percent_text(item.share))} ({html.escape(days_part)})")
        others = sum(item.share for item in bar.expiries[BAR_EXPIRIES_SHOWN:])
        if bar.expiries[BAR_EXPIRIES_SHOWN:]:
            parts.append(f"others {html.escape(percent_text(others))}")
        if abs(bar.unexplained_notional) >= UNEXPLAINED_TOLERANCE_USD:
            parts.append(html.escape(f"not explained: {bar.unexplained_notional:,.0f} USD"))
        body.append(
            f'<tr><th scope="row" class="num">{html.escape(strike_text(bar.strike))}</th>'
            f'<td>{html.escape(TYPE_TEXT.get(bar.option_type, bar.option_type))}</td>'
            f'<td>{html.escape(SIDE_TEXT.get(bar.direction, bar.direction))}</td>'
            f'<td class="num">{html.escape(usd_text(bar.total_notional_usd))}</td>'
            f'<td class="num">{html.escape(percent_text(bar.blocked_share))}</td>'
            f'<td class="wrap">{", ".join(parts) if parts else html.escape(NOT_COMPUTED)}</td></tr>')
    return ('<div class="table-wrap"><table class="bar-expiries"><caption>The biggest bars of the all-expirations chart and the '
            'expiries that carry them (notional = contracts x strike)</caption>'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def _leg_text(leg: BiggestLeg) -> str:
    kind = TYPE_TEXT.get(leg.option_type, leg.option_type).lower()
    return (f"{leg.expiration} {strike_text(leg.strike)} {kind} {SIDE_TEXT.get(leg.side, leg.side)} "
            f"{leg.contracts:,.1f} contracts, {usd_text(leg.notional_usd)}")


def block_structure_table(blocks: Sequence[BlockStructure]) -> str:
    """The largest block trades of a window: what each is made of (legs, expiries, sides, types, biggest legs, label)."""
    headers = ["Block", "Time (UTC)", "Legs (window / declared)", "Expiries", "Bought / sold", "Calls / puts", "Notional",
               "Biggest legs", "What it is"]
    head = "".join(f'<th scope="col"{" class=\"num\"" if index in (2, 4, 5, 6) else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    body = []
    for block in blocks:
        declared = str(block.declared_leg_count) if block.declared_leg_count is not None else NOT_COMPUTED
        legs = "<br>".join(html.escape(_leg_text(leg)) for leg in block.biggest_legs)
        body.append(
            f'<tr><th scope="row">{html.escape(block.block_trade_id)}</th>'
            f'<td>{html.escape(format_utc(block.first_time))}</td>'
            f'<td class="num">{block.legs_in_window} / {html.escape(declared)}</td>'
            f'<td class="wrap">{html.escape(", ".join(block.expiries))}</td>'
            f'<td class="num">{block.legs_bought} / {block.legs_sold}</td>'
            f'<td class="num">{block.calls} / {block.puts}</td>'
            f'<td class="num">{html.escape(usd_text(block.notional_usd))}</td>'
            f'<td class="wrap">{legs}</td>'
            f'<td class="wrap">{html.escape(block.label)}</td></tr>')
    return ('<div class="table-wrap"><table class="block-structures"><caption>Largest block trades of the window by notional '
            '(contracts x strike); buy/sell is the taker side of each leg</caption>'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def taker_flow_table(days: List[dict], count: int = 7) -> str:
    """The last ``count`` complete days of the taker flow (newest first); the rows are built in memory from complete days."""
    rows = []
    for day in sorted(days, key=lambda item: item["day"], reverse=True)[:count]:
        buy, sell, ratio = day.get("taker_buy_usd"), day.get("taker_sell_usd"), day.get("ratio")
        net = buy - sell if buy is not None and sell is not None else None
        rows.append([day["day"], millions_text(buy), millions_text(sell), millions_text(net),
                     f"{ratio:.3f}" if ratio is not None else NOT_COMPUTED])
    return table_fragment(["Day (UTC)", "Taker buy", "Taker sell", "Net", "Buy/sell ratio"], rows,
                          caption=f"Last {len(rows)} complete days", numeric_columns=(1, 2, 3, 4))
