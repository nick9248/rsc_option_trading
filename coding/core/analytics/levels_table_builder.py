"""
Joins the per-strike results already produced for one expiration into the
single row-per-strike shape the GUI's Levels tab reads
(institutional_metrics_spec.md section 9(c), Task D3).

Pure: takes an already-built ``ExpirationBundle``, never touches psycopg2,
requests, or any repository -- mirrors the "new Core classes... pure, no
DB/API imports" convention section 10 of the spec establishes for this wave
(``historical_normalizer.py``, ``dealer_inventory_calculator.py``, etc.).

Task D3's brief requires the GUI to contain "no arithmetic beyond
formatting" (CLAUDE.md Code Quality Checklist section 3). Two of this
row's fields genuinely combine two already-computed numbers (summing a
strike's call-leg and put-leg net taker flow into one directional number;
averaging a strike's call-leg and put-leg day-over-day IV change into one
number) -- that combination lives here, not in ``on_chain_analysis_tab.py``,
per the brief's explicit instruction that this kind of computation belongs
in the service/core layer.
"""

from typing import Dict, Optional, Tuple

from coding.core.analytics.results.analysis_result import ExpirationBundle
from coding.core.analytics.results.levels_table_results import LevelsTable, LevelsTableRow


def _combine_sum(call_value: Optional[float], put_value: Optional[float]) -> Optional[float]:
    """
    Sum two optional leg values (used for net taker flow -- a directional
    volume quantity). None only when BOTH legs are absent; a present leg
    with the other absent is returned as-is (the absent leg contributes 0,
    same convention ``FlowTotals``/OI already use elsewhere for a strike
    with trades on only one side).
    """
    if call_value is None and put_value is None:
        return None
    return (call_value or 0.0) + (put_value or 0.0)


def _combine_average(call_value: Optional[float], put_value: Optional[float]) -> Optional[float]:
    """
    Average two optional leg values (used for delta-1d IV -- a vol-points
    quantity, not a volume). None only when BOTH legs are absent; a single
    present leg is returned unaveraged.
    """
    if call_value is not None and put_value is not None:
        return (call_value + put_value) / 2.0
    if call_value is not None:
        return call_value
    if put_value is not None:
        return put_value
    return None


