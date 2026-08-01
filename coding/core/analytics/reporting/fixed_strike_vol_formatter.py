"""
Text formatting for the FIXED-STRIKE VOL CHANGE report section
(institutional_metrics_spec.md section 7 / Task C8).

Renders the per-strike day-over-day IV change vs the ATM move, restricted
to strikes within the same +/-10% ATM region the sticky-strike/sticky-delta
ladder itself evaluates (spec: "compact"), split into separate CALLS/PUTS
blocks per spec section 7(c)'s report mock.

Reuses Task C1's HistoricalNormalizer/historical-context convention of an
explicit "insufficient history" message instead of a fabricated table --
task-C8-brief.md's graceful-fallback requirement -- whenever
``FixedStrikeVolCalculator`` returns ``regime == "INDETERMINATE"``: the
section still renders (never suppressed to ""), but shows the reason
instead of numbers that cannot be trusted.

ASCII-only output (no U+0394 "Δ"): mirrors delta_flow_formatter's review
fix (Minor #3) -- this machine's default console codec is cp1252, which
raises ``UnicodeEncodeError`` on that character.
"""

from typing import Optional

from coding.core.analytics.fixed_strike_vol_calculator import ATM_REGION_PCT
from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult

_SEPARATOR = "-" * 80
_ROW_FORMAT = "{strike:>10}{iv:>10}{d_iv:>10}{d_vs_atm:>10}"


def _insufficient_history_message(result: FixedStrikeVolResult) -> str:
    """
    Pick the most specific reason the comparison cannot be trusted, in the
    same priority order ``FixedStrikeVolCalculator._determine_regime``
    checks them.
    """
    if result.prior_date is None:
        return "Insufficient history: no comparable prior snapshot found"
    if result.stale_prior:
        return (
            "Insufficient history: no comparable prior snapshot "
            f"(last: {result.prior_date.isoformat()})"
        )
    if result.atm_iv_today is None or result.atm_iv_prior is None:
        return "Insufficient history: ATM IV unavailable for today or the prior snapshot"
    return "Insufficient history: no overlapping strikes within the ATM region to attribute a regime"


def _format_header(result: FixedStrikeVolResult) -> str:
    header = f"FIXED-STRIKE VOL CHANGE -- {result.expiration}"
    if result.prior_date is not None:
        header += f"  ({result.prior_date.isoformat()} vs {result.today_date.isoformat()} UTC)"
    else:
        header += f"  ({result.today_date.isoformat()} UTC)"
    return header


def _format_summary_line(result: FixedStrikeVolResult) -> str:
    spot_part = ""
    if result.spot_prior is not None and result.spot_today is not None:
        # Independent review (Task C8 fix round, Important #1): the
        # anchor the service now passes here is this expiry's FORWARD
        # price (bugfix_spec.md Item 7's settlement-space convention),
        # not the spot index -- labelling it "Spot" after fixing the
        # underlying anchor mismatch would just relocate the same
        # confusion into the report text.
        move = f"{result.spot_move_pct:+.2f}%" if result.spot_move_pct is not None else "n/a"
        spot_part = f"Fwd {result.spot_prior:,.0f} -> {result.spot_today:,.0f} ({move})     "

    atm_part = f"ATM IV {result.atm_iv_prior:.2f} -> {result.atm_iv_today:.2f} ({result.d_atm:+.2f})"
    return spot_part + atm_part


def _format_side_block(label: str, rows) -> list:
    if not rows:
        return []
    lines = [
        label,
        _ROW_FORMAT.format(strike="Strike", iv="IV", d_iv="Chg 1d", d_vs_atm="vs ATM"),
    ]
    for row in sorted(rows, key=lambda r: r.strike):
        d_vs_atm_str = f"{row.d_vs_atm:+.2f}" if row.d_vs_atm is not None else "n/a"
        lines.append(_ROW_FORMAT.format(
            strike=f"{row.strike:,.0f}",
            iv=f"{row.iv_today:.2f}",
            d_iv=f"{row.d_iv:+.2f}",
            d_vs_atm=d_vs_atm_str,
        ))
    lines.append("")
    return lines


def format_fixed_strike_vol_section(result: Optional[FixedStrikeVolResult]) -> str:
    """
    Render the FIXED-STRIKE VOL CHANGE section for one expiration.

    Args:
        result: ``FixedStrikeVolCalculator.calculate()``'s output for this
            expiration, or ``None`` (no data at all -- e.g. no repository,
            or building the matrix raised -- see
            ``OnChainAnalysisService._calculate_fixed_strike_vol_matrix``).

    Returns:
        "" when ``result`` is ``None`` (matches the codebase's existing
        "no data -> no section" convention, e.g.
        ``format_historical_context_section``). Otherwise ALWAYS renders a
        section -- even ``regime == "INDETERMINATE"`` gets a header plus an
        explicit reason, never a silently-empty string, so the reader can
        tell "this metric has nothing to say yet" apart from "this metric
        was never wired up".
    """
    if result is None:
        return ""

    lines = [_format_header(result), _SEPARATOR]

    if result.regime == "INDETERMINATE":
        lines.append(_insufficient_history_message(result))
        lines.append("")
        return "\n".join(lines)

    lines.append(_format_summary_line(result))
    lines.append(_SEPARATOR)

    # spec section 7(c): "compact, +/-10% of spot" -- the footer's
    # n_strikes_matched/n_strikes_unmatched counts are NEVER restricted to
    # this region (they describe the whole matched/unmatched matrix); only
    # the displayed rows are.
    region_rows = [
        row for row in result.rows
        if row.moneyness_pct is not None and row.moneyness_pct <= ATM_REGION_PCT
    ]
    calls = [row for row in region_rows if row.option_type == "C"]
    puts = [row for row in region_rows if row.option_type == "P"]

    lines.extend(_format_side_block("CALLS", calls))
    lines.extend(_format_side_block("PUTS", puts))

    lines.append(_SEPARATOR)
    lines.append(
        f"Regime: {result.regime}   "
        f"({result.n_strikes_matched} strikes matched, {result.n_strikes_unmatched} unmatched)"
    )
    lines.append("")

    return "\n".join(lines)
