"""
Text formatting for the per-expiration section of the on-chain analysis report.

Originally extracted verbatim (behavior-preserving) from
``OnChainAnalyzer.generate_report()`` / ``OnChainAnalyzer._format_trend()``
per refactor_design_spec.md section T3.

institutional_metrics_spec.md section 9 (Task D2 — report restructure):
``format_expiration_section`` now renders ONLY the instrument-count summary
and the raw OI/volume-by-strike table. The following full multi-line blocks
(several carrying trend arrows against a single prior DB snapshot) are
DELETED from this function per the removals table:

- MAX PAIN ANALYSIS (strike, distance-from-current, trend)
- PUT/CALL RATIO (Open Interest) (value, percentile/bias label, trend)
- VOLUME STATISTICS (call/put/total volume, ratio, trend)
- MONEYNESS ANALYSIS (ITM/OTM) (the hard-coded "OI Skew: Heavy OTM
  (Speculative)"/"Heavy ITM (Hedging)"/"Balanced" label, plus the full
  CALLS/PUTS/COMBINED TOTALS notional breakdown)
- SUPPORT/RESISTANCE LEVELS (raw-OI top-3 call/put strikes + short-term
  nearest levels) — deleted outright, not replaced by a one-liner: the
  reader relies on the already-existing GEX-based Call Resistance/Put
  Support levels in gex_dex_formatter.format_gex_dex_section's KEY LEVELS
  block instead ("merge into existing GEX key levels"). The strike table's
  own "Resistance"/"Support" per-row annotations (sourced from the same
  raw-OI top-3 sets) are removed for the same reason; the "<< MAX PAIN"
  annotation is kept — it is a compact strike marker, not the deleted
  multi-line block.

Their one-line replacements (Max Pain, P/C ratio, Moneyness ITM%, Volume
P/C, VWAP-IV gap) live in ``format_context_section`` below, rendered by
``report_formatter.py`` as the LAST section in each expiration's block
(spec 9(b) per-expiry order item 8, "CONTEXT"). Trend arrows against a
single prior snapshot (the old ``format_trend_delta`` helper) are deleted
everywhere in this module — spec 9's removals table: "Trend arrows vs 1
prior snapshot -> delete everywhere"; superseded by the percentile-based
context task C1's HistoricalNormalizer already provides elsewhere in the
report (PCR percentile here, IV/skew percentiles in other sections).
"""

from typing import Optional

from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.vol_surface_results import VolSurfaceResult
from coding.core.analytics.volatility_surface_calculator import (
    MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION,
)

_SUB_SEPARATOR = "-" * 80


def format_expiration_section(analysis: ExpirationAnalysisResult) -> str:
    """
    Render the summary + open-interest/volume-by-strike table for one
    expiration.

    Args:
        analysis: The expiration's computed analysis result.

    Returns:
        Formatted multi-line string (no leading/trailing separators — the
        caller is responsible for the "EXPIRATION: ..." header line and the
        surrounding section separators).
    """
    lines = []

    lines.append(
        f"Total Instruments: {analysis.total_instruments} "
        f"({analysis.call_count} Calls, {analysis.put_count} Puts)"
    )
    lines.append("")

    max_pain_strike = analysis.max_pain.max_pain_strike

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

    for row in analysis.strike_rows:
        strike = row.strike
        notes_str = "<< MAX PAIN" if strike == max_pain_strike else ""

        lines.append(
            f"{strike:>10,.0f}  {row.call_oi:>10,.0f}  "
            f"{row.put_oi:>10,.0f}  {row.call_volume:>10,.2f}  "
            f"{row.put_volume:>10,.2f}  {notes_str}"
        )
    lines.append("")

    return "\n".join(lines)


