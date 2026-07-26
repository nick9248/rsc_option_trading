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

    # 25-Delta Skew
    skew = result.skew_25d
    if skew.skew is not None:
        lines.append(f"25-Delta Skew: {skew.skew:+.1f}% ({skew.interpretation})")
        lines.append(
            f"  25d Put: {skew.put_25d_iv:.1f}% (K={skew.put_25d_strike:,.0f})  |  "
            f"25d Call: {skew.call_25d_iv:.1f}% (K={skew.call_25d_strike:,.0f})"
        )
    else:
        lines.append(f"25-Delta Skew: {skew.interpretation}")
    lines.append("")

    # ATM IV
    atm_iv = result.atm_iv
    if atm_iv is not None:
        lines.append(f"ATM IV: {atm_iv:.1f}%")
        lines.append("")

    # VWAP IV (if available)
    vwap_iv = result.vwap_iv
    mark_iv_avg = result.mark_iv_average
    if vwap_iv is not None and mark_iv_avg is not None:
        diff = vwap_iv - mark_iv_avg
        if diff > 1:
            aggression = "Buyers aggressive (VWAP > Mark)"
        elif diff < -1:
            aggression = "Sellers aggressive (VWAP < Mark)"
        else:
            aggression = "Balanced"
        lines.append(f"VWAP IV: {vwap_iv:.1f}%  |  Mark IV: {mark_iv_avg:.1f}%  |  Diff: {diff:+.1f}%")
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

    # Second-Order Greeks
    second = result.second_order_greeks
    lines.append("SECOND-ORDER GREEKS:")
    lines.append(f"  Net Vanna Exposure: {second.net_vanna:+.6f}")
    lines.append(f"  Net Charm Exposure: {second.net_charm:+.6f}")
    lines.append(f"  Vanna Signal: {second.vanna_signal}")
    lines.append(f"  Charm Signal: {second.charm_signal}")
    lines.append("")

    return "\n".join(lines)
