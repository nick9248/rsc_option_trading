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

from coding.core.analytics.market_wide_calculator import FUNDING_PERIODS_PER_YEAR
from coding.core.analytics.results.market_wide_results import (
    BlockTradesResult,
    CrossAssetCorrelationResult,
    FuturesBasisResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    SkewTermStructureEntry,
    SkewTermStructureResult,
    TermStructureResult,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
)

_SUB_SEPARATOR = "-" * 80


def _format_rr25_cell(entry: SkewTermStructureEntry) -> str:
    """
    RR25 column: value, then percentile+regime when C1's HistoricalNormalizer
    has enough accumulated 30d history, else the same "insufficient history"
    fallback C1 established (n/a + observation count) -- volatility_skew_
    history starts empty per Decision D10, so this is the expected state
    for a while.
    """
    if entry.rr_25d is None:
        return "insufficient chain"
    if entry.rr_percentile_30d is not None:
        return f"{entry.rr_25d:+.2f}  p{entry.rr_percentile_30d:.0f} {entry.rr_regime_30d}"
    return f"{entry.rr_25d:+.2f}  n/a ({entry.rr_n_30d} obs)"


def _format_bf25_cell(entry: SkewTermStructureEntry) -> str:
    """
    BF25 column: matches the spec's worked example format exactly -- value
    + percentile, no regime word (unlike the RR25 column). Same
    insufficient-history fallback as RR25.
    """
    if entry.bf_25d is None:
        return "insufficient chain"
    if entry.bf_percentile_30d is not None:
        return f"{entry.bf_25d:+.2f}  p{entry.bf_percentile_30d:.0f}"
    return f"{entry.bf_25d:+.2f}  n/a ({entry.bf_n_30d} obs)"


