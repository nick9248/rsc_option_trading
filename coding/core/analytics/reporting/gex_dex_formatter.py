"""
Text formatting for GEX/DEX (gamma/delta exposure) sections.

Extracted verbatim (behavior-preserving) from
``GexDexCalculator.generate_report_section()`` /
``GexDexCalculator.generate_aggregate_report_section()`` per
refactor_design_spec.md section T3, operating on the typed ``GexDexResult``
(section T2) instead of the calculator's internal dict.

Not yet wired into ``GexDexCalculator`` — ``calculate()`` still returns a
dict until T4 replaces it with ``GexDexResult``. Until then,
``OnChainAnalyzer`` keeps consuming the calculator's own
``generate_report_section()`` text verbatim (T3's "temporary adapter").

bugfix_spec.md Item 2 / task B1: the old "Zero Gamma Level" line (a
strike-axis cumulative-GEX sign-crossing artifact) is renamed to
"Cumulative GEX Zero Strike" and no longer labeled a gamma flip. A new
"GAMMA PROFILE" block reports the actual re-priced dealer-gamma flip
(``zero_gamma_level``) so a reader can tell the two apart -- see
``GexDexKeyLevels``'s docstring for why both are kept.

bugfix_spec.md Item 8: the old "TOTALS" block presented ``total_net_gex``
(a DEALER-side number: the SqueezeMetrics call-put-gamma-split heuristic)
and ``total_net_dex`` (a HOLDER-side number: the raw sum of what option
owners hold) side by side with no positioning label at all -- one
convention silently swapped for the other mid-report. Replaced with two
explicitly labelled blocks: "EXPOSURES -- HOLDER SIDE" (pure arithmetic on
observable OI/Greeks, no assumption) and "ASSUMED DEALER VIEW" (the
SqueezeMetrics heuristic, explicitly named as an assumption). Every line
that names an actor ("dealers...") now lives in the dealer block only.
``total_net_gex``/``total_net_dex`` keep their exact values (dealer_gamma_
exposure_total / delta_exposure_holder_total are aliases) -- this is
presentation-only, not a value change.
"""

from coding.core.analytics.results.gex_dex_results import GexDexResult
from coding.core.analytics.results.market_wide_results import GammaRolloffResult

_SEPARATOR = "-" * 80

_REGIME_LABELS = {
    "POSITIVE": "POSITIVE - dealers long gamma (stabilizing)",
    "NEGATIVE": "NEGATIVE - dealers short gamma (amplifying volatility)",
    "FLAT": "FLAT - net dealer gamma ~0 across the book",
    "UNKNOWN": "UNKNOWN - insufficient data to re-price",
}


