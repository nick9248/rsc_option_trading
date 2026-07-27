"""
Result models for GEX/DEX (gamma/delta exposure) analysis.

Frozen dataclasses per refactor_design_spec.md section 2.2. Mirror the dict
shape historically produced by ``GexDexCalculator.calculate()`` and
``GexDexCalculator.aggregate_across_expirations()``.
"""

from dataclasses import dataclass
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
    """Full GEX/DEX result for one expiration (or the cross-expiry aggregate)."""

    strike_rows: Tuple[GexDexStrikeRow, ...]
    cumulative_gex: Dict[float, float]
    cumulative_dex: Dict[float, float]
    key_levels: GexDexKeyLevels
    spot_price: float
    total_net_gex: float
    total_net_dex: float
    currency: str
    expiration_count: Optional[int] = None  # set only on the AGGREGATE result

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
        }
        if self.expiration_count is not None:
            result["expiration_count"] = self.expiration_count
        return result
