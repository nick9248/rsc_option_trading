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

    # bugfix_spec.md Item 9: 25-delta RISK REVERSAL (call - put, market
    # convention -- SpotGamma/MenthorQ/Glassnode), not the old unqualified
    # "skew" (put - call, opposite sign). Prints the actual selected deltas
    # too (9.4 edge case: a thin book's "closest to +-0.25" pick may not be
    # close at all).
    #
    # Wave-I-C Fix 8: put_25d_delta/call_25d_delta are the |delta| actually
    # implied AT put_25d_strike/call_25d_strike (VolatilitySurfaceCalculator.
    # InterpPoint.delta) -- not the ±0.25 target. *_25d_strike is a bracket
    # midpoint, not delta-solved, so it is not in general exactly the
    # target-delta strike; printing the target here would overclaim
    # precision the interpolation doesn't have.
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

    # institutional_metrics_spec.md section 9 (Task D2): "VWAP IV vs mark
    # IV -> one line, matched-baseline only". The two-line VWAP/Matched-
    # Mark/Diff + aggression-label block that used to render here is
    # deleted -- its one-line replacement is expiry_formatter.py's
    # format_context_section (CONTEXT section, rendered last in this
    # expiration's block). bugfix_spec.md Item 3's matched-baseline fix
    # (mark_iv_average is the volume-weighted, same-instruments baseline,
    # never a chain-wide average) is unchanged -- this is a text-rendering
    # removal only, not a data-model or calculation change.

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

        # bugfix_spec.md Item 10 / C1 review Important #3: this per-
        # moneyness-bucket bias still comes from the hard-coded 0.7/1.0/1.3
        # thresholds (VolatilitySurfaceCalculator._calculate_pc_by_moneyness
        # -- no verified per-bucket history source exists yet to reclassify
        # it on a percentile basis, unlike the whole-expiration ratio).
        # Printing it as the report's only remaining directional PCR label
        # would leave the exact defect Item 10 exists to kill standing.
        # Minimum viable fix until per-bucket history exists: raw ratio
        # only, no directional tag. bucket.bias is still computed/stored
        # (to_dict() unaffected) -- only this report line stops rendering it.
        if ratio is None:
            # Wave-H-A (Task 5): ZERO instruments in this bucket -- distinct
            # from the inf case below (some puts, no calls).
            ratio_str = "N/A (No Instruments)"
        elif ratio == float("inf"):
            ratio_str = "N/A (No Call OI)"
        else:
            ratio_str = f"P/C = {ratio:.2f}"

        lines.append(f"  {label} ({rng}):{'':>5}{ratio_str}")
    lines.append("")

    # institutional_metrics_spec.md section 4(c) / task C5: "Report --
    # replaces the aggregate vanna/charm advice block entirely." The
    # "SECOND-ORDER GREEKS" text that used to render here (holder-side raw
    # + assumed-dealer aggregate scalar, tau via gamma-inversion) is
    # superseded by the new per-strike "VANNA / CHARM PROFILE" section
    # (coding/core/analytics/reporting/exposure_profile_formatter.py,
    # wired in by report_formatter.py immediately after GEX/DEX and dealer
    # inventory), which uses true instrument-name-derived tau instead of
    # the gamma-inversion this aggregate scalar depends on (spec 4(b)).
    # ``result.second_order_greeks`` itself is UNCHANGED -- this is a
    # text-rendering removal only, not a data-model change. It still feeds
    # synthesis.py's ScoringEngine.score_vanna_charm via ExpiryMetrics.
    # net_vanna/net_charm (bugfix_spec.md Item 8 fix-review Important #3),
    # which this task does not touch.

    return "\n".join(lines)
