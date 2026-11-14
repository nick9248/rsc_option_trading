"""
Small HTML fragments of the report sections built from analysis results (pure; all text is escaped through the helpers of
``report_page``). Missing values are written as "not computed", never as zero.
"""

from datetime import datetime
from typing import List, Optional, Sequence, Tuple

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
