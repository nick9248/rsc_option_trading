"""
Mutable builder for one on-chain analysis run's typed aggregate result.

refactor_design_spec.md section T6 / D2: ``OnChainAnalysisResult`` is frozen
(core stays immutable), so something has to accumulate its fields while the
service's phase methods run one at a time. ``OnChainAnalysisBuilder`` is that
accumulator — it lives in the service layer (not core), replaces the
analyzer's 14 setters conceptually, and ``.build()`` returns the frozen
aggregate once every phase has reported in.

T6 is a dual-write step: the service continues populating the analyzer (the
live report/synthesis/persistence path, unchanged) *and* this builder (an
additional, currently-unconsumed typed path), so behavior is byte-identical
while the typed aggregate becomes available for T7/T8 to migrate onto.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.results.analysis_result import (
    ExpirationBundle,
    IvPercentileResult,
    MarketMetricsResult,
    OiChangesResult,
    OnChainAnalysisResult,
    TrendSnapshot,
)
from coding.core.analytics.results.dealer_inventory_results import DealerInventoryResult
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.exposure_profile_results import ExposureProfileResult
from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult
from coding.core.analytics.results.flow_results import FlowResult
from coding.core.analytics.results.gex_dex_results import GexDexResult
from coding.core.analytics.results.market_wide_results import MarketWideResult
from coding.core.analytics.results.vol_surface_results import VolSurfaceResult


@dataclass
class _ExpirationAccumulator:
    """Mutable per-expiration scratch space, frozen into an ExpirationBundle at build()."""

    analysis: Optional[ExpirationAnalysisResult] = None
    gex_dex: Optional[GexDexResult] = None
    dealer_inventory: Optional[DealerInventoryResult] = None
    exposure_profile: Optional[ExposureProfileResult] = None
    fixed_strike_vol: Optional[FixedStrikeVolResult] = None
    flow: Optional[FlowResult] = None
    vol_surface: Optional[VolSurfaceResult] = None
    oi_changes: Optional[OiChangesResult] = None
    iv_percentile: Optional[IvPercentileResult] = None
    trend: Optional[TrendSnapshot] = None
    flow_chart_paths: Dict[str, str] = None
    enriched_instruments: Tuple[Dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.flow_chart_paths is None:
            self.flow_chart_paths = {}


class OnChainAnalysisBuilder:
    """
    Mutable accumulator for one analysis run. Replaces OnChainAnalyzer's 14
    setters (refactor_design_spec.md section 2.7) — moved out of core, made
    type-checked, and frozen once at ``.build()``.
    """

    def __init__(
        self,
        currency: str,
        underlying_price: float,
        parsed_instruments: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        self._currency = currency
        self._underlying_price = underlying_price
        self._parsed_instruments: Dict[str, Tuple[Dict[str, Any], ...]] = {
            expiration: tuple(instruments) for expiration, instruments in parsed_instruments.items()
        }
        self._market_metrics: Optional[MarketMetricsResult] = None
        self._market_wide: Optional[MarketWideResult] = None
        self._recent_trades: Tuple[Dict[str, Any], ...] = ()
        self._per_expiration: Dict[str, _ExpirationAccumulator] = {}

    def _slot(self, expiration: str) -> _ExpirationAccumulator:
        if expiration not in self._per_expiration:
            self._per_expiration[expiration] = _ExpirationAccumulator()
        return self._per_expiration[expiration]

    def set_market_metrics(self, metrics: MarketMetricsResult) -> None:
        self._market_metrics = metrics

    def set_expiration_analysis(self, expiration: str, result: ExpirationAnalysisResult) -> None:
        self._slot(expiration).analysis = result

    def set_gex_dex(self, expiration: str, result: GexDexResult) -> None:
        self._slot(expiration).gex_dex = result

    def set_dealer_inventory(self, expiration: str, result: DealerInventoryResult) -> None:
        self._slot(expiration).dealer_inventory = result

    def set_exposure_profile(self, expiration: str, result: ExposureProfileResult) -> None:
        self._slot(expiration).exposure_profile = result

    def set_fixed_strike_vol(self, expiration: str, result: FixedStrikeVolResult) -> None:
        self._slot(expiration).fixed_strike_vol = result

    def set_flow(self, expiration: str, result: FlowResult) -> None:
        self._slot(expiration).flow = result

    def set_flow_charts(self, expiration: str, paths: Dict[str, str]) -> None:
        self._slot(expiration).flow_chart_paths = dict(paths)

    def set_vol_surface(self, expiration: str, result: VolSurfaceResult) -> None:
        self._slot(expiration).vol_surface = result

    def set_oi_changes(self, expiration: str, result: OiChangesResult) -> None:
        self._slot(expiration).oi_changes = result

    def set_iv_percentile(self, expiration: str, result: IvPercentileResult) -> None:
        self._slot(expiration).iv_percentile = result

    def set_trend(self, expiration: str, snapshot: Optional[TrendSnapshot]) -> None:
        self._slot(expiration).trend = snapshot

    def set_enriched_instruments(self, expiration: str, instruments: List[Dict[str, Any]]) -> None:
        self._slot(expiration).enriched_instruments = tuple(instruments)

    def set_recent_trades(self, trades: List[Dict[str, Any]]) -> None:
        self._recent_trades = tuple(trades)

    def set_market_wide(self, result: MarketWideResult) -> None:
        self._market_wide = result

    def build(self) -> OnChainAnalysisResult:
        """
        Assemble the frozen ``OnChainAnalysisResult``.

        Only expirations that received ``set_expiration_analysis`` become an
        ``ExpirationBundle`` — matches the legacy
        ``OnChainAnalyzer.generate_report()``'s ``if not analysis: continue``
        skip for an expiration ``analyze_expiration()`` couldn't produce a
        result for. Expirations are ordered chronologically-by-string
        (``sorted()``), matching ``OnChainAnalyzer.get_expirations()``.

        Missing market-wide/market-metrics data become ``None``/empty
        defaults rather than raising — a partial run (a failed phase) must
        still produce a usable, if incomplete, result.
        """
        bundles = []
        for expiration in sorted(self._per_expiration.keys()):
            acc = self._per_expiration[expiration]
            if acc.analysis is None:
                continue
            bundles.append(
                ExpirationBundle(
                    expiration=expiration,
                    analysis=acc.analysis,
                    gex_dex=acc.gex_dex,
                    dealer_inventory=acc.dealer_inventory,
                    exposure_profile=acc.exposure_profile,
                    fixed_strike_vol=acc.fixed_strike_vol,
                    flow=acc.flow,
                    vol_surface=acc.vol_surface,
                    oi_changes=acc.oi_changes,
                    iv_percentile=acc.iv_percentile,
                    trend=acc.trend,
                    flow_chart_paths=dict(acc.flow_chart_paths or {}),
                    enriched_instruments=acc.enriched_instruments,
                )
            )

        atm_iv_by_expiration = {
            expiration: acc.vol_surface.atm_iv
            for expiration, acc in self._per_expiration.items()
            if acc.vol_surface is not None and acc.vol_surface.atm_iv is not None
        }

        return OnChainAnalysisResult(
            currency=self._currency,
            underlying_price=self._underlying_price,
            # Task G2-C: naive-local datetime.now() -> explicit UTC. This is
            # also the "now" SynthesisMapper.build_expiry_metrics threads
            # through to MarketWideCalculator.calculate_dte (replacing its
            # own now-deleted naive-local DTE duplicate) -- it must be
            # timezone-aware or that call raises.
            generated_at=datetime.now(timezone.utc),
            market_metrics=self._market_metrics or MarketMetricsResult(
                dvol=None, iv_percentile=None, iv_rank=None,
                current_funding=None, funding_8h=None,
            ),
            expirations=tuple(bundles),
            market_wide=self._market_wide or MarketWideResult(
                spot_price=self._underlying_price, currency=self._currency,
                dvol=None, iv_percentile_365d=None, aggregate_gex_dex=None,
                term_structure=None, futures_basis=None, realized_volatility=None,
                variance_risk_premium=None, volatility_cone=None,
                perpetual_funding=None, block_trades=None,
                cross_asset_correlation=None, failed_sections=(),
            ),
            parsed_instruments=self._parsed_instruments,
            atm_iv_by_expiration=atm_iv_by_expiration,
            recent_trades=self._recent_trades,
        )