def _key_levels_and_totals_lines(result: GexDexResult, dex_unit: str) -> list:
    """Shared KEY LEVELS / TOTALS / interpretation / GAMMA PROFILE block for both sections."""
    lines = []
    key_levels = result.key_levels
    lines.append("KEY LEVELS:")

    if key_levels.call_resistance:
        cr = key_levels.call_resistance
        lines.append(f"  Call Resistance: ${cr.strike:,.0f} (Net GEX: {cr.net_gex:+,.2f} USD)")
    else:
        lines.append("  Call Resistance: None found")

    if key_levels.put_support:
        ps = key_levels.put_support
        lines.append(f"  Put Support: ${ps.strike:,.0f} (Net GEX: {ps.net_gex:+,.2f} USD)")
    else:
        lines.append("  Put Support: None found")

    if key_levels.cumulative_gex_zero_strike:
        lines.append(
            f"  Cumulative GEX Zero Strike: ${key_levels.cumulative_gex_zero_strike:,.0f} "
            "(strike-axis cumulative-GEX sign change -- NOT a re-priced gamma flip; "
            "see GAMMA PROFILE below)"
        )
    else:
        lines.append("  Cumulative GEX Zero Strike: Not detected")

    lines.append("")

    # Task G2-A (Wave G fresh audit, bug 2): disclose the completeness gap
    # directly in this section whenever there is one -- not just via the
    # header EVIDENCE line's now-conditional "full book" claim. Only
    # rendered when instruments_missing_gamma > 0 (matches the
    # "no data -> no section" convention every other conditional block in
    # this file already uses); no threshold gate here, unlike the
    # EVIDENCE line's claim -- ANY known gap is worth surfacing at the
    # point readers look for it, even a small one.
    if result.instruments_missing_gamma > 0:
        total_oi = sum(row.call_oi + row.put_oi for row in result.strike_rows)
        if total_oi > 0:
            pct_note = f" ({min(result.oi_missing_gamma / total_oi, 1.0):.1%} of total OI)"
        elif result.oi_missing_gamma > 0:
            # Wave G re-review (Important #2): total_oi == 0 with a real
            # OI gap means every instrument this expiration had was
            # dropped before it ever reached the strike table (100%
            # ticker-fetch failure) -- 100% missing, not "nothing to
            # compute a percentage from".
            pct_note = " (100.0% of total OI)"
        else:
            pct_note = ""
        lines.append(
            f"DATA COMPLETENESS: {result.instruments_missing_gamma} instrument(s) "
            f"({result.oi_missing_gamma:,.2f} OI){pct_note} had missing gamma/delta or "
            "failed their ticker fetch entirely -- excluded from or zeroed out of the "
            "exposure totals above"
        )
        lines.append("")

    # bugfix_spec.md Item 8: holder-side raw exposures -- pure arithmetic on
    # observable OI/Greeks (call_gamma + put_gamma etc.), no ASSUMED-DEALER
    # heuristic layered on top. No actor other than "the holder" is named
    # here.
    #
    # Wave-I-C Fix 4: the old label claimed "no positioning assumption",
    # which overclaims -- summing call-side and put-side exposure as if
    # every single open contract is held LONG (never short) IS itself a
    # positioning assumption, just a different one from the dealer-side
    # heuristic below (and a defensible convention, since OI alone can't
    # distinguish a long holder from a short one). State it plainly
    # instead of claiming there is none.
    lines.append("EXPOSURES -- HOLDER SIDE (assumes every open contract is held long)")
    lines.append(_SEPARATOR)
    holder_gamma = result.gamma_exposure_holder_total
    holder_dex = result.delta_exposure_holder_total
    lines.append(f"  Gamma Exposure: {holder_gamma:+,.2f} USD per 1% move")
    lines.append(f"  Delta Exposure: {holder_dex:+,.4f} {dex_unit}")
    if holder_dex > 0:
        lines.append("  -> Option holders are net long delta")
    elif holder_dex < 0:
        lines.append("  -> Option holders are net short delta")
    else:
        lines.append("  -> Option holders are delta-neutral")
    lines.append("")

    # bugfix_spec.md Item 8: the SqueezeMetrics assumed-dealer heuristic
    # (dealers long calls / short puts for gamma; short whatever holders
    # hold for delta) -- explicitly labelled as an assumption, derived from
    # the holder-side numbers above, not conflated with them.
    lines.append("ASSUMED DEALER VIEW  (assumption: dealers long calls / short puts for")
    lines.append("                      gamma, short customer delta)")
    lines.append(_SEPARATOR)
    dealer_gamma = result.dealer_gamma_exposure_total
    dealer_dex = result.dealer_delta_exposure_total
    lines.append(f"  Dealer Gamma:   {dealer_gamma:+,.2f} USD per 1% move")
    if dealer_gamma > 0:
        lines.append("                  -> POSITIVE: dealers long gamma, stabilizing (buy dips/sell rallies)")
    elif dealer_gamma < 0:
        lines.append("                  -> NEGATIVE: dealers short gamma, amplifying volatility")
    else:
        lines.append("                  -> NEUTRAL")
    # bugfix_spec.md Item 8 fix-review (Critical #2, then round-2 Important
    # finding on the first fix): this line originally asserted a
    # directional "bullish"/"bearish" call that directly contradicted
    # synthesis.ScoringEngine.score_dex's own docstring in this same
    # codebase. The first fix replaced it with "...they sell/buy the
    # underlying as spot rises" -- still wrong: whether dealers buy or
    # sell AS SPOT MOVES is a GAMMA question (the Dealer Gamma line two
    # lines above already answers it -- short gamma means dealers buy as
    # spot rises, long gamma means they sell), not a delta question. Delta
    # only tells you the hedge direction needed RIGHT NOW to get back to
    # neutral -- no spot-direction claim. Present tense, mechanics only.
    lines.append(f"  Dealer Delta:   {dealer_dex:+,.4f} {dex_unit}")
    if dealer_dex > 0:
        lines.append("                  -> Dealers net long delta; hedging back to neutral means selling the underlying")
    elif dealer_dex < 0:
        lines.append("                  -> Dealers net short delta; hedging back to neutral means buying the underlying")
    else:
        lines.append("                  -> Dealers delta-neutral")
    lines.append("")

    lines.extend(_gamma_profile_lines(result))

    return lines


