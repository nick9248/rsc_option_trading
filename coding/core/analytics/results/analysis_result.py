"""
Result models for the full on-chain analysis run (all expirations + market-wide).

Frozen dataclasses per refactor_design_spec.md section 2.6. ``OnChainAnalysisResult``
is the top-level aggregate assembled by ``OnChainAnalysisBuilder`` (service layer,
introduced in T6); it is not wired to any producer yet in T2/T3.

``atm_iv_by_expiration`` and ``recent_trades`` are public fields on the result —
this replaces the analyzer's private ``_atm_ivs``/``_recent_trades`` attributes
(H4) without adding getters to a class that no longer exists once
``OnChainAnalyzer`` is narrowed (T10).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from coding.core.analytics.historical_normalizer import NormalizedMetric
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.flow_results import FlowResult
from coding.core.analytics.results.gex_dex_results import GexDexResult
from coding.core.analytics.results.market_wide_results import MarketWideResult
from coding.core.analytics.results.vol_surface_results import VolSurfaceResult


@dataclass(frozen=True)
class MarketMetricsResult:
    """Currency-wide market metrics (not derived from the book summary data)."""

    dvol: Optional[float]
    iv_percentile: Optional[float]
    iv_rank: Optional[float]
    current_funding: Optional[float]
    funding_8h: Optional[float]


@dataclass(frozen=True)
class TrendSnapshot:
    """Previous DB snapshot for one expiration (all fields Optional)."""

    max_pain_strike: Optional[float]
    call_oi: Optional[float]
    put_oi: Optional[float]
    pc_ratio: Optional[float]
    total_volume: Optional[float]
    volume_ratio: Optional[float]


@dataclass(frozen=True)
class OiChangeRow:
    """Open-interest change for one (strike, option_type) between two snapshots."""

    strike: float
    option_type: str
    previous_oi: float
    current_oi: float
    change: float
    change_pct: float


@dataclass(frozen=True)
class OiChangesResult:
    """Significant open-interest changes since the previous snapshot."""

    rows: Tuple[OiChangeRow, ...]  # already sorted, capped at 15 for display
    total_significant: int
    has_previous_snapshot: bool


@dataclass(frozen=True)
class IvPercentileResult:
    """IV percentile of the current ATM IV vs its own history."""

    atm_strike: float
    current_iv: float
    percentile: float
    history_days: int


@dataclass(frozen=True)
class ExpirationBundle:
    """Every per-expiration result, bundled together."""

    expiration: str
    analysis: ExpirationAnalysisResult
    gex_dex: Optional[GexDexResult]
    flow: Optional[FlowResult]
    vol_surface: Optional[VolSurfaceResult]
    oi_changes: Optional[OiChangesResult]
    iv_percentile: Optional[IvPercentileResult]
    trend: Optional[TrendSnapshot]
    flow_chart_paths: Dict[str, str]  # {"distribution"|"net_flow"|"trend": path}
    enriched_instruments: Tuple[Dict[str, Any], ...]  # greeks-enriched raw dicts


@dataclass(frozen=True)
class OnChainAnalysisResult:
    """Top-level aggregate for one full on-chain analysis run."""

    currency: str
    underlying_price: float
    generated_at: datetime
    market_metrics: MarketMetricsResult
    expirations: Tuple[ExpirationBundle, ...]  # sorted chronologically-by-string
    market_wide: MarketWideResult
    parsed_instruments: Dict[str, Tuple[Dict[str, Any], ...]]  # expiration -> parsed dicts
    atm_iv_by_expiration: Dict[str, float]
    recent_trades: Tuple[Dict[str, Any], ...]
    # institutional_metrics_spec.md section 1: percentile/z-score context for
    # the front-month AVAILABLE metrics (net GEX, PCR-OI, total OI, DVOL,
    # funding -- VRP is deliberately excluded, see
    # OnChainAnalysisService._build_normalized_metrics's docstring). Default
    # empty dict keeps every pre-existing direct OnChainAnalysisResult(...)
    # constructor (tests, the builder) working unchanged.
    normalized_metrics: Dict[str, NormalizedMetric] = field(default_factory=dict)
    # C1 review Important #1: which expiration normalized_metrics' per-
    # expiry entries (net GEX, PCR-OI, total OI) describe -- the report
    # has no way to tell a reader which expiry the numbers are for
    # without this. Set by _pick_front_month_expiration, the true
    # nearest-DTE expiration, not a lexicographic string sort.
    normalized_metrics_front_month: Optional[str] = None
    # C1 review Important #4: the most-stale queried table's max
    # timestamp, set only when it exceeds the spec's 3h staleness
    # threshold (institutional_metrics_spec.md section 1(c)) -- None means
    # either fresh, or freshness could not be determined.
    normalized_metrics_stale_since: Optional[datetime] = None

    def bundle(self, expiration: str) -> Optional[ExpirationBundle]:
        """Return the ``ExpirationBundle`` for ``expiration``, or ``None`` if absent."""
        for eb in self.expirations:
            if eb.expiration == expiration:
                return eb
        return None

    def expiration_names(self) -> Tuple[str, ...]:
        """Return the expiration strings in this result, in stored order."""
        return tuple(eb.expiration for eb in self.expirations)
