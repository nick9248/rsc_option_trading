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

import math
from datetime import datetime, timezone
from typing import Optional

from coding.core.analytics.market_wide_calculator import FUNDING_PERIODS_PER_YEAR
from coding.core.analytics.reporting.delta_flow_formatter import (
    format_delta_flow_coverage_line,
)
from coding.core.analytics.results.market_wide_results import (
    BlockTradesResult,
    CrossAssetCorrelationResult,
    ForwardVolResult,
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

    # Task C4 review Minor #1: column widths must fit the WIDEST rendered
    # cell, not just the T3.1-style numeric-only examples. Worst case:
    # RR25 = sign+2 decimals (up to 7: e.g. "-100.00") + "  p" + up to 3
    # percentile digits ("p100") + " " + the longest regime label
    # ("EXTREME HIGH"/"EXTREME LOW", 12/11 chars) = ~26. BF25 has no
    # regime word but the "insufficient chain"/"n/a (N obs)" fallback
    # strings (19/~19 chars) are the binding constraint there.
    _RR25_WIDTH = 26
    _BF25_WIDTH = 21

    lines.append(
        f"  {'Expiry':<10}  {'DTE':>7}  {'ATM IV':>8}  "
        f"{'RR25':>{_RR25_WIDTH}}  {'BF25':>{_BF25_WIDTH}}  {'Chain':>9}"
    )

    for entry in result.entries:
        dte_str = f"{entry.dte:.1f}d"
        atm_str = f"{entry.atm_iv_interp:.2f}%" if entry.atm_iv_interp is not None else "n/a"
        chain_str = f"{entry.n_quotes_used} quotes" if entry.n_quotes_used is not None else "n/a"
        lines.append(
            f"  {entry.expiration:<10}  {dte_str:>7}  {atm_str:>8}  "
            f"{_format_rr25_cell(entry):>{_RR25_WIDTH}}  "
            f"{_format_bf25_cell(entry):>{_BF25_WIDTH}}  {chain_str:>9}"
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


_FORWARD_VOL_WINDOW_WIDTH = 22
_FORWARD_VOL_FWD_WIDTH = 22  # fits "NEG VARIANCE (-0.9600)" (spec 8(c) edge case)


def format_forward_vol_section(result: Optional[ForwardVolResult]) -> str:
    """
    Render the FORWARD VOL section (institutional_metrics_spec.md
    section 8, Task C9), directly after IV TERM STRUCTURE in the report
    (which it explains) -- see ``_MARKET_WIDE_SECTION_ORDER`` in
    report_formatter.py.

    ASCII-only (no sigma/arrow unicode glyphs): mirrors
    fixed_strike_vol_formatter.py's explicit convention -- this machine's
    default console codec is cp1252, which raises ``UnicodeEncodeError``
    on non-ASCII glyphs.

    Negative-variance row (spec 8(c) edge case): the "Forward sigma"
    column shows literal text ``NEG VARIANCE (<raw variance>)`` instead of
    a number -- the raw (negative) variance is the diagnostic value here,
    not a fabricated square root of a negative number. The section header
    additionally gains a DATA QUALITY warning line when ANY bucket has
    negative variance (``result.has_negative_variance``), so a reader
    scanning only the header still sees the alarm.
    """
    lines = ["FORWARD VOL", _SUB_SEPARATOR]

    if result is None or not result.buckets:
        lines.append("  No forward vol data available")
        lines.append("")
        return "\n".join(lines)

    if result.has_negative_variance:
        lines.append(
            "  DATA QUALITY WARNING: negative forward variance on at least "
            "one leg below -- calendar-spread arb / stale ATM IV, not a "
            "trade signal"
        )

    lines.append(
        f"  {'Window':<{_FORWARD_VOL_WINDOW_WIDTH}}  {'T1':>7}  {'T2':>7}  "
        f"{'sigma1':>7}  {'sigma2':>7}  "
        f"{'Forward sigma':>{_FORWARD_VOL_FWD_WIDTH}}  {'Flag':<13}"
    )

    for bucket in result.buckets:
        window = f"{bucket.from_expiry} -> {bucket.to_expiry}"
        if bucket.negative_variance:
            fwd_str = f"NEG VARIANCE ({bucket.fwd_var:.4f})"
        else:
            fwd_str = f"{bucket.fwd_vol_pct:.2f}"
        flag_str = "EVENT PREMIUM" if "EVENT_PREMIUM" in bucket.flags else ""

        lines.append(
            f"  {window:<{_FORWARD_VOL_WINDOW_WIDTH}}  {bucket.t1_days:>6.1f}d  "
            f"{bucket.t2_days:>6.1f}d  {bucket.sigma1_pct:>7.2f}  "
            f"{bucket.sigma2_pct:>7.2f}  {fwd_str:>{_FORWARD_VOL_FWD_WIDTH}}  {flag_str:<13}"
        )

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
    """
    Render the BLOCK TRADES section (institutional_metrics_spec.md section
    9 / Migration M2, Task D1): one row per block (grouped by
    ``block_trade_id``, showing leg count / combined premium / combo
    structure), followed by a clearly separated LARGE PRINTS section for
    the pre-existing notional-filter list -- explicitly labelled "screen
    prints, not blocks" so the two are never confused. A trade that is
    part of a block never also appears in large prints (no double
    counting; enforced upstream by ``MarketWideCalculator.
    detect_block_trades``).

    History is not backfillable: block_trade_id was never persisted
    before migration 022, so the block section states ``tracked_since``
    as its effective start date rather than implying "no data" when the
    window happens to contain zero blocks.
    """
    if result is None:
        lines = ["BLOCK TRADES", _SUB_SEPARATOR, "  No recent trade data available", ""]
        return "\n".join(lines)

    lines = ["BLOCK TRADES", _SUB_SEPARATOR]
    if result.tracked_since:
        lines.append(
            f"  Tracked since {result.tracked_since} "
            "(block_trade_id was not captured before this date; history is not backfillable)"
        )

    if not result.blocks:
        lines.append("  No blocks detected in recent activity")
    else:
        lines.append(
            f"  {'Block ID':>16}  {'Legs':>4}  {'Structure':>24}  "
            f"{'Premium (USD)':>16}  {'Time':>12}"
        )
        lines.append(
            f"  {'--------':>16}  {'----':>4}  {'---------':>24}  "
            f"{'-------------':>16}  {'----':>12}"
        )
        for block in result.blocks:
            if block.timestamp:
                # independent review round 2 (Important #2): naive-local
                # datetime is the exact banned bug class this campaign has
                # already spent 5 fix rounds on -- explicit UTC always.
                time_str = datetime.fromtimestamp(
                    block.timestamp / 1000, tz=timezone.utc
                ).strftime("%H:%M:%S")
            else:
                time_str = "N/A"
            structure = block.combo_id or "N/A"
            leg_str = (
                str(block.leg_count)
                if block.leg_count == block.observed_leg_count
                else f"{block.observed_leg_count}/{block.leg_count}"
            )
            lines.append(
                f"  {block.block_trade_id:>16}  {leg_str:>4}  {structure:>24}  "
                f"${block.combined_premium_usd:>15,.0f}  {time_str:>12}"
            )
    lines.append("")

    lines.append(
        f"LARGE PRINTS (screen prints, not blocks; >${result.notional_threshold:,.0f} notional)"
    )
    lines.append(_SUB_SEPARATOR)

    if not result.trades:
        lines.append("  No large prints detected in recent activity")
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
            # Wave H fresh-audit finding (Task Wave-H-C): this table sits
            # ~38 lines below the block-trades table above, which was
            # already fixed to render explicit UTC ("independent review
            # round 2 (Important #2): naive-local datetime is the exact
            # banned bug class this campaign has already spent 5 fix
            # rounds on") -- that fix simply missed this sibling table in
            # the same rendered report section. Same fix, same reasoning.
            time_str = datetime.fromtimestamp(
                trade.timestamp / 1000, tz=timezone.utc
            ).strftime("%H:%M:%S")
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


def format_cross_asset_correlation_line(
    result: Optional[CrossAssetCorrelationResult],
    currency: str,
) -> str:
    """
    One-line BTC/ETH change-correlation fact (institutional_metrics_spec.md
    section 9: "BTC/ETH correlation -> one line, and on DAILY CHANGES not
    levels"). Both correlations already ran on daily changes before this
    task -- price correlation via ``_calculate_return_correlation`` (log
    returns), DVOL correlation via log CHANGES of the raw DVOL levels
    (bugfix_spec.md Item 11) -- this function only demotes the report TEXT
    from a full titled section to one line, part of the market-wide
    CONTEXT block (``format_market_wide_context_section``).
    """
    other = result.other_currency if result is not None else ""
    pair = f"{currency}/{other}" if other else currency

    if result is None or result.price_correlation is None:
        price_str = "insufficient data"
    else:
        price_str = f"{result.price_correlation:.2f}"

    if result is not None and result.dvol_correlation is not None:
        if result.dvol_correlation_observations is not None:
            dvol_str = (
                f"{result.dvol_correlation:.2f} (log changes, "
                f"{result.dvol_correlation_observations}d)"
            )
        else:
            dvol_str = f"{result.dvol_correlation:.2f}"
    elif result is not None and result.sample_size > 0:
        dvol_str = "insufficient data"
    else:
        dvol_str = "N/A"

    return f"{pair} change-correlation (30d): price {price_str}  |  DVOL {dvol_str}"


def format_expected_move_line(dvol: Optional[float], underlying_price: float) -> str:
    """
    One-line expected daily/weekly/monthly move, integer dollars
    (institutional_metrics_spec.md section 9: "Expected daily/weekly/
    monthly move -> one line, integer dollars"). Replaces the header's old
    three-line $+% breakdown (report_formatter.OnChainReportFormatter.
    render_header).
    """
    if dvol is None:
        return "Expected Move: N/A (no DVOL)"

    daily_move = dvol / 100 / math.sqrt(365) * underlying_price
    weekly_move = dvol / 100 / math.sqrt(52) * underlying_price
    monthly_move = dvol / 100 / math.sqrt(12) * underlying_price
    return (
        f"Expected Move: 1d ${daily_move:,.0f}  |  7d ${weekly_move:,.0f}  |  "
        f"30d ${monthly_move:,.0f}"
    )


def format_market_wide_context_section(
    cross_asset: Optional[CrossAssetCorrelationResult],
    currency: str,
    dvol: Optional[float],
    underlying_price: float,
    delta_flow_has_total: bool = False,
    delta_flow_hours_present: int = 0,
    delta_flow_lookback_hours: float = 24.0,
    delta_flow_stale_since: Optional[datetime] = None,
) -> str:
    """
    Render the market-wide CONTEXT section (institutional_metrics_spec.md
    section 9(b), market-wide order item 10): one line each for BTC/ETH
    change-correlation and the expected move, plus (independent review
    round 2, Important #2) the delta-flow coverage/staleness disclosure.
    Rendered LAST in the market-wide block.

    The delta-flow coverage line is a table-wide fact (``flow_delta_
    hourly``'s coverage over the whole report window, the same value for
    every expiration) -- it is rendered here, once, rather than repeated
    in every expiration's per-expiry DELTA-ADJUSTED FLOW section (see
    ``delta_flow_formatter.format_delta_flow_coverage_line``'s docstring).
    Only rendered when ``delta_flow_has_total`` is True -- mirrors the
    prior (now-dropped) ``format_delta_flow_section``'s own gate ("no
    data -> no section": only render when there is an "ALL" bucket in
    ``OnChainAnalysisResult.delta_flow_buckets``), so an offline/no-
    repository run doesn't print a misleading "0/24h" line.

    Args:
        delta_flow_has_total: Whether ``OnChainAnalysisResult.
            delta_flow_buckets`` has an ``"ALL"`` entry -- the same
            "no data -> no section" gate as the (unused-in-production)
            ``format_delta_flow_section``.
        delta_flow_hours_present: See ``format_delta_flow_coverage_line``.
        delta_flow_lookback_hours: See ``format_delta_flow_coverage_line``.
        delta_flow_stale_since: See ``format_delta_flow_coverage_line``.
    """
    lines = ["CONTEXT", _SUB_SEPARATOR]
    lines.append(format_cross_asset_correlation_line(cross_asset, currency))
    lines.append(format_expected_move_line(dvol, underlying_price))
    if delta_flow_has_total:
        lines.append(
            format_delta_flow_coverage_line(
                delta_flow_hours_present, delta_flow_lookback_hours, delta_flow_stale_since,
            )
        )
    lines.append("")
    return "\n".join(lines)
