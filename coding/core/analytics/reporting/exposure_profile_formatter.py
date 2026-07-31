"""
Text formatting for the per-strike vanna/charm exposure profile (VEX/CEX)
section, institutional_metrics_spec.md section 4(c), Task C5.

Replaces the old aggregate "SECOND-ORDER GREEKS" narrative block that used
to live in ``vol_surface_formatter.format_vol_surface_section`` (spec 4(c):
"Report -- replaces the aggregate vanna/charm advice block entirely"). That
old block's underlying data (``VolSurfaceResult.second_order_greeks``,
computed via gamma-inversion tau) is UNCHANGED and still feeds
``synthesis.py``'s ``score_vanna_charm`` scoring engine -- only the TEXT
rendering of it is removed, superseded here by a per-strike table with
true instrument-name-derived tau (spec 4(b)).

Format matches the existing GEX/DEX per-strike table's pattern
(``gex_dex_formatter.format_gex_dex_section``): a fixed-width header,
separator rules, one row per strike, followed by peak/total summary lines.
No interpretive sentence -- per spec 4(c), "the numbers and their peaks are
the output."

Decision D7 (BINDING, established Wave B/task B2): holder-side raw is the
primary number, assumed-dealer view alongside in brackets -- same
convention/vocabulary as ``gex_dex_formatter``'s "EXPOSURES -- HOLDER SIDE"
/ "ASSUMED DEALER VIEW" blocks and ``vol_surface_formatter``'s
(now-removed) second-order-greeks block, reused unchanged.
"""

from typing import Optional

from coding.core.analytics.results.exposure_profile_results import ExposureProfileResult

_SEPARATOR = "-" * 80


def _format_millions(value: float) -> str:
    """"+3.42M" style, matching spec 4(c)'s sample report totals line."""
    return f"{value / 1_000_000:+.2f}M"


def _format_peak_strike(strike: Optional[float]) -> str:
    return f"${strike:,.0f}" if strike is not None else "None"


def format_exposure_profile_section(result: ExposureProfileResult, currency: str) -> str:
    """
    Render the VANNA / CHARM PROFILE section for one expiration.

    Args:
        result: Combined holder-side/assumed-dealer exposure profile result.
        currency: Underlying currency symbol. Unused in the rendered text
            today (VEX/CEX are always USD-denominated per spec 4(b), unlike
            GEX/DEX's per-currency DEX unit) -- kept for signature parity
            with the other per-expiration formatters.

    Returns:
        Formatted string for inclusion in the analysis report.
    """
    del currency  # not rendered -- VEX/CEX are always USD, see docstring

    lines = []
    lines.append("VANNA / CHARM PROFILE (holder-side raw; assumed-dealer view in brackets)")
    lines.append(_SEPARATOR)
    lines.append(
        f"{'Strike':>10}  {'Call OI':>10}  {'Put OI':>10}  "
        f"{'VEX (USD/vol pt)':>28}  {'CEX (USD/day)':>28}"
    )
    lines.append(
        f"{'------':>10}  {'-------':>10}  {'------':>10}  "
        f"{'----------------':>28}  {'-------------':>28}"
    )

    for row in result.strike_rows:
        vex_str = f"{row.vex_holder:+,.0f} [{row.vex_assumed_dealer:+,.0f}]"
        cex_str = f"{row.cex_holder:+,.0f} [{row.cex_assumed_dealer:+,.0f}]"
        lines.append(
            f"{row.strike:>10,.0f}  {row.call_oi:>10,.0f}  {row.put_oi:>10,.0f}  "
            f"{vex_str:>28}  {cex_str:>28}"
        )

    lines.append(_SEPARATOR)
    lines.append(
        f"Peak vanna strike: {_format_peak_strike(result.peak_vanna_strike)}"
        f"    Peak charm strike: {_format_peak_strike(result.peak_charm_strike)}"
    )
    lines.append(
        f"Total VEX: {_format_millions(result.total_vex_holder)} USD/vol pt"
        f"   Total CEX: {_format_millions(result.total_cex_holder)} USD/day"
    )
    lines.append("")

    return "\n".join(lines)
