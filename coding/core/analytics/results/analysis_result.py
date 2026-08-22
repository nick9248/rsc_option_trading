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
from coding.core.analytics.results.dealer_inventory_results import DealerInventoryResult
from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.exposure_profile_results import ExposureProfileResult
from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult
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
    # Wave H Task H-F, Fix 4: the DVOL lookback's observation count was
    # already computed (len(close_values)) and logged to progress_callback,
    # but never carried into the report -- a reader could not tell "IV Rank
    # 50.0% from 200 observations" (fine) apart from "...from 1 observation"
    # (not fine, and previously that degenerate case fabricated 50.0
    # outright -- see iv_rank's own None-on-degenerate-range fix). Optional
    # with a default so existing call sites that construct this dataclass
    # without it are unaffected.
    iv_rank_observation_count: Optional[int] = None


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
    # institutional_metrics_spec.md section 2 / task C3: taker-flow-inferred
    # dealer positioning (the third, separately-labeled view alongside
    # gex_dex's holder-side/assumed-dealer pair -- D7). Optional/defaulted
    # so every pre-existing direct ExpirationBundle(...) constructor (tests,
    # the builder) keeps working unchanged. None when the additive
    # computation guard caught an unexpected error (see
    # OnChainAnalysisService._calculate_inferred_dealer_positioning).
    dealer_inventory: Optional[DealerInventoryResult] = None

    # institutional_metrics_spec.md section 4 \ task C5: per-strike
    # vanna/charm exposure profile (VEX/CEX), holder-side/assumed-dealer
    # pair -- same D7 convention as gex_dex/dealer_inventory above.
    # Optional/defaulted so every pre-existing direct ExpirationBundle(...)
    # constructor (tests, the builder) keeps working unchanged. None when
    # the additive computation guard caught an unexpected error (see
    # OnChainAnalysisService._calculate_exposure_profile).
    exposure_profile: Optional[ExposureProfileResult] = None

    # institutional_metrics_spec.md section 7 / Task C8: fixed-strike vol
    # change matrix (day-over-day IV change per strike vs the ATM move,
    # sticky-strike/sticky-delta/repriced attribution). Optional/defaulted
    # so every pre-existing direct ExpirationBundle(...) constructor
    # (tests, the builder) keeps working unchanged. None when there is no
    # repository, or building the matrix raised unexpectedly (see
    # OnChainAnalysisService._calculate_fixed_strike_vol_matrix) -- distinct
    # from a present result with regime == "INDETERMINATE" (a normal,
    # renderable "insufficient history" outcome, not an error).
    fixed_strike_vol: Optional[FixedStrikeVolResult] = None


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

    # institutional_metrics_spec.md section 6 / task C7: signed delta-
    # weighted, premium-weighted taker flow (HIRO analog), summed over
    # delta_flow_lookback_hours from the persisted flow_delta_hourly table
    # (OnChainAnalysisService._build_delta_flow_summary) -- NOT recomputed
    # from raw trades at report time. One FlowBucket per expiration that
    # traded in the window, plus exactly one with expiration == "ALL" (the
    # currency-level total). Empty tuple (default) when there is no
    # repository, or flow_delta_hourly has no rows yet for this window --
    # matches the codebase's "no data -> no section" convention; see
    # format_delta_flow_section.
    delta_flow_buckets: Tuple[FlowBucket, ...] = ()
    delta_flow_lookback_hours: float = 24.0
    # Review fix (Important #4): coverage/recency signal -- how many
    # hourly "ALL" rows are actually present in the window
    # (DatabaseRepository.get_delta_flow_coverage), and the most recently
    # persisted hour when it is stale (more than
    # OnChainAnalysisService._DELTA_FLOW_STALENESS_THRESHOLD_HOURS behind
    # "now"), else None. Mirrors normalized_metrics_stale_since's
    # convention above.
    delta_flow_hours_present: int = 0
    delta_flow_stale_since: Optional[datetime] = None

    def bundle(self, expiration: str) -> Optional[ExpirationBundle]:
        """Return the ``ExpirationBundle`` for ``expiration``, or ``None`` if absent."""
        for eb in self.expirations:
            if eb.expiration == expiration:
                return eb
        return None

    def expiration_names(self) -> Tuple[str, ...]:
        """Return the expiration strings in this result, in stored order."""
        return tuple(eb.expiration for eb in self.expirations)
