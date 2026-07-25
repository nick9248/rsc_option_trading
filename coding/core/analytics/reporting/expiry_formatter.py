"""
Text formatting for the per-expiration section of the on-chain analysis report.

Extracted verbatim (behavior-preserving) from
``OnChainAnalyzer.generate_report()`` / ``OnChainAnalyzer._format_trend()``
per refactor_design_spec.md section T3. Every line this module produces was
previously produced inline inside ``generate_report()`` — the golden-master
characterization test (tests/characterization/test_onchain_golden_master.py)
is the proof this extraction did not change a single byte of output.
"""

from typing import Optional

from coding.core.analytics.results.analysis_result import TrendSnapshot
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult

_SUB_SEPARATOR = "-" * 80


def format_trend_delta(current: float, previous: Optional[float], is_ratio: bool = False) -> str:
    """
    Format a trend arrow + delta against a previous value.

    Args:
        current: Current value.
        previous: Previous value, or None if unavailable.
        is_ratio: If True, format as ratio (2 decimal places); otherwise as integer.

    Returns:
        Formatted trend string, or empty string if no previous value.
    """
    if previous is None:
        return ""
    delta = current - previous
    if delta == 0:
        return "  [→ unchanged]"
    arrow = "↑" if delta > 0 else "↓"
    if is_ratio:
        return f"  [{arrow} from {previous:.2f}, {delta:+.2f}]"
    return f"  [{arrow} from {previous:,.0f}, {delta:+,.0f}]"