def format_skew_term_structure_section(result: Optional[SkewTermStructureResult]) -> str:
    """
    Render the SKEW TERM STRUCTURE section (institutional_metrics_spec.md
    section 3(c)): one row per expiry (ordered by DTE, already sorted by
    the caller), RR25/BF25 with their own 30d percentile+regime once
    enough history has accumulated in ``volatility_skew_history``, else
    the C1 "insufficient history" fallback -- expected for a while after
    this table starts accumulating fresh (Decision D10 discards the old
    degenerate skew_25d history rather than reusing it).
    """
    lines = [
        "SKEW TERM STRUCTURE (25-delta, quote convention: RR = call IV - put IV)",
        _SUB_SEPARATOR,
    ]

    if result is None or not result.entries:
        lines.append("  No skew data available")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"  {'Expiry':<10}  {'DTE':>7}  {'ATM IV':>8}  "
        f"{'RR25':>20}  {'BF25':>14}  {'Chain':>9}"
    )

    for entry in result.entries:
        dte_str = f"{entry.dte:.1f}d"
        atm_str = f"{entry.atm_iv_interp:.2f}%" if entry.atm_iv_interp is not None else "n/a"
        chain_str = f"{entry.n_quotes_used} quotes" if entry.n_quotes_used is not None else "n/a"
        lines.append(
            f"  {entry.expiration:<10}  {dte_str:>7}  {atm_str:>8}  "
            f"{_format_rr25_cell(entry):>20}  {_format_bf25_cell(entry):>14}  {chain_str:>9}"
        )

    if result.rr_slope is not None:
        direction = (
            "back-month more put-skewed" if result.rr_slope < 0
            else "back-month more call-skewed"
        )
        lines.append(f"  RR slope (front->back): {result.rr_slope:+.2f} vol pts   [{direction}]")

    lines.append("")
    return "\n".join(lines)


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
    """
    Render the FUTURES BASIS section.

    Mirrors MarketWideCalculator.calculate_futures_basis exactly (must stay
    in lockstep — bugfix_spec.md Item 5): the convention header, and "n/a"
    instead of a formatted percentage when annualized_premium_pct is None.
    A suppressed tenor distinguishes "expired" (dte < 0) from "<1d"
    (dte == 0), matching the legacy text-generation branch this function
    replaces (T6, carried from A4 review) — the raw (unannualized) basis is
    shown alongside the "<1d" case, reconstructed from mark_price/index_price
    since it is not itself a stored field (Decision D12).
    """
    lines = ["FUTURES BASIS (annualized simple, ACT/365, to 08:00 UTC settlement)", _SUB_SEPARATOR]

    if result is None or not result.entries:
        lines.append("  No futures data available")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  {'Future':>20}  {'Price':>12}  {'Spot':>12}  {'Ann. Premium':>12}")
    lines.append(f"  {'------':>20}  {'-----':>12}  {'----':>12}  {'------------':>12}")

    for entry in result.entries:
        raw_basis_note = ""
        if entry.annualized_premium_pct is None:
            if entry.dte is None:
                ann_display = "n/a"
            elif entry.dte < 0:
                ann_display = "n/a (expired)"
            else:
                ann_display = "n/a (<1d)"
                basis_pct = ((entry.mark_price - entry.index_price) / entry.index_price) * 100.0
                raw_basis_note = f"  (raw basis: {basis_pct:.4f}%)"
        else:
            ann_display = f"{entry.annualized_premium_pct:>.1f}%"
        lines.append(
            f"  {entry.instrument_name:>20}  ${entry.mark_price:>11,.0f}  "
            f"${entry.index_price:>11,.0f}  {ann_display:>12}{raw_basis_note}"
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
    """
    Render the VOLATILITY CONE section.

    T10 (refactor_design_spec.md): the legacy table has 6 columns (Window /
    Current / 25th / Median / 75th / Pctile) -- rendered here when
    ``result.stats_by_window`` is populated (the one production call site
    always populates it). Falls back to the 2-column
    (Window/Pctile)-only table when it is not (e.g. a caller/test that only
    ever needed the percentile), matching this function's own prior
    behavior for that case.
    """
    lines = ["VOLATILITY CONE", _SUB_SEPARATOR]

    if result is None or not result.percentile_by_window:
        lines.append("  Insufficient price history for vol cone")
        lines.append("")
        return "\n".join(lines)

    if result.stats_by_window:
        lines.append(
            f"  {'Window':>8}  {'Current':>8}  {'25th':>8}  "
            f"{'Median':>8}  {'75th':>8}  {'Pctile':>8}"
        )
        lines.append(
            f"  {'------':>8}  {'-------':>8}  {'----':>8}  "
            f"{'------':>8}  {'----':>8}  {'------':>8}"
        )
        for window in sorted(result.stats_by_window.keys()):
            stats = result.stats_by_window[window]
            lines.append(
                f"  {window:>6}d  {stats.current_rv:>7.1f}%  {stats.p25:>7.1f}%  "
                f"{stats.p50:>7.1f}%  {stats.p75:>7.1f}%  {stats.percentile:>6.0f}th"
            )
    else:
        lines.append(f"  {'Window':>8}  {'Pctile':>8}")
        lines.append(f"  {'------':>8}  {'------':>8}")
        for window in sorted(result.percentile_by_window.keys()):
            percentile = result.percentile_by_window[window]
            lines.append(f"  {window:>6}d  {percentile:>6.0f}th")

    lines.append("")
    return "\n".join(lines)


def format_perpetual_funding_section(result: Optional[PerpetualFundingResult]) -> str:
    """
    Render the PERPETUAL FUNDING & OI section.

    Mirrors MarketWideCalculator.calculate_perpetual_funding_trend exactly
    (must stay in lockstep — bugfix_spec.md Item 4): annualization uses
    funding_8h (the realised 8h rate), never funding_rate/current_funding
    (the instantaneous accruing rate) — a 61x divergence was observed live
    between the two. Gates on funding_8h OR funding_rate being present, not
    funding_rate alone, so a missing instantaneous reading doesn't suppress
    a present 8h reading (or vice versa).
    """
    lines = ["PERPETUAL FUNDING & OI", _SUB_SEPARATOR]

    if result is None or (result.funding_8h is None and result.funding_rate is None):
        lines.append("  Funding data not available")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  Perp OI: {result.perp_open_interest:,.0f} USD")
    if result.funding_8h is not None:
        ann_funding = result.funding_8h * FUNDING_PERIODS_PER_YEAR * 100
        lines.append(
            f"  Funding (8h): {result.funding_8h * 100:.4f}%  |  "
            f"Annualized: {ann_funding:.2f}%  |  Trend: {result.funding_trend}"
        )
    else:
        lines.append("  Funding (8h): not available")
    if result.funding_rate is not None:
        lines.append(f"  Instantaneous funding: {result.funding_rate * 100:.4f}%")

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
        # bugfix_spec.md Item 11: the label must say "log changes" so a
        # reader comparing against a previously-stored levels-based value
        # knows why the number changed.
        if result.dvol_correlation_observations is not None:
            lines.append(
                f"  DVOL Correlation (log changes, {result.dvol_correlation_observations}d): "
                f"{result.dvol_correlation:.2f}"
            )
        else:
            lines.append(f"  DVOL Correlation: {result.dvol_correlation:.2f}")
    elif result.sample_size > 0:
        lines.append("  DVOL Correlation: Insufficient data")
    else:
        lines.append("  DVOL Correlation: N/A")

    lines.append("")
    return "\n".join(lines)