def build_levels_table(bundle: ExpirationBundle) -> LevelsTable:
    """
    Build the per-strike ``LevelsTable`` for one ``ExpirationBundle``.

    Strike universe is ``bundle.analysis.strike_rows`` (every strike this
    expiration has open interest for) -- the same canonical strike list
    the existing text report already iterates. Every other per-strike
    source (gex_dex, dealer_inventory, exposure_profile, flow,
    fixed_strike_vol) is looked up by strike; a source with no entry for a
    given strike contributes its documented "unavailable" value (see
    ``LevelsTableRow``'s docstring) rather than a fabricated 0/None
    ambiguity.

    Args:
        bundle: One expiration's full result bundle.

    Returns:
        LevelsTable with one row per strike in ``bundle.analysis.
        strike_rows``, ascending by strike (the source order).
    """
    gex_dex = bundle.gex_dex
    dealer_inventory = bundle.dealer_inventory
    exposure_profile = bundle.exposure_profile
    flow = bundle.flow
    fixed_strike_vol = bundle.fixed_strike_vol

    gex_dex_by_strike: Dict[float, object] = (
        {row.strike: row for row in gex_dex.strike_rows} if gex_dex is not None else {}
    )
    dealer_inventory_by_strike: Dict[float, object] = (
        {row.strike: row for row in dealer_inventory.strike_rows}
        if dealer_inventory is not None
        else {}
    )
    exposure_by_strike: Dict[float, object] = (
        {row.strike: row for row in exposure_profile.strike_rows}
        if exposure_profile is not None
        else {}
    )

    inferred_available = dealer_inventory is not None and dealer_inventory.render_inferred

    net_taker_flow_available = flow is not None and flow.sufficient_data

    delta_1d_iv_available = (
        fixed_strike_vol is not None and not fixed_strike_vol.stale_prior
    )
    iv_change_by_strike: Dict[float, Dict[str, float]] = {}
    if delta_1d_iv_available:
        for iv_row in fixed_strike_vol.rows:
            iv_change_by_strike.setdefault(iv_row.strike, {})[iv_row.option_type] = iv_row.d_iv

    gex_dex_key_levels = gex_dex.key_levels if gex_dex is not None else None
    call_wall_assumed_strike = (
        gex_dex_key_levels.call_resistance.strike
        if gex_dex_key_levels is not None and gex_dex_key_levels.call_resistance is not None
        else None
    )
    put_support_assumed_strike = (
        gex_dex_key_levels.put_support.strike
        if gex_dex_key_levels is not None and gex_dex_key_levels.put_support is not None
        else None
    )
    hvl_assumed = gex_dex_key_levels.hvl if gex_dex_key_levels is not None else None

    dealer_inventory_key_levels = (
        dealer_inventory.key_levels if inferred_available else None
    )
    call_wall_inferred_strike = (
        dealer_inventory_key_levels.call_resistance.strike
        if dealer_inventory_key_levels is not None
        and dealer_inventory_key_levels.call_resistance is not None
        else None
    )
    put_support_inferred_strike = (
        dealer_inventory_key_levels.put_support.strike
        if dealer_inventory_key_levels is not None
        and dealer_inventory_key_levels.put_support is not None
        else None
    )
    hvl_inferred = (
        dealer_inventory_key_levels.hvl if dealer_inventory_key_levels is not None else None
    )

    max_pain_strike = bundle.analysis.max_pain.max_pain_strike

    rows = []
    for strike_row in bundle.analysis.strike_rows:
        strike = strike_row.strike
        gex_dex_row = gex_dex_by_strike.get(strike)
        dealer_row = dealer_inventory_by_strike.get(strike) if inferred_available else None
        exposure_row = exposure_by_strike.get(strike)

        call_iv_change = iv_change_by_strike.get(strike, {}).get("C") if delta_1d_iv_available else None
        put_iv_change = iv_change_by_strike.get(strike, {}).get("P") if delta_1d_iv_available else None

        net_taker_flow = None
        if net_taker_flow_available:
            flow_entry_by_type = flow.flow_data.get(strike, {})
            call_flow_entry = flow_entry_by_type.get("C")
            put_flow_entry = flow_entry_by_type.get("P")
            net_taker_flow = _combine_sum(
                call_flow_entry.net_flow if call_flow_entry is not None else None,
                put_flow_entry.net_flow if put_flow_entry is not None else None,
            )

        rows.append(
            LevelsTableRow(
                strike=strike,
                call_oi=strike_row.call_oi,
                put_oi=strike_row.put_oi,
                net_gex_holder=(
                    gex_dex_row.gamma_exposure_holder if gex_dex_row is not None else 0.0
                ),
                net_gex_assumed=gex_dex_row.net_gex if gex_dex_row is not None else 0.0,
                net_gex_inferred=(dealer_row.inferred_gex if dealer_row is not None else None),
                net_dex=gex_dex_row.net_dex if gex_dex_row is not None else 0.0,
                vex=exposure_row.vex_holder if exposure_row is not None else 0.0,
                cex=exposure_row.cex_holder if exposure_row is not None else 0.0,
                net_taker_flow=net_taker_flow,
                delta_1d_iv=_combine_average(call_iv_change, put_iv_change),
                is_call_wall_assumed=(
                    call_wall_assumed_strike is not None and strike == call_wall_assumed_strike
                ),
                is_put_support_assumed=(
                    put_support_assumed_strike is not None and strike == put_support_assumed_strike
                ),
                is_hvl_assumed=(hvl_assumed is not None and strike == hvl_assumed),
                is_call_wall_inferred=(
                    call_wall_inferred_strike is not None and strike == call_wall_inferred_strike
                ),
                is_put_support_inferred=(
                    put_support_inferred_strike is not None and strike == put_support_inferred_strike
                ),
                is_hvl_inferred=(hvl_inferred is not None and strike == hvl_inferred),
                is_max_pain=(max_pain_strike is not None and strike == max_pain_strike),
            )
        )

    return LevelsTable(
        expiration=bundle.expiration,
        rows=tuple(rows),
        inferred_available=inferred_available,
        net_taker_flow_available=net_taker_flow_available,
        delta_1d_iv_available=delta_1d_iv_available,
    )
