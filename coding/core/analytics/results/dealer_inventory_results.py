"""
Result models for taker-flow-inferred dealer positioning
(institutional_metrics_spec.md section 2 / task C3).

Frozen dataclasses, mirroring the pattern established by
``gex_dex_results.py`` (bugfix_spec.md Item 8 / task B1's holder-side-raw +
labeled-assumed-dealer-view convention -- D7, task-C3-brief.md). This module
adds a THIRD, separately-labeled view (inferred dealer positioning from
signed taker flow) -- it does not redefine or blend the existing two.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class DealerInventoryStrikeRow:
    """
    Per-strike inferred dealer inventory (Glassnode taker-flow method).

    ``dealer_net_c``/``dealer_net_p`` are the NEGATED cumulative signed taker
    flow per leg (dealer is the mirror of taker flow) -- NOT OI-weighted,
    unlike ``GexDexStrikeRow.call_gamma``/``put_gamma``, because the signed
    taker-flow accumulation already encodes position size directly.
    """

    strike: float
    dealer_net_c: float
    dealer_net_p: float
    inferred_gex: float
    inferred_dex: float
    call_gross_volume: float = 0.0
    put_gross_volume: float = 0.0
    call_trade_count: int = 0
    put_trade_count: int = 0


@dataclass(frozen=True)
class DealerInventoryLevel:
    """A strike and its inferred GEX value (call wall / put support, inferred sign)."""

    strike: float
    inferred_gex: float


@dataclass(frozen=True)
class DealerInventoryKeyLevels:
    """Key levels re-detected on the INFERRED sign (spec §2(c)): "key levels
    (walls, flip) recomputed on inferred sign" -- these are independent
    strikes from the assumed-view's ``GexDexKeyLevels``, not a copy of them."""

    call_resistance: Optional[DealerInventoryLevel]
    put_support: Optional[DealerInventoryLevel]
    hvl: Optional[float]  # cumulative inferred-GEX zero-crossing strike


@dataclass(frozen=True)
class DealerInventoryCoverageReport:
    """
    Output of ``DealerInventoryCalculator.coverage_report`` -- the OI-bound
    violation check (spec §2(b)): a maker's (dealer's) position can never
    exceed total open interest, so ``|cumulative net taker| > OI`` is an
    impossible-strike violation. This is the empirical half of decision D9's
    gate (the other half, trade-history HOUR coverage, is computed at the
    service layer from ``DatabaseRepository.get_trade_hour_coverage`` --
    this calculator never queries).
    """

    n_strikes: int
    """Number of (strike, option_type) legs considered -- flow rows WITH an
    OI reference only (see ``legs_excluded_no_oi``), matching spec T2.2's
    per-leg count."""

    n_violations: int
    violation_rate: float
    """0.0 when ``n_strikes == 0`` (no legs to violate -- avoids a
    ZeroDivisionError on a zero-trade expiry; the coverage/hours half of the
    gate is what correctly fails a zero-trade expiry, not this field)."""

    worst_strikes: Tuple[Dict[str, Any], ...] = ()
    """Up to 5 worst violations, each
    {"strike", "option_type", "taker_net", "open_interest", "excess"},
    sorted by excess (|taker_net| - open_interest) descending."""

    legs_excluded_no_oi: int = 0
    """Fix round (Important #3): legs with trade flow but NO entry in
    ``oi_by_instrument`` (as opposed to a real 0 OI) -- e.g. an instrument
    whose ticker fetch failed transiently and was dropped from
    ``instruments_with_greeks`` upstream. Excluded from ``n_strikes``/
    ``violation_rate`` entirely (bugfix_spec.md section 2(c): stale/unpriced
    legs are dropped from the calculation and counted separately, never
    folded into the violation numerator/denominator) rather than defaulting
    to a 0 OI reference, which would make any nonzero flow on that leg look
    like a violation purely from missing data, not a real data-quality
    problem."""


@dataclass(frozen=True)
class DealerInventoryResult:
    """
    Full per-expiration inferred-dealer-positioning result.

    D9 (task-C3-brief.md, BINDING): ``render_inferred`` is the single
    decision point -- ``coverage >= 0.95 AND violation_rate <= 0.05``. When
    False, ``unavailable_reason`` explains why and the report must fall back
    to the existing assumed-dealer view (never silently blend the two).
    """

    strike_rows: Tuple[DealerInventoryStrikeRow, ...]
    key_levels: DealerInventoryKeyLevels
    total_inferred_gex: float
    total_inferred_dex: float
    spot_price: float
    currency: str
    t0_epoch_ms: int
    """Window start used for the signed-taker-flow accumulation:
    max(first trade seen for this expiry, 2026-04-25 coverage-stable date)."""

    coverage: float
    """present_hours / expected_hours of trade-history since t0 (0.0 if
    expected_hours == 0, e.g. t0 == now -- avoids ZeroDivisionError)."""

    violation_rate: float
    n_signed_trades: int
    render_inferred: bool
    unavailable_reason: Optional[str] = None
    stale_strikes: Tuple[Dict[str, Any], ...] = ()
    """Strikes/legs with trade history but absent from the current chain
    (expired/delisted mid-window) -- dropped from strike_rows, diagnostic
    only. Each entry: {"strike", "option_type", "taker_net"}."""

    # --- Additive fields (Task Wave-H-E) ---
    instruments_missing_gamma: int = 0
    """Count of legs folded into ``strike_rows`` by ``DealerInventoryCalculator.
    calculate()`` whose gamma OR delta came back null (the leg IS in the
    current chain -- unlike a leg entirely absent from ``greeks_by_
    instrument``, which is not this calculator's concern at all). Their
    GEX/DEX contribution is 0.0, not "unknown" -- this field is what makes
    that distinguishable from a leg that genuinely has zero exposure.
    Mirrors ``GexDexResult.instruments_missing_gamma``'s naming and
    semantics (Task G2-A) -- same failure class, this calculator's own
    ``_or_0.0`` bug had gone unfixed by that earlier task because it lives
    in a different calculator entirely."""

    oi_missing_gamma: float = 0.0
    """Sum of open_interest belonging to ``instruments_missing_gamma`` --
    the OI-weighted magnitude of the completeness gap, reused from the same
    enriched-instruments list as ``greeks_by_instrument`` (no second
    query). Unlike ``GexDexResult.oi_missing_gamma``, this calculator's own
    GEX/DEX formulas are NOT OI-weighted (see the module docstring) -- this
    field is a pure diagnostic magnitude, not an input to any formula
    here."""
