"""
Text formatting for the open-interest-change and IV-percentile sections.

Extracted verbatim (behavior-preserving) from
``OnChainAnalysisService._format_oi_changes`` (lines ~802-876) and the
inline IV-percentile block (lines ~776-797) per
refactor_design_spec.md section T3, operating on the typed
``OiChangesResult`` / ``IvPercentileResult`` (section T2) instead of raw
dicts.

Not yet wired into the service — the service still builds and pre-renders
this text itself until T8 kills the text splitter. Until then,
``OnChainAnalyzer`` keeps consuming the service's own pre-rendered text
verbatim (T3's "temporary adapter").

Callers are responsible for the legacy "omit the whole section" gating —
``OiChangesResult`` is only rendered when ``has_previous_snapshot`` is True
(matching the legacy ``if not prev_oi: return None`` early exit, which
skipped setting ``oi_changes_data`` at all rather than rendering an empty
section).
"""

from coding.core.analytics.results.analysis_result import IvPercentileResult, OiChangesResult

_SUB_SEPARATOR = "-" * 80


def format_oi_changes_section(result: OiChangesResult) -> str:
    """
    Render the LARGE OI CHANGES (Day-over-Day) section.

    Args:
        result: OI changes since the previous snapshot. Callers should only
            invoke this when ``result.has_previous_snapshot`` is True — the
            legacy code omitted the section entirely otherwise.

    Returns:
        Formatted string for inclusion in the analysis report.
    """
    lines = ["LARGE OI CHANGES (Day-over-Day)", _SUB_SEPARATOR]

    if not result.rows:
        lines.append("  No significant OI changes (>20%) detected")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        f"  {'Strike':>10}  {'Type':>4}  {'Prev OI':>10}  "
        f"{'Curr OI':>10}  {'Change':>10}  {'Change%':>8}"
    )
    lines.append(
        f"  {'------':>10}  {'----':>4}  {'-------':>10}  "
        f"{'-------':>10}  {'------':>10}  {'-------':>8}"
    )

    for row in result.rows[:15]:
        type_label = "Call" if row.option_type == "C" else "Put"
        lines.append(
            f"  {row.strike:>10,.0f}  {type_label:>4}  "
            f"{row.previous_oi:>10,.0f}  {row.current_oi:>10,.0f}  "
            f"{row.change:>+10,.0f}  {row.change_pct:>+7.1f}%"
        )

    lines.append("")
    return "\n".join(lines)


def format_iv_percentile_section(result: IvPercentileResult) -> str:
    """
    Render the IV PERCENTILE (per-expiry) block.

    Args:
        result: IV percentile result for one expiration's ATM strike.

    Returns:
        Formatted string, ending in a blank line (matching the legacy
        in-service concatenation ``existing + iv_section``, where
        ``iv_section`` always ended with an extra blank line).
    """
    lines = [
        f"IV PERCENTILE (per-expiry, {result.history_days} days history)",
        _SUB_SEPARATOR,
        f"ATM Strike: ${result.atm_strike:,.0f}  |  Current IV: {result.current_iv:.1f}%  |  "
        f"Percentile: {result.percentile:.1f}%",
    ]

    if result.percentile >= 80:
        lines.append("  IV is very high relative to history - favor selling vol")
    elif result.percentile <= 20:
        lines.append("  IV is very low relative to history - favor buying vol")

    return "\n".join(lines) + "\n\n"
