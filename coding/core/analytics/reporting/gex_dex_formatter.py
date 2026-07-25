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
"""

from coding.core.analytics.results.gex_dex_results import GexDexResult

_SEPARATOR = "-" * 80


def _key_levels_and_totals_lines(result: GexDexResult, dex_unit: str) -> list:
    """Shared KEY LEVELS / TOTALS / interpretation block for both sections."""
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

    if key_levels.hvl:
        lines.append(f"  Zero Gamma Level: ${key_levels.hvl:,.0f}")
    else:
        lines.append("  Zero Gamma Level: Not detected")

    lines.append("")

    lines.append("TOTALS:")
    lines.append(f"  Total Net GEX: {result.total_net_gex:+,.2f} USD")
    lines.append(f"  Total Net DEX: {result.total_net_dex:+,.4f} {dex_unit}")
    lines.append("")

    total_gex = result.total_net_gex
    if total_gex > 0:
        gex_interp = "Positive (Dealers long gamma - stabilizing, buy dips/sell rallies)"
    elif total_gex < 0:
        gex_interp = "Negative (Dealers short gamma - amplifying volatility)"
    else:
        gex_interp = "Neutral"
    lines.append(f"  GEX Environment: {gex_interp}")

    total_dex = result.total_net_dex
    if total_dex > 0:
        dex_interp = "Positive (Net long delta - bullish pressure)"
    elif total_dex < 0:
        dex_interp = "Negative (Net short delta - bearish pressure)"
    else:
        dex_interp = "Neutral"
    lines.append(f"  DEX Environment: {dex_interp}")
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
        if key_levels.hvl and strike == key_levels.hvl:
            notes.append("Zero Gamma Level")

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
