"""
Text formatting for the per-expiry volatility surface section.

Extracted verbatim (behavior-preserving) from
``VolatilitySurfaceCalculator.generate_report_section()`` per
refactor_design_spec.md section T3, operating on the typed
``VolSurfaceResult`` (section T2), now wired in at T4.

The "IV BY STRIKE" table uses ``VolSurfaceResult.merged_iv_by_strike()`` for
the legacy per-strike ``{call_iv, put_iv}`` view — that grouping used to be
re-derived here (A3-review carried finding); it now lives on the model,
making this formatter a pure consumer.
"""

from coding.core.analytics.results.vol_surface_results import VolSurfaceResult
from coding.core.analytics.volatility_surface_calculator import (
    MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION,
    VWAP_AGGRESSION_THRESHOLD_POINTS,
)

_SUB_SEPARATOR = "-" * 80


def format_vol_surface_section(result: VolSurfaceResult, expiration: str) -> str:
    """
    Render the volatility surface analysis section.

    Args:
        result: Volatility surface result for one expiration.
        expiration: Expiration date string. Unused in the rendered text today
            (matching the legacy method, which never printed it here) — kept
            for signature parity with the other per-expiration formatters and
            for callers that may want it in a future section.

    Returns:
        Formatted string for inclusion in the analysis report.
    """
    del expiration  # not rendered — see module docstring

    lines = []
    lines.append("VOLATILITY SURFACE ANALYSIS")
    lines.append(_SUB_SEPARATOR)

    # bugfix_spec.md Item 9: 25-delta RISK REVERSAL (call - put, market
    # convention -- SpotGamma/MenthorQ/Glassnode), not the old unqualified
    # "skew" (put - call, opposite sign). Prints the actual selected deltas
    # too (9.4 edge case: a thin book's "closest to +-0.25" pick may not be
    # close at all).
    skew = result.skew_25d
    if skew.risk_reversal_25d is not None:
        lines.append(
            f"25Δ Risk Reversal (call − put): {skew.risk_reversal_25d:+.1f}% "
            f"({skew.interpretation})"
        )
        put_delta_str = f", Δ={skew.put_25d_delta:+.3f}" if skew.put_25d_delta is not None else ""
        call_delta_str = f", Δ={skew.call_25d_delta:+.3f}" if skew.call_25d_delta is not None else ""
        lines.append(
            f"  25d Put: {skew.put_25d_iv:.1f}% (K={skew.put_25d_strike:,.0f}{put_delta_str})  |  "
            f"25d Call: {skew.call_25d_iv:.1f}% (K={skew.call_25d_strike:,.0f}{call_delta_str})"
        )
    else:
        lines.append(f"25Δ Risk Reversal: {skew.interpretation}")
    lines.append("")

    # ATM IV
    atm_iv = result.atm_iv
    if atm_iv is not None:
        lines.append(f"ATM IV: {atm_iv:.1f}%")
        lines.append("")

    # VWAP IV vs the matched (volume-weighted, same-instruments) mark IV
    # baseline (if available) — bugfix_spec.md Item 3. Mirrors
    # VolatilitySurfaceCalculator.generate_report_section exactly (must stay
    # in lockstep — see that method's docstring).
    vwap_iv = result.vwap_iv
    mark_iv_baseline = result.mark_iv_average
    if vwap_iv is not None and mark_iv_baseline is not None:
        if result.traded_instrument_count < MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION:
            lines.append(
                f"VWAP IV: {vwap_iv:.1f}%  |  Matched Mark IV: {mark_iv_baseline:.1f}%  "
                f"(only {result.traded_instrument_count} instrument(s) traded - "
                f"aggression signal suppressed)"
            )
        else:
            diff = vwap_iv - mark_iv_baseline
            if diff > VWAP_AGGRESSION_THRESHOLD_POINTS:
                aggression = "Buyers aggressive (VWAP > Mark)"
            elif diff < -VWAP_AGGRESSION_THRESHOLD_POINTS:
                aggression = "Sellers aggressive (VWAP < Mark)"
            else:
                aggression = "Balanced"
            lines.append(
                f"VWAP IV: {vwap_iv:.1f}%  |  Matched Mark IV: {mark_iv_baseline:.1f}%  "
                f"|  Diff: {diff:+.1f}%  ({result.traded_instrument_count} instruments)"
            )
            lines.append(f"  {aggression}")
        lines.append("")

    # IV by Strike (show most relevant strikes around spot). The per-strike
    # {call_iv, put_iv} merge lives on the model (carried finding) —
    # this formatter is a pure consumer of merged_iv_by_strike().
    merged = result.merged_iv_by_strike()

    if merged:
        lines.append("IV BY STRIKE:")
        lines.append(f"  {'Strike':>10}  {'Call IV':>10}  {'Put IV':>10}")
        lines.append(f"  {'------':>10}  {'-------':>10}  {'------':>10}")

        # Filter to ±30% of spot for readability
        for strike, entry in merged.items():
            if result.spot_price > 0:
                distance = abs(strike - result.spot_price) / result.spot_price
                if distance > 0.30:
                    continue

            call_iv = f"{entry['call_iv']:.1f}%" if entry["call_iv"] is not None else "   -"
            put_iv = f"{entry['put_iv']:.1f}%" if entry["put_iv"] is not None else "   -"
            lines.append(f"  {strike:>10,.0f}  {call_iv:>10}  {put_iv:>10}")
        lines.append("")

    # P/C by Moneyness
    pc = result.pc_by_moneyness
    lines.append("P/C RATIO BY MONEYNESS:")
    for bucket, label in [(pc.atm, "ATM"), (pc.near_otm, "Near-OTM"), (pc.far_otm, "Far-OTM")]:
        rng = bucket.range_label
        ratio = bucket.ratio
        bias = bucket.bias

        if ratio == float("inf"):
            ratio_str = "N/A (No Call OI)"
        else:
            ratio_str = f"P/C = {ratio:.2f} ({bias})"

        lines.append(f"  {label} ({rng}):{'':>5}{ratio_str}")
    lines.append("")

    # bugfix_spec.md Item 8: holder-side raw sums (no positioning
    # assumption) and the assumed-dealer view (negation), explicitly
    # labelled and separated -- the signals below are derived from the
    # DEALER fields (see VolatilitySurfaceCalculator._calculate_second_
    # order_greeks), not the holder sum, so the narrative names the actor
    # only where the assumption is stated.
    second = result.second_order_greeks
    lines.append("SECOND-ORDER GREEKS -- HOLDER SIDE (raw, no positioning assumption)")
    lines.append(f"  Vanna Exposure: {second.vanna_exposure_holder:+.6f}")
    lines.append(f"  Charm Exposure: {second.charm_exposure_holder:+.6f}")
    lines.append("")
    lines.append("ASSUMED DEALER VIEW  (assumption: dealers short customer vanna/charm)")
    lines.append(f"  Dealer Vanna:   {second.dealer_vanna_exposure:+.6f}")
    lines.append(f"  Dealer Charm:   {second.dealer_charm_exposure:+.6f}")
    lines.append(f"  Vanna Signal: {second.vanna_signal}")
    lines.append(f"  Charm Signal: {second.charm_signal}")
    lines.append("")

    return "\n".join(lines)