def format_expiration_section(
    analysis: ExpirationAnalysisResult,
    spot_price: float,
    trend: Optional[TrendSnapshot],
) -> str:
    """
    Render the summary / max-pain / put-call-ratio / volume / moneyness /
    strike-table / support-resistance blocks for one expiration.

    Args:
        analysis: The expiration's computed analysis result.
        spot_price: Current underlying spot price (matches the legacy
            ``self.underlying_price`` used throughout this section).
        trend: Previous DB snapshot for this expiration, or None if there is
            no prior record (or trend data was never set).

    Returns:
        Formatted multi-line string (no leading/trailing separators —
        the caller is responsible for the "EXPIRATION: ..." header line
        and the surrounding section separators).
    """
    lines = []

    # Summary
    lines.append(
        f"Total Instruments: {analysis.total_instruments} "
        f"({analysis.call_count} Calls, {analysis.put_count} Puts)"
    )
    lines.append("")

    # Max Pain
    max_pain_strike = analysis.max_pain.max_pain_strike
    lines.append("MAX PAIN ANALYSIS")
    lines.append(_SUB_SEPARATOR)
    if max_pain_strike is not None:
        lines.append(f"Max Pain Strike: ${max_pain_strike:,.0f}")
        diff = spot_price - max_pain_strike
        diff_pct = (diff / max_pain_strike * 100) if max_pain_strike else 0
        lines.append(f"Distance from Current: ${diff:+,.2f} ({diff_pct:+.2f}%)")
    else:
        lines.append("Max Pain Strike: N/A")

    if trend is not None:
        prev_mp = trend.max_pain_strike
        if prev_mp is not None and max_pain_strike is not None:
            trend_str = format_trend_delta(max_pain_strike, prev_mp)
            lines.append(f"Trend (Max Pain): {trend_str.strip()}")

    lines.append("")

    # Put/Call Ratio
    pcr = analysis.put_call_ratio
    lines.append("PUT/CALL RATIO (Open Interest)")
    lines.append(_SUB_SEPARATOR)
    lines.append(f"Total Call OI: {pcr.total_call_oi:,.0f}")
    lines.append(f"Total Put OI: {pcr.total_put_oi:,.0f}")
    if pcr.ratio != float("inf"):
        lines.append(f"P/C Ratio: {pcr.ratio:.2f} ({pcr.bias})")
    else:
        lines.append(f"P/C Ratio: N/A (No Call OI)")

    if trend is not None:
        prev_call_oi = trend.call_oi
        prev_put_oi = trend.put_oi
        prev_pc = trend.pc_ratio
        if prev_call_oi is not None:
            lines.append(
                f"Trend (Call OI):  {format_trend_delta(pcr.total_call_oi, prev_call_oi).strip()}"
            )
            lines.append(
                f"Trend (Put OI):   {format_trend_delta(pcr.total_put_oi, prev_put_oi).strip()}"
            )
        if prev_pc is not None and pcr.ratio != float("inf"):
            lines.append(
                f"Trend (P/C):      {format_trend_delta(pcr.ratio, prev_pc, is_ratio=True).strip()}"
            )

    lines.append("")

    # Volume Stats
    vol = analysis.volume_stats
    lines.append("VOLUME STATISTICS")
    lines.append(_SUB_SEPARATOR)
    lines.append(f"Total Call Volume: {vol.total_call_volume:,.2f}")
    lines.append(f"Total Put Volume: {vol.total_put_volume:,.2f}")
    lines.append(f"Total Volume: {vol.total_volume:,.2f}")
    if vol.volume_ratio != float("inf"):
        lines.append(f"Volume P/C Ratio: {vol.volume_ratio:.2f}")
    else:
        lines.append("Volume P/C Ratio: N/A (No Call Volume)")

    if trend is not None:
        prev_vol = trend.total_volume
        prev_vr = trend.volume_ratio
        if prev_vol is not None:
            lines.append(
                f"Trend (Volume):   {format_trend_delta(vol.total_volume, prev_vol).strip()}"
            )
        if prev_vr is not None and vol.volume_ratio != float("inf"):
            lines.append(
                f"Trend (Vol P/C):  {format_trend_delta(vol.volume_ratio, prev_vr, is_ratio=True).strip()}"
            )

    lines.append("")

    # ITM/OTM Analysis (Deribit-style, no ATM)
    money = analysis.moneyness
    totals = money.totals
    calls = money.calls
    puts = money.puts

    lines.append("MONEYNESS ANALYSIS (ITM/OTM)")
    lines.append(_SUB_SEPARATOR)
    lines.append(f"OI Skew: {money.oi_skew}")
    lines.append("")

    # Calls breakdown
    lines.append("CALLS:")
    lines.append(
        f"  ITM: {calls.itm_oi:>8,.0f} OI    "
        f"Notional: ${calls.itm_notional:>14,.2f}    ({calls.itm_pct:>5.2f}%)"
    )
    lines.append(
        f"  OTM: {calls.otm_oi:>8,.0f} OI    "
        f"Notional: ${calls.otm_notional:>14,.2f}    ({calls.otm_pct:>5.2f}%)"
    )
    lines.append(
        f"  Total: {calls.total_oi:>6,.0f} OI    "
        f"Notional: ${calls.total_notional:>14,.2f}"
    )
    lines.append("")

    # Puts breakdown
    lines.append("PUTS:")
    lines.append(
        f"  ITM: {puts.itm_oi:>8,.0f} OI    "
        f"Notional: ${puts.itm_notional:>14,.2f}    ({puts.itm_pct:>5.2f}%)"
    )
    lines.append(
        f"  OTM: {puts.otm_oi:>8,.0f} OI    "
        f"Notional: ${puts.otm_notional:>14,.2f}    ({puts.otm_pct:>5.2f}%)"
    )
    lines.append(
        f"  Total: {puts.total_oi:>6,.0f} OI    "
        f"Notional: ${puts.total_notional:>14,.2f}"
    )
    lines.append("")

    # Combined totals
    lines.append("COMBINED TOTALS:")
    lines.append(
        f"  ITM: {totals.itm_oi:>8,.0f} OI    "
        f"Notional: ${totals.itm_notional:>14,.2f}    ({totals.itm_pct:>5.2f}%)"
    )
    lines.append(
        f"  OTM: {totals.otm_oi:>8,.0f} OI    "
        f"Notional: ${totals.otm_notional:>14,.2f}    ({totals.otm_pct:>5.2f}%)"
    )
    lines.append(
        f"  Total: {totals.total_oi:>6,.0f} OI    "
        f"Notional: ${totals.total_notional:>14,.2f}"
    )
    lines.append("")

    # Open Interest and Volume by Strike
    lines.append("OPEN INTEREST & VOLUME BY STRIKE")
    lines.append(_SUB_SEPARATOR)
    lines.append(
        f"{'Strike':>10}  {'Call OI':>10}  {'Put OI':>10}  "
        f"{'Call Vol':>10}  {'Put Vol':>10}  Notes"
    )
    lines.append(
        f"{'------':>10}  {'--------':>10}  {'-------':>10}  "
        f"{'--------':>10}  {'-------':>10}  -----"
    )

    sr = analysis.support_resistance

    # Get top OI strikes for annotations
    top_call_strikes = set(level.strike for level in sr.resistance_levels)
    top_put_strikes = set(level.strike for level in sr.support_levels)

    for row in analysis.strike_rows:
        strike = row.strike

        notes = []
        if strike == max_pain_strike:
            notes.append("<< MAX PAIN")
        if strike in top_call_strikes:
            notes.append("Resistance")
        if strike in top_put_strikes:
            notes.append("Support")

        notes_str = " | ".join(notes) if notes else ""

        lines.append(
            f"{strike:>10,.0f}  {row.call_oi:>10,.0f}  "
            f"{row.put_oi:>10,.0f}  {row.call_volume:>10,.2f}  "
            f"{row.put_volume:>10,.2f}  {notes_str}"
        )
    lines.append("")

    # Support/Resistance Levels
    lines.append("SUPPORT/RESISTANCE LEVELS")
    lines.append(_SUB_SEPARATOR)

    lines.append("RESISTANCE (Top 3 Call OI):")
    for i, level in enumerate(sr.resistance_levels, 1):
        lines.append(f"  {i}. ${level.strike:,.0f} - Call OI: {level.open_interest:,.0f}")
    if not sr.resistance_levels:
        lines.append("  None found")
    lines.append("")

    lines.append("SUPPORT (Top 3 Put OI):")
    for i, level in enumerate(sr.support_levels, 1):
        lines.append(f"  {i}. ${level.strike:,.0f} - Put OI: {level.open_interest:,.0f}")
    if not sr.support_levels:
        lines.append("  None found")
    lines.append("")

    lines.append(f"SHORT-TERM LEVELS (nearest to current price ${spot_price:,.2f}):")
    if sr.short_term_resistance:
        lines.append(
            f"  Nearest Resistance: ${sr.short_term_resistance.strike:,.0f} "
            f"(Call OI: {sr.short_term_resistance.open_interest:,.0f})"
        )
    else:
        lines.append("  Nearest Resistance: None found above current price")

    if sr.short_term_support:
        lines.append(
            f"  Nearest Support: ${sr.short_term_support.strike:,.0f} "
            f"(Put OI: {sr.short_term_support.open_interest:,.0f})"
        )
    else:
        lines.append("  Nearest Support: None found below current price")

    lines.append("")

    return "\n".join(lines)
