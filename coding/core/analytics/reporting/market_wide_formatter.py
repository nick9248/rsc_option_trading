"""
Text formatting for the cross-expiry market-wide metrics sections.

Extracted verbatim (behavior-preserving) from ``MarketWideCalculator``'s
``calculate_*`` methods (which today interleave calculation and text
formatting, returning a ``(text, structured_dict)`` tuple) per
refactor_design_spec.md section T3. Each function here takes only the typed
result model (section T2) and renders text.

Not yet wired into ``MarketWideCalculator`` — its ``calculate_*`` methods
keep returning ``(text, dict)`` tuples until T4 splits calculation from
formatting there. Until then, ``OnChainAnalyzer`` keeps consuming the
calculator's own pre-rendered text verbatim (T3's "temporary adapter").

Every function accepts ``Optional[...]`` and renders the same
"no data"/"insufficient data" message the legacy method showed for its
early-return case when the corresponding phase produced no result.
"""

from datetime import datetime
from typing import Optional

from coding.core.analytics.results.market_wide_results import (
    BlockTradesResult,
    CrossAssetCorrelationResult,
    FuturesBasisResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    TermStructureResult,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
)

_SUB_SEPARATOR = "-" * 80


def format_term_structure_section(result: Optional[TermStructureResult]) -> str:
    """Render the IV TERM STRUCTURE section."""
    lines = ["IV TERM STRUCTURE", _SUB_SEPARATOR]

    if result is None:
        lines.append("  No ATM IV data available")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  {'Expiration':>12}  {'DTE':>5}  {'ATM IV':>8}")
    lines.append(f"  {'----------':>12}  {'---':>5}  {'------':>8}")

    for entry in result.entries:
        lines.append(f"  {entry.expiration:>12}  {entry.dte:>5}  {entry.atm_iv:>7.1f}%")

    if len(result.entries) >= 2:
        diff = result.spread_signed
        if result.shape == "CONTANGO":
            shape_label = f"CONTANGO (+{diff:.1f} pts)"
        elif result.shape == "BACKWARDATION":
            shape_label = f"BACKWARDATED ({diff:.1f} pts)"
        else:
            shape_label = f"FLAT ({diff:+.1f} pts)"
        lines.append(f"  Structure: {shape_label}")

    lines.append("")
    return "\n".join(lines)


def format_futures_basis_section(result: Optional[FuturesBasisResult]) -> str:
    """Render the FUTURES BASIS section."""
    lines = ["FUTURES BASIS", _SUB_SEPARATOR]

    if result is None or not result.entries:
        lines.append("  No futures data available")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  {'Future':>20}  {'Price':>12}  {'Spot':>12}  {'Ann. Premium':>12}")
    lines.append(f"  {'------':>20}  {'-----':>12}  {'----':>12}  {'------------':>12}")

    for entry in result.entries:
        lines.append(
            f"  {entry.instrument_name:>20}  ${entry.mark_price:>11,.0f}  "
            f"${entry.index_price:>11,.0f}  {entry.annualized_premium_pct:>11.1f}%"
        )

    lines.append("")
    return "\n".join(lines)


def format_realized_volatility_section(result: Optional[RealizedVolatilityResult]) -> str:
    """Render the REALIZED VOLATILITY section."""
    lines = ["REALIZED VOLATILITY", _SUB_SEPARATOR]

    if result is None or not result.rv_by_window:
        lines.append("  Insufficient price history")
        lines.append("")
        return "\n".join(lines)

    rv_strs = [f"{window}d: {rv * 100:.1f}%" for window, rv in result.rv_by_window.items()]
    lines.append(f"  {' | '.join(rv_strs)}")
    lines.append("")
    return "\n".join(lines)


def format_vrp_section(result: Optional[VarianceRiskPremiumResult]) -> str:
    """Render the VOLATILITY RISK PREMIUM (VRP) section."""
    lines = ["VOLATILITY RISK PREMIUM (VRP)", _SUB_SEPARATOR]

    if result is None or result.dvol is None:
        lines.append("  DVOL not available")
        lines.append("")
        return "\n".join(lines)

    if result.signal in ("VERY_EXPENSIVE", "EXPENSIVE"):
        advice = "Sell vol"
    elif result.signal in ("VERY_CHEAP", "CHEAP"):
        advice = "Buy vol"
    else:
        advice = "Neutral"

    lines.append(
        f"  DVOL: {result.dvol:.1f}%  |  30d RV: {result.rv_30d * 100:.1f}%  |  "
        f"VRP: {result.vrp:+.1f} pts ({result.signal} - {advice})"
    )
    lines.append("")
    return "\n".join(lines)


