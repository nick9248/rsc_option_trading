"""
Result models for GEX/DEX (gamma/delta exposure) analysis.

Frozen dataclasses per refactor_design_spec.md section 2.2. Mirror the dict
shape historically produced by ``GexDexCalculator.calculate()`` and
``GexDexCalculator.aggregate_across_expirations()``.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class GexDexStrikeRow:
    """
    Per-strike gamma/delta exposure and open interest.

    ``net_gamma``, ``cumulative_gex``, ``cumulative_dex`` duplicate data also
    held at the ``GexDexResult`` level (``cumulative_gex``/``cumulative_dex``
    dicts) — this mirrors the legacy ``GexDexCalculator.strike_data`` shape
    exactly (it stores the running cumulative sums back onto each strike's
    own entry as it iterates), which the golden-master fixture depends on.

    bugfix_spec.md Item 8: ``net_gex``/``net_dex`` are DEPRECATED names for
    exactly one release -- ``net_gex`` is the ASSUMED-DEALER gamma exposure
    (dealers long calls / short puts, the SqueezeMetrics heuristic; kept
    unrenamed since it is the specific published convention this number
    already means) and ``net_dex`` is the HOLDER-side (raw, no positioning
    assumption) delta exposure. ``dealer_gamma_exposure``/
    ``delta_exposure_holder`` are exact aliases of them (same value, the
    correctly-labelled name); ``gamma_exposure_holder``/
    ``dealer_delta_exposure`` are the previously-missing other half of each
    pair. All four default from ``net_gex``/``net_dex``/``call_gamma``/
    ``put_gamma`` via ``__post_init__`` when not given explicitly, so
    existing construction sites (tests, ``GexDexCalculator``) that only set
    the original fields keep working unchanged.
    """

    strike: float
    call_gamma: float
    put_gamma: float
    call_delta: float
    put_delta: float
    call_oi: float
    put_oi: float
    net_gex: float
    net_dex: float
    net_gamma: float
    cumulative_gex: float
    cumulative_dex: float

    # --- Additive fields (bugfix_spec.md Item 8) ---
    gamma_exposure_holder: Optional[float] = None
    """(call_gamma + put_gamma) * S^2 * 0.01 -- holder-side gamma exposure,
    same units/scaling as net_gex/dealer_gamma_exposure (USD per 1% spot
    move). This is what ``GexDexCalculator._calculate_gex_dex`` (the real
    production construction path) computes and passes explicitly.

    bugfix_spec.md Item 8 fix-review (Important #4): the ``__post_init__``
    fallback below -- used ONLY when a caller constructs this row directly
    without passing the field (e.g. a test or another producer that hasn't
    migrated) -- has no S^2 available at the row level and defaults to the
    RAW, UNSCALED ``call_gamma + put_gamma`` sum instead. That fallback
    value is a placeholder for callers that don't care about this field's
    accuracy, not a second unit convention -- do not read a row's
    ``gamma_exposure_holder`` as USD-scaled unless it came from
    ``GexDexCalculator``. Unifying the two construction paths' units is a
    separate, riskier follow-up, not done here."""

    delta_exposure_holder: Optional[float] = None
    """Alias of ``net_dex`` (same value, correctly-labelled name)."""

    dealer_gamma_exposure: Optional[float] = None
    """Alias of ``net_gex`` (same value, correctly-labelled name)."""

    dealer_delta_exposure: Optional[float] = None
    """-net_dex -- the assumed-dealer view (dealers are short what
    customers, i.e. holders, hold)."""

    def __post_init__(self) -> None:
        if self.gamma_exposure_holder is None:
            object.__setattr__(self, "gamma_exposure_holder", self.call_gamma + self.put_gamma)
        if self.delta_exposure_holder is None:
            object.__setattr__(self, "delta_exposure_holder", self.net_dex)
        if self.dealer_gamma_exposure is None:
            object.__setattr__(self, "dealer_gamma_exposure", self.net_gex)
        if self.dealer_delta_exposure is None:
            object.__setattr__(self, "dealer_delta_exposure", -self.net_dex)


@dataclass(frozen=True)
class GexDexLevel:
    """A strike and its net GEX value (used for call resistance / put support)."""

    strike: float
    net_gex: float


@dataclass(frozen=True)
class GexDexKeyLevels:
    """
    Key trading levels detected from the GEX/DEX profile.

    bugfix_spec.md Item 2 / task B1: ``hvl``/``gamma_flip`` are a strike-axis
    cumulative-net-GEX sign-crossing artifact -- a property of how open
    interest happens to be distributed along the strike axis, not of how
    dealer gamma actually responds to the underlying moving. They were
    historically mislabeled "Zero Gamma Level"; the correctly-labeled,
    identical value is exposed as ``cumulative_gex_zero_strike`` below.
    ``hvl``/``gamma_flip`` are kept (not renamed/deleted) because
    ``repository.save_onchain_snapshot`` persists ``key_levels.hvl`` into the
    LIVE ``hvl_level`` DB column read by the straddle regime gate and IC/BF
    scanners, and ``synthesis.py`` (morning-note rendering) also reads
    ``key_levels.hvl`` directly -- decision D3 (task-B1-brief.md) keeps both
    that column and every attribute name any persistence/synthesis path
    already reads unchanged in this task.

    The actual re-priced dealer-gamma flip (SpotGamma's "Zero Gamma Level"
    definition) is ``zero_gamma_level``, computed by
    ``GammaProfileCalculator`` re-pricing Black-Scholes gamma across a grid
    of hypothetical spot levels (sticky-strike). It is NOT wired into any
    persisted/regime-gate-reading column in this task.
    """

    call_resistance: Optional[GexDexLevel]
    put_support: Optional[GexDexLevel]
    hvl: Optional[float]  # DEPRECATED name -- see cumulative_gex_zero_strike above
    gamma_flip: Optional[float]  # DEPRECATED name -- historically always equal to hvl

    # --- Additive fields (task B1 / bugfix_spec.md Item 2) ---
    cumulative_gex_zero_strike: Optional[float] = None
    """Renamed, correctly-documented alias of ``hvl``/``gamma_flip``: the
    strike where CUMULATIVE net GEX (summed strike-by-strike) changes sign.
    Same value as ``hvl``. NOT a re-priced gamma flip -- see
    ``zero_gamma_level``."""

    zero_gamma_level: Optional[float] = None
    """NEW, correct: the hypothetical spot price at which re-priced total
    dealer gamma changes sign (the actual gamma flip). ``None`` if the
    re-priced profile never crosses zero within +-50% of spot, if the book
    is FLAT (net dealer gamma identically zero), or if inputs were
    insufficient (e.g. no legs, non-positive spot)."""

    zero_gamma_crossings: Tuple[float, ...] = ()
    """All zero-gamma crossings found on the re-pricing grid, ascending. A
    book can legitimately have more than one; ``zero_gamma_level`` is
    whichever of these sits nearest current spot."""

    net_gex_at_spot: Optional[float] = None
    """NetGEX(spot) from the re-priced profile (NOT the strike-axis
    ``total_net_gex``, which can differ due to Deribit gamma rounding). This,
    not the ZGL location, is what determines the current gamma regime."""

    gamma_regime: Optional[str] = None
    """"POSITIVE" | "NEGATIVE" | "FLAT" | "UNKNOWN" (re-priced profile)."""

    legs_skipped: int = 0
    """Legs excluded from the re-pricing grid (expiring within 1 hour, or
    otherwise gated) -- see GammaProfileCalculator.calculate()."""


@dataclass(frozen=True)
class GexDexResult:
    """
    Full GEX/DEX result for one expiration (or the cross-expiry aggregate).

    bugfix_spec.md Item 8: ``total_net_gex``/``total_net_dex`` are DEPRECATED
    names for exactly one release -- see ``GexDexStrikeRow``'s docstring for
    the same holder/dealer distinction at the total level.
    ``dealer_gamma_exposure_total``/``delta_exposure_holder_total`` are exact
    aliases (same value); ``gamma_exposure_holder_total``/
    ``dealer_delta_exposure_total`` are the previously-missing other half.
    All four default via ``__post_init__`` when not given explicitly (from
    ``total_net_gex``/``total_net_dex``, or summed from ``strike_rows`` for
    ``gamma_exposure_holder_total``), so existing construction sites keep
    working unchanged.
    """

    strike_rows: Tuple[GexDexStrikeRow, ...]
    cumulative_gex: Dict[float, float]
    cumulative_dex: Dict[float, float]
    key_levels: GexDexKeyLevels
    spot_price: float
    total_net_gex: float
    total_net_dex: float
    currency: str
    expiration_count: Optional[int] = None  # set only on the AGGREGATE result

    # --- Additive fields (bugfix_spec.md Item 8) ---
    gamma_exposure_holder_total: Optional[float] = None
    """Holder-side raw gamma exposure (>= 0 always) -- Sigma over
    strike_rows.gamma_exposure_holder."""

    delta_exposure_holder_total: Optional[float] = None
    """Alias of ``total_net_dex`` (same value, correctly-labelled name)."""

    dealer_gamma_exposure_total: Optional[float] = None
    """Alias of ``total_net_gex`` (same value, correctly-labelled name)."""

    dealer_delta_exposure_total: Optional[float] = None
    """-total_net_dex -- the assumed-dealer view."""

    def __post_init__(self) -> None:
        if self.dealer_gamma_exposure_total is None:
            object.__setattr__(self, "dealer_gamma_exposure_total", self.total_net_gex)
        if self.delta_exposure_holder_total is None:
            object.__setattr__(self, "delta_exposure_holder_total", self.total_net_dex)
        if self.dealer_delta_exposure_total is None:
            object.__setattr__(self, "dealer_delta_exposure_total", -self.total_net_dex)
        if self.gamma_exposure_holder_total is None:
            total = sum(row.gamma_exposure_holder for row in self.strike_rows) if self.strike_rows else 0.0
            object.__setattr__(self, "gamma_exposure_holder_total", total)

    def to_dict(self) -> Dict[str, Any]:
        """
        Reproduce the legacy ``GexDexCalculator.calculate()`` /
        ``aggregate_across_expirations()`` dict shape. Consumed by
        ``repository.save_onchain_snapshot`` and
        ``scripts/backfill_gex_dex_history.py::_extract_update_values``.
        """
        strike_data: Dict[float, Dict[str, float]] = {
            row.strike: {
                "call_gamma": row.call_gamma,
                "put_gamma": row.put_gamma,
                "call_delta": row.call_delta,
                "put_delta": row.put_delta,
                "call_oi": row.call_oi,
                "put_oi": row.put_oi,
                "net_gex": row.net_gex,
                "net_dex": row.net_dex,
                "net_gamma": row.net_gamma,
                "cumulative_gex": row.cumulative_gex,
                "cumulative_dex": row.cumulative_dex,
                # bugfix_spec.md Item 8 (additive):
                "gamma_exposure_holder": row.gamma_exposure_holder,
                "delta_exposure_holder": row.delta_exposure_holder,
                "dealer_gamma_exposure": row.dealer_gamma_exposure,
                "dealer_delta_exposure": row.dealer_delta_exposure,
            }
            for row in self.strike_rows
        }

        kl = self.key_levels
        result: Dict[str, Any] = {
            "strike_data": strike_data,
            "cumulative_gex": dict(self.cumulative_gex),
            "cumulative_dex": dict(self.cumulative_dex),
            "key_levels": {
                "call_resistance": (
                    {"strike": kl.call_resistance.strike, "net_gex": kl.call_resistance.net_gex}
                    if kl.call_resistance is not None
                    else None
                ),
                "put_support": (
                    {"strike": kl.put_support.strike, "net_gex": kl.put_support.net_gex}
                    if kl.put_support is not None
                    else None
                ),
                "hvl": kl.hvl,
                "gamma_flip": kl.gamma_flip,
                "cumulative_gex_zero_strike": kl.cumulative_gex_zero_strike,
                "zero_gamma_level": kl.zero_gamma_level,
                "zero_gamma_crossings": list(kl.zero_gamma_crossings),
                "net_gex_at_spot": kl.net_gex_at_spot,
                "gamma_regime": kl.gamma_regime,
                "legs_skipped": kl.legs_skipped,
            },
            "spot_price": self.spot_price,
            "total_net_gex": self.total_net_gex,
            "total_net_dex": self.total_net_dex,
            # bugfix_spec.md Item 8 (additive):
            "gamma_exposure_holder_total": self.gamma_exposure_holder_total,
            "delta_exposure_holder_total": self.delta_exposure_holder_total,
            "dealer_gamma_exposure_total": self.dealer_gamma_exposure_total,
            "dealer_delta_exposure_total": self.dealer_delta_exposure_total,
        }
        if self.expiration_count is not None:
            result["expiration_count"] = self.expiration_count
        return result
