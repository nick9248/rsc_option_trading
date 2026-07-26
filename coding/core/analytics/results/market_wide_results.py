"""
Result models for market-wide (cross-expiry) metrics.

Frozen dataclasses per refactor_design_spec.md section 2.5. Mirror the dict
shapes historically produced by ``MarketWideCalculator``'s ``calculate_*``
methods and assembled into ``OnChainAnalyzer.market_wide_structured``.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from coding.core.analytics.results.gex_dex_results import GexDexResult


@dataclass(frozen=True)
class TermStructureEntry:
    """One expiration's point on the IV term structure curve."""

    expiration: str
    dte: int
    atm_iv: float


@dataclass(frozen=True)
class TermStructureResult:
    """IV term structure across expirations."""

    entries: Tuple[TermStructureEntry, ...]
    shape: str  # "CONTANGO" | "BACKWARDATION" | "FLAT"
    spread: float  # unsigned pts (scoring)
    spread_signed: float  # signed pts (display)
    iv_by_dte: Dict[int, float]


@dataclass(frozen=True)
class FuturesBasisEntry:
    """Annualized basis for one futures instrument."""

    instrument_name: str
    dte: Optional[int]
    mark_price: float
    index_price: float
    # Optional[float]: None when annualization is suppressed for a
    # sub-daily tenor, or the tenor is already expired (bugfix_spec.md
    # Item 5; Decision D12 — None means weight-zero downstream, never
    # "neutral"). The raw (unannualized) basis is still computable from
    # mark_price/index_price even when this is None.
    annualized_premium_pct: Optional[float]


@dataclass(frozen=True)
class FuturesBasisResult:
    """Futures basis across all future instruments."""

    entries: Tuple[FuturesBasisEntry, ...]
    # {expiry_label: annualized_pct} — synthesis reads this. Values are
    # Optional[float] (Decision D12): None for a suppressed/expired tenor,
    # filtered out (weight-zero) rather than treated as neutral by every
    # consumer (see synthesis.py's basis_values list-comprehension filter).
    futures_basis: Dict[str, Optional[float]]


@dataclass(frozen=True)
class RealizedVolatilityResult:
    """Multi-window realized volatility (10d/20d/30d), as decimals."""

    rv_by_window: Dict[int, float]  # {10: .., 20: .., 30: ..}, decimals (0.585 = 58.5%)

    @property
    def rv_10d(self) -> float:
        return self.rv_by_window.get(10, 0.0)

    @property
    def rv_20d(self) -> float:
        return self.rv_by_window.get(20, 0.0)

    @property
    def rv_30d(self) -> float:
        return self.rv_by_window.get(30, 0.0)


@dataclass(frozen=True)
class VarianceRiskPremiumResult:
    """Variance risk premium (DVOL vs realized volatility)."""

    vrp: float  # points
    signal: str  # "VERY_EXPENSIVE" | "EXPENSIVE" | "FAIR" | "CHEAP" | "VERY_CHEAP"
    dvol: Optional[float]
    rv_30d: float


@dataclass(frozen=True)
class VolatilityConeResult:
    """Percentile of current realized volatility vs its historical range, per window."""

    percentile_by_window: Dict[int, float]

    @property
    def cone_10d_pctile(self) -> float:
        return self.percentile_by_window.get(10, 0.0)

    @property
    def cone_20d_pctile(self) -> float:
        return self.percentile_by_window.get(20, 0.0)

    @property
    def cone_30d_pctile(self) -> float:
        return self.percentile_by_window.get(30, 0.0)


@dataclass(frozen=True)
class PerpetualFundingResult:
    """Perpetual funding rate and open interest, with trend."""

    perp_open_interest: float
    funding_rate: Optional[float]  # current funding, decimal
    funding_8h: Optional[float]  # decimal
    funding_trend: str  # "Rising" | "Falling" | "Stable"
    history_points: int


@dataclass(frozen=True)
class BlockTrade:
    """A single detected block (large-notional) trade."""

    timestamp: Optional[int]
    instrument_name: str
    amount: float
    direction: str
    notional: float
    implied_volatility: Optional[float]