def format_volatility_cone_section(result: Optional[VolatilityConeResult]) -> str:
    """Render the VOLATILITY CONE section."""
    lines = ["VOLATILITY CONE", _SUB_SEPARATOR]

    if result is None or not result.percentile_by_window:
        lines.append("  Insufficient price history for vol cone")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  {'Window':>8}  {'Pctile':>8}")
    lines.append(f"  {'------':>8}  {'------':>8}")

    for window in sorted(result.percentile_by_window.keys()):
        percentile = result.percentile_by_window[window]
        lines.append(f"  {window:>6}d  {percentile:>6.0f}th")

    lines.append("")
    return "\n".join(lines)


def format_perpetual_funding_section(result: Optional[PerpetualFundingResult]) -> str:
    """Render the PERPETUAL FUNDING & OI section."""
    lines = ["PERPETUAL FUNDING & OI", _SUB_SEPARATOR]

    if result is None or result.funding_rate is None:
        lines.append("  Funding data not available")
        lines.append("")
        return "\n".join(lines)

    funding_pct = result.funding_rate * 100
    lines.append(
        f"  Perp OI: {result.perp_open_interest:,.0f} USD  |  "
        f"Funding: {funding_pct:.4f}%  |  Trend: {result.funding_trend}"
    )

    if result.funding_8h is not None:
        ann_funding = result.funding_rate * 3 * 365 * 100
        lines.append(
            f"  8h Funding: {result.funding_8h * 100:.4f}%  |  "
            f"Annualized: {ann_funding:.1f}%"
        )

    lines.append("")
    return "\n".join(lines)


def format_block_trades_section(result: Optional[BlockTradesResult]) -> str:
    """Render the BLOCK TRADES section."""
    threshold = result.notional_threshold if result is not None else 100_000.0
    lines = [f"BLOCK TRADES (>${threshold:,.0f} notional)", _SUB_SEPARATOR]

    if result is None:
        lines.append("  No recent trade data available")
        lines.append("")
        return "\n".join(lines)

    if not result.trades:
        lines.append("  No block trades detected in recent activity")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"  {'Time':>12}  {'Instrument':>25}  {'Size':>8}  "
        f"{'Dir':>5}  {'Notional':>14}  {'IV':>6}"
    )
    lines.append(
        f"  {'----':>12}  {'----------':>25}  {'----':>8}  "
        f"{'---':>5}  {'--------':>14}  {'--':>6}"
    )

    for trade in result.trades:
        if trade.timestamp:
            time_str = datetime.fromtimestamp(trade.timestamp / 1000).strftime("%H:%M:%S")
        else:
            time_str = "N/A"

        iv_str = f"{trade.implied_volatility:.1f}%" if trade.implied_volatility else "N/A"

        lines.append(
            f"  {time_str:>12}  {trade.instrument_name:>25}  "
            f"{trade.amount:>8.1f}  {trade.direction:>5}  "
            f"${trade.notional:>13,.0f}  {iv_str:>6}"
        )

    lines.append("")
    return "\n".join(lines)


def format_cross_asset_correlation_section(
    result: Optional[CrossAssetCorrelationResult],
    currency: str,
) -> str:
    """Render the CROSS-ASSET CORRELATION section."""
    other = result.other_currency if result is not None else ""
    lines = [f"CROSS-ASSET CORRELATION (30d, {currency}/{other})", _SUB_SEPARATOR]

    if result is None:
        lines.append("  Price Correlation: Insufficient data")
        lines.append("  DVOL Correlation: N/A")
        lines.append("")
        return "\n".join(lines)

    if result.price_correlation is not None:
        lines.append(f"  Price Correlation: {result.price_correlation:.2f}")
    else:
        lines.append("  Price Correlation: Insufficient data")

    if result.dvol_correlation is not None:
        lines.append(f"  DVOL Correlation: {result.dvol_correlation:.2f}")
    elif result.sample_size > 0:
        lines.append("  DVOL Correlation: Insufficient data")
    else:
        lines.append("  DVOL Correlation: N/A")

    lines.append("")
    return "\n".join(lines)