def _gamma_profile_lines(result: GexDexResult) -> list:
    """
    GAMMA PROFILE block (bugfix_spec.md Item 2 / F2.3.3): the re-priced,
    sticky-strike dealer-gamma flip, distinct from the strike-axis
    "Cumulative GEX Zero Strike" above.
    """
    key_levels = result.key_levels
    spot_price = result.spot_price

    lines = ["GAMMA PROFILE (re-priced, sticky-strike):"]

    net_gex_at_spot = key_levels.net_gex_at_spot
    regime = key_levels.gamma_regime
    regime_label = _REGIME_LABELS.get(regime, _REGIME_LABELS["UNKNOWN"])
    if net_gex_at_spot is not None:
        lines.append(
            f"  Net GEX at spot ${spot_price:,.0f}: {net_gex_at_spot:+,.2f} USD ({regime_label})"
        )
    else:
        lines.append(f"  Net GEX at spot ${spot_price:,.0f}: not available ({regime_label})")

    zero_gamma_level = key_levels.zero_gamma_level
    if zero_gamma_level is not None:
        pct_from_spot = (
            (zero_gamma_level - spot_price) / spot_price * 100 if spot_price else 0.0
        )
        lines.append(
            f"  Zero Gamma Level:         ${zero_gamma_level:,.0f}  ({pct_from_spot:+.1f}% from spot)"
        )
    elif regime == "POSITIVE":
        lines.append(
            "  Zero Gamma Level:         none within ±50% of spot "
            "(net GEX positive across the whole range)"
        )
    elif regime == "NEGATIVE":
        lines.append(
            "  Zero Gamma Level:         none within ±50% of spot "
            "(net GEX negative across the whole range)"
        )
    elif regime == "FLAT":
        lines.append(
            "  Zero Gamma Level:         none (net dealer gamma is ~0 across the whole book)"
        )
    else:
        lines.append("  Zero Gamma Level:         not available (insufficient data to re-price)")

    crossings = list(key_levels.zero_gamma_crossings)
    other_crossings = [c for c in crossings if c != zero_gamma_level]
    if other_crossings:
        other_str = ", ".join(f"${c:,.0f}" for c in sorted(other_crossings))
        lines.append(f"  Other crossings:          {other_str}")
    else:
        lines.append("  Other crossings:          none")

    lines.append("")
    return lines


def format_gex_dex_section(result: GexDexResult, currency: str) -> str:
    """
    Render the per-expiration GEX/DEX section (KEY LEVELS, TOTALS,
    per-strike table).

    Args:
        result: GEX/DEX result for one expiration.
        currency: Underlying currency symbol for unit labels.

    Returns:
        Formatted string for inclusion in the analysis report.
    """
    lines = []
    lines.append("GEX/DEX ANALYSIS (Gamma & Delta Exposure)")
    lines.append(_SEPARATOR)
    lines.append(f"Spot Price: ${result.spot_price:,.2f}")
    lines.append("")

    lines.extend(_key_levels_and_totals_lines(result, currency))

    key_levels = result.key_levels

    lines.append("GEX/DEX BY STRIKE:")
    lines.append(_SEPARATOR)
    lines.append(
        "Net GEX = assumed-dealer gamma exposure, USD per 1% spot move."
    )
    lines.append(
        f"{'Strike':>10}  {'Net GEX(USD)':>13}  {'Net DEX(' + currency + ')':>12}  "
        f"{'Cum GEX(USD)':>13}  {'Cum DEX(' + currency + ')':>12}  Notes"
    )
    lines.append(
        f"{'------':>10}  {'-------':>13}  {'-------':>12}  "
        f"{'-------':>13}  {'-------':>12}  -----"
    )

    for row in sorted(result.strike_rows, key=lambda r: r.strike):
        strike = row.strike
        cumulative_gex = result.cumulative_gex.get(strike, 0.0)
        cumulative_dex = result.cumulative_dex.get(strike, 0.0)

        notes = []
        if key_levels.call_resistance and strike == key_levels.call_resistance.strike:
            notes.append("Call Resistance")
        if key_levels.put_support and strike == key_levels.put_support.strike:
            notes.append("Put Support")
        if key_levels.cumulative_gex_zero_strike and strike == key_levels.cumulative_gex_zero_strike:
            notes.append("Cumulative GEX Zero Strike")

        notes_str = " | ".join(notes) if notes else ""

        lines.append(
            f"{strike:>10,.0f}  {row.net_gex:>+12,.2f}  {row.net_dex:>+12,.4f}  "
            f"{cumulative_gex:>+12,.2f}  {cumulative_dex:>+12,.4f}  {notes_str}"
        )

    lines.append("")
    return "\n".join(lines)


