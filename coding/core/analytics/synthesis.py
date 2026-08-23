"""
ALGORITHMIC SYNTHESIS ENGINE
=============================
Converts quantitative on-chain metrics into an institutional-grade
executive summary using a rule-based scoring and narrative generation system.

Architecture:
    Raw Metrics → Scoring Engine → Regime Classification → Narrative Templates → Executive Summary

Author: Nick (Wuppertal University / Institutional Options Desk)
Version: 2.0
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum, IntEnum
import logging
from datetime import datetime, timedelta, timezone

from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.results.analysis_result import OnChainAnalysisResult
from coding.core.analytics.results.market_wide_results import GammaRolloffResult
from coding.core.analytics.thresholds import (
    GEX_NORMALIZED_REGIME_THRESHOLD, RISK_REVERSAL_MILD_POINTS, RISK_REVERSAL_STRONG_POINTS,
)

logger = logging.getLogger(__name__)


# =============================================================================
# SECTION 1: DATA STRUCTURES
# =============================================================================

class Signal(IntEnum):
    """Directional signal strength. IntEnum for arithmetic in TRANSITION logic."""
    STRONG_BEARISH = -2
    BEARISH = -1
    NEUTRAL = 0
    BULLISH = 1
    STRONG_BULLISH = 2


class VolRegime(Enum):
    """Volatility regime classification"""
    SUPPRESSED = "suppressed"  # Positive GEX, low IV, negative VRP
    NORMAL = "normal"  # Mixed signals
    ELEVATED = "elevated"  # High IV, positive VRP
    EXPLOSIVE = "explosive"  # Negative GEX, high IV, extreme skew


class MarketRegime(Enum):
    """Overall market regime"""
    RANGE_BOUND_NEUTRAL = "range_bound_neutral"  # Neutral + suppressed/normal vol
    RANGE_BOUND_BULLISH = "range_bound_bullish"  # Bullish + suppressed vol
    RANGE_BOUND_BEARISH = "range_bound_bearish"  # Bearish + suppressed vol
    RANGE_BOUND_ELEVATED = "range_bound_elevated"  # Neutral + elevated vol
    TRENDING_UP = "trending_up"  # Bullish + normal vol
    TRENDING_DOWN = "trending_down"  # Bearish + normal vol
    VOLATILE_BULLISH = "vol_bullish"  # Bullish + elevated/explosive vol
    VOLATILE_BEARISH = "vol_bearish"  # Bearish + elevated/explosive vol
    TRANSITION = "transition"  # Conflicting signals across timeframes


@dataclass
class ExpiryMetrics:
    """
    Parsed metrics for a single expiry.

    Task G2-G (Wave G fresh audit, follow-on to G2-B's MarketWideMetrics
    fix): every field below that can genuinely be absent upstream for THIS
    expiry (the vol surface never computed, second-order Greeks never
    computed, no identified GEX level in this expiry's strike range) is
    ``Optional[float]`` with no fabricated ``0.0`` default. Before this
    fix, ``SynthesisMapper.build_expiry_metrics`` collapsed a real
    ``None`` (e.g. ``VolSurfaceResult.atm_iv is None`` because no ATM
    instruments were found, or ``SkewResult.risk_reversal_25d is None``)
    into a fabricated ``0.0`` via patterns like
    ``vol.atm_iv if vol is not None and vol.atm_iv is not None else 0.0``.
    That fabricated zero then flowed into scorers with no ``None`` branch
    (e.g. ``score_skew``) and was scored as the SAME thing as a
    genuinely-measured 0 -- for ``risk_reversal_25d`` this meant "no
    skew data" silently became "RR25 +0.0%: Normal", a confident,
    specific reading for a metric that was never measured. Every scorer
    and narrative formatter consuming these fields now has an explicit
    ``None`` branch that returns a neutral score at (near-)zero weight or
    an "insufficient data"/"N/A" disclosure string, instead of silently
    treating absence as a measured value.

    Wave-H-A (Task 5, correcting this docstring's own prior claim):
    ``pc_atm``/``pc_near_otm``/``pc_far_otm`` are ``Optional[float]``.
    They used to be documented here as "DELIBERATELY left as plain float,
    NOT touched" because ``MoneynessBucket.ratio`` was believed to be an
    intentional "H2 fix" convention ("ALWAYS present; 0.0 when
    undefined"). That belief was wrong: the 0.0-for-undefined convention
    was itself the bug (an empty moneyness bucket -- zero instruments --
    was indistinguishable from a genuinely-measured ratio of 0.0, and fed
    a "Strong Bullish" bias label off the fabricated zero). ``
    MoneynessBucket.ratio`` is now ``Optional[float]`` (``None`` for a
    truly empty bucket), and these three fields propagate that ``None``
    the same way every other genuinely-absent field on this dataclass
    does. ``pc_ratio``'s ``inf`` -> ``99.0`` cap is a separate, still
    deliberately-left-alone convention, unaffected by this fix.

    ``max_pain``'s fallback to spot price when ``calculate_max_pain``
    returns ``None`` was PREVIOUSLY judged (incorrectly, by this
    campaign's own earlier pass) to belong in that same "deliberate,
    documented, harmless" category. It does not: unlike ``pc_ratio``, the
    fallback value is fed straight into ``score_max_pain_gravity`` at
    three call sites in ``SynthesisEngine.run``, which has no way to tell
    "measured max pain that happens to equal spot" from "no max pain was
    ever computed" -- it produces a real, non-zero-weight score and a
    confident-sounding message (e.g. "Max pain $64,371 is near spot
    (+0.0%)") from a computation that never ran. Task Wave-H-B Fix 4: the
    display fallback stays (a report line has to show something), but
    ``max_pain_sufficient_data`` (mirroring ``flow_sufficient_data``'s
    value-plus-sufficiency-flag pattern below) travels alongside it so
    the scorer can tell the difference and take its own explicit
    insufficient-data branch instead of scoring the fabricated value as a
    real measurement.
    """
    expiry: str
    dte: int
    total_oi: int
    notional: float
    max_pain: float
    pc_ratio: float
    # CARRIED FINDING (B2 review, task C1): this expiry's OWN forward/
    # underlying price (bundle.analysis.underlying_price -- the same
    # source bugfix_spec.md Item 7's report_formatter fix uses), not the
    # single global market.spot_price. _generate_timeframe_section's
    # max-pain-distance line and all three score_max_pain_gravity call
    # sites anchor against this field now instead of one shared spot
    # value across every expiry -- the same defect class Item 7 already
    # fixed at the report/formatter layer.
    underlying_price: float

    # GEX/DEX. call_resistance_strike/call_resistance_gex/
    # put_support_strike/put_support_gex/hvl_strike: None exactly when
    # GexDexKeyLevels.call_resistance/put_support/hvl is None -- no
    # identified level in this expiry's strike range -- NOT a measured
    # strike/GEX of 0.0.
    total_gex: float
    total_dex: float
    gex_environment: str  # "Positive" or "Negative"
    call_resistance_strike: Optional[float]
    call_resistance_gex: Optional[float]
    put_support_strike: Optional[float]
    put_support_gex: Optional[float]
    hvl_strike: Optional[float]
    """GexDexKeyLevels.hvl -- the STRIKE where cumulative net GEX changes
    sign (a strike-axis OI-distribution artifact), NOT the re-priced Zero
    Gamma Level (GexDexKeyLevels.zero_gamma_level, a different quantity
    from GammaProfileCalculator -- not carried by this dataclass). Wave-H-A
    Task 6: this field was previously commented "# Zero gamma level",
    which is the wrong label for what it actually holds -- see
    GexDexKeyLevels's own docstring for the full distinction."""

    # Volatility surface. atm_iv: None exactly when
    # VolatilitySurfaceCalculator._calculate_atm_iv found no ATM
    # instruments -- NOT a measured 0% IV.
    atm_iv: Optional[float]
    # bugfix_spec.md Item 9: renamed from skew_25d (put IV - call IV, a
    # non-standard sign) to risk_reversal_25d (call IV - put IV -- the
    # market "25-delta risk reversal" convention). Positive = calls richer
    # (bullish/upside speculation); negative = puts richer (bearish/
    # downside hedging demand) -- the opposite sign from the old skew_25d.
    # None exactly when the vol surface (or its skew_25d sub-result)
    # never computed for this expiry -- NOT a measured 0.0% (Normal).
    risk_reversal_25d: Optional[float]
    put_25d_iv: Optional[float]
    call_25d_iv: Optional[float]

    # Moneyness P/C. None exactly when MoneynessBucket.ratio is None (zero
    # instruments in this bucket) -- NOT a measured 0.0 ratio (Wave-H-A
    # Task 5; see this dataclass's own docstring above).
    pc_atm: Optional[float]
    pc_near_otm: Optional[float]
    pc_far_otm: Optional[float]

    # Second-order Greeks. bugfix_spec.md Item 8 fix-review (Important #3):
    # these are the ASSUMED-DEALER exposures (SecondOrderGreeks.
    # dealer_vanna_exposure/dealer_charm_exposure), not the holder-side raw
    # sum -- F8.4 requires score_* functions to consume the dealer fields,
    # matching the report text's own dealer-derived vanna_signal/
    # charm_signal. None exactly when the vol surface never computed for
    # this expiry (``vol is None``), OR (Wave-H-A Task 4) when the vol
    # surface DID compute but nothing could be measured for vanna/charm
    # specifically (every instrument's greeks were unavailable --
    # SecondOrderGreeks.dealer_vanna_exposure/dealer_charm_exposure are
    # themselves Optional now, for exactly this reason) -- NOT a measured
    # net_vanna/net_charm of 0.0, which score_vanna_charm treats as its
    # own distinct "zero" signal.
    net_vanna: Optional[float]
    net_charm: Optional[float]

    # Flow
    flow_bias: str  # "Heavy Buying", "Moderate Selling", etc.
    flow_trend: str

    # Fields with defaults
    total_volume: int = 0
    top_buy_strikes: List[dict] = field(default_factory=list)
    top_sell_strikes: List[dict] = field(default_factory=list)
    # bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review): True unless the
    # flow analyzer's data-sufficiency gate tripped for this expiry. When
    # False, the scoring engine must contribute the flow score at weight 0,
    # not a neutral score at full weight — a neutral score is itself a claim.
    flow_sufficient_data: bool = True
    # Task Wave-H-B Fix 4: True unless calculate_max_pain returned None
    # (nothing to compute from) and ``max_pain`` above is therefore the
    # spot-price display fallback, not a real measurement. When False,
    # score_max_pain_gravity must take its explicit insufficient-data
    # branch (weight zero) instead of scoring the fallback as if it were
    # a genuine max-pain reading -- same value-plus-sufficiency-flag
    # pattern as ``flow_sufficient_data`` above.
    max_pain_sufficient_data: bool = True


@dataclass
class MarketWideMetrics:
    """
    Parsed market-wide metrics.

    Task G2-B (Wave G fresh audit, BLOCKER): every field below that can
    genuinely be absent upstream (the source calculation never ran, or
    raised) is ``Optional[float]`` with a ``None`` default — NOT ``0.0``.
    Before this fix, ``SynthesisMapper.build_market_wide`` collapsed a
    real ``None`` (e.g. ``MarketWideResult.dvol is None`` because DVOL
    was unavailable) into a fabricated ``0.0`` via ``mw.dvol or 0.0``.
    That fabricated zero then flowed into scorers with no ``None``
    branch (e.g. ``score_iv_percentile``) and was scored as the SAME
    thing as a genuinely-measured 0 — for ``iv_percentile_365d`` this
    meant "no data" silently became "IV 0th pctile: Extremely cheap —
    strong buy-vol", the single strongest, highest-confidence signal the
    scorer can emit, for a metric that was never measured. Every scorer
    consuming these fields now has an explicit ``None`` branch that
    returns a neutral score at (near-)zero weight/confidence and an
    "insufficient data" message, instead of silently treating absence as
    a measured extreme.
    """
    spot_price: float
    dvol: Optional[float]
    iv_percentile_365d: Optional[float]
    funding_rate: Optional[float]
    funding_8h: Optional[float]

    # Term structure. Task Wave-H-B Fix 2: these three are genuinely
    # Optional -- None exactly when MarketWideResult.term_structure is
    # None (fewer than 2 usable per-expiry ATM IVs, the whole term-
    # structure computation never ran) -- NOT a fabricated "CONTANGO"
    # reading. "FLAT" is also a real, computed, non-CONTANGO/
    # BACKWARDATION shape (calculator emits it when |back - front| <=
    # 2pts) and must pass through as "FLAT", not get relabelled
    # "CONTANGO" -- the old normalize-to-CONTANGO logic in
    # SynthesisMapper.build_market_wide collapsed all three of (a) ts is
    # None, (b) shape == "FLAT", and (c) a genuine backwardated tilt too
    # small to cross the calculator's +/-2pt threshold into the SAME
    # fabricated "CONTANGO" reading -- and for case (c) specifically,
    # term_structure_spread_signed still carried the real (negative)
    # sign, producing self-contradictory output like "CONTANGO (-1.8pts)"
    # in the report header.
    term_structure_shape: Optional[str]  # "CONTANGO", "BACKWARDATION", "FLAT", or None
    term_structure_spread: Optional[float]  # abs pts — used for scoring
    term_structure_spread_signed: Optional[float] = None  # signed pts (back - front) — used for display
    iv_by_dte: Dict[int, float] = field(default_factory=dict)

    # Realized vol. None exactly when MarketWideResult.realized_volatility
    # is None (the whole RV calculation never ran) -- NOT a measured 0.0.
    rv_10d: Optional[float] = None
    rv_20d: Optional[float] = None
    rv_30d: Optional[float] = None

    # VRP. None exactly when MarketWideResult.variance_risk_premium is
    # None (DVOL or realized vol unavailable) -- NOT a measured 0.0.
    vrp: Optional[float] = None

    # Vol cone. None exactly when MarketWideResult.volatility_cone is
    # None, OR that specific window was never computed -- NOT a measured
    # 0th percentile.
    cone_10d_pctile: Optional[float] = None
    cone_20d_pctile: Optional[float] = None
    cone_30d_pctile: Optional[float] = None

    # Futures basis. Optional[float] values (Decision D12, bugfix_spec.md
    # Item 5): None for a suppressed sub-daily/expired tenor. See the
    # basis_values filter at score_futures_basis's call site below, which
    # already relies on this being possible at runtime.
    futures_basis: Dict[str, Optional[float]] = field(default_factory=dict)

    # Perp. None exactly when MarketWideResult.perpetual_funding is None
    # (the whole funding calculation never ran) -- NOT a measured 0 OI.
    perp_oi: Optional[float] = None
    perp_funding_trend: str = "Stable"

    # Cross-asset. None exactly when MarketWideResult.cross_asset_correlation
    # is None, or the underlying correlation itself is None (insufficient
    # sample) -- NOT a measured zero correlation.
    btc_eth_price_corr: Optional[float] = None
    btc_eth_dvol_corr: Optional[float] = None

    # Block trades (institutional_metrics_spec.md section 9 / Migration M2,
    # Task D1 review round 2, Important #3): `blocks` is grouped by
    # block_trade_id (real blocks, one row per block); `large_prints` is
    # the pre-existing notional-filter list, relabelled here for the same
    # reason the report was -- a large single-leg screen print is not a
    # block, and narrating it as one conflates exactly what section 9 was
    # written to separate.
    blocks: List[dict] = field(default_factory=list)
    large_prints: List[dict] = field(default_factory=list)

    # Aggregate GEX/DEX across all expirations
    aggregate_total_gex: float = 0.0
    aggregate_total_dex: float = 0.0
    aggregate_call_resistance: Optional[Dict] = None
    aggregate_put_support: Optional[Dict] = None
    aggregate_hvl: Optional[float] = None
    """Cross-expiration GexDexKeyLevels.hvl -- see ExpiryMetrics.hvl_strike's
    docstring for the strike-axis-artifact-vs-Zero-Gamma-Level distinction
    this field is also subject to (Wave-H-A Task 6)."""

    # Task Wave-I-A Fix 1: threaded through unchanged from
    # MarketWideResult.gamma_rolloff so generate_risk_factors can reuse
    # GexDexCalculator.calculate_rolloff_profile's own "GAMMA CLIFF"
    # near-term concentration flag (>30% of gamma mass within 7 DTE)
    # instead of the largest-OI expiry's DTE, which for BTC is almost
    # always a far-dated quarterly and can never trip a near-term pin-risk
    # check. None exactly when the whole roll-off computation never ran
    # (e.g. no per-expiry GEX at all) -- NOT "no gamma cliff".
    gamma_rolloff: Optional[GammaRolloffResult] = None

    # Task G2-B Finding 3: names of MarketWideResult sections whose
    # calculation raised (threaded through from
    # MarketWideOrchestrator.run(), which used to hardcode this at `()`
    # unconditionally -- see market_wide_orchestrator.py). Read by
    # SynthesisEngine.run() to disclose which inputs are missing because
    # of an actual error, distinct from inputs that are simply not
    # computed yet (those already disclose via the Optional fields above
    # being None).
    failed_sections: Tuple[str, ...] = ()


# =============================================================================
# SECTION 2: SCORING ENGINE
# =============================================================================

class ScoringEngine:
    """
    Converts raw metrics into directional and volatility scores.

    Each scorer returns a tuple: (score: float, weight: float, reasoning: str)
    Scores range from -2 (strong bearish) to +2 (strong bullish)
    Weights range from 0 to 1 (importance multiplier)
    """

    # -------------------------------------------------------------------------
    # DIRECTIONAL SCORES
    # -------------------------------------------------------------------------

    @staticmethod
    def score_pc_ratio(pc_ratio: float, dte: Optional[int] = None) -> Tuple[float, float, str]:
        """
        Put/Call ratio interpretation with contrarian dampening at extremes
        and DTE score clamping.

        Extremes (<0.40 or >2.00) are contrarian-dampened: reduced score
        magnitude and lower weight, similar to how score_funding treats
        extreme positioning.

        DTE <= 2: scores clamped to ±1.0 (settlement-day noise).
        """
        if pc_ratio < 0.40:
            score, weight = 1.0, 0.5
            reason = f"P/C {pc_ratio:.2f}: Extreme call dominance — contrarian caution"
        elif pc_ratio < 0.60:
            score, weight = 2.0, 0.7
            reason = f"P/C {pc_ratio:.2f}: Strong call dominance"
        elif pc_ratio < 0.80:
            score, weight = 1.0, 0.7
            reason = f"P/C {pc_ratio:.2f}: Bullish call lean"
        elif pc_ratio < 1.00:
            score, weight = 0.0, 0.5
            reason = f"P/C {pc_ratio:.2f}: Balanced"
        elif pc_ratio < 1.30:
            score, weight = -1.0, 0.7
            reason = f"P/C {pc_ratio:.2f}: Moderate put lean"
        elif pc_ratio <= 2.00:
            score, weight = -2.0, 0.7
            reason = f"P/C {pc_ratio:.2f}: Extreme hedging/fear"
        else:
            score, weight = -1.0, 0.5
            reason = f"P/C {pc_ratio:.2f}: Extreme put dominance — contrarian caution"

        # DTE score clamping
        if dte is not None and dte <= 2:
            score = max(-1.0, min(1.0, score))
            reason += " [DTE≤2 clamped]"

        return (score, weight, reason)

    @staticmethod
    def score_dex(total_dex: float, spot: float = 100000.0,
                  dte: Optional[int] = None) -> Tuple[float, float, str]:
        """
        Delta Exposure interpretation, normalized by spot price.

        Positive DEX → market net long delta → dealers short delta →
        dealers buy underlying to hedge → bullish.

        Thresholds normalized: dex/spot.
        At BTC $100K: ±0.005 = ±500, ±0.001 = ±100.
        """
        if spot <= 0:
            spot = 100000.0
        dex_norm = total_dex / spot

        if dex_norm > 0.005:
            score, weight = 2.0, 0.8
            reason = f"DEX/spot {dex_norm:+.4f}: Strong bullish dealer pressure"
        elif dex_norm > 0.001:
            score, weight = 1.0, 0.8
            reason = f"DEX/spot {dex_norm:+.4f}: Moderate bullish pressure"
        elif dex_norm > -0.001:
            score, weight = 0.0, 0.5
            reason = f"DEX/spot {dex_norm:+.4f}: Neutral dealer delta"
        elif dex_norm > -0.005:
            score, weight = -1.0, 0.8
            reason = f"DEX/spot {dex_norm:+.4f}: Moderate bearish pressure"
        else:
            score, weight = -2.0, 0.8
            reason = f"DEX/spot {dex_norm:+.4f}: Strong bearish dealer pressure"

        # DTE score clamping
        if dte is not None and dte <= 2:
            score = max(-1.0, min(1.0, score))
            reason += " [DTE≤2 clamped]"

        return (score, weight, reason)

    @staticmethod
    def score_max_pain_gravity(max_pain: float, spot: float,
                               dte: Optional[int] = None,
                               sufficient_data: bool = True) -> Tuple[float, float, str]:
        """
        Max pain pull interpretation with DTE-scaled weight.

        Gravity effect is strongest near expiration and weakens with time.
        DTE-based weight: 0-7→0.5, 8-14→0.4, 15-30→0.3, >30→0.15.
        Neutral always uses weight 0.2 regardless of DTE.

        Task Wave-H-B Fix 4: ``max_pain`` is deliberately still typed
        plain ``float`` here (not ``Optional``) because ExpiryMetrics.
        max_pain always carries a display value (the real max-pain strike,
        or the spot-price fallback when calculate_max_pain returned
        None) -- ``sufficient_data`` is the separate signal for whether
        that value is a genuine measurement, mirroring
        ``score_flow_gated``'s ``sufficient_data`` gate for
        ``flow_sufficient_data``. When False, the caller is passing the
        spot-price fallback (or otherwise knows this reading is
        fabricated) -- score it as insufficient data (weight zero)
        instead of a real "near spot" reading, which is exactly what the
        fallback would otherwise produce (distance_pct == 0.0, a
        confident-sounding "Max pain $X is near spot (+0.0%)" for a
        computation that never ran).
        """
        if not sufficient_data:
            return (0.0, 0.0,
                    "Max pain: insufficient data (calculation did not resolve a strike) — weight zero, no signal")

        distance_pct = (max_pain - spot) / spot * 100

        # DTE-scaled weight for non-neutral scores
        if dte is not None:
            if dte <= 7:
                dte_weight = 0.5
            elif dte <= 14:
                dte_weight = 0.4
            elif dte <= 30:
                dte_weight = 0.3
            else:
                dte_weight = 0.15
        else:
            dte_weight = 0.4  # default fallback

        if distance_pct > 10:
            return (2.0, dte_weight, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% above spot — strong upward pull")
        elif distance_pct > 5:
            return (1.0, dte_weight, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% above — moderate pull up")
        elif distance_pct > -5:
            return (0.0, 0.2, f"Max pain ${max_pain:,.0f} is near spot ({distance_pct:+.1f}%)")
        elif distance_pct > -10:
            return (-1.0, dte_weight, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% below — pull down")
        else:
            return (-2.0, dte_weight, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% below — strong downward pull")

    @staticmethod
    def score_funding(funding_8h: Optional[float]) -> Tuple[float, float, str]:
        """
        Funding rate interpretation. Uses funding_8h only.

        Annualized rate = funding_8h × 3 × 365.
        Positive funding → crowded long → contrarian bearish.
        Negative funding → crowded short → contrarian bullish.

        Task G2-B: `funding_8h is None` means the perpetual-funding
        section never ran/no reading was available -- weight-zero the
        same way ``score_flow_gated`` already does for insufficient flow
        data, rather than defaulting to 0.0 (which would silently score
        as "Neutral leverage", a real claim about a real measurement).
        """
        if funding_8h is None:
            return (0.0, 0.0, "Funding: insufficient data — weight zero")

        ann_rate = funding_8h * 3 * 365

        if abs(ann_rate) < 5:
            return (0.0, 0.3, f"Funding {ann_rate:.1f}% ann: Neutral leverage")
        elif ann_rate > 20:
            return (-2.0, 0.6, f"Funding {ann_rate:.1f}% ann: Extremely crowded long")
        elif ann_rate > 10:
            return (-1.0, 0.5, f"Funding {ann_rate:.1f}% ann: Crowded long")
        elif ann_rate < -20:
            return (2.0, 0.6, f"Funding {ann_rate:.1f}% ann: Extremely crowded short")
        elif ann_rate < -10:
            return (1.0, 0.5, f"Funding {ann_rate:.1f}% ann: Crowded short")
        else:
            return (0.0, 0.3, f"Funding {ann_rate:.1f}% ann: Mild positioning")

    @staticmethod
    def score_flow(flow_bias: str, flow_trend: str) -> Tuple[float, float, str]:
        """
        Flow analysis interpretation.

        Combines bias (current direction) with trend (acceleration/deceleration).

        Bias mapping:
            "Heavy Buying"    → +2
            "Moderate Buying"  → +1
            "Mixed/Neutral"    → 0
            "Moderate Selling" → -1
            "Heavy Selling"    → -2

        Trend adjustment:
            "Accelerating Buy"      → +0.5
            "Steady Buy"            → +0.25
            "Reversing to Sell"     → -0.5 (weakens buy signal)
            "Decelerating Buy"      → -0.25
            "Accelerating Sell"     → -0.5
            "Steady Sell"           → -0.25
            "Reversing to Buy"      → +0.5
            "Decelerating Sell"     → +0.25
        """
        bias_map = {
            "Heavy Buying": 2.0,
            "Moderate Buying": 1.0,
            "Mixed/Neutral": 0.0,
            "Balanced": 0.0,
            "No Data": 0.0,
            "Moderate Selling": -1.0,
            "Heavy Selling": -2.0,
        }

        trend_map = {
            "Accelerating Buy Pressure": 0.5,
            "Steady Buy Pressure": 0.25,
            "Decelerating Buy Pressure": -0.25,
            "Reversing to Sell Pressure": -0.5,
            "Accelerating Sell Pressure": -0.5,
            "Steady Sell Pressure": -0.25,
            "Decelerating Sell Pressure": 0.25,
            "Reversing to Buy Pressure": 0.5,
            "Mixed/Neutral Flow": 0.0,
        }

        base = bias_map.get(flow_bias, None)
        if base is None:
            logger.warning(f"Unrecognized flow_bias: '{flow_bias}' — defaulting to 0.0")
            base = 0.0

        adjustment = trend_map.get(flow_trend, None)
        if adjustment is None:
            logger.warning(f"Unrecognized flow_trend: '{flow_trend}' — defaulting to 0.0")
            adjustment = 0.0

        score = max(-2.0, min(2.0, base + adjustment))

        return (score, 0.6, f"Flow: {flow_bias} + {flow_trend} → net {score:+.1f}")

    @classmethod
    def score_flow_gated(
        cls, flow_bias: str, flow_trend: str, sufficient_data: bool
    ) -> Tuple[float, float, str]:
        """
        score_flow, with the weight forced to 0 when the flow analyzer's
        data-sufficiency gate tripped (bugfix_spec.md Item 6 / F6.3.4,
        carried from A4 review).

        An insufficient-data expiry's ``flow_bias``/``flow_trend`` are the
        literal ``"Insufficient flow data"`` sentinel strings, which
        ``score_flow`` doesn't recognize and scores as 0.0 (logging a
        warning) — but at the *legacy* weight of 0.6 that "neutral" score
        still dilutes the weighted average (denominator inflation) as if it
        were a real, if uncertain, signal. Weight 0 removes it entirely —
        a neutral score is itself a claim.
        """
        score, weight, description = cls.score_flow(flow_bias, flow_trend)
        if not sufficient_data:
            return (score, 0.0, f"{description} [insufficient data - weight zero]")
        return (score, weight, description)

    @staticmethod
    def score_vanna_charm(net_vanna: Optional[float], net_charm: Optional[float],
                          iv_pctile: Optional[float] = 50.0, gex_total: float = 0.0,
                          spot: float = 100000.0) -> Tuple[float, float, str]:
        """
        Second-order Greeks interpretation.

        Vanna is IV-regime-conditional:
        - iv_pctile > 60: IV likely to drop → positive vanna = bullish
        - iv_pctile < 40: IV likely to rise → positive vanna = BEARISH (reversed)
        - 40-60: no clear IV direction → vanna signal = 0

        Zero inputs produce 0 signal (no phantom signals).

        Gamma-adjusted weight: negative GEX amplifies, positive GEX dampens.

        Task G2-B: ``iv_pctile=None`` (IV percentile unavailable) is
        treated the same as the mid-range 40-60 band -- "no clear IV
        direction" is already the correct, honest description of "we
        don't know," and that band already produces vanna_signal=0.0
        with no fabricated direction, so no separate branch is needed.

        Task G2-G: ``net_vanna``/``net_charm`` are ``None`` exactly when
        the vol surface never computed for this expiry -- genuinely
        missing, NOT a measured 0.0. This is a DIFFERENT case from
        ``net_vanna == 0``/``net_charm == 0`` below (a real reading that
        happens to net to zero, e.g. a balanced call/put book), which
        must keep producing its own "zero" signal/direction text -- a
        real measurement of "no structural drift" is not the same claim
        as "we never measured it". None short-circuits to a neutral
        score at zero weight with an explicit insufficient-data message,
        before either the vanna or charm band logic runs.
        """
        if net_vanna is None or net_charm is None:
            return (0.0, 0.0, "Vanna/Charm: insufficient data (vol surface unavailable) — weight zero")

        if iv_pctile is None:
            iv_pctile = 50.0

        # Vanna signal (IV-regime-conditional)
        if net_vanna == 0:
            vanna_signal = 0.0
            vanna_dir = "zero"
        elif iv_pctile > 60:
            vanna_signal = 1.0 if net_vanna > 0 else -1.0
            vanna_dir = "bullish" if net_vanna > 0 else "bearish"
        elif iv_pctile < 40:
            # Reversed: IV rising, not falling
            vanna_signal = -1.0 if net_vanna > 0 else 1.0
            vanna_dir = "bearish(IV rising)" if net_vanna > 0 else "bullish(IV rising)"
        else:
            vanna_signal = 0.0
            vanna_dir = "neutral(IV mid-range)"

        # Charm signal
        if net_charm == 0:
            charm_signal = 0.0
            charm_dir = "zero"
        else:
            charm_signal = 1.0 if net_charm > 0 else -1.0
            charm_dir = "bullish" if net_charm > 0 else "bearish"

        combined = (vanna_signal + charm_signal) / 2

        # Gamma-adjusted weight
        if spot <= 0:
            spot = 100000.0
        gex_normalized = gex_total / spot
        if gex_normalized < -50:
            weight = 0.4
        elif gex_normalized > 50:
            weight = 0.15
        else:
            weight = 0.3

        return (combined, weight, f"Vanna {vanna_dir} + Charm {charm_dir} → structural drift {combined:+.1f}")

    @staticmethod
    def score_futures_basis(basis_front: float) -> Tuple[float, float, str]:
        """
        Futures basis interpretation (front contract only).

        Contango (positive basis) → bullish structural demand.
        Backwardation (negative basis) → stress.
        """
        if basis_front > 10:
            return (2.0, 0.5, f"Basis {basis_front:.1f}% front: Strong contango — bullish demand")
        elif basis_front > 5:
            return (1.0, 0.5, f"Basis {basis_front:.1f}% front: Moderate contango")
        elif basis_front > -2:
            return (0.0, 0.3, f"Basis {basis_front:.1f}% front: Flat")
        elif basis_front > -5:
            return (-1.0, 0.5, f"Basis {basis_front:.1f}% front: Mild backwardation — stress signal")
        else:
            return (-2.0, 0.6, f"Basis {basis_front:.1f}% front: Strong backwardation — severe stress")

    # -------------------------------------------------------------------------
    # VOLATILITY SCORES
    # -------------------------------------------------------------------------

    @staticmethod
    def score_iv_percentile(iv_pctile: Optional[float]) -> Tuple[float, float, str]:
        """
        IV Percentile interpretation for vol regime.

        Score represents vol richness (positive = expensive, negative = cheap):
            > 90th → Extremely expensive (+2) — strong sell vol
            > 75th → Expensive (+1)           — sell vol
            25-75th → Normal (0)              — neutral
            < 25th → Cheap (-1)               — buy vol
            < 10th → Extremely cheap (-2)     — strong buy vol

        Task G2-B (BLOCKER): ``iv_pctile=None`` (IV percentile unavailable)
        used to be silently converted to ``0.0`` upstream by
        ``SynthesisMapper.build_market_wide`` and fell into the final
        ``else`` branch below -- "IV 0th pctile: Extremely cheap — strong
        buy-vol" (score -2.0, confidence/weight 0.8), the single strongest
        and most confident signal this scorer can emit, for a metric that
        was never measured. None now gets its own explicit branch: a
        neutral score at zero weight, with the reasoning saying plainly
        that the data is missing.
        """
        if iv_pctile is None:
            return (0.0, 0.0, "IV percentile: insufficient data — weight zero, no signal")

        if iv_pctile > 90:
            return (2.0, 0.8, f"IV {iv_pctile:.0f}th pctile: Extremely expensive — strong sell-vol edge")
        elif iv_pctile > 75:
            return (1.0, 0.7, f"IV {iv_pctile:.0f}th pctile: Expensive — moderate sell-vol edge")
        elif iv_pctile > 25:
            return (0.0, 0.5, f"IV {iv_pctile:.0f}th pctile: Normal range")
        elif iv_pctile > 10:
            return (-1.0, 0.7, f"IV {iv_pctile:.0f}th pctile: Cheap — buy-vol opportunity")
        else:
            return (-2.0, 0.8, f"IV {iv_pctile:.0f}th pctile: Extremely cheap — strong buy-vol")

    @staticmethod
    def score_vrp(vrp: Optional[float], rv_10d: Optional[float], rv_20d: Optional[float],
                  rv_30d: Optional[float], cone_30d_pctile: Optional[float]) -> Tuple[float, float, str]:
        """
        Variance Risk Premium interpretation with stale-data correction.

        VRP = DVOL - 30d RV
        Positive VRP → IV > RV → selling premium has edge
        Negative VRP → IV < RV → buying premium has edge

        CRITICAL ADJUSTMENT:
        If 30d RV is at extreme percentile (>90th or <10th on cone),
        it's likely driven by a single event. Use shorter windows
        to estimate forward-looking VRP.

        Forward VRP = DVOL - avg(10d RV, 20d RV)
        Use forward VRP when 30d cone percentile > 85th or < 15th.

        Task G2-B (BLOCKER): ``vrp=None`` (DVOL or realized vol
        unavailable) used to be silently converted to ``0.0`` upstream,
        landing in the "Neutral" band at weight 0.5 -- a claim that vol is
        fairly priced, when in fact nothing was measured. ``vrp=None``
        now returns a neutral score at zero weight instead. The
        stale-data correction inputs (``rv_10d``/``rv_20d``/``rv_30d``/
        ``cone_30d_pctile``) can independently be None (that section
        failed while VRP itself succeeded) -- the correction is skipped
        (falls back to raw ``vrp``) rather than crashing or fabricating a
        correction from a missing percentile/RV window.
        """
        if vrp is None:
            return (0.0, 0.0, "VRP: insufficient data (DVOL or realized volatility unavailable) — weight zero")

        # Stale-data correction: only for cone > 85 (spike inflated 30d RV)
        # Cone < 15 (abnormally quiet) uses raw VRP — 10d/20d are equally quiet
        if (cone_30d_pctile is not None and cone_30d_pctile > 85
                and rv_10d is not None and rv_20d is not None and rv_30d is not None):
            forward_rv = (rv_10d + rv_20d) / 2
            dvol_approx = vrp + rv_30d
            forward_vrp = dvol_approx - forward_rv
            effective_vrp = forward_vrp
            stale_note = (f"30d RV at {cone_30d_pctile:.0f}th pctile (STALE). "
                          f"Forward VRP using 10d/20d avg: {forward_vrp:+.1f}pts")
        elif cone_30d_pctile is not None and cone_30d_pctile > 85:
            # Cone says stale but the RV windows needed for the forward-VRP
            # correction are themselves missing — disclose the gap rather
            # than silently applying (or silently skipping) the correction.
            effective_vrp = vrp
            stale_note = (f"30d RV at {cone_30d_pctile:.0f}th pctile (STALE), but 10d/20d/30d RV "
                          f"unavailable — forward-VRP correction skipped, using raw VRP")
        elif cone_30d_pctile is not None and cone_30d_pctile < 15:
            effective_vrp = vrp
            stale_note = (f"30d RV at {cone_30d_pctile:.0f}th pctile — abnormally quiet period. "
                          f"If realized vol reverts to historical norms, VRP will compress. "
                          f"Treat current sell-vol edge as potentially overstated")
        elif cone_30d_pctile is None:
            effective_vrp = vrp
            stale_note = "30d RV percentile: insufficient data — stale-data correction not applied"
        else:
            effective_vrp = vrp
            stale_note = f"30d RV within normal range"

        if effective_vrp > 10:
            return (2.0, 0.8, f"VRP {effective_vrp:+.1f}pts: Premium extremely rich — sell vol. {stale_note}")
        elif effective_vrp > 5:
            return (1.0, 0.7, f"VRP {effective_vrp:+.1f}pts: Moderate sell-vol edge. {stale_note}")
        elif effective_vrp > -5:
            return (0.0, 0.5, f"VRP {effective_vrp:+.1f}pts: Neutral. {stale_note}")
        elif effective_vrp > -10:
            return (-1.0, 0.7, f"VRP {effective_vrp:+.1f}pts: Vol is cheap — buy vol. {stale_note}")
        else:
            return (-2.0, 0.8, f"VRP {effective_vrp:+.1f}pts: Extreme mispricing — strong buy vol. {stale_note}")

    @staticmethod
    def score_skew(risk_reversal_25d: Optional[float]) -> Tuple[float, float, str]:
        """
        25-Delta Risk Reversal (bugfix_spec.md Item 9 / Decision D6).

        Re-signed from the old ``skew_25d`` (put IV - call IV, a magnitude-
        of-fear axis where BOTH extremes scored positive-ish/negative-ish
        independent of direction) to the market "risk reversal" convention
        (call IV - put IV). This is now a genuinely DIRECTIONAL axis:
        +2 = calls much richer (upside speculation), -2 = puts much richer
        (downside hedging demand) -- a NEGATIVE risk reversal scores
        NEGATIVE, a POSITIVE one scores POSITIVE (acceptance test T9.4).

        Decision D6 (task-B2-brief.md, already ruled): correctness over
        historical comparability, same as every other convention fix in
        this campaign -- an intentional, reviewed break, not a bug.

        Consumer note: ``classify_vol_regime``'s EXPLOSIVE-regime trigger
        reads this score and was re-signed alongside this function (fix-
        review Critical #1, ``skew_score >= 1`` -> ``skew_score <= -1`` --
        see that method's own docstring for the full history; not repeated
        here to avoid two copies of the same story drifting out of sync).

        Feeds into: vol regime classifier, risk factors, trade recs, vol
        assessment narrative. Does NOT feed into directional scoring
        (``all_direction_scores``).

        Task G2-G: ``risk_reversal_25d=None`` (vol surface/skew never
        computed for this expiry) is now its own explicit branch -- a
        neutral score at (near-)zero weight and an "insufficient data"
        message, instead of falling through to the ``<= MILD_POINTS``
        band and being reported as "RR25 +0.0%: Normal", a specific,
        confident reading for a metric that was never measured (the same
        BLOCKER-class defect Task G2-B fixed for
        ``score_iv_percentile``/``iv_percentile_365d=None``). Task G2-D
        (commit 9f14e53) unified per-expiry and market-wide RR25 onto one
        bracket-gated computation, which makes this None case fire more
        often than the old ungated picker did -- this was already latent
        before G2-D, just less frequently triggered.
        """
        if risk_reversal_25d is None:
            return (0.0, 0.0, "RR25: insufficient data — weight zero, no signal")

        if risk_reversal_25d < -RISK_REVERSAL_STRONG_POINTS:
            return (-2.0, 0.6, f"RR25 {risk_reversal_25d:+.1f}%: Extreme put demand — fear elevated")
        elif risk_reversal_25d < -RISK_REVERSAL_MILD_POINTS:
            return (-1.0, 0.6, f"RR25 {risk_reversal_25d:+.1f}%: Heavy hedging demand")
        elif risk_reversal_25d <= RISK_REVERSAL_MILD_POINTS:
            return (0.0, 0.4, f"RR25 {risk_reversal_25d:+.1f}%: Normal")
        elif risk_reversal_25d <= RISK_REVERSAL_STRONG_POINTS:
            return (1.0, 0.5, f"RR25 {risk_reversal_25d:+.1f}%: Calls richer — upside speculation")
        else:
            return (2.0, 0.6, f"RR25 {risk_reversal_25d:+.1f}%: Calls much richer — unusual")

    @staticmethod
    def score_term_structure(shape: Optional[str], spread: Optional[float],
                             iv_by_dte: Dict[int, float]) -> Tuple[float, float, str]:
        """
        Term structure interpretation.

        Contango (back > front) → Normal, market expects vol to persist
        Backwardation (front > back) → Near-term fear, sell near-term premium

        Score represents selling opportunity:
            Strong contango (>10pts) → Sell back months (+2)
            Moderate contango (5-10) → Normal (+1)
            Flat (±5)               → Neutral (0)
            Moderate backwardation   → Sell front month (-1)
            Strong backwardation     → Extreme near-term fear (-2)

        IMPORTANT: Check for kinks — if front 3 expiries have wildly
        different IVs, the structure is kinked (near-expiry distortion).

        Task Wave-H-B Fix 2: ``shape``/``spread`` are now genuinely
        Optional (``MarketWideMetrics.term_structure_shape``/
        ``term_structure_spread`` -- None when fewer than 2 usable
        per-expiry ATM IVs were available upstream). This scorer used to
        have no None branch at all -- the mapper fabricated a confident
        "CONTANGO" for every missing-data case, which this scorer then
        happily scored as a real reading. ``shape == "FLAT"`` is also a
        real, computed, non-CONTANGO/BACKWARDATION reading (the
        calculator's own third shape, emitted when |back - front| <=
        2pts) and gets its own neutral branch instead of falling into
        the old bare ``else`` (which treated anything not literally
        "CONTANGO" as backwardation).
        """
        if shape is None or spread is None:
            return (0.0, 0.0,
                    "Term structure: insufficient data (fewer than 2 usable expiries) — weight zero, no signal")

        # Check for kinks in front end
        sorted_dtes = sorted(iv_by_dte.keys())
        kink_detected = False
        if len(sorted_dtes) >= 3:
            front_ivs = [iv_by_dte[d] for d in sorted_dtes[:3]]
            if max(front_ivs) - min(front_ivs) > 15:
                kink_detected = True

        kink_note = " (KINKED front end — near-expiry distortion detected)" if kink_detected else ""

        if shape == "CONTANGO":
            if spread > 10:
                return (2.0, 0.5,
                        f"Contango +{spread:.0f}pts: Back months rich — calendar spread opportunity{kink_note}")
            elif spread > 5:
                return (1.0, 0.4, f"Contango +{spread:.0f}pts: Normal curve{kink_note}")
            else:
                return (0.0, 0.3, f"Contango +{spread:.0f}pts: Flat{kink_note}")
        elif shape == "BACKWARDATION":
            if spread > 10:
                return (-2.0, 0.6,
                        f"Backwardation -{spread:.0f}pts: Extreme near-term fear — sell front month{kink_note}")
            elif spread > 5:
                return (-1.0, 0.5, f"Backwardation -{spread:.0f}pts: Near-term stress{kink_note}")
            else:
                return (0.0, 0.3, f"Backwardation -{spread:.0f}pts: Mild{kink_note}")
        else:
            # shape == "FLAT" (calculator: |back - front| <= 2pts)
            return (0.0, 0.3, f"Term structure flat ({spread:.1f}pts): Neutral{kink_note}")

    # -------------------------------------------------------------------------
    # FRAGILITY DETECTION (post-scoring confidence adjustment)
    # -------------------------------------------------------------------------

    @staticmethod
    def detect_fragility(
            all_direction_scores: List[Tuple[float, float, str]],
            funding_8h: Optional[float]
    ) -> Tuple[float, str]:
        """
        Detect fragile crowding setups where flow consensus is strong
        but positioning is extreme.

        Returns (multiplier, level): HIGH→0.5, MODERATE→0.7, NONE→1.0.
        This is NOT a scorer — it's a post-hoc confidence multiplier.

        Task G2-B: ``funding_8h=None`` (funding data unavailable) cannot
        support a fragility claim one way or the other -- return the
        no-op multiplier (1.0, "NONE") rather than treating a fabricated
        0.0 as "definitely not crowded" (which happened to be harmless
        under the old thresholds here, since 0.0 never exceeds the ±15%
        annualized bands, but was still the wrong reason to reach the
        right answer).
        """
        if funding_8h is None:
            return (1.0, "NONE")

        # Compute directional avg excluding funding scores
        non_funding = [(s, w, r) for s, w, r in all_direction_scores
                       if "funding" not in r.lower()]

        if not non_funding:
            return (1.0, "NONE")

        weighted_sum = sum(s * w for s, w, _ in non_funding)
        total_weight = sum(w for _, w, _ in non_funding)
        if total_weight == 0:
            return (1.0, "NONE")

        avg_excl_funding = weighted_sum / total_weight
        ann_rate = funding_8h * 3 * 365

        bullish_fragile = avg_excl_funding > 0.8 and ann_rate > 15
        bearish_fragile = avg_excl_funding < -0.8 and ann_rate < -15

        if bullish_fragile or bearish_fragile:
            if abs(ann_rate) > 25:
                return (0.5, "HIGH")
            else:
                return (0.7, "MODERATE")

        return (1.0, "NONE")


# =============================================================================
# SECTION 3: REGIME CLASSIFIER
# =============================================================================

class RegimeClassifier:
    """
    Combines individual scores into regime classifications.

    Two-axis classification:
        1. Directional regime (bull/bear/neutral)
        2. Volatility regime (suppressed/normal/elevated/explosive)

    These combine into a MarketRegime.
    """

    @staticmethod
    def classify_direction(scores: List[Tuple[float, float, str]]) -> Tuple[Signal, float, List[str]]:
        """
        Weighted average of directional scores → Signal enum.

        Returns: (signal, confidence, reasoning_list)
        Confidence = weighted score magnitude / max possible magnitude,
        scaled by how much of the input actually carried weight (Task
        Wave-I-A Fix 2 — see below).

        Task Wave-I-A Fix 2: ``confidence`` used to be
        ``abs(avg_score) / 2.0`` alone — the magnitude of the *tilt*
        among whichever scores happened to carry weight, with no
        reference to how many of the scores handed in actually
        contributed one. Every weight-zero "insufficient data" entry
        (``score_flow_gated``, ``score_max_pain_gravity``'s
        ``sufficient_data=False`` branch, etc.) contributes exactly 0 to
        both ``weighted_sum`` and ``total_weight`` — a complete no-op in
        the weighted average — so a run where only 2 of 10 attempted
        scorers actually resolved reported the IDENTICAL "confidence" as
        a run where all 10 resolved and happened to average to the same
        tilt. Worse: turning a real, weakly-disagreeing measurement into
        a weight-zero "insufficient data" entry (same score, weight
        1.0 -> 0.0) used to *raise* the reported number, because the
        diluting entry was dropped from the average entirely instead of
        correctly signaling "we know less than before". Reproduced
        directly: three real signals (1.0, 1.0, -0.4) all at weight 1.0
        gave confidence 0.267; downgrading only the third to weight 0.0
        (same score, now "insufficient data") raised it to 0.5 — with
        strictly less real information feeding the number, not more.

        Fix: multiply the tilt magnitude by a data-coverage factor — the
        fraction of the scores handed in that actually carried weight
        (were not themselves an insufficient-data placeholder). When
        every input resolves (coverage == 1.0, the common case), this is
        numerically identical to the old formula; it only pulls the
        number down when some of the scores that were attempted came
        back weight-zero, which is exactly the case the old formula was
        blind to. (Relabeling the output text alone was considered and
        rejected — the fix is in scope here since coverage is fully
        computable from ``scores`` itself, no new plumbing needed, and
        this metric gates nothing downstream — it is display-only in the
        rendered report — so widening its meaning carries no behavioral
        risk beyond the displayed percentage.)
        """
        if not scores:
            return (Signal.NEUTRAL, 0.0, ["No directional data"])

        weighted_sum = sum(s[0] * s[1] for s in scores)
        total_weight = sum(s[1] for s in scores)

        if total_weight == 0:
            return (Signal.NEUTRAL, 0.0, ["No weighted data"])

        avg_score = weighted_sum / total_weight
        # Data-coverage factor: what fraction of the scores handed in
        # actually carried weight (i.e. were not an insufficient-data
        # placeholder at weight 0). Bounded [0, 1]; total_weight > 0 here
        # (guarded above) guarantees at least one entry has weight > 0,
        # so this is never 0.
        coverage = sum(1 for s in scores if s[1] > 0) / len(scores)
        confidence = (abs(avg_score) / 2.0) * coverage  # tilt magnitude x data coverage

        reasons = [s[2] for s in scores if abs(s[0]) > 0]

        if avg_score > 1.0:
            return (Signal.STRONG_BULLISH, confidence, reasons)
        elif avg_score > 0.3:
            return (Signal.BULLISH, confidence, reasons)
        elif avg_score > -0.3:
            return (Signal.NEUTRAL, confidence, reasons)
        elif avg_score > -1.0:
            return (Signal.BEARISH, confidence, reasons)
        else:
            return (Signal.STRONG_BEARISH, confidence, reasons)

    @staticmethod
    def classify_vol_regime(
            gex_total: float,
            iv_pctile_score: float,
            vrp_score: float,
            skew_score: float,
            spot: float = 100000.0,
            term_structure_score: float = 0.0,
    ) -> Tuple[VolRegime, List[str]]:
        """
        Classify volatility regime. GEX normalized by spot.

        Decision tree:
            1. GEX/spot > 20 AND IV low → SUPPRESSED
            2. GEX/spot < -20 AND IV high AND skew extreme → EXPLOSIVE
            3. IV high AND (VRP confirms OR term structure stressed) → ELEVATED
            4. IV high alone → ELEVATED (mixed confirmation)
            5. Otherwise → NORMAL

        bugfix_spec.md Item 9 / Decision D6 fix-review (Critical #1): the
        EXPLOSIVE branch's "steep skew" condition must check the
        crash-correlated side of ``score_skew``'s output. Before Item 9,
        ``score_skew`` took the old put-call ``skew_25d`` (positive = puts
        richer) and this branch checked ``skew_score >= 1`` -- correctly
        firing on extreme PUT-side richness (the side that empirically
        correlates with crash/explosive vol, per score_skew's own historical
        design). Item 9 re-signed ``score_skew`` to the market
        ``risk_reversal_25d`` convention (call - put), which FLIPS which
        real-world condition produces a positive vs. negative score: a
        negative score now means puts-richer (the same crash-correlated
        condition), not a positive one. The B2 sub-task 3 commit re-signed
        score_skew but did NOT re-sign this consumer -- an oversight
        (flagged in task-B2-report.md, confirmed as a real regression in
        review, fixed here): on the live golden fixture the old condition
        made EXPLOSIVE literally unreachable (every expiry's risk reversal
        scored negative -1, so ``skew_score >= 1`` never fired). The
        correct condition, restoring the original semantics against the
        NEW sign convention, is ``skew_score <= -1``.

        Also note (per the same review): re-signing score_skew was not a
        pure sign flip of otherwise-identical bands -- the threshold widths
        themselves changed. The old put-call bands were 4/8/12 points
        (Balanced up to 4, Heavy at 8, Extreme at 12); the new
        RISK_REVERSAL_MILD_POINTS/STRONG_POINTS bands are 1/5 points. The
        score now reacts to roughly 4x-2.4x smaller risk-reversal moves
        than before at the mild/strong boundaries respectively -- a real
        sensitivity increase, not a relabeling. Not remediated here (out of
        this fix round's scope per the coordinator's ruling) -- flagged for
        awareness only.
        """
        if spot <= 0:
            spot = 100000.0
        gex_normalized = gex_total / spot
        reasons = []

        if gex_normalized > 20 and iv_pctile_score <= 0:
            regime = VolRegime.SUPPRESSED
            reasons.append(f"Positive GEX (norm {gex_normalized:+.1f}) + low IV → Volatility suppressed")
        elif gex_normalized < -20 and iv_pctile_score >= 1 and skew_score <= -1:
            regime = VolRegime.EXPLOSIVE
            reasons.append(f"Negative GEX (norm {gex_normalized:+.1f}) + high IV + steep put-side skew → Explosive regime")
        elif iv_pctile_score >= 1 and (vrp_score >= 1 or term_structure_score <= -1):
            regime = VolRegime.ELEVATED
            reasons.append(f"High IV + {'VRP confirms' if vrp_score >= 1 else 'term structure stressed'} → Elevated vol")
        elif iv_pctile_score >= 1:
            regime = VolRegime.ELEVATED
            reasons.append(f"High IV ({iv_pctile_score:+.1f}), mixed confirmation → Elevated vol")
        else:
            regime = VolRegime.NORMAL
            reasons.append("Normal volatility regime")

        return (regime, reasons)

    @staticmethod
    def classify_market_regime(
            direction: Signal,
            vol_regime: VolRegime,
            near_term_direction: Signal,
            far_term_direction: Signal
    ) -> Tuple[MarketRegime, str]:
        """
        Combine direction + vol regime into market regime.

        TRANSITION requires conflicting signals AND magnitude >= 2
        (at least one side STRONG, or both moderate).
        """
        # Check for conflicting timeframes with minimum magnitude
        magnitude = abs(near_term_direction.value) + abs(far_term_direction.value)
        conflicting = near_term_direction.value * far_term_direction.value < 0
        if conflicting and magnitude >= 2:
            return (MarketRegime.TRANSITION,
                    f"Conflicting signals: near-term {near_term_direction.name} vs far-term {far_term_direction.name}")

        # Map direction + vol to regime
        if direction in (Signal.STRONG_BEARISH, Signal.BEARISH):
            if vol_regime in (VolRegime.ELEVATED, VolRegime.EXPLOSIVE):
                return (MarketRegime.VOLATILE_BEARISH, "Bearish + elevated vol = risk-off")
            elif vol_regime == VolRegime.SUPPRESSED:
                return (MarketRegime.RANGE_BOUND_BEARISH, "Bearish lean but vol suppressed = grind lower in range")
            else:
                return (MarketRegime.TRENDING_DOWN, "Bearish + normal vol = trending lower")

        elif direction in (Signal.STRONG_BULLISH, Signal.BULLISH):
            if vol_regime in (VolRegime.ELEVATED, VolRegime.EXPLOSIVE):
                return (MarketRegime.VOLATILE_BULLISH, "Bullish + elevated vol = volatile rally")
            elif vol_regime == VolRegime.SUPPRESSED:
                return (MarketRegime.RANGE_BOUND_BULLISH, "Bullish lean but vol suppressed = consolidation")
            else:
                return (MarketRegime.TRENDING_UP, "Bullish + normal vol = trending higher")

        else:  # Neutral
            if vol_regime == VolRegime.EXPLOSIVE:
                return (MarketRegime.TRANSITION, "Neutral direction + explosive vol = breakout imminent")
            elif vol_regime == VolRegime.ELEVATED:
                return (MarketRegime.RANGE_BOUND_ELEVATED, "Neutral + elevated vol = premium selling opportunity")
            elif vol_regime == VolRegime.SUPPRESSED:
                return (MarketRegime.RANGE_BOUND_NEUTRAL, "Neutral + suppressed vol = range-bound")
            else:
                return (MarketRegime.RANGE_BOUND_NEUTRAL, "Neutral direction = range-bound")


# =============================================================================
# SECTION 4: NARRATIVE GENERATOR
# =============================================================================

class NarrativeGenerator:
    """
    Converts regime classifications and scores into human-readable
    executive summary using templated narrative generation.
    """

    # -------------------------------------------------------------------------
    # REGIME DESCRIPTIONS
    # -------------------------------------------------------------------------

    # Task G2-G: ``{put_support}``/``{call_resistance}`` carry NO numeric
    # format spec (were ``${put_support:,.0f}``/``${call_resistance:,.0f}``,
    # literal "$" included). ``generate_regime_narrative`` now pre-formats
    # both into a display string ("$65,000" or "an unidentified level
    # (insufficient data)", "$" included) before calling ``.format()`` --
    # same reasoning/pattern as VOL_TEMPLATES' ``{iv_pctile}`` fix below.
    # ``{max_pain}`` keeps its numeric spec: max_pain is deliberately never
    # None (falls back to spot price -- see ExpiryMetrics' docstring), so
    # it never needs a display-string branch.
    REGIME_TEMPLATES = {
        MarketRegime.RANGE_BOUND_NEUTRAL: (
            "RANGE-BOUND (NEUTRAL) regime. {gex_detail} "
            "Expect price to oscillate between put support at {put_support} "
            "and call resistance at {call_resistance}. "
            "Priority: sell premium via symmetric iron condors/strangles, harvest theta decay."
        ),
        MarketRegime.RANGE_BOUND_BULLISH: (
            "RANGE-BOUND (BULLISH) regime. Vol suppressed but bullish lean. {gex_detail} "
            "Expect grind-higher in range toward {call_resistance}. "
            "Priority: sell put spreads preferred, skew short strikes higher."
        ),
        MarketRegime.RANGE_BOUND_BEARISH: (
            "RANGE-BOUND (BEARISH) regime. Vol suppressed but bearish lean. {gex_detail} "
            "Expect grind-lower in range toward {put_support}. "
            "Priority: sell call spreads preferred, skew short strikes lower."
        ),
        MarketRegime.RANGE_BOUND_ELEVATED: (
            "RANGE-BOUND (ELEVATED VOL) regime. No directional lean but vol is expensive. "
            "Best premium-selling environment — widest wings, most aggressive capture. "
            "Put support at {put_support}, call resistance at {call_resistance}. "
            "Priority: wide iron condors at GEX support/resistance."
        ),
        MarketRegime.TRENDING_UP: (
            "TRENDING-UP regime. Bullish structural positioning with normal volatility. "
            "Max pain gravity pulling price toward ${max_pain:,.0f}. "
            "Priority: long call spreads, short put spreads. Avoid naked short calls."
        ),
        MarketRegime.TRENDING_DOWN: (
            "TRENDING-DOWN regime. Bearish structural positioning with normal volatility. "
            "Put support at {put_support} is the key level to watch. "
            "Priority: long put spreads, protective puts. Sell call spreads on bounces."
        ),
        MarketRegime.VOLATILE_BULLISH: (
            "VOLATILE-BULLISH regime. Bullish direction but negative gamma will amplify moves. "
            "Expect outsized moves to the upside with violent pullbacks. "
            "Priority: long calls/call spreads with defined risk. Buy vol on dips."
        ),
        MarketRegime.VOLATILE_BEARISH: (
            "VOLATILE-BEARISH regime. Bearish direction amplified by negative gamma. "
            "This is the highest-risk environment — cascading liquidations possible. "
            "Priority: long puts, long straddles, cash. Avoid all short-vol positions."
        ),
        MarketRegime.TRANSITION: (
            "TRANSITION regime. {conflict_detail} "
            "Market structure is shifting — expect regime change within {transition_window}. "
            "Priority: reduce position sizing, favor defined-risk structures, wait for clarity."
        ),
    }

    # -------------------------------------------------------------------------
    # VOL RECOMMENDATION TEMPLATES
    # -------------------------------------------------------------------------

    # Task G2-B: ``{iv_pctile}`` has NO numeric format spec (was
    # ``{iv_pctile:.0f}``) -- ``generate_vol_narrative`` pre-formats it
    # into a display string ("73" or "insufficient data") before calling
    # ``.format()``, since a bare ``:.0f`` spec cannot render "insufficient
    # data" without raising. ``vrp`` keeps its numeric spec: every template
    # that references ``vrp`` is only ever selected when ``vrp`` is not
    # None (see ``generate_vol_narrative``'s ``vrp is None`` branch, which
    # forces the "neutral" template -- the one template with no
    # ``vrp``/``iv_pctile`` placeholders at all).
    #
    # Task G2-G: ``{sell_iv}``/``{skew}`` ALSO have NO numeric format spec
    # now (were ``{sell_iv:.1f}%``/``{skew:+.1f}%``) -- both
    # ``risk_reversal_25d`` (ExpiryMetrics field, vol surface may never
    # have computed for this expiry) and ``sell_iv``
    # (``best_sell_expiry.atm_iv``, same reason) are genuinely Optional
    # independent of whether ``vrp`` selected this template, so
    # ``generate_vol_narrative`` pre-formats both into display strings
    # ("-8.0%"/"insufficient data") the same way it already does for
    # ``iv_pctile``.
    VOL_TEMPLATES = {
        "sell_strong": (
            "Volatility is expensive (IV at {iv_pctile}th percentile, "
            "VRP {vrp:+.1f}pts). {vrp_adjustment} "
            "Sell premium in {sell_expiry} where ATM IV is {sell_iv}. "
            "RR25 at {skew} makes {rich_side} puts the higher-edge side to sell."
        ),
        "sell_moderate": (
            "Volatility is moderately elevated (IV at {iv_pctile}th percentile). "
            "{vrp_adjustment} "
            "Selling premium has edge but size conservatively. "
            "Favor {sell_expiry} expiry, {rich_side} side."
        ),
        "neutral": (
            "Volatility is fairly priced. No strong edge in selling or buying premium. "
            "Focus on directional trades with defined risk structures."
        ),
        "buy_moderate": (
            "Volatility is cheap (IV at {iv_pctile}th percentile, "
            "VRP {vrp:+.1f}pts). Long vol positions have edge. "
            "Buy {buy_expiry} straddles or strangles. Favor {cheap_side} side."
        ),
        "buy_strong": (
            "Volatility is extremely cheap. Strongly favor long vol. "
            "VRP {vrp:+.1f}pts suggests systematic underpricing. "
            "Buy vol across the curve, emphasize {buy_expiry}."
        ),
    }

    # -------------------------------------------------------------------------
    # KEY LEVELS TEMPLATE
    # -------------------------------------------------------------------------

    # Wave-H-A (Task 6): this template has no live consumer -- confirmed
    # never ``.format()``-ed anywhere in this codebase (an earlier audit's
    # finding, re-confirmed here) -- but the label was still wrong for
    # what {hvl} actually is, a landmine for whoever wires this up next.
    # {hvl} is GexDexKeyLevels.hvl: the STRIKE where CUMULATIVE net GEX
    # (summed strike-by-strike) changes sign -- a strike-axis artifact of
    # how open interest is distributed, NOT the re-priced dealer-gamma
    # flip. The actual "Zero Gamma Level" is GexDexKeyLevels.
    # zero_gamma_level (computed separately by GammaProfileCalculator,
    # bugfix_spec.md Item 2) and is not threaded into this template at
    # all. See gex_dex_calculator.py's class docstring ("Key Levels"
    # section) and GexDexKeyLevels's own docstring for the full
    # distinction -- the live full-report formatter
    # (gex_dex_formatter.py's KEY LEVELS block) already uses the correct
    # "Cumulative GEX Zero Strike" label for this same value.
    LEVELS_TEMPLATE = (
        "KEY LEVELS: "
        "Resistance ${resistance:,.0f} (call wall {res_oi:,} OI). "
        "Support ${support:,.0f} (put wall {sup_oi:,} OI). "
        "Max pain ${max_pain:,.0f} ({mp_distance:+.1f}% from spot). "
        "Cumulative GEX Zero Strike ${hvl:,.0f} (strike-axis artifact, NOT "
        "a re-priced gamma flip)."
    )

    # -------------------------------------------------------------------------
    # RISK TEMPLATE
    # -------------------------------------------------------------------------

    RISK_TEMPLATE = (
        "RISK FACTORS: "
        "{risk_items}"
    )

    # -------------------------------------------------------------------------
    # GENERATION METHODS
    # -------------------------------------------------------------------------

    @classmethod
    def generate_regime_narrative(
            cls,
            regime: MarketRegime,
            spot: float,
            put_support: Optional[float],
            call_resistance: Optional[float],
            max_pain: float,
            gex_total: float,
            conflict_detail: str = "",
            transition_window: str = "7-14 days"
    ) -> str:
        """
        Generate regime description with filled parameters.

        Task G2-G: ``put_support``/``call_resistance`` (the largest
        expiry's GEX-derived strike levels) are genuinely Optional -- no
        identified level in that expiry's strike range, NOT a level at
        strike 0.0. Pre-formatted into a display string ("$65,000" or an
        explicit "insufficient data" phrase) before ``.format()`` runs, so
        a missing level neither raises (a bare ``:,.0f`` spec on None)
        nor silently renders as "put support at $0" -- a specific,
        wrong price level. ``max_pain`` keeps its direct numeric format:
        it's deliberately never None (falls back to spot price).
        """

        gex_millions = gex_total / 1_000_000
        if gex_millions > 0:
            gex_detail = f"Positive gamma (+{gex_millions:.1f}M GEX) is dampening volatility — dealers buy dips and sell rallies."
        else:
            gex_detail = f"Negative gamma ({gex_millions:.1f}M GEX) is amplifying moves — dealers chase momentum both directions."

        put_support_display = (
            f"${put_support:,.0f}" if put_support is not None
            else "an unidentified level (insufficient data)"
        )
        call_resistance_display = (
            f"${call_resistance:,.0f}" if call_resistance is not None
            else "an unidentified level (insufficient data)"
        )

        template = cls.REGIME_TEMPLATES.get(regime, cls.REGIME_TEMPLATES[MarketRegime.RANGE_BOUND_NEUTRAL])

        return template.format(
            put_support=put_support_display,
            call_resistance=call_resistance_display,
            max_pain=max_pain,
            gex_detail=gex_detail,
            conflict_detail=conflict_detail,
            transition_window=transition_window,
        )

    @classmethod
    def generate_vol_narrative(
            cls,
            iv_pctile: Optional[float],
            vrp: Optional[float],
            vrp_adjustment: str,
            risk_reversal_25d: Optional[float],
            sell_expiry: str,
            sell_iv: Optional[float],
            buy_expiry: str = "",
    ) -> str:
        """
        Generate volatility assessment narrative.

        bugfix_spec.md Item 9 fix-review (Important #6): this used to take
        the legacy put-call ``skew`` (positive = puts richer) and printed
        it under the generic label "Skew" -- the same report bundle's GEX/
        DEX/vol-surface sections had already switched to the RR25 (call -
        put) convention and sign, so the SAME quantity was shown with TWO
        CONTRADICTORY signs under an unlabelled name in one report. Now
        takes ``risk_reversal_25d`` directly (market convention) and prints
        it as "RR25", with the rich/cheap-side thresholds re-signed to
        match (mechanically: substitute risk_reversal_25d = -skew into the
        prior skew>8/skew>=4/skew<4 boundaries -- same decision boundaries,
        opposite-signed input, no new thresholds introduced).

        Task G2-B: ``vrp=None`` (VRP unavailable) forces ``template_key``
        to "neutral" -- the one template with no ``{vrp}``/``{iv_pctile}``
        placeholder, so it can never fabricate a directional vol call from
        missing data, and never crashes trying to format ``None`` with a
        numeric spec. ``iv_pctile=None`` is pre-formatted into a plain
        "insufficient data" string (the templates' ``{iv_pctile}``
        placeholder carries no format spec for exactly this reason) so it
        can still render inside a "sell"/"buy" template driven by a real,
        non-None ``vrp``.

        Task G2-G: ``risk_reversal_25d``/``sell_iv`` are independently
        Optional from ``vrp`` (the largest/best-sell expiry's vol surface
        can be missing even when market-wide VRP is real) -- both are
        pre-formatted into display strings the same way ``iv_pctile`` is,
        and the rich/cheap-side classification below gets its own explicit
        None branch instead of comparing None against a numeric threshold
        (which raises ``TypeError`` in Python 3).
        """
        iv_pctile_display = f"{iv_pctile:.0f}" if iv_pctile is not None else "insufficient data"
        risk_reversal_display = (
            f"{risk_reversal_25d:+.1f}%" if risk_reversal_25d is not None else "insufficient data"
        )
        sell_iv_display = f"{sell_iv:.1f}%" if sell_iv is not None else "insufficient data"

        # Determine which template — unified ±5 thresholds matching VRP scorer
        if vrp is None:
            template_key = "neutral"
        elif vrp > 10:
            template_key = "sell_strong"
        elif vrp > 5:
            template_key = "sell_moderate"
        elif vrp > -5:
            template_key = "neutral"
        elif vrp > -10:
            template_key = "buy_moderate"
        else:
            template_key = "buy_strong"

        # Rich/cheap side based on the risk reversal (re-signed: rr = -skew).
        # Task G2-G: None is its own branch; the two existing threshold
        # checks below (rich_side's tri-branch at -8/-4, cheap_side's
        # independent < -4 check) are otherwise UNCHANGED -- deliberately
        # not merged into one set of branches, since cheap_side's boundary
        # (strict < -4) differs from rich_side's middle branch (<= -4) at
        # exactly risk_reversal_25d == -4.0.
        if risk_reversal_25d is None:
            rich_side = "RR25 unavailable — insufficient data to identify rich/cheap side"
            cheap_side = "insufficient data"
        else:
            if risk_reversal_25d < -8:
                rich_side = "OTM puts are rich — selling put premium has edge"
            elif risk_reversal_25d <= -4:
                rich_side = "RR25 is normal — no clear rich/cheap side"
            else:
                rich_side = "OTM puts are cheap relative to calls — tail risk underpriced"
            cheap_side = "calls" if risk_reversal_25d < -4 else "puts"

        template = cls.VOL_TEMPLATES[template_key]

        return template.format(
            iv_pctile=iv_pctile_display,
            vrp=vrp,
            vrp_adjustment=vrp_adjustment,
            skew=risk_reversal_display,
            sell_expiry=sell_expiry,
            sell_iv=sell_iv_display,
            buy_expiry=buy_expiry,
            rich_side=rich_side,
            cheap_side=cheap_side,
        )

    @classmethod
    def generate_risk_factors(
            cls,
            cone_30d_pctile: Optional[float],
            gex_total: float,
            gamma_rolloff: Optional[GammaRolloffResult],
            funding_8h: Optional[float],
            risk_reversal_25d: Optional[float],
            spot: float = 100000.0,
            fragility_multiplier: float = 1.0,
            fragility_level: str = "NONE",
    ) -> str:
        """
        Generate risk factor list based on thresholds.

        bugfix_spec.md Item 9 fix-review (Important #6): takes
        ``risk_reversal_25d`` (market convention) directly now, not the
        legacy put-call ``skew`` -- the "Extreme skew" threshold below is
        re-signed to match (rr = -skew; puts-rich extreme is now a large
        NEGATIVE risk reversal).

        Task G2-B: ``cone_30d_pctile``/``funding_8h`` can now be None
        (unavailable) -- both threshold checks below are skipped, never
        evaluated against a fabricated 0.0, when their input is missing.
        A missing input is not itself a "risk factor" to report here; the
        run-level DATA QUALITY section is where its absence gets disclosed.

        Task G2-G: ``risk_reversal_25d`` is the same kind of genuinely-
        Optional field (the largest expiry's vol surface may never have
        computed) -- its threshold check below is skipped the same way,
        for the same reason.

        Task Wave-I-A Fix 1: ``gamma_rolloff`` replaces the old
        ``largest_expiry_dte`` parameter. The old "Major expiry in N DTE"
        trigger used ``largest_expiry`` = ``max(expiries, key=lambda e:
        e.total_oi)`` -- for BTC the largest-OI expiry is essentially
        always a far-dated quarterly, so ``largest_expiry_dte <= 3`` could
        never fire even when a genuinely near-dated expiry carried a large
        share of the book's gamma mass (real pin risk). This reuses
        ``GexDexCalculator.calculate_rolloff_profile``'s own "GAMMA CLIFF"
        flag (``gamma_cliff_7d``, threshold >30% of gamma mass within 7
        DTE -- see ``format_gamma_rolloff_section``) instead of inventing
        a second, parallel near-term-concentration metric. ``None`` when
        the roll-off computation never ran (no per-expiry GEX at all) --
        the check is skipped, same "missing input is not itself a risk
        factor" convention as ``cone_30d_pctile``/``funding_8h`` above.
        """

        risks = []

        if cone_30d_pctile is not None and cone_30d_pctile > 90:
            risks.append(
                f"30d RV at {cone_30d_pctile:.0f}th percentile — recent extreme move may repeat or mean-revert violently")

        # GEX threshold normalized by spot
        if spot <= 0:
            spot = 100000.0
        gex_norm = gex_total / spot
        if gex_norm < -50:
            gex_m = gex_total / 1_000_000
            risks.append(
                f"Deeply negative GEX ({gex_m:.1f}M, norm {gex_norm:.0f}) — cascading stop-outs possible")

        if gamma_rolloff is not None and gamma_rolloff.gamma_cliff_7d:
            near_expiries = [r.expiration for r in gamma_rolloff.rows if r.dte_days <= 7.0]
            expiry_note = ", ".join(near_expiries) if near_expiries else "near-dated expiries"
            risks.append(
                f"GAMMA CLIFF: {gamma_rolloff.cum_share_7d:.0f}% of gamma mass expires "
                f"within 7 DTE ({expiry_note}) — pin risk and gamma spike around max pain"
            )

        # Funding threshold: |funding_8h| > 0.03% per 8h (~32.85% ann)
        if funding_8h is not None and abs(funding_8h) > 0.03:
            direction = "long" if funding_8h > 0 else "short"
            ann_rate = abs(funding_8h) * 3 * 365
            level = "Extreme" if ann_rate > 20 else "Elevated"
            risks.append(
                f"{level} funding ({funding_8h:.4f}% per 8h, ~{ann_rate:.1f}% ann) "
                f"— crowded {direction} at risk of squeeze"
            )

        if risk_reversal_25d is not None and risk_reversal_25d < -12:
            risks.append(f"Extreme RR25 ({risk_reversal_25d:+.1f}%) — tail hedging elevated, crash risk priced in")

        # Fragility flag
        if fragility_multiplier < 1.0:
            risks.append(
                f"Fragility {fragility_level}: directional consensus + extreme positioning — reversal risk elevated"
            )

        if not risks:
            risks.append("No elevated risk factors detected")

        return cls.RISK_TEMPLATE.format(risk_items=" | ".join(risks))

    @classmethod
    def generate_trade_recommendations(
            cls,
            regime: MarketRegime,
            vol_regime: VolRegime,
            iv_pctile: Optional[float],
            risk_reversal_25d: Optional[float],
            gex_total: float,
            near_term_expiry: str,
            far_term_expiry: str,
            skew_expiry: str = "",
            vrp: Optional[float] = None,
    ) -> str:
        """
        Generate trade recommendations based on regime.

        This is the money shot — what do you actually DO?

        Framework:
            1. Premium selling (iron condors, strangles) → Range-bound + high IV
            2. Directional spreads (verticals) → Trending + normal IV
            3. Long vol (straddles, strangles) → Explosive regime or cheap IV
            4. Calendar spreads → Term structure dislocation
            5. Risk reversal → Strong skew + directional view
            6. Cash/reduce → Transition regime

        bugfix_spec.md Item 9 fix-review (Important #6): takes
        ``risk_reversal_25d`` (market convention, call - put) directly now,
        not the legacy put-call ``skew`` -- every threshold below is
        re-signed to match (rr = -skew; same decision boundaries, no new
        thresholds introduced).

        Task G2-B: ``iv_pctile=None`` (IV percentile unavailable) must not
        silently satisfy an IV threshold check via a fabricated 0.0 --
        both IV-gated strategies below now require ``iv_pctile is not
        None`` before comparing it, so a missing IV percentile can never
        trigger "cheap IV, buy vol" (the old ``0.0 < 30`` phantom trigger)
        or be blocked from "expensive IV, sell vol" for the wrong reason.

        Task G2-G: ``risk_reversal_25d=None`` is handled per-strategy, not
        with one blanket guard, because each strategy uses it differently:
        Strategy 1's skew-adjustment sub-recommendation gets its own
        explicit None branch (the IC recommendation itself is still
        printed -- ``iv_pctile``/``regime``/``vol_regime`` already justify
        it; only the skew-specific wing adjustment degrades to "insufficient
        data"). Strategy 3 is triggered by ``regime`` alone, so its RR25
        annotation is pre-formatted into a display string rather than
        skipping the whole recommendation. Strategy 4 IS gated on
        ``risk_reversal_25d`` itself (a specific RR25 threshold trigger,
        same as ``iv_pctile is not None`` gating Strategies 1/2) -- a
        missing reading cannot satisfy "RR25 < -10%", so the whole
        recommendation is skipped, matching the existing iv_pctile pattern
        rather than inventing a new one.

        Task Wave-H-B Fix 3: Strategies 1 and 2 used to trigger purely off
        ``iv_pctile`` thresholds (>70 / <30) with no awareness of VRP --
        the SAME report's vol-assessment narrative (``generate_vol_narrative``,
        ``VOL_TEMPLATES``) independently picks its sell/buy framing off
        ``vrp`` thresholds (>10 / <-10) alone. IV percentile (relative-to-
        365-day-history valuation) and VRP (implied vs. realized, the
        direct priced-vol-vs-actual-vol edge measure -- see
        ``score_vrp``'s docstring) are genuinely different, uncorrelated
        reads and CAN legitimately disagree (e.g. IV is cheap vs. its own
        year but still priced above a currently-quiet realized vol). Left
        unarbitrated, this produced a report that said "sell premium" in
        the vol-assessment narrative and "PRIMARY — Long Straddle" (buy
        vol) in trade recommendations, both with top billing, no
        disclosure of the conflict.

        VRP is the more direct, actionable edge measure for a premium
        trade (it's a direct comparison of priced vol to realized vol,
        not a historical-percentile rank), so it takes precedence when
        the two disagree: the IV-percentile-triggered strategy is
        demoted from PRIMARY to an explicitly-flagged, reduced-confidence
        idea rather than silently standing as equal-billing advice. A
        missing ``vrp`` (None) cannot disagree with anything -- both
        strategies fall back to their original iv_pctile-only behavior.
        """
        recommendations = []

        # Task Wave-H-B Fix 3: VRP vs. IV-percentile disagreement,
        # computed once and applied to whichever strategy it contradicts.
        # "Disagreement" is scoped to the two thresholds these strategies
        # already use (70/30), not score_vrp's finer bands, so a mild VRP
        # reading that doesn't itself justify a strong sell/buy call
        # doesn't manufacture a conflict that isn't really there.
        vrp_says_sell = vrp is not None and vrp > 5   # score_vrp: sell-vol edge or stronger
        vrp_says_buy = vrp is not None and vrp < -5   # score_vrp: buy-vol edge or stronger

        # Strategy 1: Premium selling conditions (all RANGE_BOUND variants)
        range_bound_regimes = (
            MarketRegime.RANGE_BOUND_NEUTRAL,
            MarketRegime.RANGE_BOUND_BULLISH,
            MarketRegime.RANGE_BOUND_BEARISH,
            MarketRegime.RANGE_BOUND_ELEVATED,
        )
        if (regime in range_bound_regimes and
                iv_pctile is not None and iv_pctile > 70 and
                vol_regime != VolRegime.EXPLOSIVE):
            # RR25-adjusted IC (re-signed: was skew > 8 / skew < 2)
            if risk_reversal_25d is None:
                skew_adj = "RR25 unavailable — symmetric wings at GEX support/resistance levels (insufficient data to skew-adjust). "
            elif risk_reversal_25d < -8:
                skew_adj = "Puts are rich — keep short put at 25-delta, push long put protection further OTM (5-delta). "
            elif risk_reversal_25d > -2:
                skew_adj = "Calls relatively expensive — keep short call at 25-delta, push long call protection further OTM. "
            else:
                skew_adj = "Normal RR25 — symmetric wings at GEX support/resistance levels. "

            # Regime-specific center shift
            if regime == MarketRegime.RANGE_BOUND_BULLISH:
                skew_adj += "Bullish lean: shift IC center upward."
            elif regime == MarketRegime.RANGE_BOUND_BEARISH:
                skew_adj += "Bearish lean: shift IC center downward."
            elif regime == MarketRegime.RANGE_BOUND_ELEVATED:
                skew_adj += "Elevated vol: widest wings for maximum premium capture."

            if vrp_says_buy:
                recommendations.append(
                    f"OPPORTUNISTIC (signals disagree) — Short Iron Condor ({near_term_expiry}): "
                    f"IV at {iv_pctile:.0f}th pctile looks expensive vs. its own history, but "
                    f"VRP {vrp:+.1f}pts says implied vol is cheap vs. currently realized vol — "
                    f"buying volatility has the more reliable near-term edge. Treat this "
                    f"sell-premium idea with reduced confidence; do not size as a primary position. "
                    f"{skew_adj}"
                    f"Target 50% of max profit, close before final 3 DTE."
                )
            else:
                recommendations.append(
                    f"PRIMARY — Short Iron Condor ({near_term_expiry}): "
                    f"Sell premium in range-bound regime. IV at {iv_pctile:.0f}th pctile provides edge. "
                    f"{skew_adj}"
                    f"Target 50% of max profit, close before final 3 DTE."
                )

        # Strategy 2: Long vol conditions
        iv_pctile_cheap = iv_pctile is not None and iv_pctile < 30
        if vol_regime == VolRegime.EXPLOSIVE or iv_pctile_cheap:
            if vol_regime != VolRegime.EXPLOSIVE and iv_pctile_cheap and vrp_says_sell:
                recommendations.append(
                    f"OPPORTUNISTIC (signals disagree) — Long Straddle/Strangle ({far_term_expiry}): "
                    f"IV at {iv_pctile:.0f}th pctile looks cheap vs. its own history, but "
                    f"VRP {vrp:+.1f}pts says implied vol is still rich vs. currently realized "
                    f"vol — selling volatility has the more reliable near-term edge. Treat this "
                    f"long-vol idea with reduced confidence; do not size as a primary position."
                )
            else:
                recommendations.append(
                    f"PRIMARY — Long Straddle/Strangle ({far_term_expiry}): "
                    f"{'Explosive gamma regime' if vol_regime == VolRegime.EXPLOSIVE else 'Cheap IV'} "
                    f"favors owning volatility. Buy ATM straddle or 25-delta strangle."
                )

        # Strategy 3: Directional spreads. Task G2-G: triggered by
        # ``regime`` alone (not risk_reversal_25d), so a missing RR25
        # reading pre-formats to "insufficient data" rather than skipping
        # a recommendation the regime already justifies.
        rr25_display = (
            f"{risk_reversal_25d:+.1f}%" if risk_reversal_25d is not None else "insufficient data"
        )
        if regime in (MarketRegime.TRENDING_UP, MarketRegime.VOLATILE_BULLISH):
            recommendations.append(
                f"SECONDARY — Bull Call Spread ({far_term_expiry}): "
                f"Bullish regime supports upside exposure. Buy near-ATM, sell at call resistance. "
                f"RR25 {rr25_display} makes calls relatively cheap vs puts."
            )
        elif regime in (MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE_BEARISH):
            recommendations.append(
                f"SECONDARY — Bear Put Spread ({far_term_expiry}): "
                f"Bearish regime supports downside positioning. "
                f"Steep RR25 ({rr25_display}) makes puts expensive — use spreads to offset."
            )

        # Strategy 4: Skew trade — exclude bearish regimes (re-signed: was skew > 10)
        bearish_exclusions = (
            MarketRegime.VOLATILE_BEARISH,
            MarketRegime.TRENDING_DOWN,
            MarketRegime.RANGE_BOUND_BEARISH,
        )
        # Task G2-G: this IS a risk_reversal_25d-gated trigger (like
        # iv_pctile gating Strategies 1/2 above) -- a missing reading
        # cannot satisfy "RR25 < -10%", so skip the whole recommendation
        # rather than fabricate a trigger from a None comparison.
        if risk_reversal_25d is not None and risk_reversal_25d < -10 and regime not in bearish_exclusions:
            skew_src = f" [{skew_expiry}]" if skew_expiry else ""
            recommendations.append(
                f"OPPORTUNISTIC — Risk Reversal ({skew_expiry or near_term_expiry}): "
                f"25D RR25{skew_src} at {risk_reversal_25d:+.1f}% is elevated (threshold: <-10%). "
                f"Sell OTM put, buy OTM call. "
                f"Verify RR25 on target expiry before executing — it varies across the curve."
            )

        # Strategy 5: Transition
        if regime == MarketRegime.TRANSITION:
            recommendations.append(
                f"DEFENSIVE — Reduce sizing, favor defined-risk structures only. "
                f"Consider long straddle on {far_term_expiry} to capture the regime shift."
            )

        if not recommendations:
            recommendations.append("No high-conviction trades identified. Monitor for regime change.")

        return "\n".join(recommendations)


# =============================================================================
# SECTION 5: MASTER SYNTHESIS PIPELINE
# =============================================================================

class SynthesisEngine:
    """
    Master pipeline that orchestrates:
        Raw Data → Scoring → Regime Classification → Narrative → Executive Summary
    """

    def __init__(self):
        self.scorer = ScoringEngine()
        self.classifier = RegimeClassifier()
        self.narrator = NarrativeGenerator()

    def run(
            self,
            market: MarketWideMetrics,
            expiries: List[ExpiryMetrics],
    ) -> str:
        """
        Run the full synthesis pipeline.

        Returns: Formatted executive summary string.
        """

        # Sort expiries by DTE, exclude DTE=0 from scoring pool
        expiries_sorted = sorted(expiries, key=lambda e: e.dte)

        # Separate near-term (0-7 DTE) and far-term (>7 DTE) expiries
        near_term = [e for e in expiries_sorted if e.dte <= 7]
        mid_term = [e for e in expiries_sorted if 7 < e.dte <= 30]
        far_term = [e for e in expiries_sorted if e.dte > 30]

        # Find the largest expiry by OI (most influential)
        largest_expiry = max(expiries_sorted, key=lambda e: e.total_oi)

        # Find the nearest meaningful expiry (>0 DTE with decent OI)
        meaningful_near = next(
            (e for e in expiries_sorted if e.dte >= 1 and e.total_oi > 500),
            expiries_sorted[0]
        )

        # Best buy expiry: highest volume where DTE > 14 (volume = execution quality proxy)
        # Fallback: highest OI with DTE > 14. If nothing: furthest-DTE expiry.
        far_candidates = [e for e in expiries_sorted if e.dte > 14]
        vol_candidates = [e for e in far_candidates if e.total_volume > 0]
        if vol_candidates:
            meaningful_far = max(vol_candidates, key=lambda e: e.total_volume)
        elif far_candidates:
            meaningful_far = max(far_candidates, key=lambda e: e.total_oi)
        else:
            meaningful_far = expiries_sorted[-1]

        spot = market.spot_price

        # =====================================================================
        # STEP 1: Score all directional metrics
        # =====================================================================

        all_direction_scores = []

        # Market-wide scores
        all_direction_scores.append(
            self.scorer.score_funding(market.funding_8h)
        )

        # Futures basis (front only). bugfix_spec.md Item 5 / Decision D12:
        # basis is now Optional[float] (None when annualization is
        # suppressed for a sub-daily tenor) — None must weight-zero here,
        # never be treated as a neutral score. Ordering is preserved (dict
        # insertion order == DTE-ascending, per synthesis_logic.md:112);
        # filtering keeps that order, it just skips suppressed entries.
        basis_values = [v for v in market.futures_basis.values() if v is not None]
        if basis_values:
            all_direction_scores.append(
                self.scorer.score_futures_basis(basis_values[0])
            )

        # Score the 3 most important expiries by OI, excluding DTE=0
        top_expiries = sorted(
            [e for e in expiries_sorted if e.dte > 0],
            key=lambda e: e.total_oi, reverse=True
        )[:3]

        for exp in top_expiries:
            all_direction_scores.append(
                self.scorer.score_pc_ratio(exp.pc_ratio, dte=exp.dte))
            all_direction_scores.append(
                self.scorer.score_dex(exp.total_dex, spot=spot, dte=exp.dte))
            all_direction_scores.append(
                # CARRIED FINDING (B2 review, task C1): this expiry's own
                # forward price, not the single global ``spot`` -- same
                # defect class as bugfix_spec.md Item 7.
                self.scorer.score_max_pain_gravity(
                    exp.max_pain, exp.underlying_price, dte=exp.dte,
                    sufficient_data=exp.max_pain_sufficient_data))
            all_direction_scores.append(
                self.scorer.score_flow_gated(exp.flow_bias, exp.flow_trend, exp.flow_sufficient_data))
            all_direction_scores.append(
                self.scorer.score_vanna_charm(
                    exp.net_vanna, exp.net_charm,
                    iv_pctile=market.iv_percentile_365d,
                    gex_total=exp.total_gex, spot=spot))

        # Near-term scores (for transition detection) — full scorer set
        near_direction_scores = []
        for exp in near_term[:3]:
            near_direction_scores.append(
                self.scorer.score_pc_ratio(exp.pc_ratio, dte=exp.dte))
            near_direction_scores.append(
                self.scorer.score_dex(exp.total_dex, spot=spot, dte=exp.dte))
            near_direction_scores.append(
                # CARRIED FINDING (B2 review, task C1): this expiry's own
                # forward price, not the single global ``spot`` -- same
                # defect class as bugfix_spec.md Item 7.
                self.scorer.score_max_pain_gravity(
                    exp.max_pain, exp.underlying_price, dte=exp.dte,
                    sufficient_data=exp.max_pain_sufficient_data))
            near_direction_scores.append(
                self.scorer.score_flow_gated(exp.flow_bias, exp.flow_trend, exp.flow_sufficient_data))
            near_direction_scores.append(
                self.scorer.score_vanna_charm(
                    exp.net_vanna, exp.net_charm,
                    iv_pctile=market.iv_percentile_365d,
                    gex_total=exp.total_gex, spot=spot))

        # Far-term scores — full scorer set
        far_direction_scores = []
        for exp in far_term[:3]:
            far_direction_scores.append(
                self.scorer.score_pc_ratio(exp.pc_ratio, dte=exp.dte))
            far_direction_scores.append(
                self.scorer.score_dex(exp.total_dex, spot=spot, dte=exp.dte))
            far_direction_scores.append(
                # CARRIED FINDING (B2 review, task C1): this expiry's own
                # forward price, not the single global ``spot`` -- same
                # defect class as bugfix_spec.md Item 7.
                self.scorer.score_max_pain_gravity(
                    exp.max_pain, exp.underlying_price, dte=exp.dte,
                    sufficient_data=exp.max_pain_sufficient_data))
            far_direction_scores.append(
                self.scorer.score_flow_gated(exp.flow_bias, exp.flow_trend, exp.flow_sufficient_data))
            far_direction_scores.append(
                self.scorer.score_vanna_charm(
                    exp.net_vanna, exp.net_charm,
                    iv_pctile=market.iv_percentile_365d,
                    gex_total=exp.total_gex, spot=spot))

        # =====================================================================
        # STEP 2: Score all vol metrics
        # =====================================================================

        iv_pctile_score = self.scorer.score_iv_percentile(market.iv_percentile_365d)

        vrp_score = self.scorer.score_vrp(
            market.vrp, market.rv_10d, market.rv_20d, market.rv_30d,
            market.cone_30d_pctile
        )

        skew_score = self.scorer.score_skew(largest_expiry.risk_reversal_25d)

        term_score = self.scorer.score_term_structure(
            market.term_structure_shape,
            market.term_structure_spread,
            market.iv_by_dte
        )

        # =====================================================================
        # STEP 3: Classify regimes
        # =====================================================================

        # Overall direction
        overall_direction, dir_confidence, dir_reasons = \
            self.classifier.classify_direction(all_direction_scores)

        # Near-term direction
        near_direction, _, _ = self.classifier.classify_direction(near_direction_scores)

        # Far-term direction
        far_direction, _, _ = self.classifier.classify_direction(far_direction_scores)

        # Fragility detection (after direction, before regime)
        fragility_multiplier, fragility_level = self.scorer.detect_fragility(
            all_direction_scores, market.funding_8h
        )
        dir_confidence *= fragility_multiplier

        # Vol regime — prefer market-wide aggregate GEX; fall back to largest expiry
        gex_for_regime = (
            market.aggregate_total_gex
            if market.aggregate_total_gex != 0.0
            else largest_expiry.total_gex
        )
        vol_regime, vol_reasons = self.classifier.classify_vol_regime(
            gex_for_regime,
            iv_pctile_score[0],
            vrp_score[0],
            skew_score[0],
            spot=spot,
            term_structure_score=term_score[0],
        )

        # Market regime
        market_regime, regime_reason = self.classifier.classify_market_regime(
            overall_direction, vol_regime, near_direction, far_direction
        )

        # =====================================================================
        # STEP 4: Calculate forward VRP for narrative
        #
        # Task G2-B: every input here (market.rv_10d/rv_20d, market.dvol,
        # market.vrp, market.cone_30d_pctile) can now genuinely be None.
        # The old code assumed all five were always real floats -- with
        # the mapper fix above, that assumption is false whenever a
        # section's calculation didn't run, so every branch below is
        # guarded and discloses "insufficient data" instead of silently
        # computing a forward-VRP proxy (or a stale-data correction) from
        # a fabricated zero.
        # =====================================================================

        forward_rv = (
            (market.rv_10d + market.rv_20d) / 2
            if market.rv_10d is not None and market.rv_20d is not None
            else None
        )
        forward_vrp = (
            market.dvol - forward_rv
            if market.dvol is not None and forward_rv is not None
            else None
        )
        effective_vrp = market.vrp

        if market.vrp is None:
            vrp_adjustment = (
                "VRP: insufficient data (DVOL or realized volatility unavailable) — "
                "no stale-data correction applied."
            )
        elif market.cone_30d_pctile is not None and market.cone_30d_pctile > 85:
            if forward_vrp is not None:
                signals_agree = (market.vrp >= 0) == (forward_vrp >= 0)
                agreement_note = (
                    "Confirms primary signal direction."
                    if signals_agree
                    else f"Conflicts with primary VRP ({market.vrp:+.1f}pts) — treat as uncertain."
                )
                vrp_adjustment = (
                    f"NOTE [model]: 30d RV at {market.cone_30d_pctile:.0f}th pctile — "
                    f"may be inflated by a prior extreme move. "
                    f"Forward VRP proxy using 10d/20d avg RV ({forward_rv:.1f}%) = {forward_vrp:+.1f}pts. "
                    f"{agreement_note} "
                    f"Primary VRP ({market.vrp:+.1f}pts) drives this recommendation."
                )
            else:
                vrp_adjustment = (
                    f"NOTE [model]: 30d RV at {market.cone_30d_pctile:.0f}th pctile — may be "
                    f"inflated by a prior extreme move, but the forward-VRP proxy is unavailable "
                    f"(10d/20d RV or DVOL missing). "
                    f"Primary VRP ({market.vrp:+.1f}pts) drives this recommendation, uncorrected."
                )
        elif market.cone_30d_pctile is not None and market.cone_30d_pctile < 15:
            vrp_adjustment = (
                f"NOTE: 30d RV at {market.cone_30d_pctile:.0f}th pctile — unusually calm period. "
                f"VRP may compress further if realized vol reverts to mean."
            )
        elif market.cone_30d_pctile is None:
            vrp_adjustment = (
                "30d RV percentile: insufficient data — cannot assess whether the primary "
                "VRP is representative of current conditions."
            )
        else:
            vrp_adjustment = "30d RV within normal range. VRP is representative."

        # =====================================================================
        # STEP 5: Generate narrative
        # =====================================================================

        put_support = largest_expiry.put_support_strike
        call_resistance = largest_expiry.call_resistance_strike
        max_pain = largest_expiry.max_pain

        header = self._generate_header(market, overall_direction, vol_regime, market_regime)

        # Task G2-B Finding 2: the regime/risk/trade narratives below must
        # describe the SAME GEX value ``gex_for_regime`` (STEP 3) already
        # used to classify vol_regime -- not ``largest_expiry.total_gex``.
        # Before this fix, the vol-regime score correctly preferred the
        # market-wide aggregate GEX (falling back to the largest-OI
        # expiry only when the aggregate is unavailable/zero), while every
        # narrative sentence quoting a "+X.XM GEX" figure was fed the
        # largest-OI expiry's OWN gamma regardless -- on the confirmed
        # live case, the narrative read "+13.7M GEX" (that one expiry's
        # value) in a sentence describing the market's positioning, while
        # the true market-wide aggregate was "+86.8M" (6.3x larger). The
        # text and the number it's allegedly describing must agree.
        regime_narrative = self.narrator.generate_regime_narrative(
            regime=market_regime,
            spot=spot,
            put_support=put_support,
            call_resistance=call_resistance,
            max_pain=max_pain,
            gex_total=gex_for_regime,
            conflict_detail=regime_reason if market_regime == MarketRegime.TRANSITION else "",
            transition_window=self._estimate_transition_window(expiries_sorted),
        )

        near_term_narrative = self._generate_timeframe_section(
            "NEAR-TERM (0-7 DTE)", near_term, spot, near_direction
        )
        mid_term_narrative = self._generate_timeframe_section(
            "MID-TERM (7-30 DTE)", mid_term, spot, overall_direction
        )
        far_term_narrative = self._generate_timeframe_section(
            "FAR-TERM (30+ DTE)", far_term, spot, far_direction
        )

        # Vol assessment — best sell expiry
        sellable_near = [e for e in expiries_sorted if 5 <= e.dte <= 30 and e.total_oi > 2000]
        if not sellable_near:
            # Fallback: nearest meaningful expiry
            sellable_near = [e for e in expiries_sorted if e.dte >= 1 and e.total_oi > 500]
        # Task G2-G: atm_iv is genuinely Optional (vol surface may never
        # have computed for a given expiry) -- max() with a key returning
        # None for some candidates and float for others raises TypeError
        # in Python 3 (None is not orderable against float). Rank a
        # missing reading below every real one instead of crashing, so a
        # real ATM IV is always preferred when at least one candidate has
        # one.
        best_sell_expiry = (
            max(sellable_near, key=lambda e: e.atm_iv if e.atm_iv is not None else float("-inf"))
            if sellable_near else meaningful_near
        )

        # bugfix_spec.md Item 9 fix-review (Important #6): the sub-task 3
        # commit fed these methods the NEGATED risk_reversal_25d (the old
        # put-call sign) so their internal thresholds "kept working" --
        # but that meant this report bundle printed the SAME quantity with
        # TWO CONTRADICTORY signs under the generic label "Skew" (RR25
        # correctly signed in the vol-surface section; "Skew" here still
        # legacy-signed), for a task whose entire point was fixing exactly
        # this kind of sign confusion. Fixed: pass risk_reversal_25d
        # directly; NarrativeGenerator's own thresholds are re-signed to
        # match in the same pass (see each method's docstring).
        vol_narrative = self.narrator.generate_vol_narrative(
            iv_pctile=market.iv_percentile_365d,
            vrp=effective_vrp,
            vrp_adjustment=vrp_adjustment,
            risk_reversal_25d=largest_expiry.risk_reversal_25d,
            sell_expiry=best_sell_expiry.expiry,
            sell_iv=best_sell_expiry.atm_iv,
            buy_expiry=meaningful_far.expiry,
        )

        risk_narrative = self.narrator.generate_risk_factors(
            cone_30d_pctile=market.cone_30d_pctile,
            gex_total=gex_for_regime,
            gamma_rolloff=market.gamma_rolloff,
            funding_8h=market.funding_8h,
            risk_reversal_25d=largest_expiry.risk_reversal_25d,
            spot=spot,
            fragility_multiplier=fragility_multiplier,
            fragility_level=fragility_level,
        )

        # Trade recommendations
        trade_narrative = self.narrator.generate_trade_recommendations(
            regime=market_regime,
            vol_regime=vol_regime,
            iv_pctile=market.iv_percentile_365d,
            risk_reversal_25d=largest_expiry.risk_reversal_25d,
            gex_total=gex_for_regime,
            near_term_expiry=best_sell_expiry.expiry,
            far_term_expiry=meaningful_far.expiry,
            skew_expiry=largest_expiry.expiry,
            # Task Wave-H-B Fix 3: same VRP value driving vol_narrative's
            # sell/buy framing above, so the two sections can't silently
            # disagree -- see generate_trade_recommendations's docstring.
            vrp=effective_vrp,
        )

        # Block trade summary
        block_narrative = self._generate_block_summary(market.blocks, market.large_prints)

        # =====================================================================
        # STEP 6: Assemble final output
        # =====================================================================

        # Task G2-B Finding 1 + Finding 3: a component scored as
        # "insufficient data" must be visibly disclosed in the assembled
        # output, not just quietly weighted to (near-)zero inside an
        # average. `dir_reasons`/`vol_reasons` (classify_direction/
        # classify_vol_regime's own reasoning lists) filter out exactly
        # the zero-score entries this fix introduces, so they can't carry
        # this disclosure -- collect it explicitly instead.
        data_quality_notes: List[str] = []
        if iv_pctile_score[2].startswith("IV percentile: insufficient data"):
            data_quality_notes.append(iv_pctile_score[2])
        if vrp_score[2].startswith("VRP: insufficient data"):
            data_quality_notes.append(vrp_score[2])
        funding_note = next(
            (r for _, w, r in all_direction_scores if r.startswith("Funding: insufficient data") and w == 0.0),
            None,
        )
        if funding_note is not None:
            data_quality_notes.append(funding_note)
        # Wave H follow-up (institutional-benchmark audit P0-4): this block
        # previously inspected ONLY iv_pctile/vrp/funding plus explicitly-
        # thrown exceptions -- it printed "All market-wide sections
        # computed successfully" over a run where every expiry had zero
        # flow trades and max pain never resolved anywhere, because
        # neither of those failure modes touches the three fields checked
        # above. Scoped to signals already computed and available here
        # (per-expiry sufficiency flags, market-wide term structure) --
        # NOT dealer-positioning coverage or the historical-percentile
        # context table, which live in the separate full-report pipeline
        # (report_formatter.py/historical_context_formatter.py) and are
        # not currently plumbed into this synthesis path at all; wiring
        # those in is a larger, separate change, not done here.
        if expiries and all(not e.flow_sufficient_data for e in expiries):
            data_quality_notes.append(
                f"Order flow: insufficient data for all {len(expiries)} expiries "
                "(no qualifying trades in the lookback window) — flow bias/trend "
                "not usable for any expiry this run"
            )
        if expiries and all(not e.max_pain_sufficient_data for e in expiries):
            data_quality_notes.append(
                f"Max pain: did not resolve for any of {len(expiries)} expiries "
                "(displayed max-pain figures are the spot-price fallback, not a "
                "computed strike, and were not scored)"
            )
        if market.term_structure_shape is None:
            data_quality_notes.append(
                "Term structure: insufficient data (fewer than 2 usable expiries) "
                "— not scored"
            )
        if market.failed_sections:
            data_quality_notes.append(
                "Sections that raised an error during calculation (not simply "
                f"unavailable): {', '.join(market.failed_sections)}"
            )

        if data_quality_notes:
            data_quality_block = "DATA QUALITY:\n" + "\n".join(
                f"  - {note}" for note in data_quality_notes
            )
        else:
            data_quality_block = "DATA QUALITY: All market-wide sections computed successfully."

        effective_vrp_display = (
            f"{effective_vrp:+.1f}pts" if effective_vrp is not None else "insufficient data"
        )
        # Task G2-G: risk_reversal_25d can be genuinely None here (largest
        # expiry's vol surface never computed) -- a direct ``:+.1f`` format
        # spec on None raises TypeError, same as every other Optional
        # field already given an "insufficient data" display string above.
        largest_rr25_display = (
            f"{largest_expiry.risk_reversal_25d:+.1f}%"
            if largest_expiry.risk_reversal_25d is not None else "insufficient data"
        )

        synthesis = f"""{header}

{regime_narrative}

{near_term_narrative}

{mid_term_narrative}

{far_term_narrative}

VOL ASSESSMENT: {vol_narrative}

{risk_narrative}

{block_narrative}

TRADE RECOMMENDATIONS:
{trade_narrative}

{data_quality_block}

SCORING DETAIL:
  Direction: {overall_direction.name} (confidence: {dir_confidence:.0%})
  Fragility: {fragility_level}
  Near-term: {near_direction.name} | Far-term: {far_direction.name}
  Vol Regime: {vol_regime.value}
  Market Regime: {market_regime.value}
  Effective VRP: {effective_vrp_display} | RR25: {largest_rr25_display}
"""

        return synthesis

    # -------------------------------------------------------------------------
    # HELPER METHODS
    # -------------------------------------------------------------------------

    def _generate_header(
            self,
            market: MarketWideMetrics,
            direction: Signal,
            vol_regime: VolRegime,
            market_regime: MarketRegime
    ) -> str:
        """
        Generate the dashboard header.

        Task G2-B: every market-wide field interpolated below can now be
        None (genuinely unavailable) -- each gets pre-formatted into a
        safe display string ("N/A") instead of applying a numeric format
        spec directly to a value that might be None (which would raise
        TypeError) or silently substituting 0.0 (which would render as a
        real, confident-looking measurement).
        """
        # Bug fix: safe IV access — find first DTE >= 5 instead of fragile index access
        front_iv = next(
            (v for k, v in sorted(market.iv_by_dte.items()) if k >= 5),
            0.0
        )

        dvol_str = f"{market.dvol:.1f}%" if market.dvol is not None else "N/A"
        iv_pctile_str = f"{market.iv_percentile_365d:.0f}th" if market.iv_percentile_365d is not None else "N/A"
        rv_10d_str = f"{market.rv_10d:.1f}%" if market.rv_10d is not None else "N/A"
        rv_20d_str = f"{market.rv_20d:.1f}%" if market.rv_20d is not None else "N/A"
        rv_30d_str = f"{market.rv_30d:.1f}%" if market.rv_30d is not None else "N/A"
        cone_30d_str = f"{market.cone_30d_pctile:.0f}th cone" if market.cone_30d_pctile is not None else "cone N/A"
        vrp_str = f"{market.vrp:+.1f}pts" if market.vrp is not None else "N/A (insufficient data)"
        funding_rate_str = f"{market.funding_rate:.4f}%" if market.funding_rate is not None else "N/A"
        funding_8h_str = f"{market.funding_8h:.4f}%" if market.funding_8h is not None else "N/A"
        # Task Wave-H-B Fix 2: term_structure_shape/spread_signed are now
        # genuinely Optional (None when fewer than 2 usable expiries) --
        # show "N/A" instead of the old fabricated "CONTANGO (+0.0pts)".
        term_structure_str = (
            f"{market.term_structure_shape} ({market.term_structure_spread_signed:+.1f}pts)"
            if market.term_structure_shape is not None and market.term_structure_spread_signed is not None
            else "N/A (insufficient data)"
        )

        # Task G2-C: naive-local datetime.now() -> explicit UTC, labeled as
        # such in the output (this module is already in
        # tests/conftest.py's frozen-clock list, same as every other
        # naive-local site this task fixed).
        return f"""================================================================================
EXECUTIVE SYNTHESIS — BTC OPTIONS MARKET
Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
================================================================================
BTC ${market.spot_price:,.2f} | Regime: {market_regime.value.upper().replace('_', ' ')}
Direction: {direction.name} | Vol: {vol_regime.value.upper()}
────────────────────────────────────────────────────────────────────────────────
DVOL: {dvol_str}  | IV Pctile: {iv_pctile_str}  | ATM IV (front): ~{front_iv:.1f}%
10d RV: {rv_10d_str}  | 20d RV: {rv_20d_str}  | 30d RV: {rv_30d_str} ({cone_30d_str})
VRP: {vrp_str}  | Term Structure: {term_structure_str}
Perp Funding: {funding_rate_str}  | 8h: {funding_8h_str}
────────────────────────────────────────────────────────────────────────────────"""

    def _generate_timeframe_section(
            self,
            label: str,
            expiries: List[ExpiryMetrics],
            spot: float,
            direction: Signal
    ) -> str:
        """Generate a timeframe section (near/mid/far)."""

        if not expiries:
            return f"{label}: No expiries in this window."

        # Aggregate GEX
        total_gex = sum(e.total_gex for e in expiries)
        total_oi = sum(e.total_oi for e in expiries)
        gex_m = total_gex / 1_000_000

        # Find key levels
        max_pains = [(e.max_pain, e.total_oi) for e in expiries]
        weighted_mp = sum(mp * oi for mp, oi in max_pains) / sum(oi for _, oi in max_pains)

        # Key expiries in this window
        key_expiry = max(expiries, key=lambda e: e.total_oi)

        gex_env = "dampening" if total_gex > 0 else "amplifying"

        lines = [
            f"{label}: {direction.name} bias | GEX {gex_m:+.1f}M ({gex_env}) | "
            f"OI: {total_oi:,} contracts",
        ]

        # Add per-expiry one-liners for the top 2 by OI
        top2 = sorted(expiries, key=lambda e: e.total_oi, reverse=True)[:2]
        for exp in top2:
            # CARRIED FINDING (B2 review, task C1): this expiry's own
            # forward price, not the single global ``spot`` shared across
            # every expiry -- the same defect class bugfix_spec.md Item 7
            # already fixed at the report/formatter layer.
            mp_dist = (exp.max_pain - exp.underlying_price) / exp.underlying_price * 100
            # bugfix_spec.md Item 9 fix-review (Important #6): print the
            # correctly-signed risk_reversal_25d, relabelled "RR25" -- the
            # legacy-signed "Skew" label here contradicted the vol-surface
            # section's own (correct) RR25 sign for the same expiry/quantity.
            #
            # Task G2-G: atm_iv/risk_reversal_25d are genuinely Optional
            # (vol surface may never have computed for this expiry) -- a
            # direct ``:.1f``/``:+.1f`` format spec on None raises
            # TypeError. "N/A" matches this line's own compact-summary
            # convention (same as the header's dvol_str/iv_pctile_str
            # etc.), distinct from the more verbose "insufficient data"
            # phrasing used in prose narratives elsewhere.
            atm_iv_display = f"{exp.atm_iv:.1f}%" if exp.atm_iv is not None else "N/A"
            rr25_display = f"{exp.risk_reversal_25d:+.1f}%" if exp.risk_reversal_25d is not None else "N/A"
            lines.append(
                f"  {exp.expiry} ({exp.dte}d): MaxPain ${exp.max_pain:,.0f} ({mp_dist:+.1f}%) | "
                f"P/C {exp.pc_ratio:.2f} | ATM IV {atm_iv_display} | "
                f"RR25 {rr25_display} | Flow: {exp.flow_bias}"
            )

        return "\n".join(lines)

    def _estimate_transition_window(self, expiries: List[ExpiryMetrics]) -> str:
        """Estimate when the regime transition will complete."""
        # Only consider expiries with meaningful OI — low-OI expiries produce
        # near-zero GEX values whose sign is effectively noise, not a regime signal.
        MIN_OI = 500
        meaningful = [e for e in expiries if e.total_oi >= MIN_OI]

        if len(meaningful) < 2:
            return "unclear — insufficient OI data"

        gex_signs = [(e.expiry, e.dte, e.total_oi, e.total_gex > 0) for e in meaningful]

        for i in range(1, len(gex_signs)):
            if gex_signs[i][3] != gex_signs[i - 1][3]:
                expiry, dte, oi, _ = gex_signs[i]
                return (
                    f"{dte} days "
                    f"(model: GEX sign change at {expiry}, OI {oi:,})"
                )

        return "unclear — monitor GEX evolution"

    def _generate_block_summary(self, blocks: List[dict], large_prints: List[dict]) -> str:
        """
        Summarize institutional block-trade activity.

        institutional_metrics_spec.md section 9 / Migration M2 (Task D1
        review round 2, Important #3): ``blocks`` (grouped by
        ``block_trade_id``, real blocks) is the primary narrative --
        ``large_prints`` (the pre-existing notional-filter list of large
        single-leg screen prints) is narrated separately, explicitly
        labelled "Screen Prints", never conflated with real blocks.
        """
        if not blocks:
            lines = ["INSTITUTIONAL FLOW (Block Trades): none detected in lookback window."]
        else:
            total_premium = sum(b.get('combined_premium_usd', 0) for b in blocks)
            lines = [
                f"INSTITUTIONAL FLOW (Block Trades): {len(blocks)} block(s), "
                f"combined premium ${total_premium / 1e6:.2f}M"
            ]

            largest = max(blocks, key=lambda b: b.get('combined_premium_usd', 0))
            structure = largest.get('combo_id') or "N/A"
            lines.append(
                f"  Largest: {largest.get('block_trade_id', 'N/A')} "
                f"({largest.get('leg_count', 0)} legs, structure: {structure}) "
                f"${largest.get('combined_premium_usd', 0) / 1e6:.2f}M"
            )

        if large_prints:
            buy_prints = [t for t in large_prints if t.get('direction') == 'buy']
            sell_prints = [t for t in large_prints if t.get('direction') == 'sell']

            total_buy_notional = sum(t.get('notional', 0) for t in buy_prints)
            total_sell_notional = sum(t.get('notional', 0) for t in sell_prints)

            lines.append(
                f"SCREEN PRINTS (large single-leg trades, not blocks): "
                f"{len(buy_prints)} buys (${total_buy_notional / 1e6:.1f}M) | "
                f"{len(sell_prints)} sells (${total_sell_notional / 1e6:.1f}M)"
            )

            largest_print = max(large_prints, key=lambda t: t.get('notional', 0))
            largest_print_iv = largest_print.get('iv')
            iv_str = f"{largest_print_iv:.1f}% IV" if largest_print_iv is not None else "N/A IV"
            lines.append(
                f"  Largest: {largest_print.get('instrument', 'N/A')} "
                f"{'BUY' if largest_print.get('direction') == 'buy' else 'SELL'} "
                f"{largest_print.get('size', 0)} BTC "
                f"(${largest_print.get('notional', 0) / 1e6:.2f}M) at "
                f"{iv_str}"
            )

        return "\n".join(lines)


# =============================================================================
# SECTION 6: SYNTHESIS MAPPER
# =============================================================================

class SynthesisMapper:
    """
    Maps a typed OnChainAnalysisResult to SynthesisEngine input dataclasses.

    Bridges the gap between the pipeline's frozen result aggregate
    (refactor_design_spec.md section T7) and the strongly-typed dataclasses
    that SynthesisEngine expects. Reads typed attributes throughout — no
    dict lookups, no calls back into an analyzer.
    """

    @classmethod
    def build_expiry_metrics(
        cls, result: OnChainAnalysisResult, expiration: str
    ) -> Optional[ExpiryMetrics]:
        """
        Build ExpiryMetrics for one expiration from the typed result.

        Returns None if critical data (GEX or instruments) is missing —
        mirrors the legacy dict-based gate exactly (T7: no behavior change).
        """
        instruments = result.parsed_instruments.get(expiration, ())
        if not instruments:
            return None

        bundle = result.bundle(expiration)
        if bundle is None or bundle.gex_dex is None:
            return None

        gex = bundle.gex_dex
        vol = bundle.vol_surface
        flow = bundle.flow

        # Task G2-C: this used to be SynthesisMapper's own duplicate DTE
        # calc (naive-local datetime.now(), local-midnight anchor, and a
        # fabricated 0 -- not None -- on parse failure). Now delegates to
        # the canonical MarketWideCalculator.calculate_dte (08:00 UTC
        # settlement anchor, exact-fractional-days floor, None on parse
        # failure -- clamped to 0 here to match this dataclass's `dte: int`
        # field and this method's own pre-existing None-on-critical-data-
        # missing contract, not because the canonical method fabricates 0
        # itself). result.generated_at is this pipeline run's own already-
        # resolved UTC "now" (OnChainAnalysisBuilder.build() stamps it) --
        # reused here rather than reading a second, independent clock.
        dte = MarketWideCalculator.calculate_dte(expiration, result.generated_at) or 0

        # Total OI and volume from parsed instruments
        total_oi = sum(i.get("open_interest", 0) for i in instruments)
        total_volume = sum(i.get("volume", 0) for i in instruments)
        notional = total_oi * result.underlying_price

        # Max pain and OI P/C ratio — read directly from the analysis result
        # (T7: stops calling analyzer.group_by_strike/calculate_max_pain/
        # calculate_put_call_ratio; the result already carries both).
        #
        # Task Wave-H-B Fix 4: calculate_max_pain returns None when it has
        # nothing to compute from -- the spot-price fallback below stays
        # for DISPLAY (a report line has to show something), but
        # max_pain_sufficient_data travels alongside it so
        # score_max_pain_gravity can tell a real measurement from this
        # fallback and take its own explicit insufficient-data branch
        # instead of scoring the fallback as if it were genuine (see the
        # ExpiryMetrics/score_max_pain_gravity docstrings).
        analysis = bundle.analysis
        max_pain = analysis.max_pain.max_pain_strike
        max_pain_sufficient_data = max_pain is not None
        if max_pain is None:
            max_pain = result.underlying_price

        pc_ratio = analysis.put_call_ratio.ratio
        if pc_ratio == float("inf"):
            pc_ratio = 99.0

        # GEX/DEX
        total_gex = gex.total_net_gex or 0.0
        total_dex = gex.total_net_dex or 0.0
        gex_environment = "Positive" if total_gex >= 0 else "Negative"

        # Task G2-G: GexDexKeyLevels.call_resistance/put_support/hvl are
        # genuinely Optional -- "no identified level in this expiry's
        # strike range", not a level/GEX at strike/value 0.0. Preserve
        # None straight through instead of the old ``... else 0.0``
        # collapse (same defect class G2-B fixed for MarketWideMetrics).
        key_levels = gex.key_levels
        call_res = key_levels.call_resistance
        put_sup = key_levels.put_support

        call_resistance_strike = call_res.strike if call_res is not None else None
        call_resistance_gex = call_res.net_gex if call_res is not None else None
        put_support_strike = put_sup.strike if put_sup is not None else None
        put_support_gex = put_sup.net_gex if put_sup is not None else None
        hvl_strike = key_levels.hvl

        # Vol surface. bugfix_spec.md Item 9: risk_reversal_25d (call -
        # put, market convention) replaces skew_25d (put - call).
        # Task G2-G: ``vol.atm_iv``/``skew.risk_reversal_25d``/
        # ``skew.put_25d_iv``/``skew.call_25d_iv`` are already
        # Optional[float] on their source result models -- None means
        # genuinely unavailable (no ATM instruments found / vol surface
        # never computed for this expiry). Pass the value straight
        # through (accessing an attribute on a None-valued field already
        # yields None) instead of collapsing to a fabricated 0.0.
        atm_iv = vol.atm_iv if vol is not None else None
        skew = vol.skew_25d if vol is not None else None
        risk_reversal_25d = skew.risk_reversal_25d if skew is not None else None
        put_25d_iv = skew.put_25d_iv if skew is not None else None
        call_25d_iv = skew.call_25d_iv if skew is not None else None

        # Wave-H-A (Task 5): pc_atm/pc_near_otm/pc_far_otm propagate
        # MoneynessBucket.ratio's own None (zero instruments in that
        # bucket, or the whole vol surface missing) -- NOT a fabricated
        # 0.0. See this mapper's ExpiryMetrics docstring for why the prior
        # "0.0 when undefined" belief was itself the bug.
        pc_moneyness = vol.pc_by_moneyness if vol is not None else None
        pc_atm = (pc_moneyness.atm.ratio if pc_moneyness is not None else None)
        pc_near_otm = (pc_moneyness.near_otm.ratio if pc_moneyness is not None else None)
        pc_far_otm = (pc_moneyness.far_otm.ratio if pc_moneyness is not None else None)

        # bugfix_spec.md Item 8 fix-review (Important #3, overrules the
        # original B2 sub-task 2 commit): F8.4 states plainly "score_*
        # functions consume the dealer fields" -- a requirement, not a
        # suggestion. score_vanna_charm's own docstring reasoning ("IV
        # drop -> positive vanna = bullish") is dealer-hedging logic, so it
        # must be fed the DEALER exposure (report text and scoring engine
        # now agree on the same book), not the holder-side raw sum. This
        # flips ExpiryMetrics.net_vanna/net_charm's sign relative to the
        # prior sub-task-2 commit and is a real, intentional scoring
        # behavior change -- score_vanna_charm's output can differ from
        # before for the same underlying market state. Covered by
        # test_synthesis.py::TestBuildExpiryMetrics (mapper wiring) and the
        # golden master (re-verified, not assumed).
        #
        # Task G2-G: dealer_vanna_exposure/dealer_charm_exposure are
        # REQUIRED (non-Optional) fields on SecondOrderGreeks whenever a
        # vol surface exists for this expiry -- the only genuinely-missing
        # case is the whole surface being absent (``vol is None``), which
        # now maps to None here instead of a fabricated 0.0 (a value
        # score_vanna_charm treats as its own distinct, real "zero
        # structural drift" signal -- see that method's None branch).
        second_order = vol.second_order_greeks if vol is not None else None
        net_vanna = second_order.dealer_vanna_exposure if second_order is not None else None
        net_charm = second_order.dealer_charm_exposure if second_order is not None else None

        # Flow. bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review):
        # flow_sufficient_data propagates the data-sufficiency gate so the
        # scoring engine can force weight 0 instead of a neutral score.
        flow_bias = flow.bias_interpretation if flow is not None else "Mixed/Neutral"
        flow_trend = flow.flow_trend if flow is not None else "Mixed/Neutral Flow"
        flow_sufficient_data = flow.sufficient_data if flow is not None else False
        top_buy_strikes = (
            [
                {
                    "strike": e.strike, "option_type": e.option_type,
                    "net_flow": e.net_flow, "buy_volume": e.volume, "buy_notional": e.notional,
                }
                for e in flow.top_buy_strikes
            ]
            if flow is not None else []
        )
        top_sell_strikes = (
            [
                {
                    "strike": e.strike, "option_type": e.option_type,
                    "net_flow": e.net_flow, "sell_volume": e.volume, "sell_notional": e.notional,
                }
                for e in flow.top_sell_strikes
            ]
            if flow is not None else []
        )

        return ExpiryMetrics(
            expiry=expiration,
            dte=dte,
            total_oi=int(total_oi),
            notional=notional,
            total_volume=int(total_volume),
            max_pain=float(max_pain),
            pc_ratio=float(pc_ratio),
            # CARRIED FINDING (B2 review, task C1): this expiry's own
            # forward price, sourced the same way Item 7's report_formatter
            # fix does -- bundle.analysis.underlying_price, not the global
            # result.underlying_price used above for notional.
            underlying_price=float(analysis.underlying_price),
            total_gex=total_gex,
            total_dex=total_dex,
            gex_environment=gex_environment,
            # Task G2-G: ``float(None)`` raises TypeError -- every field
            # that can now genuinely be None guards the cast so a missing
            # reading propagates as None, not a crash or a fabricated 0.0.
            call_resistance_strike=(float(call_resistance_strike) if call_resistance_strike is not None else None),
            call_resistance_gex=(float(call_resistance_gex) if call_resistance_gex is not None else None),
            put_support_strike=(float(put_support_strike) if put_support_strike is not None else None),
            put_support_gex=(float(put_support_gex) if put_support_gex is not None else None),
            hvl_strike=(float(hvl_strike) if hvl_strike is not None else None),
            atm_iv=(float(atm_iv) if atm_iv is not None else None),
            risk_reversal_25d=(float(risk_reversal_25d) if risk_reversal_25d is not None else None),
            put_25d_iv=(float(put_25d_iv) if put_25d_iv is not None else None),
            call_25d_iv=(float(call_25d_iv) if call_25d_iv is not None else None),
            pc_atm=(float(pc_atm) if pc_atm is not None else None),
            pc_near_otm=(float(pc_near_otm) if pc_near_otm is not None else None),
            pc_far_otm=(float(pc_far_otm) if pc_far_otm is not None else None),
            net_vanna=(float(net_vanna) if net_vanna is not None else None),
            net_charm=(float(net_charm) if net_charm is not None else None),
            flow_bias=flow_bias,
            flow_trend=flow_trend,
            top_buy_strikes=top_buy_strikes,
            top_sell_strikes=top_sell_strikes,
            flow_sufficient_data=flow_sufficient_data,
            max_pain_sufficient_data=max_pain_sufficient_data,
        )

    @staticmethod
    def build_market_wide(result: OnChainAnalysisResult) -> MarketWideMetrics:
        """Build MarketWideMetrics from the typed OnChainAnalysisResult.market_wide."""
        mw = result.market_wide

        # RV values from calculator are decimals (e.g. 0.585 = 58.5%).
        # dvol and vrp are in percentage points (e.g. 58.7, -7.6).
        # Multiply RV by 100 here so all vol fields share the same scale.
        #
        # Task G2-B: `rv is None` means the realized-volatility calculation
        # never ran (MarketWideOrchestrator._calculate_realized_volatility
        # returned None) -- genuinely missing, not a measured 0.0. Preserve
        # None so score_vrp's None branch (not a fabricated-zero branch)
        # fires downstream.
        rv = mw.realized_volatility
        rv_10d = (rv.rv_10d * 100) if rv is not None else None
        rv_20d = (rv.rv_20d * 100) if rv is not None else None
        rv_30d = (rv.rv_30d * 100) if rv is not None else None

        # API funding values are also decimals (e.g. -0.000201 = -0.0201%).
        # Multiply by 100 so score_funding thresholds (5/10/20%) work correctly.
        #
        # Task G2-B: two independent None cases, both genuinely "no data",
        # neither a measured 0.0 -- (1) `funding is None`: the whole
        # perpetual-funding phase never ran; (2) `funding.funding_rate`/
        # `funding.funding_8h` is None even though `funding` exists: the
        # ticker itself returned no reading for that specific field
        # (PerpetualFundingResult declares both Optional). The old
        # `(funding.funding_rate or 0.0) if funding is not None else 0.0`
        # collapsed BOTH cases into a fabricated zero.
        funding = mw.perpetual_funding
        funding_rate = (
            funding.funding_rate * 100
            if funding is not None and funding.funding_rate is not None
            else None
        )
        funding_8h = (
            funding.funding_8h * 100
            if funding is not None and funding.funding_8h is not None
            else None
        )

        # Task Wave-H-B Fix 2: term_structure is genuinely Optional --
        # ``ts is None`` means fewer than 2 usable per-expiry ATM IVs
        # (the whole computation never ran), NOT a measured "CONTANGO".
        # The calculator's own ``shape`` field (CONTANGO/BACKWARDATION/
        # FLAT) is already authoritative when ts is not None -- pass it
        # through unchanged instead of "normalizing" every non-CONTANGO/
        # BACKWARDATION reading (including real FLAT readings and small
        # genuinely-backwardated tilts) into a fabricated "CONTANGO".
        # That old normalize-to-CONTANGO logic is exactly what produced
        # self-contradictory output like "CONTANGO (-1.8pts)" -- the
        # signed spread (below) carried the true sign while the shape
        # label always said CONTANGO regardless.
        ts = mw.term_structure
        if ts is not None:
            ts_shape = ts.shape
            ts_spread = ts.spread
        else:
            ts_shape = None
            ts_spread = None

        # futures_basis: trust insertion order from the source (DTE-ascending,
        # per synthesis_logic.md:112) — Optional[float] values pass through
        # unchanged (Decision D12; score_futures_basis's caller filters None).
        futures_basis = dict(mw.futures_basis.futures_basis) if mw.futures_basis is not None else {}

        # Aggregate GEX/DEX
        agg_gex = mw.aggregate_gex_dex
        if agg_gex is not None:
            agg_key_levels = agg_gex.key_levels
            aggregate_total_gex = agg_gex.total_net_gex or 0.0
            aggregate_total_dex = agg_gex.total_net_dex or 0.0
            aggregate_call_resistance = (
                {"strike": agg_key_levels.call_resistance.strike,
                 "net_gex": agg_key_levels.call_resistance.net_gex}
                if agg_key_levels.call_resistance is not None else None
            )
            aggregate_put_support = (
                {"strike": agg_key_levels.put_support.strike,
                 "net_gex": agg_key_levels.put_support.net_gex}
                if agg_key_levels.put_support is not None else None
            )
            aggregate_hvl = agg_key_levels.hvl
        else:
            aggregate_total_gex = 0.0
            aggregate_total_dex = 0.0
            aggregate_call_resistance = None
            aggregate_put_support = None
            aggregate_hvl = None

        # institutional_metrics_spec.md section 9 / Migration M2 (Task D1
        # review round 2, Important #3): `blocks` (real, block_trade_id-
        # grouped) and `large_prints` (the pre-existing notional-filter
        # list) are wired separately -- large_prints already excludes any
        # trade with a block_trade_id (see MarketWideCalculator.
        # detect_block_trades), so the two never conflate or double-count.
        blocks = [
            {
                "block_trade_id": b.block_trade_id, "leg_count": b.leg_count,
                "observed_leg_count": b.observed_leg_count, "combo_id": b.combo_id,
                "combined_premium_usd": b.combined_premium_usd,
                "total_amount": b.total_amount, "instruments": b.instruments,
                "timestamp": b.timestamp,
            }
            for b in (mw.block_trades.blocks if mw.block_trades is not None else ())
        ]
        large_prints = [
            {
                "timestamp": t.timestamp, "instrument": t.instrument_name,
                "size": t.amount, "amount": t.amount, "direction": t.direction,
                "notional": t.notional, "iv": t.implied_volatility,
            }
            for t in (mw.block_trades.trades if mw.block_trades is not None else ())
        ]

        corr = mw.cross_asset_correlation

        return MarketWideMetrics(
            spot_price=mw.spot_price or result.underlying_price,
            # Task G2-B (BLOCKER): `mw.dvol`/`mw.iv_percentile_365d` are
            # already Optional[float] on MarketWideResult -- None means
            # genuinely unavailable. `mw.dvol or 0.0` used to collapse
            # that None (and a legitimate 0.0) into the same fabricated
            # zero; pass the value straight through so downstream scorers
            # see the real None and take their explicit insufficient-data
            # branch instead of scoring a phantom extreme.
            dvol=mw.dvol,
            iv_percentile_365d=mw.iv_percentile_365d,
            funding_rate=funding_rate,
            funding_8h=funding_8h,
            term_structure_shape=ts_shape,
            term_structure_spread=ts_spread,
            # Task Wave-H-B Fix 2: preserve None (was `... or 0.0`,
            # which fabricated a measured-looking 0.0 spread whenever
            # ts was None OR ts.spread_signed was genuinely 0.0).
            term_structure_spread_signed=ts.spread_signed if ts is not None else None,
            iv_by_dte=dict(ts.iv_by_dte) if ts is not None else {},
            rv_10d=rv_10d,
            rv_20d=rv_20d,
            rv_30d=rv_30d,
            # None exactly when the VRP calculation never ran (dvol or
            # 30d RV unavailable) -- not a measured 0.0.
            vrp=(mw.variance_risk_premium.vrp if mw.variance_risk_premium is not None else None),
            # `.get(window)` (no default) so a window missing from the
            # dict -- OR the whole `volatility_cone` result being None --
            # both yield None, never a fabricated 0th percentile.
            cone_10d_pctile=(
                mw.volatility_cone.percentile_by_window.get(10)
                if mw.volatility_cone is not None else None
            ),
            cone_20d_pctile=(
                mw.volatility_cone.percentile_by_window.get(20)
                if mw.volatility_cone is not None else None
            ),
            cone_30d_pctile=(
                mw.volatility_cone.percentile_by_window.get(30)
                if mw.volatility_cone is not None else None
            ),
            futures_basis=futures_basis,
            # None exactly when the perpetual-funding phase never ran --
            # not a measured zero open interest.
            perp_oi=(funding.perp_open_interest if funding is not None else None),
            perp_funding_trend=(funding.funding_trend if funding is not None else "Stable"),
            # CrossAssetCorrelationResult.price_correlation/dvol_correlation
            # are already Optional[float] (None on insufficient sample) --
            # pass through unchanged instead of collapsing to 0.0, same as
            # `corr is None` (the whole correlation phase never ran).
            btc_eth_price_corr=(
                corr.price_correlation if corr is not None else None
            ),
            btc_eth_dvol_corr=(
                corr.dvol_correlation if corr is not None else None
            ),
            blocks=blocks,
            large_prints=large_prints,
            aggregate_total_gex=aggregate_total_gex,
            aggregate_total_dex=aggregate_total_dex,
            aggregate_call_resistance=aggregate_call_resistance,
            aggregate_put_support=aggregate_put_support,
            aggregate_hvl=aggregate_hvl,
            # Task Wave-I-A Fix 1: same "already computed elsewhere,
            # thread it through unchanged" pattern as failed_sections
            # below -- MarketWideResult.gamma_rolloff already carries the
            # GAMMA CLIFF flag/rows; no re-computation here.
            gamma_rolloff=mw.gamma_rolloff,
            # Task G2-B Finding 3: thread the orchestrator's real
            # failed-sections list through instead of dropping it on the
            # floor -- MarketWideResult already carries it correctly.
            failed_sections=tuple(mw.failed_sections),
        )

    @classmethod
    def build_all(cls, result: OnChainAnalysisResult) -> Tuple[MarketWideMetrics, List[ExpiryMetrics]]:
        """Build complete input for SynthesisEngine from a typed OnChainAnalysisResult."""
        market = cls.build_market_wide(result)
        expiries = [
            m for exp in result.expiration_names()
            if (m := cls.build_expiry_metrics(result, exp)) is not None
        ]
        return market, expiries


# =============================================================================
# SECTION 7: EXAMPLE USAGE WITH CURRENT DATA
# =============================================================================

def build_from_current_data():
    """
    Example: Build synthesis from the current report data.

    In production, you'd parse this from your report output.
    This shows how to wire up the data structures.
    """

    # Market-wide metrics
    market = MarketWideMetrics(
        spot_price=65707.65,
        dvol=52.83,
        iv_percentile_365d=87.2,
        funding_rate=0.0000,
        funding_8h=-0.0017,
        term_structure_shape="CONTANGO",
        term_structure_spread=20.0,
        iv_by_dte={
            0: 30.3, 1: 25.6, 2: 37.2, 3: 43.2, 6: 49.1,
            13: 49.0, 20: 49.3, 27: 49.2, 55: 48.0, 90: 48.2,
            118: 48.7, 209: 49.8, 300: 50.3
        },
        rv_10d=50.8,
        rv_20d=46.8,
        rv_30d=64.6,
        vrp=-11.8,
        cone_10d_pctile=69.0,
        cone_20d_pctile=55.0,
        cone_30d_pctile=99.0,
        futures_basis={
            "6MAR26": -1.7, "13MAR26": 0.2, "27MAR26": 1.1,
            "24APR26": 1.9, "26JUN26": 2.6, "25SEP26": 3.2, "25DEC26": 3.7
        },
        perp_oi=1_083_530_970,
        perp_funding_trend="Stable",
        btc_eth_price_corr=0.93,
        btc_eth_dvol_corr=0.93,
        # institutional_metrics_spec.md section 9 / Migration M2 (Task D1
        # review round 2): this hardcoded example data predates block_trade_id
        # capture entirely, so these are large single-leg screen prints, not
        # real blocks -- `blocks` intentionally has no example data here.
        large_prints=[
            {"instrument": "BTC-6MAR26-50000-P", "size": 30.0, "direction": "sell",
             "notional": 1_968_044, "iv": 101.7},
            {"instrument": "BTC-24APR26-77000-C", "size": 20.0, "direction": "buy",
             "notional": 1_312_848, "iv": 44.8},
            {"instrument": "BTC-25DEC26-190000-C", "size": 20.0, "direction": "sell",
             "notional": 1_311_412, "iv": 53.8},
            {"instrument": "BTC-24APR26-76000-C", "size": 36.0, "direction": "buy",
             "notional": 2_363_120, "iv": 44.7},
            {"instrument": "BTC-3MAR26-60000-P", "size": 54.0, "direction": "buy",
             "notional": 3_541_829, "iv": 66.7},
            {"instrument": "BTC-20MAR26-64000-P", "size": 16.4, "direction": "buy",
             "notional": 1_070_063, "iv": 51.8},
        ]
    )

    # Key expiries (abbreviated — in production, parse all from report)
    expiries = [
        ExpiryMetrics(
            expiry="28FEB26", dte=0, total_oi=6181,
            notional=406_145_555, max_pain=66000, pc_ratio=2.39,
            underlying_price=65707.65,

            total_gex=-12_402_566, total_dex=-390.66,
            gex_environment="Negative",
            call_resistance_strike=66000, call_resistance_gex=265785,
            put_support_strike=65000, put_support_gex=-4_578_703,
            hvl_strike=66000,
            atm_iv=30.3, risk_reversal_25d=-11.7, put_25d_iv=37.3, call_25d_iv=25.6,

            pc_atm=2.60, pc_near_otm=2.37, pc_far_otm=0.0,
            net_vanna=0.000062, net_charm=59.96,
            flow_bias="Heavy Buying", flow_trend="Decelerating Buy Pressure",
        ),
        ExpiryMetrics(
            expiry="6MAR26", dte=6, total_oi=23883,
            notional=1_569_282_663, max_pain=67000, pc_ratio=1.23,
            underlying_price=65707.65,

            total_gex=-7_885_127, total_dex=-1496.14,
            gex_environment="Negative",
            call_resistance_strike=70000, call_resistance_gex=2_263_058,
            put_support_strike=58000, put_support_gex=-4_456_432,
            hvl_strike=65500,
            atm_iv=49.1, risk_reversal_25d=-8.9, put_25d_iv=55.3, call_25d_iv=46.5,

            pc_atm=2.45, pc_near_otm=0.82, pc_far_otm=1.72,
            net_vanna=0.000349, net_charm=93.19,
            flow_bias="Heavy Buying", flow_trend="Reversing to Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="13MAR26", dte=13, total_oi=8785,
            notional=577_248_276, max_pain=66000, pc_ratio=0.93,
            underlying_price=65707.65,

            total_gex=-13_297, total_dex=-146.97,
            gex_environment="Negative",
            call_resistance_strike=75000, call_resistance_gex=1_200_177,
            put_support_strike=55000, put_support_gex=-1_147_244,
            hvl_strike=66000,
            atm_iv=49.0, risk_reversal_25d=-10.4, put_25d_iv=57.2, call_25d_iv=46.8,

            pc_atm=1.44, pc_near_otm=0.60, pc_far_otm=1.75,
            net_vanna=0.000179, net_charm=22.69,
            flow_bias="Moderate Buying", flow_trend="Reversing to Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="27MAR26", dte=27, total_oi=149488,
            notional=9_822_511_753, max_pain=80000, pc_ratio=0.70,
            underlying_price=65707.65,

            total_gex=-14_812_074, total_dex=-26914.66,
            gex_environment="Negative",
            call_resistance_strike=80000, call_resistance_gex=3_116_454,
            put_support_strike=60000, put_support_gex=-6_600_975,
            hvl_strike=67000,
            atm_iv=49.2, risk_reversal_25d=-9.2, put_25d_iv=55.8, call_25d_iv=46.7,

            pc_atm=1.38, pc_near_otm=1.81, pc_far_otm=0.51,
            net_vanna=0.001561, net_charm=94.23,
            flow_bias="Heavy Selling", flow_trend="Decelerating Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="24APR26", dte=55, total_oi=39117,
            notional=2_570_273_003, max_pain=70000, pc_ratio=0.68,
            underlying_price=65707.65,

            total_gex=3_072_631, total_dex=-1179.31,
            gex_environment="Positive",
            call_resistance_strike=75000, call_resistance_gex=2_095_712,
            put_support_strike=60000, put_support_gex=-3_372_438,
            hvl_strike=84000,
            atm_iv=48.0, risk_reversal_25d=-8.6, put_25d_iv=53.9, call_25d_iv=45.3,

            pc_atm=2.00, pc_near_otm=0.95, pc_far_otm=0.41,
            net_vanna=0.000944, net_charm=27.06,
            flow_bias="Heavy Buying", flow_trend="Mixed/Neutral Flow",
        ),
        ExpiryMetrics(
            expiry="26JUN26", dte=118, total_oi=70893,
            notional=4_658_212_431, max_pain=85000, pc_ratio=0.91,
            underlying_price=65707.65,

            total_gex=-8_447_524, total_dex=-11001.86,
            gex_environment="Negative",
            call_resistance_strike=90000, call_resistance_gex=489_690,
            put_support_strike=60000, put_support_gex=-2_930_370,
            hvl_strike=72000,
            atm_iv=48.7, risk_reversal_25d=-7.4, put_25d_iv=53.7, call_25d_iv=46.3,

            pc_atm=1.54, pc_near_otm=3.37, pc_far_otm=0.64,
            net_vanna=0.001305, net_charm=17.56,
            flow_bias="Moderate Selling", flow_trend="Steady Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="25DEC26", dte=300, total_oi=45475,
            notional=2_988_048_812, max_pain=80000, pc_ratio=0.60,
            underlying_price=65707.65,

            total_gex=2_890_951, total_dex=352.39,
            gex_environment="Positive",
            call_resistance_strike=120000, call_resistance_gex=2_422_244,
            put_support_strike=60000, put_support_gex=-1_777_383,
            hvl_strike=120000,
            atm_iv=50.3, risk_reversal_25d=-4.8, put_25d_iv=52.8, call_25d_iv=48.0,
            pc_atm=1.01, pc_near_otm=5.94, pc_far_otm=0.41,
            net_vanna=0.000954, net_charm=5.22,
            flow_bias="Moderate Selling", flow_trend="Accelerating Sell Pressure",
        ),
    ]

    # Run synthesis
    engine = SynthesisEngine()
    summary = engine.run(market, expiries)

    return summary


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    summary = build_from_current_data()
    print(summary)
