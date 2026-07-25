"""
Text formatting for the buy/sell trade-flow section.

Extracted verbatim (behavior-preserving) from
``BuySellFlowAnalyzer.generate_report_section()`` per
refactor_design_spec.md section T3, operating on the typed ``FlowResult``
(section T2) instead of the analyzer's internal dict.

Not yet wired into ``BuySellFlowAnalyzer`` — ``calculate()`` still returns a
dict until T5 replaces it with ``FlowResult``. Until then, ``OnChainAnalyzer``
keeps consuming the analyzer's own ``generate_report_section()`` text
verbatim (T3's "temporary adapter").
"""

from coding.core.analytics.results.flow_results import FlowResult

_SEPARATOR = "-" * 80


def format_flow_section(result: FlowResult, lookback_hours: float) -> str:
    """
    Render the buy/sell flow analysis section.

    Args:
        result: Buy/sell flow result for one expiration.
        lookback_hours: Hours the flow window covers (display only — the
            result itself already carries ``window_start_ms``/``window_end_ms``).

    Returns:
        Formatted string for inclusion in the analysis report.
    """
    lines = []

    lines.append("BUY/SELL FLOW ANALYSIS (Trade Direction-Based)")
    lines.append(_SEPARATOR)
    lines.append(f"Spot Price: ${result.spot_price:,.2f}")
    lines.append(f"Lookback Window: {lookback_hours} hours")
    lines.append(f"Trades Analyzed: {result.trade_count}")
    lines.append("")

    totals = result.expiration_totals
    lines.append("EXPIRATION-LEVEL FLOW:")
    lines.append(f"  Calls:  Buy: {totals.call_buy_volume:>10,.1f}  Sell: {totals.call_sell_volume:>10,.1f}")
    lines.append(f"  Puts:   Buy: {totals.put_buy_volume:>10,.1f}  Sell: {totals.put_sell_volume:>10,.1f}")
    lines.append(f"  Bias: {result.bias_interpretation}")
    lines.append(f"  Trend: {result.flow_trend}")
    lines.append("")

    lines.append("TOP 5 STRIKES BY BUYING PRESSURE:")
    lines.append(_SEPARATOR)
    if result.top_buy_strikes:
        lines.append(
            f"{'Strike':>10}  {'Type':>6}  {'Net Flow':>12}  {'Buy Vol':>12}  {'Buy Notional':>15}"
        )
        lines.append(
            f"{'------':>10}  {'----':>6}  {'---------':>12}  {'--------':>12}  {'-------------':>15}"
        )
        for item in result.top_buy_strikes:
            lines.append(
                f"{item.strike:>10,.0f}  {item.option_type:>6}  "
                f"{item.net_flow:>+12,.1f}  {item.volume:>12,.1f}  "
                f"${item.notional:>14,.2f}"
            )
    else:
        lines.append("  No net buying detected")
    lines.append("")

    lines.append("TOP 5 STRIKES BY SELLING PRESSURE:")
    lines.append(_SEPARATOR)
    if result.top_sell_strikes:
        lines.append(
            f"{'Strike':>10}  {'Type':>6}  {'Net Flow':>12}  {'Sell Vol':>12}  {'Sell Notional':>15}"
        )
        lines.append(
            f"{'------':>10}  {'----':>6}  {'---------':>12}  {'---------':>12}  {'--------------':>15}"
        )
        for item in result.top_sell_strikes:
            lines.append(
                f"{item.strike:>10,.0f}  {item.option_type:>6}  "
                f"{item.net_flow:>+12,.1f}  {item.volume:>12,.1f}  "
                f"${item.notional:>14,.2f}"
            )
    else:
        lines.append("  No net selling detected")
    lines.append("")

    return "\n".join(lines)
