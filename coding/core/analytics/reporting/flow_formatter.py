"""
Text formatting for the buy/sell trade-flow section.

Extracted verbatim (behavior-preserving) from
``BuySellFlowAnalyzer.generate_report_section()`` per
refactor_design_spec.md section T3, operating on the typed ``FlowResult``
(section T2), wired in at T5 to match
BuySellFlowAnalyzer.generate_report_section exactly — including the
bugfix_spec.md Item 6 / Decision D5 data-sufficiency quality gate. This
formatter has no production callers yet (T8 wires it in when the text
splitter is deleted); it must still track the live path in lockstep so T8's
wiring is a no-op rather than a silent regression.
"""

from datetime import datetime, timezone

from coding.core.analytics.buy_sell_flow_analyzer import (
    INSUFFICIENT_DATA_LABEL,
    LOW_CONFIDENCE_SUFFIX,
    MINIMUM_TRADES_FOR_SECTION,
)
from coding.core.analytics.results.flow_results import FlowResult

_SEPARATOR = "-" * 80


def format_flow_strike_tables(result: FlowResult) -> str:
    """
    Render just the TOP 5 STRIKES BY BUYING/SELLING PRESSURE tables --
    no header, no "EXPIRATION-LEVEL FLOW" bias/trend headline.

    institutional_metrics_spec.md section 6(c) (Task D2 independent review,
    Important #1): "Report (replaces the contract-count 'net flow'
    headline; keep the per-strike table)" -- this function IS "the
    per-strike table" half of that instruction. Shared by
    ``format_flow_section`` (the full legacy section, still directly
    tested but no longer wired into the live report) and
    ``delta_flow_formatter.format_delta_adjusted_flow_section`` (the live
    per-expiry DELTA-ADJUSTED FLOW slot, section 9(b) per-expiry order
    item 5), which replaces the "EXPIRATION-LEVEL FLOW" bias/trend
    headline this function omits with this expiry's own signed
    HIRO/premium/gross line instead.

    Callers are responsible for their own "insufficient data" gating and
    message wording -- this function assumes ``result.sufficient_data`` is
    True (matching every existing call site's own gate) and always renders
    both tables.
    """
    lines = []

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


def format_flow_section(result: FlowResult, lookback_hours: float) -> str:
    """
    Render the buy/sell flow analysis section.

    Args:
        result: Buy/sell flow result for one expiration.
        lookback_hours: Unused — kept for signature parity with older
            callers; the window is rendered from
            ``result.window_start_ms``/``window_end_ms`` (the actual
            fetched window), not this display-only hint.

    Returns:
        Formatted string for inclusion in the analysis report.
    """
    del lookback_hours  # not rendered — see docstring; result carries the real window
    lines = []

    start_str = datetime.fromtimestamp(result.window_start_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    end_str = datetime.fromtimestamp(result.window_end_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    lines.append("BUY/SELL FLOW ANALYSIS (Trade Direction-Based)")
    lines.append(_SEPARATOR)
    lines.append(f"Spot Price: ${result.spot_price:,.2f}")
    lines.append(f"Window: {start_str} -> {end_str} UTC")
    lines.append(f"Trades Analyzed: {result.trade_count}")
    lines.append("")

    if not result.sufficient_data:
        lines.append(
            f"  ** INSUFFICIENT FLOW DATA ** - {result.trade_count} trade(s) in window, "
            f"{MINIMUM_TRADES_FOR_SECTION} required. Flow section suppressed."
        )
        lines.append("")
        return "\n".join(lines)

    totals = result.expiration_totals
    confidence_tag = LOW_CONFIDENCE_SUFFIX if result.low_confidence else ""
    trend_tag = (
        LOW_CONFIDENCE_SUFFIX
        if result.low_confidence and result.flow_trend != INSUFFICIENT_DATA_LABEL
        else ""
    )
    lines.append("EXPIRATION-LEVEL FLOW:")
    lines.append(f"  Calls:  Buy: {totals.call_buy_volume:>10,.1f}  Sell: {totals.call_sell_volume:>10,.1f}")
    lines.append(f"  Puts:   Buy: {totals.put_buy_volume:>10,.1f}  Sell: {totals.put_sell_volume:>10,.1f}")
    lines.append(f"  Bias: {result.bias_interpretation}{confidence_tag}")
    lines.append(f"  Trend: {result.flow_trend}{trend_tag}")
    lines.append("")

    lines.append(format_flow_strike_tables(result))

    return "\n".join(lines)