def _format_vwap_iv_gap_line(vol_surface: Optional[VolSurfaceResult]) -> str:
    """
    One-line VWAP-IV-vs-matched-mark-IV gap (institutional_metrics_spec.md
    section 9: "VWAP IV vs mark IV -> one line, matched-baseline only").

    ``mark_iv_average`` is the volume-weighted, same-instruments MATCHED
    baseline (bugfix_spec.md Item 3) — never the chain-wide average. That
    fix already landed in ``VolatilitySurfaceCalculator``/
    ``VolSurfaceResult``; this function only demotes the report TEXT to one
    line, it does not change which baseline is used.
    """
    if vol_surface is None:
        return "VWAP-IV gap: N/A (no vol surface data)"

    vwap_iv = vol_surface.vwap_iv
    mark_iv_baseline = vol_surface.mark_iv_average
    if vwap_iv is None or mark_iv_baseline is None:
        return "VWAP-IV gap: N/A (no VWAP data)"

    if vol_surface.traded_instrument_count < MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION:
        return (
            f"VWAP-IV gap: n/a (only {vol_surface.traded_instrument_count} "
            "instrument(s) traded)"
        )

    diff = vwap_iv - mark_iv_baseline
    return (
        f"VWAP-IV gap: {diff:+.1f}%  (VWAP {vwap_iv:.1f}% vs Matched Mark "
        f"{mark_iv_baseline:.1f}%, {vol_surface.traded_instrument_count} instr)"
    )


def format_context_section(
    analysis: ExpirationAnalysisResult,
    spot_price: float,
    vol_surface: Optional[VolSurfaceResult],
) -> str:
    """
    Render the per-expiry CONTEXT section (institutional_metrics_spec.md
    section 9(b), per-expiry order item 8): one line each for Max Pain,
    P/C ratio, Moneyness ITM%, Volume P/C, and the VWAP-IV gap. Rendered
    LAST in each expiration's block, after every other section.

    Args:
        analysis: The expiration's computed analysis result.
        spot_price: This expiration's own forward price (bugfix_spec.md
            Item 7 anchor — NOT a global index shared across expirations),
            used only for the Max Pain distance-from-spot percentage.
        vol_surface: This expiration's volatility surface result, or None
            when unavailable (matches the "no data -> N/A line" convention
            — CONTEXT always prints exactly one line per fact, never omits
            a line the way other sections omit themselves entirely).

    Returns:
        Formatted multi-line string.
    """
    lines = ["CONTEXT", _SUB_SEPARATOR]

    # Max Pain -- one line, no trend, no separate "Distance from Current"
    # line (institutional_metrics_spec.md section 9).
    max_pain_strike = analysis.max_pain.max_pain_strike
    if max_pain_strike is not None:
        diff_pct = (spot_price - max_pain_strike) / max_pain_strike * 100 if max_pain_strike else 0.0
        lines.append(f"Max Pain: ${max_pain_strike:,.0f}  ({diff_pct:+.2f}% from spot)")
    else:
        lines.append("Max Pain: N/A")

    # P/C Ratio -- value + percentile only, no word bias label (T9.2
    # acceptance: report must not contain "Strong Bullish"/"Strong
    # Bearish" etc, and must show a "p"+digits marker on this line,
    # matching the RR25/BF25 percentile-cell convention already shipped in
    # market_wide_formatter.py).
    pcr = analysis.put_call_ratio
    if pcr.ratio == float("inf"):
        lines.append("P/C Ratio: N/A (No Call OI)")
    elif pcr.percentile_90d is not None:
        lines.append(
            f"P/C Ratio: {pcr.ratio:.2f}  p{pcr.percentile_90d:.0f} "
            f"(90d history, n={pcr.history_n_90d})"
        )
    else:
        lines.append(
            f"P/C Ratio: {pcr.ratio:.2f}  "
            f"(n={pcr.history_n_90d} - insufficient history for a percentile)"
        )

    # Moneyness -- one line, ITM% calls / ITM% puts (institutional_metrics_
    # spec.md section 9: replaces the full CALLS/PUTS/COMBINED TOTALS block
    # and the hard-coded oi_skew label).
    money = analysis.moneyness
    lines.append(
        f"Moneyness: ITM calls {money.calls.itm_pct:.1f}%  |  "
        f"ITM puts {money.puts.itm_pct:.1f}%"
    )

    # Volume P/C -- one line.
    vol = analysis.volume_stats
    if vol.volume_ratio == float("inf"):
        lines.append("Volume P/C: N/A (No Call Volume)")
    else:
        lines.append(f"Volume P/C: {vol.volume_ratio:.2f}")

    # VWAP-IV gap -- one line, matched-baseline only.
    lines.append(_format_vwap_iv_gap_line(vol_surface))

    lines.append("")
    return "\n".join(lines)