def format_aggregate_gex_dex_section(result: GexDexResult, spot_price: float, currency: str) -> str:
    """
    Render the market-wide (cross-expiry aggregate) GEX/DEX section.

    No per-strike table — with hundreds of strikes merged across expirations
    it would be unreadable. Per-strike data remains available on ``result``
    for programmatic access.

    Args:
        result: Aggregated GEX/DEX result (``expiration_count`` set).
        spot_price: Current underlying spot price.
        currency: Underlying currency symbol for unit labels.

    Returns:
        Formatted string for inclusion in the market-wide report section.
    """
    lines = []
    expiration_count = result.expiration_count or 0

    lines.append(f"MARKET-WIDE GEX/DEX LEVELS (All {expiration_count} Expirations Aggregated)")
    lines.append(_SEPARATOR)
    lines.append(f"Spot Price: ${spot_price:,.2f}")
    lines.append("")

    lines.extend(_key_levels_and_totals_lines(result, currency))

    return "\n".join(lines)


def format_gamma_rolloff_section(result: GammaRolloffResult) -> str:
    """
    Render the GAMMA ROLL-OFF section (institutional_metrics_spec.md
    section 5, Task C6): market-wide, placed immediately after AGGREGATE
    GEX/DEX per the spec's report layout.

    ``result.gross_total == 0`` (no gamma anywhere -- e.g. no expiries, or
    every per-expiry ``total_net_gex`` is 0) renders a short "no gamma"
    placeholder instead of a table (spec 5(c) edge case) -- there is
    nothing to divide by, and printing a table of ``None`` shares would be
    misleading, not just ugly.

    Net GEX is always USD (bugfix_spec.md Item 8 / GexDexCalculator class
    docstring) -- no currency/unit parameter needed here, unlike the
    GEX/DEX sections, which also render a DEX column in the underlying
    currency.
    """
    lines = ["GAMMA ROLL-OFF", _SEPARATOR]

    if result.gross_total <= 0:
        lines.append("no gamma (no open interest / net GEX anywhere across expiries)")
        lines.append(_SEPARATOR)
        lines.append("")
        return "\n".join(lines)

    # The 7d boundary row is the LAST row (chronological order) with
    # dte_days <= 7.0 -- the mockup in spec 5(c) marks only that one row,
    # not every row inside the window.
    boundary_idx = None
    for i, row in enumerate(result.rows):
        if row.dte_days <= 7.0:
            boundary_idx = i

    lines.append(
        f"{'Expiry':<12} {'DTE':>7}  {'Net GEX (USD, signed)':>22}  "
        f"{'Share':>7}  {'Cumulative':>10}"
    )
    for i, row in enumerate(result.rows):
        marker = "   <-- 7d" if i == boundary_idx else ""
        lines.append(
            f"{row.expiration:<12} {row.dte_days:>6.1f}d  {row.net_gex:>+22,.2f}  "
            f"{row.share_pct:>6.1f}%  {row.cum_share_pct:>9.1f}%{marker}"
        )
    lines.append(_SEPARATOR)

    net_contrib_7d = result.rows[boundary_idx].cum_net_gex if boundary_idx is not None else 0.0

    flag_suffix = (
        "  ** GAMMA CLIFF ** (threshold 30% -- presentation flag, not a trading signal)"
        if result.gamma_cliff_7d
        else " (below the 30% threshold; presentation flag, not a trading signal)"
    )
    lines.append(
        f"{result.cum_share_7d:.1f}% of gamma mass expires within 7 days{flag_suffix}"
    )
    lines.append(f"Signed net contribution rolling off in 7d: {net_contrib_7d:+,.2f} USD")
    if result.cum_share_30d is not None:
        lines.append(f"Cumulative gamma mass within 30 days: {result.cum_share_30d:.1f}%")
    lines.append("")
    return "\n".join(lines)
