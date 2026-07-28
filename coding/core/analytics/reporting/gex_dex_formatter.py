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

    # bugfix_spec.md Item 8: holder-side raw exposures -- pure arithmetic on
    # observable OI/Greeks, no positioning assumption. No actor is named here.
    lines.append("EXPOSURES -- HOLDER SIDE (raw, no positioning assumption)")
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
    # bugfix_spec.md Item 8 fix-review (Critical #2): this line previously
    # asserted a directional "bullish"/"bearish" call that directly
    # contradicted synthesis.ScoringEngine.score_dex's own docstring in
    # this same codebase (positive DEX -> dealers short delta -> dealers
    # BUY to hedge -> bullish; the formatter had "dealers net short delta -
    # bearish pressure", the opposite conclusion from the same number).
    # Ruling: describe the hedging MECHANICS only (matches bugfix_spec.md
    # F8.3.2's own example text, which never labels this bullish/bearish
    # either) -- do not re-derive a directional tag a second time.
    lines.append(f"  Dealer Delta:   {dealer_dex:+,.4f} {dex_unit}")
    if dealer_dex > 0:
        lines.append("                  -> Dealers net long delta; they sell the underlying as spot rises")
    elif dealer_dex < 0:
        lines.append("                  -> Dealers net short delta; they buy the underlying as spot rises")
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