@dataclass(frozen=True)
class BlockTradesResult:
    """Detected block trades (top 10 by notional)."""

    trades: Tuple[BlockTrade, ...]  # top 10
    notional_threshold: float
    total_detected: int


@dataclass(frozen=True)
class CrossAssetCorrelationResult:
    """Price/DVOL correlation between this currency and another."""

    other_currency: str
    price_correlation: Optional[float]
    dvol_correlation: Optional[float]
    sample_size: int


@dataclass(frozen=True)
class MarketWideResult:
    """Aggregate of every market-wide (cross-expiry) metric for one currency."""

    spot_price: float
    currency: str
    dvol: Optional[float]
    iv_percentile_365d: Optional[float]
    aggregate_gex_dex: Optional[GexDexResult]
    term_structure: Optional[TermStructureResult]
    futures_basis: Optional[FuturesBasisResult]
    realized_volatility: Optional[RealizedVolatilityResult]
    variance_risk_premium: Optional[VarianceRiskPremiumResult]
    volatility_cone: Optional[VolatilityConeResult]
    perpetual_funding: Optional[PerpetualFundingResult]
    block_trades: Optional[BlockTradesResult]
    cross_asset_correlation: Optional[CrossAssetCorrelationResult]
    failed_sections: Tuple[str, ...]  # M5: names of sections whose calculation raised

    def to_flat_dict(self) -> Dict[str, Any]:
        """
        Reproduce the flattened dict ``SynthesisMapper.build_market_wide``
        reads today (``analyzer.market_wide_structured``). Keys for a
        sub-result that is ``None`` are omitted — downstream readers already
        use ``mw.get(key) or default`` (see ``synthesis.py``), so omission is
        equivalent to an absent/never-set key in the legacy dict.
        """
        flat: Dict[str, Any] = {
            "spot_price": self.spot_price,
            "dvol": self.dvol,
            "iv_percentile_365d": self.iv_percentile_365d,
        }

        ts = self.term_structure
        if ts is not None:
            flat["shape"] = ts.shape
            flat["spread"] = ts.spread
            flat["spread_signed"] = ts.spread_signed
            flat["iv_by_dte"] = dict(ts.iv_by_dte)

        fb = self.futures_basis
        if fb is not None:
            flat["futures_basis"] = dict(fb.futures_basis)

        rv = self.realized_volatility
        if rv is not None:
            flat["rv_10d"] = rv.rv_10d
            flat["rv_20d"] = rv.rv_20d
            flat["rv_30d"] = rv.rv_30d

        vrp = self.variance_risk_premium
        if vrp is not None:
            flat["vrp"] = vrp.vrp
            flat["signal"] = vrp.signal

        cone = self.volatility_cone
        if cone is not None:
            flat["cone_10d_pctile"] = cone.cone_10d_pctile
            flat["cone_20d_pctile"] = cone.cone_20d_pctile
            flat["cone_30d_pctile"] = cone.cone_30d_pctile

        pf = self.perpetual_funding
        if pf is not None:
            flat["perp_oi"] = pf.perp_open_interest
            flat["perp_funding_trend"] = pf.funding_trend
            if pf.funding_rate is not None:
                flat["funding_rate"] = pf.funding_rate
            if pf.funding_8h is not None:
                flat["funding_8h"] = pf.funding_8h

        bt = self.block_trades
        if bt is not None:
            flat["block_trades"] = [
                {
                    "timestamp": t.timestamp,
                    "instrument": t.instrument_name,
                    "size": t.amount,
                    "amount": t.amount,
                    "direction": t.direction,
                    "notional": t.notional,
                    "iv": t.implied_volatility,
                }
                for t in bt.trades
            ]

        corr = self.cross_asset_correlation
        if corr is not None:
            if corr.price_correlation is not None:
                flat["btc_eth_price_corr"] = corr.price_correlation
            if corr.dvol_correlation is not None:
                flat["btc_eth_dvol_corr"] = corr.dvol_correlation

        return flat
