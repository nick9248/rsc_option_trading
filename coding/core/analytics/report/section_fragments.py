"""
Small HTML fragments of the report sections built from analysis results (pure; all text is escaped through the helpers of
``report_page``). Missing values are written as "not computed", never as zero.
"""

import html
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from coding.core.analytics.open_interest.expiry_open_interest import ExpiryOpenInterest
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels
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


def dealer_levels_caption(levels: DealerFlowLevels) -> str:
    """The comparison table of both views of one expiry and the plausibility check, shown above its chart."""
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
    return table + paragraph_fragment(check, "" if levels.gate_passed else "verdict-failed") + notes


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


def taker_flow_table(days: List[dict], count: int = 7) -> str:
    """The last ``count`` complete days of the taker flow (newest first), from the chart service's data file."""
    rows = []
    for day in sorted(days, key=lambda item: item["day"], reverse=True)[:count]:
        buy, sell, ratio = day.get("taker_buy_usd"), day.get("taker_sell_usd"), day.get("ratio")
        net = buy - sell if buy is not None and sell is not None else None
        rows.append([day["day"], millions_text(buy), millions_text(sell), millions_text(net),
                     f"{ratio:.3f}" if ratio is not None else NOT_COMPUTED])
    return table_fragment(["Day (UTC)", "Taker buy", "Taker sell", "Net", "Buy/sell ratio"], rows,
                          caption=f"Last {len(rows)} complete days", numeric_columns=(1, 2, 3, 4))
