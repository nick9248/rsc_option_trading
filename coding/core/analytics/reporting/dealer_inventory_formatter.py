"""
Text formatting for the taker-flow-inferred dealer positioning section
(institutional_metrics_spec.md section 2 / task C3).

D7 (established Wave B, task B1/bugfix_spec.md Item 8): ``gex_dex_formatter.
py`` already labels its two dealer-side views "ASSUMED DEALER VIEW" (the
SqueezeMetrics call-put-split heuristic) alongside the holder-side raw
numbers. This module adds a THIRD, separately-labeled dealer-side view
(taker-flow-inferred) and presents it side by side with the SAME "ASSUMED"
label already established there -- it does not invent new vocabulary for the
existing convention.

D9 (BINDING, task-C3-brief.md): never render the inferred view for a
gate-failed expiry, never blend the two views. A gate failure prints an
explicit "INFERRED DEALER VIEW UNAVAILABLE (...)" line and nothing else --
no inferred GEX/DEX number is ever formatted into that branch's text, even
though ``DealerInventoryCalculator.calculate()`` computed one.
"""

from datetime import datetime, timezone
from typing import Optional

from coding.core.analytics.results.dealer_inventory_results import DealerInventoryResult
from coding.core.analytics.results.gex_dex_results import GexDexResult

_SEPARATOR = "-" * 80


def _gamma_sign_label(value: Optional[float]) -> str:
    if value is None:
        return "UNKNOWN"
    if value > 0:
        return "POSITIVE"
    if value < 0:
        return "NEGATIVE"
    return "FLAT"


def _format_level(strike: Optional[float]) -> str:
    return f"${strike:,.0f}" if strike is not None else "None"


def format_dealer_inventory_section(
    dealer_result: DealerInventoryResult,
    gex_dex_result: Optional[GexDexResult],
    currency: str,
) -> str:
    """
    Render the "DEALER POSITIONING -- TWO VIEWS" section for one expiration.

    Additive to the existing GEX/DEX ANALYSIS section (spec §2(c)): never
    modifies ``format_gex_dex_section``'s own text, sits in its own new
    block. Placed immediately after GEX/DEX in ``report_formatter.py`` so
    the two "ASSUMED DEALER VIEW" labels (this section's and gex_dex_
    formatter's) read as the same convention, not two different ones.

    Args:
        dealer_result: The typed inferred-positioning result for this
            expiration (D9 already decided ``render_inferred``).
        gex_dex_result: The SAME expiration's GEX/DEX result, supplying the
            ASSUMED-view comparison numbers. ``None`` is handled
            defensively (should not happen in production wiring -- dealer_
            result is only ever computed alongside a GexDexResult -- but
            report generation must never crash on it).
        currency: Underlying currency symbol, for DEX unit labeling.

    Returns:
        Formatted multi-line string.
    """
    lines = ["DEALER POSITIONING -- TWO VIEWS", _SEPARATOR]

    if not dealer_result.render_inferred:
        lines.append(f"INFERRED DEALER VIEW UNAVAILABLE ({dealer_result.unavailable_reason})")
        lines.append(
            "  -> falling back to the ASSUMED convention (see GEX/DEX ANALYSIS -- "
            "ASSUMED DEALER VIEW above)"
        )
        lines.append("")
        return "\n".join(lines)

    assumed_gex = gex_dex_result.dealer_gamma_exposure_total if gex_dex_result is not None else None
    assumed_kl = gex_dex_result.key_levels if gex_dex_result is not None else None
    assumed_cr = assumed_kl.call_resistance.strike if assumed_kl and assumed_kl.call_resistance else None
    assumed_ps = assumed_kl.put_support.strike if assumed_kl and assumed_kl.put_support else None

    inferred_kl = dealer_result.key_levels
    inferred_cr = inferred_kl.call_resistance.strike if inferred_kl.call_resistance else None
    inferred_ps = inferred_kl.put_support.strike if inferred_kl.put_support else None

    t0_date = datetime.fromtimestamp(dealer_result.t0_epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    lines.append(f"{'':<26}{'ASSUMED (long calls/short puts)':>34}{'INFERRED (taker flow)':>28}")

    assumed_gex_str = f"{assumed_gex:+,.2f} USD" if assumed_gex is not None else "not available"
    inferred_gex_str = f"{dealer_result.total_inferred_gex:+,.2f} USD"
    lines.append(f"{'Total net GEX':<26}{assumed_gex_str:>34}{inferred_gex_str:>28}")

    assumed_levels_str = f"{_format_level(assumed_cr)} / {_format_level(assumed_ps)}"
    inferred_levels_str = f"{_format_level(inferred_cr)} / {_format_level(inferred_ps)}"
    lines.append(f"{'Call wall / Put support':<26}{assumed_levels_str:>34}{inferred_levels_str:>28}")

    assumed_regime = _gamma_sign_label(assumed_gex)
    inferred_regime = _gamma_sign_label(dealer_result.total_inferred_gex)
    lines.append(f"{'Gamma sign at spot':<26}{assumed_regime:>34}{inferred_regime:>28}")

    lines.append(f"{'Inference basis':<26}{'convention (SqueezeMetrics)':>34}{'signed taker flow':>28}")
    lines.append(f"{'':<60}{dealer_result.n_signed_trades:,} signed trades since {t0_date}")
    lines.append(
        f"{'':<60}coverage {dealer_result.coverage * 100:.2f}% | "
        f"viol {dealer_result.violation_rate * 100:.1f}%"
    )
    lines.append("")

    return "\n".join(lines)
