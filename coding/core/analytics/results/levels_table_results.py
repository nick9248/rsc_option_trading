"""
Result models for the GUI "Levels" tab per-strike table
(institutional_metrics_spec.md section 9(c), Task D3).

Frozen dataclasses. ``LevelsTableRow`` joins fields already produced by
five separate per-expiration results (``GexDexResult``,
``DealerInventoryResult``, ``ExposureProfileResult``, ``FlowResult``,
``FixedStrikeVolResult``) into one row per strike -- the shape the GUI's
Levels ``QTableWidget`` reads directly, so the GUI layer performs no join,
no lookup, and no arithmetic of its own (CLAUDE.md Code Quality Checklist
section 3). See ``coding.core.analytics.levels_table_builder`` for the pure
builder that produces this shape from an ``ExpirationBundle``.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LevelsTableRow:
    """
    One strike's combined GEX/DEX/VEX/CEX/flow/IV-change data.

    ``net_gex_holder``/``net_gex_assumed``/``net_gex_inferred`` are the
    three sign conventions institutional_metrics_spec.md section 10's
    "Sign-convention unification" note establishes (holder-side raw +
    assumed-dealer view, plus section 2's separately-labeled inferred
    view) -- the GUI's sign-convention radio button selects which of
    these three drives row coloring; it does not compute a fourth.

    ``net_gex_holder``/``net_gex_assumed``/``net_dex``/``vex``/``cex`` are
    ``None`` when this strike has no matching row in the relevant source
    (``GexDexResult.strike_rows``/``ExposureProfileResult.strike_rows``) --
    e.g. a total Greeks-fetch outage (the whole section is absent), a
    *partial* Greeks-fetch failure (this specific strike's legs failed
    while others succeeded), or ``ExposureProfileCalculator`` dropping a
    strike's legs on missing/non-finite ``mark_iv``. Independent review
    (Task D3 round 1, Important #1) caught an earlier version of this
    dataclass defaulting these to ``0.0`` instead -- indistinguishable from
    "this strike really has flat/zero exposure" and, worse, rendered by the
    GUI as a uniform green ("positive") column on a total outage. ``None``
    here matches the convention ``net_gex_inferred``/``net_taker_flow``/
    ``delta_1d_iv`` already used correctly below.

    ``net_gex_inferred`` is ``None`` when the inferred view's coverage
    gate (``DealerInventoryResult.render_inferred``) failed for this
    expiration -- never a silently-substituted 0.

    ``net_taker_flow`` is the SUM of this strike's call-leg and put-leg
    net flow (``FlowResult.flow_data[strike]``) -- a directional volume
    quantity, so combining legs by addition (not averaging) matches how
    OI/volume are already combined elsewhere in this codebase. ``None``
    when the expiration's flow section was gated insufficient
    (``FlowResult.sufficient_data is False``) or the strike had no flow
    data at all -- never a silently-substituted 0.

    ``delta_1d_iv`` is the AVERAGE of this strike's matched call-leg and
    put-leg day-over-day IV change (``FixedStrikeVolResult.rows``) -- an
    IV quantity, so combining legs by averaging (not summing) matches how
    ATM IV is conventionally the average of the call/put smile at that
    strike. ``None`` when there is no fixed-strike-vol result for this
    expiration, the prior snapshot was stale
    (``FixedStrikeVolResult.stale_prior``), or neither leg matched at this
    strike -- never a silently-substituted 0.

    The four ``is_*_assumed``/``is_*_inferred`` marker pairs are
    convention-specific (assumed-dealer key levels and inferred-sign key
    levels are independently-detected strikes, per
    ``DealerInventoryKeyLevels``'s docstring) -- the GUI picks the pair
    matching the active sign-convention radio ("holder" reads the
    assumed-dealer markers, since holder-side GEX has no independent
    zero-crossing of its own to detect a wall/support/HVL from; see
    ``levels_table_builder`` module docstring). ``is_max_pain`` is
    convention-independent (max pain is computed from OI/premium alone).
    """

    strike: float
    call_oi: float
    put_oi: float
    net_gex_holder: Optional[float]
    net_gex_assumed: Optional[float]
    net_gex_inferred: Optional[float]
    net_dex: Optional[float]
    vex: Optional[float]
    cex: Optional[float]
    net_taker_flow: Optional[float]
    delta_1d_iv: Optional[float]
    is_call_wall_assumed: bool = False
    is_put_support_assumed: bool = False
    is_hvl_assumed: bool = False
    is_call_wall_inferred: bool = False
    is_put_support_inferred: bool = False
    is_hvl_inferred: bool = False
    is_max_pain: bool = False


@dataclass(frozen=True)
class LevelsTable:
    """Full per-strike levels table for one expiration."""

    expiration: str
    rows: Tuple[LevelsTableRow, ...]  # sorted by strike ascending
    gex_dex_available: bool
    """Whether ``GexDexResult`` was present for this expiration at all
    (independent review, Task D3 round 1, Important #1) -- False means
    every row's ``net_gex_holder``/``net_gex_assumed``/``net_dex`` is
    None (a total Greeks-fetch outage). A row's individual fields can
    still be None even when this is True (a *partial* outage affecting
    only some strikes) -- this flag only reports the whole-section case,
    matching ``inferred_available``'s own scope."""

    exposure_available: bool
    """Whether ``ExposureProfileResult`` was present for this expiration
    at all -- False means every row's ``vex``/``cex`` is None (a total
    exposure-profile computation failure). Same whole-section-only scope
    as ``gex_dex_available`` -- see that field's docstring."""

    inferred_available: bool
    """Whether ``net_gex_inferred``/``is_*_inferred`` carry real data for
    this expiration (``DealerInventoryResult.render_inferred``) -- False
    means the inferred sign-convention radio option has nothing to show
    and every row's inferred fields are None/False."""

    net_taker_flow_available: bool
    """Whether ``net_taker_flow`` carries real data (the expiration's flow
    section passed its data-sufficiency gate) -- False means every row's
    ``net_taker_flow`` is None."""

    delta_1d_iv_available: bool
    """Whether ``delta_1d_iv`` carries real data (a non-stale fixed-strike
    vol comparison exists for this expiration) -- False means every row's
    ``delta_1d_iv`` is None."""
