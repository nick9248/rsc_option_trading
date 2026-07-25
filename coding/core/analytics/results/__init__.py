"""
Result models for on-chain analysis.

Frozen dataclasses (Decision D1 in refactor_design_spec.md — not Pydantic;
these are internally computed, never deserialized from untrusted input).
Re-exports every model so consumers can do
``from coding.core.analytics.results import ExpirationAnalysisResult``
regardless of which module actually defines it.
"""

from coding.core.analytics.results.analysis_result import (
    ExpirationBundle,
    IvPercentileResult,
    MarketMetricsResult,
    OiChangeRow,
    OiChangesResult,
    OnChainAnalysisResult,
    TrendSnapshot,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    LevelRef,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    StrikeOiRow,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.flow_results import (
    FlowResult,
    FlowTotals,
    StrikeFlowEntry,
    TopStrikeEntry,
)
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
)
from coding.core.analytics.results.market_wide_results import (
    BlockTrade,
    BlockTradesResult,
    CrossAssetCorrelationResult,
    FuturesBasisEntry,
    FuturesBasisResult,
    MarketWideResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    TermStructureEntry,
    TermStructureResult,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
)
from coding.core.analytics.results.vol_surface_results import (
    IvByStrikeRow,
    MoneynessBucket,
    PutCallByMoneyness,
    SecondOrderGreeks,
    SkewResult,
    VolSurfaceResult,
)

__all__ = [
    # expiry_results
    "StrikeOiRow",
    "MaxPainResult",
    "PutCallRatioResult",
    "VolumeStatsResult",
    "MoneynessLeg",
    "MoneynessResult",
    "LevelRef",
    "SupportResistanceResult",
    "ExpirationAnalysisResult",
    # gex_dex_results
    "GexDexStrikeRow",
    "GexDexLevel",
    "GexDexKeyLevels",
    "GexDexResult",
    # flow_results
    "StrikeFlowEntry",
    "FlowTotals",
    "TopStrikeEntry",
    "FlowResult",
    # vol_surface_results
    "IvByStrikeRow",
    "SkewResult",
    "MoneynessBucket",
    "PutCallByMoneyness",
    "SecondOrderGreeks",
    "VolSurfaceResult",
    # market_wide_results
    "TermStructureEntry",
    "TermStructureResult",
    "FuturesBasisEntry",
    "FuturesBasisResult",
    "RealizedVolatilityResult",
    "VarianceRiskPremiumResult",
    "VolatilityConeResult",
    "PerpetualFundingResult",
    "BlockTrade",
    "BlockTradesResult",
    "CrossAssetCorrelationResult",
    "MarketWideResult",
    # analysis_result
    "MarketMetricsResult",
    "TrendSnapshot",
    "OiChangeRow",
    "OiChangesResult",
    "IvPercentileResult",
    "ExpirationBundle",
    "OnChainAnalysisResult",
]
