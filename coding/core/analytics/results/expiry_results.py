"""
Result models for per-expiration on-chain analysis.

Frozen dataclasses per refactor_design_spec.md section 2.1. These mirror the
dict shapes historically produced by ``OnChainAnalyzer.analyze_expiration()``
and its helper methods (``calculate_max_pain``, ``calculate_put_call_ratio``,
``calculate_volume_stats``, ``analyze_moneyness``, ``find_support_resistance``).

The dataclasses are frozen (immutable) — but fields holding ``dict``/``tuple``
contents are only immutable at the container level; callers must treat their
contents as read-only by convention.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class StrikeOiRow:
    """Open interest and volume for one strike, split by call/put."""

    strike: float
    call_oi: float
    put_oi: float
    call_volume: float
    put_volume: float


@dataclass(frozen=True)
class MaxPainResult:
    """Max pain strike and the pain (option-writer loss) profile by strike."""

    max_pain_strike: Optional[float]
    pain_by_strike: Dict[float, float]
    min_pain_value: float


@dataclass(frozen=True)
class PutCallRatioResult:
    """Put/call ratio computed from open interest."""

    total_call_oi: float
    total_put_oi: float
    ratio: float  # float("inf") when call OI == 0 (unchanged legacy semantics)
    bias: str


@dataclass(frozen=True)
class VolumeStatsResult:
    """Total call/put volume and the volume-based put/call ratio."""

    total_call_volume: float
    total_put_volume: float
    total_volume: float
    volume_ratio: float  # float("inf") when call volume == 0


@dataclass(frozen=True)
class MoneynessLeg:
    """ITM/OTM open-interest and notional breakdown for one leg (calls, puts, or totals)."""

    itm_oi: float
    otm_oi: float
    total_oi: float
    itm_notional: float
    otm_notional: float
    total_notional: float
    itm_pct: float
    otm_pct: float


@dataclass(frozen=True)
class MoneynessResult:
    """Moneyness (ITM/OTM) analysis across calls, puts, and combined totals."""

    calls: MoneynessLeg
    puts: MoneynessLeg
    totals: MoneynessLeg
    oi_skew: str


@dataclass(frozen=True)
class LevelRef:
    """A support/resistance strike and the open interest backing it."""

    strike: float
    open_interest: float  # call_oi for resistance, put_oi for support


@dataclass(frozen=True)
class SupportResistanceResult:
    """Top support/resistance levels plus nearest short-term levels."""

    resistance_levels: Tuple[LevelRef, ...]
    support_levels: Tuple[LevelRef, ...]
    short_term_resistance: Optional[LevelRef]
    short_term_support: Optional[LevelRef]


@dataclass(frozen=True)
class ExpirationAnalysisResult:
    """Full analysis result for a single expiration."""

    expiration: str
    underlying_price: float
    total_instruments: int
    call_count: int
    put_count: int
    strike_rows: Tuple[StrikeOiRow, ...]  # sorted by strike ascending
    max_pain: MaxPainResult
    put_call_ratio: PutCallRatioResult
    volume_stats: VolumeStatsResult
    moneyness: MoneynessResult
    support_resistance: SupportResistanceResult

    def to_dict(self) -> Dict[str, Any]:
        """
        Reproduce the legacy ``OnChainAnalyzer.analyze_expiration()`` dict
        shape, key-for-key. Consumed by ``repository.save_onchain_snapshot``
        and ``ProspectiveCollector``.
        """
        strike_data: Dict[float, Dict[str, float]] = {
            row.strike: {
                "call_oi": row.call_oi,
                "put_oi": row.put_oi,
                "call_volume": row.call_volume,
                "put_volume": row.put_volume,
            }
            for row in self.strike_rows
        }

        def _leg_dict(leg: MoneynessLeg) -> Dict[str, float]:
            return {
                "itm_oi": leg.itm_oi,
                "otm_oi": leg.otm_oi,
                "total_oi": leg.total_oi,
                "itm_notional": leg.itm_notional,
                "otm_notional": leg.otm_notional,
                "total_notional": leg.total_notional,
                "itm_pct": leg.itm_pct,
                "otm_pct": leg.otm_pct,
            }

        sr = self.support_resistance
        return {
            "expiration": self.expiration,
            "underlying_price": self.underlying_price,
            "total_instruments": self.total_instruments,
            "call_count": self.call_count,
            "put_count": self.put_count,
            "strike_data": strike_data,
            "max_pain": {
                "max_pain_strike": self.max_pain.max_pain_strike,
                "pain_by_strike": dict(self.max_pain.pain_by_strike),
                "min_pain_value": self.max_pain.min_pain_value,
            },
            "put_call_ratio": {
                "total_call_oi": self.put_call_ratio.total_call_oi,
                "total_put_oi": self.put_call_ratio.total_put_oi,
                "ratio": self.put_call_ratio.ratio,
                "bias": self.put_call_ratio.bias,
            },
            "volume_stats": {
                "total_call_volume": self.volume_stats.total_call_volume,
                "total_put_volume": self.volume_stats.total_put_volume,
                "total_volume": self.volume_stats.total_volume,
                "volume_ratio": self.volume_stats.volume_ratio,
            },
            "moneyness": {
                "calls": _leg_dict(self.moneyness.calls),
                "puts": _leg_dict(self.moneyness.puts),
                "totals": _leg_dict(self.moneyness.totals),
                "oi_skew": self.moneyness.oi_skew,
            },
            "support_resistance": {
                "resistance_levels": [
                    {"strike": level.strike, "call_oi": level.open_interest}
                    for level in sr.resistance_levels
                ],
                "support_levels": [
                    {"strike": level.strike, "put_oi": level.open_interest}
                    for level in sr.support_levels
                ],
                "short_term_resistance": (
                    {
                        "strike": sr.short_term_resistance.strike,
                        "call_oi": sr.short_term_resistance.open_interest,
                    }
                    if sr.short_term_resistance is not None
                    else None
                ),
                "short_term_support": (
                    {
                        "strike": sr.short_term_support.strike,
                        "put_oi": sr.short_term_support.open_interest,
                    }
                    if sr.short_term_support is not None
                    else None
                ),
            },
        }
