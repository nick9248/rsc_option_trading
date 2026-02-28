"""
ALGORITHMIC SYNTHESIS ENGINE
=============================
Converts quantitative on-chain metrics into an institutional-grade
executive summary using a rule-based scoring and narrative generation system.

Architecture:
    Raw Metrics → Scoring Engine → Regime Classification → Narrative Templates → Executive Summary

Author: Nick (Wuppertal University / Institutional Options Desk)
Version: 1.1
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# =============================================================================
# SECTION 1: DATA STRUCTURES
# =============================================================================

class Signal(Enum):
    """Directional signal strength"""
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
    RISK_OFF = "risk_off"  # Bearish + elevated vol
    RANGE_BOUND = "range_bound"  # Neutral direction + suppressed vol
    TRENDING_UP = "trending_up"  # Bullish + normal/suppressed vol
    TRENDING_DOWN = "trending_down"  # Bearish + normal vol
    VOLATILE_BULLISH = "vol_bullish"  # Bullish + elevated/explosive vol
    VOLATILE_BEARISH = "vol_bearish"  # Bearish + elevated/explosive vol
    TRANSITION = "transition"  # Conflicting signals across timeframes


@dataclass
class ExpiryMetrics:
    """Parsed metrics for a single expiry"""
    expiry: str
    dte: int
    total_oi: int
    notional: float
    max_pain: float
    pc_ratio: float
    volume_pc_ratio: float

    # GEX/DEX
    total_gex: float
    total_dex: float
    gex_environment: str  # "Positive" or "Negative"
    call_resistance_strike: float
    call_resistance_gex: float
    put_support_strike: float
    put_support_gex: float
    hvl_strike: float  # Zero gamma level

    # Volatility surface
    atm_iv: float
    skew_25d: float  # put IV - call IV
    put_25d_iv: float
    call_25d_iv: float
    vwap_iv: float
    mark_iv: float

    # Moneyness P/C
    pc_atm: float
    pc_near_otm: float
    pc_far_otm: float

    # Second-order Greeks
    net_vanna: float
    net_charm: float

    # Flow
    flow_bias: str  # "Heavy Buying", "Moderate Selling", etc.
    flow_trend: str
    top_buy_strikes: List[dict] = field(default_factory=list)
    top_sell_strikes: List[dict] = field(default_factory=list)

    # OI changes
    large_oi_changes: List[dict] = field(default_factory=list)


@dataclass
class MarketWideMetrics:
    """Parsed market-wide metrics"""
    spot_price: float
    dvol: float
    iv_percentile_365d: float
    funding_rate: float
    funding_8h: float

    # Term structure
    term_structure_shape: str  # "CONTANGO" or "BACKWARDATION"
    term_structure_spread: float  # abs pts — used for scoring
    term_structure_spread_signed: float = 0.0  # signed pts (back - front) — used for display
    iv_by_dte: Dict[int, float] = field(default_factory=dict)

    # Realized vol
    rv_10d: float = 0.0
    rv_20d: float = 0.0
    rv_30d: float = 0.0

    # VRP
    vrp: float = 0.0

    # Vol cone
    cone_10d_pctile: float = 0.0
    cone_20d_pctile: float = 0.0
    cone_30d_pctile: float = 0.0

    # Futures basis
    futures_basis: Dict[str, float] = field(default_factory=dict)

    # Perp
    perp_oi: float = 0.0
    perp_funding_trend: str = "Stable"

    # Cross-asset
    btc_eth_price_corr: float = 0.0
    btc_eth_dvol_corr: float = 0.0

    # Block trades
    block_trades: List[dict] = field(default_factory=list)


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
    def score_pc_ratio(pc_ratio: float) -> Tuple[float, float, str]:
        """
        Put/Call ratio interpretation.

        Thresholds (OI-based):
            < 0.60  → Strong Bullish (+2)   — extreme call dominance
            0.60-0.80 → Bullish (+1)        — moderate call lean
            0.80-1.00 → Neutral (0)         — balanced
            1.00-1.30 → Bearish (-1)        — moderate put lean
            > 1.30  → Strong Bearish (-2)   — extreme hedging / fear

        IMPORTANT NUANCE: Very high P/C (>2.0) in short-dated expiries
        often signals hedging, not directional bearishness. The weight
        should decrease for 0-3 DTE expiries with extreme P/C.
        """
        if pc_ratio < 0.60:
            return (2.0, 0.7, f"P/C {pc_ratio:.2f}: Extreme call dominance")
        elif pc_ratio < 0.80:
            return (1.0, 0.7, f"P/C {pc_ratio:.2f}: Bullish call lean")
        elif pc_ratio < 1.00:
            return (0.0, 0.5, f"P/C {pc_ratio:.2f}: Balanced")
        elif pc_ratio < 1.30:
            return (-1.0, 0.7, f"P/C {pc_ratio:.2f}: Moderate put lean")
        else:
            return (-2.0, 0.7, f"P/C {pc_ratio:.2f}: Extreme hedging/fear")

    @staticmethod
    def score_gex(total_gex: float, spot: float) -> Tuple[float, float, str]:
        """
        Gamma Exposure interpretation.

        GEX doesn't give direction — it gives REGIME.
        Positive GEX → dealers dampen moves → range-bound (neutral score)
        Negative GEX → dealers amplify moves → breakout likely

        The directional signal comes from DEX, not GEX.
        GEX magnitude relative to notional matters.

        Thresholds (absolute, BTC-calibrated):
            > +5M  → Strong dampening    (score: 0, high confidence range)
            > +1M  → Moderate dampening   (score: 0, moderate confidence)
            ±1M    → Neutral gamma        (score: 0, low confidence)
            < -1M  → Moderate amplifying  (score: 0, but flag breakout risk)
            < -5M  → Strong amplifying    (score: 0, HIGH breakout risk)
        """
        gex_millions = total_gex / 1_000_000

        if gex_millions > 5:
            return (0.0, 0.3, f"GEX +{gex_millions:.1f}M: Strong dampening — range likely")
        elif gex_millions > 1:
            return (0.0, 0.3, f"GEX +{gex_millions:.1f}M: Moderate dampening")
        elif gex_millions > -1:
            return (0.0, 0.2, f"GEX {gex_millions:.1f}M: Neutral gamma")
        elif gex_millions > -5:
            return (0.0, 0.3, f"GEX {gex_millions:.1f}M: Amplifying — breakout risk")
        else:
            return (0.0, 0.4, f"GEX {gex_millions:.1f}M: EXTREME amplification — explosive move likely")

    @staticmethod
    def score_dex(total_dex: float) -> Tuple[float, float, str]:
        """
        Delta Exposure interpretation.

        DEX represents net dealer delta — the directional pressure
        from options market makers' hedging activity.

        Positive DEX → dealers are net long → bullish hedging pressure
        Negative DEX → dealers are net short → bearish hedging pressure

        Thresholds (BTC-calibrated):
            > +500   → Strong bullish pressure (+2)
            > +100   → Moderate bullish (+1)
            ±100     → Neutral (0)
            < -100   → Moderate bearish (-1)
            < -500   → Strong bearish (-2)

        For very large expiries (>50K OI), scale thresholds up by 5x.
        """
        if total_dex > 500:
            return (2.0, 0.8, f"DEX +{total_dex:.0f}: Strong bullish dealer pressure")
        elif total_dex > 100:
            return (1.0, 0.8, f"DEX +{total_dex:.0f}: Moderate bullish pressure")
        elif total_dex > -100:
            return (0.0, 0.5, f"DEX {total_dex:.0f}: Neutral dealer delta")
        elif total_dex > -500:
            return (-1.0, 0.8, f"DEX {total_dex:.0f}: Moderate bearish pressure")
        else:
            return (-2.0, 0.8, f"DEX {total_dex:.0f}: Strong bearish dealer pressure")

    @staticmethod
    def score_max_pain_gravity(max_pain: float, spot: float) -> Tuple[float, float, str]:
        """
        Max pain pull interpretation.

        Max pain exerts gravitational pull on price, especially for
        large expiries within 7 DTE. The effect weakens with:
        - Time to expiry (>14 DTE: minimal effect)
        - Distance from spot (>10%: unlikely to reach)
        - OI size (small expiry: weak pull)

        Score based on direction of pull:
            Max pain > spot + 5% → Bullish pull (+1 to +2)
            Max pain within ±5%  → Neutral (weak pull)
            Max pain < spot - 5% → Bearish pull (-1 to -2)
        """
        distance_pct = (max_pain - spot) / spot * 100

        if distance_pct > 10:
            return (2.0, 0.4, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% above spot — strong upward pull")
        elif distance_pct > 5:
            return (1.0, 0.4, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% above — moderate pull up")
        elif distance_pct > -5:
            # Reduce weight when max pain is near spot — less informative
            return (0.0, 0.2, f"Max pain ${max_pain:,.0f} is near spot ({distance_pct:+.1f}%)")
        elif distance_pct > -10:
            return (-1.0, 0.4, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% below — pull down")
        else:
            return (-2.0, 0.4, f"Max pain ${max_pain:,.0f} is {distance_pct:+.1f}% below — strong downward pull")

    @staticmethod
    def score_funding(funding_rate: float, funding_8h: float) -> Tuple[float, float, str]:
        """
        Funding rate interpretation.

        Positive funding → longs pay shorts → crowded long → mildly bearish
        Negative funding → shorts pay longs → crowded short → mildly bullish
        Zero → balanced

        Thresholds (annualized):
            > 20%  → Extremely crowded long (-2)
            > 10%  → Crowded long (-1)
            ±5%    → Neutral (0)
            < -10% → Crowded short (+1)
            < -20% → Extremely crowded short (+2)
        """
        # Bug fix: use funding_8h (8h rate) for the annualized calculation
        # funding_8h × 3 periods/day × 365 days = annualized rate
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

        base = bias_map.get(flow_bias, 0.0)
        adjustment = trend_map.get(flow_trend, 0.0)
        score = max(-2.0, min(2.0, base + adjustment))

        return (score, 0.6, f"Flow: {flow_bias} + {flow_trend} → net {score:+.1f}")

    @staticmethod
    def score_vanna_charm(net_vanna: float, net_charm: float) -> Tuple[float, float, str]:
        """
        Second-order Greeks interpretation.

        Vanna: How delta changes with IV.
            Positive vanna + IV dropping → dealers buy underlying (bullish)
            Positive vanna + IV rising  → dealers sell underlying (bearish)
            Since we're in high IV percentile (likely to mean-revert down),
            positive vanna is bullish.

        Charm: How delta changes with time.
            Positive charm → time decay pushes dealer delta positive (bullish drift)
            Negative charm → time decay pushes dealer delta negative (bearish drift)

        These are low-weight but persistent signals.
        """
        vanna_signal = 1.0 if net_vanna > 0 else -1.0
        charm_signal = 1.0 if net_charm > 0 else -1.0

        # Average the two, weight low
        combined = (vanna_signal + charm_signal) / 2

        vanna_dir = "bullish" if net_vanna > 0 else "bearish"
        charm_dir = "bullish" if net_charm > 0 else "bearish"

        return (combined, 0.3, f"Vanna {vanna_dir} + Charm {charm_dir} → structural drift {combined:+.1f}")

    @staticmethod
    def score_futures_basis(basis_front: float, basis_back: float) -> Tuple[float, float, str]:
        """
        Futures basis interpretation.

        Contango (positive basis) → market expects higher prices → bullish
        Backwardation (negative basis) → stress, forced selling → bearish

        Thresholds (annualized premium):
            > 10%  → Strong contango, bullish structural demand (+2)
            > 5%   → Moderate contango (+1)
            ±2%    → Flat (0)
            < -2%  → Mild backwardation (-1)
            < -5%  → Strong backwardation, stress (-2)
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
    def score_iv_percentile(iv_pctile: float) -> Tuple[float, float, str]:
        """
        IV Percentile interpretation for vol regime.

        Score represents vol richness (positive = expensive, negative = cheap):
            > 90th → Extremely expensive (+2) — strong sell vol
            > 75th → Expensive (+1)           — sell vol
            25-75th → Normal (0)              — neutral
            < 25th → Cheap (-1)               — buy vol
            < 10th → Extremely cheap (-2)     — strong buy vol
        """
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
    def score_vrp(vrp: float, rv_10d: float, rv_20d: float, rv_30d: float,
                  cone_30d_pctile: float) -> Tuple[float, float, str]:
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
        """
        # Check if 30d RV is at extreme (stale signal)
        use_forward = cone_30d_pctile > 85 or cone_30d_pctile < 15

        if use_forward:
            forward_rv = (rv_10d + rv_20d) / 2
            # Recalculate forward VRP using DVOL implied from context
            # We approximate: DVOL ≈ VRP + rv_30d
            dvol_approx = vrp + rv_30d
            forward_vrp = dvol_approx - forward_rv
            effective_vrp = forward_vrp
            stale_note = (f"30d RV at {cone_30d_pctile:.0f}th pctile (STALE). "
                          f"Forward VRP using 10d/20d avg: {forward_vrp:+.1f}pts")
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
    def score_skew(skew_25d: float) -> Tuple[float, float, str]:
        """
        25-Delta Skew interpretation.

        Positive skew → puts more expensive than calls → hedging demand

        Thresholds:
            > 12%  → Extreme fear/hedging (+2 vol score, skew trades attractive)
            > 8%   → Heavy hedging demand (+1)
            4-8%   → Normal hedging (0)
            < 4%   → Complacent (-1)
            < 0%   → Inverted skew (-2, rare, extremely bullish or complacent)
        """
        if skew_25d > 12:
            return (2.0, 0.6, f"Skew {skew_25d:+.1f}%: Extreme put demand — fear elevated")
        elif skew_25d > 8:
            return (1.0, 0.6, f"Skew {skew_25d:+.1f}%: Heavy hedging demand")
        elif skew_25d > 4:
            return (0.0, 0.4, f"Skew {skew_25d:+.1f}%: Normal")
        elif skew_25d > 0:
            return (-1.0, 0.5, f"Skew {skew_25d:+.1f}%: Complacent — puts relatively cheap")
        else:
            return (-2.0, 0.6, f"Skew {skew_25d:+.1f}%: Inverted — extremely unusual")

    @staticmethod
    def score_term_structure(shape: str, spread: float,
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
        """
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
        else:
            if spread > 10:
                return (-2.0, 0.6,
                        f"Backwardation -{spread:.0f}pts: Extreme near-term fear — sell front month{kink_note}")
            elif spread > 5:
                return (-1.0, 0.5, f"Backwardation -{spread:.0f}pts: Near-term stress{kink_note}")
            else:
                return (0.0, 0.3, f"Backwardation -{spread:.0f}pts: Mild{kink_note}")

    @staticmethod
    def score_vwap_vs_mark(vwap_iv: float, mark_iv: float) -> Tuple[float, float, str]:
        """
        VWAP IV vs Mark IV interpretation.

        VWAP < Mark → Sellers aggressive (filling below mid) → bearish flow
        VWAP > Mark → Buyers aggressive (paying above mid) → bullish flow

        Thresholds:
            Diff > +5%  → Strong buyer urgency
            Diff > +2%  → Moderate buyer pressure
            ±2%         → Balanced
            Diff < -2%  → Moderate seller pressure
            Diff < -5%  → Strong seller urgency
        """
        diff = vwap_iv - mark_iv

        if diff > 5:
            return (1.0, 0.4, f"VWAP-Mark {diff:+.1f}%: Buyers paying up aggressively")
        elif diff > 2:
            return (0.5, 0.3, f"VWAP-Mark {diff:+.1f}%: Moderate buyer urgency")
        elif diff > -2:
            return (0.0, 0.2, f"VWAP-Mark {diff:+.1f}%: Balanced flow")
        elif diff > -5:
            return (-0.5, 0.3, f"VWAP-Mark {diff:+.1f}%: Sellers aggressive")
        else:
            return (-1.0, 0.4, f"VWAP-Mark {diff:+.1f}%: Strong seller urgency")


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
        Confidence = weighted score magnitude / max possible magnitude
        """
        if not scores:
            return (Signal.NEUTRAL, 0.0, ["No directional data"])

        weighted_sum = sum(s[0] * s[1] for s in scores)
        total_weight = sum(s[1] for s in scores)

        if total_weight == 0:
            return (Signal.NEUTRAL, 0.0, ["No weighted data"])

        avg_score = weighted_sum / total_weight
        confidence = abs(avg_score) / 2.0  # Normalize to 0-1

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
            skew_score: float
    ) -> Tuple[VolRegime, List[str]]:
        """
        Classify volatility regime from vol-related scores.

        Decision tree:
            1. If GEX strongly positive AND IV low → SUPPRESSED
            2. If GEX negative AND IV high AND skew extreme → EXPLOSIVE
            3. If IV high but GEX positive → ELEVATED (capped upside on vol)
            4. Otherwise → NORMAL
        """
        gex_millions = gex_total / 1_000_000
        reasons = []

        if gex_millions > 2 and iv_pctile_score <= 0:
            regime = VolRegime.SUPPRESSED
            reasons.append(f"Positive GEX ({gex_millions:+.1f}M) + low IV → Volatility suppressed")
        elif gex_millions < -2 and iv_pctile_score >= 1 and skew_score >= 1:
            regime = VolRegime.EXPLOSIVE
            reasons.append(f"Negative GEX ({gex_millions:+.1f}M) + high IV + steep skew → Explosive regime")
        elif iv_pctile_score >= 1:
            regime = VolRegime.ELEVATED
            reasons.append(f"High IV ({iv_pctile_score:+.1f}) but mixed gamma → Elevated vol")
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

        Also checks for TRANSITION regime when near-term and
        far-term signals conflict.
        """
        # Check for conflicting timeframes
        if (near_term_direction.value * far_term_direction.value < 0 and
                abs(near_term_direction.value) > 0 and abs(far_term_direction.value) > 0):
            return (MarketRegime.TRANSITION,
                    f"Conflicting signals: near-term {near_term_direction.name} vs far-term {far_term_direction.name}")

        # Map direction + vol to regime
        if direction in (Signal.STRONG_BEARISH, Signal.BEARISH):
            if vol_regime in (VolRegime.ELEVATED, VolRegime.EXPLOSIVE):
                return (MarketRegime.VOLATILE_BEARISH, "Bearish + elevated vol = risk-off")
            elif vol_regime == VolRegime.SUPPRESSED:
                return (MarketRegime.RANGE_BOUND, "Bearish lean but vol suppressed = grind lower in range")
            else:
                return (MarketRegime.TRENDING_DOWN, "Bearish + normal vol = trending lower")

        elif direction in (Signal.STRONG_BULLISH, Signal.BULLISH):
            if vol_regime in (VolRegime.ELEVATED, VolRegime.EXPLOSIVE):
                return (MarketRegime.VOLATILE_BULLISH, "Bullish + elevated vol = volatile rally")
            elif vol_regime == VolRegime.SUPPRESSED:
                return (MarketRegime.RANGE_BOUND, "Bullish lean but vol suppressed = consolidation")
            else:
                return (MarketRegime.TRENDING_UP, "Bullish + normal vol = trending higher")

        else:  # Neutral
            if vol_regime == VolRegime.EXPLOSIVE:
                return (MarketRegime.TRANSITION, "Neutral direction + explosive vol = breakout imminent")
            elif vol_regime == VolRegime.SUPPRESSED:
                return (MarketRegime.RANGE_BOUND, "Neutral + suppressed vol = range-bound")
            else:
                return (MarketRegime.RANGE_BOUND, "Neutral direction = range-bound")


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

    REGIME_TEMPLATES = {
        MarketRegime.RISK_OFF: (
            "RISK-OFF regime. Bearish price action amplified by negative gamma. "
            "Dealers are short gamma and will accelerate selling into weakness. "
            "Priority: capital preservation, long puts, reduce notional exposure."
        ),
        MarketRegime.RANGE_BOUND: (
            "RANGE-BOUND regime. {gex_detail} "
            "Expect price to oscillate between put support at ${put_support:,.0f} "
            "and call resistance at ${call_resistance:,.0f}. "
            "Priority: sell premium via iron condors/strangles, harvest theta decay."
        ),
        MarketRegime.TRENDING_UP: (
            "TRENDING-UP regime. Bullish structural positioning with normal volatility. "
            "Max pain gravity pulling price toward ${max_pain:,.0f}. "
            "Priority: long call spreads, short put spreads. Avoid naked short calls."
        ),
        MarketRegime.TRENDING_DOWN: (
            "TRENDING-DOWN regime. Bearish structural positioning with normal volatility. "
            "Put support at ${put_support:,.0f} is the key level to watch. "
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

    VOL_TEMPLATES = {
        "sell_strong": (
            "Volatility is expensive (IV at {iv_pctile:.0f}th percentile, "
            "VRP {vrp:+.1f}pts). {vrp_adjustment} "
            "Sell premium in {sell_expiry} where ATM IV is {sell_iv:.1f}%. "
            "Skew at {skew:+.1f}% makes {rich_side} puts the higher-edge side to sell."
        ),
        "sell_moderate": (
            "Volatility is moderately elevated (IV at {iv_pctile:.0f}th percentile). "
            "{vrp_adjustment} "
            "Selling premium has edge but size conservatively. "
            "Favor {sell_expiry} expiry, {rich_side} side."
        ),
        "neutral": (
            "Volatility is fairly priced. No strong edge in selling or buying premium. "
            "Focus on directional trades with defined risk structures."
        ),
        "buy_moderate": (
            "Volatility is cheap (IV at {iv_pctile:.0f}th percentile, "
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

    LEVELS_TEMPLATE = (
        "KEY LEVELS: "
        "Resistance ${resistance:,.0f} (call wall {res_oi:,} OI). "
        "Support ${support:,.0f} (put wall {sup_oi:,} OI). "
        "Max pain ${max_pain:,.0f} ({mp_distance:+.1f}% from spot). "
        "Zero-gamma (HVL) ${hvl:,.0f}."
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
            put_support: float,
            call_resistance: float,
            max_pain: float,
            gex_total: float,
            conflict_detail: str = "",
            transition_window: str = "7-14 days"
    ) -> str:
        """Generate regime description with filled parameters."""

        gex_millions = gex_total / 1_000_000
        if gex_millions > 0:
            gex_detail = f"Positive gamma (+{gex_millions:.1f}M GEX) is dampening volatility — dealers buy dips and sell rallies."
        else:
            gex_detail = f"Negative gamma ({gex_millions:.1f}M GEX) is amplifying moves — dealers chase momentum both directions."

        template = cls.REGIME_TEMPLATES.get(regime, cls.REGIME_TEMPLATES[MarketRegime.RANGE_BOUND])

        return template.format(
            put_support=put_support,
            call_resistance=call_resistance,
            max_pain=max_pain,
            gex_detail=gex_detail,
            conflict_detail=conflict_detail,
            transition_window=transition_window,
        )

    @classmethod
    def generate_vol_narrative(
            cls,
            iv_pctile: float,
            vrp: float,
            vrp_adjustment: str,
            skew: float,
            sell_expiry: str,
            sell_iv: float,
            buy_expiry: str = "",
    ) -> str:
        """Generate volatility assessment narrative."""

        # Determine which template
        if vrp > 10:
            template_key = "sell_strong"
        elif vrp > 3:
            template_key = "sell_moderate"
        elif vrp > -3:
            template_key = "neutral"
        elif vrp > -10:
            template_key = "buy_moderate"
        else:
            template_key = "buy_strong"

        rich_side = "OTM" if skew > 6 else "ATM"
        cheap_side = "calls" if skew > 4 else "puts"

        template = cls.VOL_TEMPLATES[template_key]

        return template.format(
            iv_pctile=iv_pctile,
            vrp=vrp,
            vrp_adjustment=vrp_adjustment,
            skew=skew,
            sell_expiry=sell_expiry,
            sell_iv=sell_iv,
            buy_expiry=buy_expiry,
            rich_side=rich_side,
            cheap_side=cheap_side,
        )

    @classmethod
    def generate_risk_factors(
            cls,
            cone_30d_pctile: float,
            gex_total: float,
            largest_expiry_dte: int,
            funding_8h: float,
            skew: float,
            funding_rate: float = 0.0,
    ) -> str:
        """Generate risk factor list based on thresholds."""

        risks = []

        if cone_30d_pctile > 90:
            risks.append(
                f"30d RV at {cone_30d_pctile:.0f}th percentile — recent extreme move may repeat or mean-revert violently")

        gex_m = gex_total / 1_000_000
        if gex_m < -5:
            risks.append(
                f"Deeply negative GEX ({gex_m:.1f}M) — cascading stop-outs possible on any directional trigger")

        if largest_expiry_dte <= 3:
            risks.append(f"Major expiry in {largest_expiry_dte} DTE — pin risk and gamma spike around max pain")

        # funding_8h is in pct (e.g. -0.0148% per 8h). Threshold |8h| > 0.01%.
        # Annualized rate uses funding_rate (= current_funding × 100), the same
        # formula the report's PERPETUAL FUNDING section uses: rate × 3 × 365.
        if abs(funding_8h) > 0.01:
            direction = "long" if funding_8h > 0 else "short"
            ann_rate = abs(funding_rate) * 3 * 365  # matches report's annualization
            level = "Extreme" if ann_rate > 20 else "Elevated"
            risks.append(
                f"{level} funding ({funding_8h:.4f}% per 8h, ~{ann_rate:.1f}% ann) "
                f"— crowded {direction} at risk of squeeze"
            )

        if skew > 12:
            risks.append(f"Extreme skew ({skew:+.1f}%) — tail hedging elevated, crash risk priced in")

        if not risks:
            risks.append("No elevated risk factors detected")

        return cls.RISK_TEMPLATE.format(risk_items=" | ".join(risks))

    @classmethod
    def generate_trade_recommendations(
            cls,
            regime: MarketRegime,
            vol_regime: VolRegime,
            iv_pctile: float,
            skew: float,
            gex_total: float,
            near_term_expiry: str,
            far_term_expiry: str,
            skew_expiry: str = "",
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
        """
        recommendations = []

        # Strategy 1: Premium selling conditions
        if (regime in (MarketRegime.RANGE_BOUND,) and
                iv_pctile > 70 and
                vol_regime != VolRegime.EXPLOSIVE):
            recommendations.append(
                f"PRIMARY — Short Iron Condor ({near_term_expiry}): "
                f"Sell premium in range-bound regime. IV at {iv_pctile:.0f}th pctile provides edge. "
                f"Place short strikes at GEX support/resistance levels. "
                f"Target 50% of max profit, close before final 3 DTE."
            )

        # Strategy 2: Long vol conditions
        if vol_regime == VolRegime.EXPLOSIVE or iv_pctile < 30:
            recommendations.append(
                f"PRIMARY — Long Straddle/Strangle ({far_term_expiry}): "
                f"{'Explosive gamma regime' if vol_regime == VolRegime.EXPLOSIVE else 'Cheap IV'} "
                f"favors owning volatility. Buy ATM straddle or 25-delta strangle."
            )

        # Strategy 3: Directional spreads
        if regime in (MarketRegime.TRENDING_UP, MarketRegime.VOLATILE_BULLISH):
            recommendations.append(
                f"SECONDARY — Bull Call Spread ({far_term_expiry}): "
                f"Bullish regime supports upside exposure. Buy near-ATM, sell at call resistance. "
                f"Skew {skew:+.1f}% makes calls relatively cheap vs puts."
            )
        elif regime in (MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE_BEARISH):
            recommendations.append(
                f"SECONDARY — Bear Put Spread ({far_term_expiry}): "
                f"Bearish regime supports downside positioning. "
                f"Steep skew ({skew:+.1f}%) makes puts expensive — use spreads to offset."
            )

        # Strategy 4: Skew trade
        if skew > 10 and regime not in (MarketRegime.VOLATILE_BEARISH, MarketRegime.RISK_OFF):
            skew_src = f" [{skew_expiry}]" if skew_expiry else ""
            recommendations.append(
                f"OPPORTUNISTIC — Risk Reversal ({skew_expiry or near_term_expiry}): "
                f"25D skew{skew_src} at {skew:+.1f}% is elevated (threshold: >10%). "
                f"Sell OTM put, buy OTM call. "
                f"Verify skew on target expiry before executing — skew varies across the curve."
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

        # Sort expiries by DTE
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

        # Find the largest far-term expiry
        meaningful_far = max(
            (e for e in expiries_sorted if e.dte > 14),
            key=lambda e: e.total_oi,
            default=expiries_sorted[-1]
        )

        # =====================================================================
        # STEP 1: Score all directional metrics
        # =====================================================================

        # Aggregate scores across key expiries (weight by OI)
        all_direction_scores = []

        # Market-wide scores
        all_direction_scores.append(
            self.scorer.score_funding(market.funding_rate, market.funding_8h)
        )

        # Futures basis
        basis_values = list(market.futures_basis.values())
        if len(basis_values) >= 2:
            all_direction_scores.append(
                self.scorer.score_futures_basis(basis_values[0], basis_values[-1])
            )

        # Score the 3 most important expiries by OI
        top_expiries = sorted(expiries_sorted, key=lambda e: e.total_oi, reverse=True)[:3]

        for exp in top_expiries:
            all_direction_scores.append(self.scorer.score_pc_ratio(exp.pc_ratio))
            all_direction_scores.append(self.scorer.score_dex(exp.total_dex))
            all_direction_scores.append(
                self.scorer.score_max_pain_gravity(exp.max_pain, market.spot_price)
            )
            all_direction_scores.append(
                self.scorer.score_flow(exp.flow_bias, exp.flow_trend)
            )
            all_direction_scores.append(
                self.scorer.score_vanna_charm(exp.net_vanna, exp.net_charm)
            )

        # Near-term scores (for transition detection)
        near_direction_scores = []
        for exp in near_term[:3]:
            near_direction_scores.append(self.scorer.score_pc_ratio(exp.pc_ratio))
            near_direction_scores.append(self.scorer.score_dex(exp.total_dex))
            near_direction_scores.append(
                self.scorer.score_flow(exp.flow_bias, exp.flow_trend)
            )

        # Far-term scores
        far_direction_scores = []
        for exp in far_term[:3]:
            far_direction_scores.append(self.scorer.score_pc_ratio(exp.pc_ratio))
            far_direction_scores.append(self.scorer.score_dex(exp.total_dex))
            far_direction_scores.append(
                self.scorer.score_flow(exp.flow_bias, exp.flow_trend)
            )

        # =====================================================================
        # STEP 2: Score all vol metrics
        # =====================================================================

        iv_pctile_score = self.scorer.score_iv_percentile(market.iv_percentile_365d)

        vrp_score = self.scorer.score_vrp(
            market.vrp, market.rv_10d, market.rv_20d, market.rv_30d,
            market.cone_30d_pctile
        )

        # Use largest expiry's skew as representative
        skew_score = self.scorer.score_skew(largest_expiry.skew_25d)

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

        # Vol regime (use aggregate GEX from largest expiry)
        vol_regime, vol_reasons = self.classifier.classify_vol_regime(
            largest_expiry.total_gex,
            iv_pctile_score[0],
            vrp_score[0],
            skew_score[0]
        )

        # Market regime
        market_regime, regime_reason = self.classifier.classify_market_regime(
            overall_direction, vol_regime, near_direction, far_direction
        )

        # =====================================================================
        # STEP 4: Calculate forward VRP for narrative
        # =====================================================================

        forward_rv = (market.rv_10d + market.rv_20d) / 2
        forward_vrp = market.dvol - forward_rv

        # effective_vrp always tracks the primary VRP (DVOL − 30d RV).
        # Forward VRP is a MODEL ESTIMATE only — included as a note but never
        # used to override the recommendation direction.  Overriding would
        # create an internal contradiction between the header (primary VRP)
        # and the VOL ASSESSMENT text.
        effective_vrp = market.vrp

        if market.cone_30d_pctile > 85:
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
        elif market.cone_30d_pctile < 15:
            vrp_adjustment = (
                f"NOTE: 30d RV at {market.cone_30d_pctile:.0f}th pctile — unusually calm period. "
                f"VRP may compress further if realized vol reverts to mean."
            )
        else:
            vrp_adjustment = "30d RV within normal range. VRP is representative."

        # =====================================================================
        # STEP 5: Generate narrative
        # =====================================================================

        # Find aggregate support/resistance from largest expiry
        put_support = largest_expiry.put_support_strike
        call_resistance = largest_expiry.call_resistance_strike
        max_pain = largest_expiry.max_pain

        # Header
        header = self._generate_header(market, overall_direction, vol_regime, market_regime)

        # Regime narrative
        regime_narrative = self.narrator.generate_regime_narrative(
            regime=market_regime,
            spot=market.spot_price,
            put_support=put_support,
            call_resistance=call_resistance,
            max_pain=max_pain,
            gex_total=largest_expiry.total_gex,
            conflict_detail=regime_reason if market_regime == MarketRegime.TRANSITION else "",
            transition_window=self._estimate_transition_window(expiries_sorted),
        )

        # Near-term section
        near_term_narrative = self._generate_timeframe_section(
            "NEAR-TERM (0-7 DTE)", near_term, market.spot_price, near_direction
        )

        # Mid-term section
        mid_term_narrative = self._generate_timeframe_section(
            "MID-TERM (7-30 DTE)", mid_term, market.spot_price, overall_direction
        )

        # Far-term section
        far_term_narrative = self._generate_timeframe_section(
            "FAR-TERM (30+ DTE)", far_term, market.spot_price, far_direction
        )

        # Vol assessment
        # Find best near-term expiry for selling (highest ATM IV with good liquidity)
        sellable_near = [e for e in expiries_sorted if 5 <= e.dte <= 30 and e.total_oi > 2000]
        best_sell_expiry = max(sellable_near, key=lambda e: e.atm_iv) if sellable_near else meaningful_near

        vol_narrative = self.narrator.generate_vol_narrative(
            iv_pctile=market.iv_percentile_365d,
            vrp=effective_vrp,
            vrp_adjustment=vrp_adjustment,
            skew=largest_expiry.skew_25d,
            sell_expiry=best_sell_expiry.expiry,
            sell_iv=best_sell_expiry.atm_iv,
            buy_expiry=meaningful_far.expiry,
        )

        # Risk factors
        risk_narrative = self.narrator.generate_risk_factors(
            cone_30d_pctile=market.cone_30d_pctile,
            gex_total=largest_expiry.total_gex,
            largest_expiry_dte=largest_expiry.dte,
            funding_8h=market.funding_8h,
            funding_rate=market.funding_rate,
            skew=largest_expiry.skew_25d,
        )

        # Trade recommendations
        trade_narrative = self.narrator.generate_trade_recommendations(
            regime=market_regime,
            vol_regime=vol_regime,
            iv_pctile=market.iv_percentile_365d,
            skew=largest_expiry.skew_25d,
            gex_total=largest_expiry.total_gex,
            near_term_expiry=best_sell_expiry.expiry,
            far_term_expiry=meaningful_far.expiry,
            skew_expiry=largest_expiry.expiry,
        )

        # Block trade summary
        block_narrative = self._generate_block_summary(market.block_trades)

        # =====================================================================
        # STEP 6: Assemble final output
        # =====================================================================

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

SCORING DETAIL:
  Direction: {overall_direction.name} (confidence: {dir_confidence:.0%})
  Near-term: {near_direction.name} | Far-term: {far_direction.name}
  Vol Regime: {vol_regime.value}
  Market Regime: {market_regime.value}
  Effective VRP: {effective_vrp:+.1f}pts | Skew: {largest_expiry.skew_25d:+.1f}%
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
        """Generate the dashboard header."""
        # Bug fix: safe IV access — find first DTE >= 5 instead of fragile index access
        front_iv = next(
            (v for k, v in sorted(market.iv_by_dte.items()) if k >= 5),
            0.0
        )

        return f"""================================================================================
EXECUTIVE SYNTHESIS — BTC OPTIONS MARKET
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
================================================================================
BTC ${market.spot_price:,.2f} | Regime: {market_regime.value.upper().replace('_', ' ')}
Direction: {direction.name} | Vol: {vol_regime.value.upper()}
────────────────────────────────────────────────────────────────────────────────
DVOL: {market.dvol:.1f}%  | IV Pctile: {market.iv_percentile_365d:.0f}th  | ATM IV (front): ~{front_iv:.1f}%
10d RV: {market.rv_10d:.1f}%  | 20d RV: {market.rv_20d:.1f}%  | 30d RV: {market.rv_30d:.1f}% ({market.cone_30d_pctile:.0f}th cone)
VRP: {market.vrp:+.1f}pts  | Term Structure: {market.term_structure_shape} ({market.term_structure_spread_signed:+.1f}pts)
Perp Funding: {market.funding_rate:.4f}%  | 8h: {market.funding_8h:.4f}%
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
            mp_dist = (exp.max_pain - spot) / spot * 100
            lines.append(
                f"  {exp.expiry} ({exp.dte}d): MaxPain ${exp.max_pain:,.0f} ({mp_dist:+.1f}%) | "
                f"P/C {exp.pc_ratio:.2f} | ATM IV {exp.atm_iv:.1f}% | "
                f"Skew {exp.skew_25d:+.1f}% | Flow: {exp.flow_bias}"
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

    def _generate_block_summary(self, block_trades: List[dict]) -> str:
        """Summarize block trade activity."""
        if not block_trades:
            return "INSTITUTIONAL FLOW: No block trades detected in lookback window."

        buy_blocks = [b for b in block_trades if b.get('direction') == 'buy']
        sell_blocks = [b for b in block_trades if b.get('direction') == 'sell']

        total_buy_notional = sum(b.get('notional', 0) for b in buy_blocks)
        total_sell_notional = sum(b.get('notional', 0) for b in sell_blocks)

        lines = [
            f"INSTITUTIONAL FLOW (Block Trades): "
            f"{len(buy_blocks)} buys (${total_buy_notional / 1e6:.1f}M) | "
            f"{len(sell_blocks)} sells (${total_sell_notional / 1e6:.1f}M)"
        ]

        # Highlight the largest block
        if block_trades:
            largest = max(block_trades, key=lambda b: b.get('notional', 0))
            lines.append(
                f"  Largest: {largest.get('instrument', 'N/A')} "
                f"{'BUY' if largest.get('direction') == 'buy' else 'SELL'} "
                f"{largest.get('size', 0)} BTC "
                f"(${largest.get('notional', 0) / 1e6:.2f}M) at {largest.get('iv', 0):.1f}% IV"
            )

        return "\n".join(lines)


# =============================================================================
# SECTION 6: SYNTHESIS MAPPER
# =============================================================================

class SynthesisMapper:
    """
    Maps OnChainAnalyzer structured data to SynthesisEngine input dataclasses.

    Bridges the gap between the analyzer's raw structured outputs and the
    strongly-typed dataclasses that SynthesisEngine expects.
    """

    @staticmethod
    def _calculate_dte(expiration: str) -> int:
        """Calculate days to expiration from expiration string like '27MAR26'."""
        try:
            exp_date = datetime.strptime(expiration, "%d%b%y")
            dte = (exp_date - datetime.now()).days
            return max(dte, 0)
        except ValueError:
            return 0

    @classmethod
    def build_expiry_metrics(cls, analyzer: Any, expiration: str) -> Optional[ExpiryMetrics]:
        """
        Build ExpiryMetrics for one expiration from analyzer structured data.

        Returns None if critical data (GEX or instruments) is missing.
        """
        instruments = analyzer.parsed_data.get(expiration, [])
        if not instruments:
            return None

        # GEX/DEX structured data — must exist for meaningful synthesis
        gex_data = analyzer.gex_dex_structured.get(expiration, {})
        if not gex_data:
            return None

        vol_data = analyzer.volatility_surface_structured.get(expiration, {})
        flow_data = analyzer.buy_sell_flow_structured.get(expiration, {})

        dte = cls._calculate_dte(expiration)

        # Total OI and volumes from parsed_data
        total_oi = sum(i.get("open_interest", 0) for i in instruments)
        notional = total_oi * analyzer.underlying_price

        call_vol = sum(i.get("volume", 0) for i in instruments if i.get("option_type") == "C")
        put_vol = sum(i.get("volume", 0) for i in instruments if i.get("option_type") == "P")
        volume_pc_ratio = (put_vol / call_vol) if call_vol > 0 else 0.0

        # Max pain and OI P/C ratio
        strike_data = analyzer.group_by_strike(instruments)
        max_pain_result = analyzer.calculate_max_pain(strike_data)
        max_pain = max_pain_result.get("max_pain_strike") or analyzer.underlying_price

        pc_result = analyzer.calculate_put_call_ratio(strike_data)
        pc_ratio = pc_result.get("ratio", 1.0)
        if pc_ratio == float("inf"):
            pc_ratio = 99.0

        # GEX/DEX
        total_gex = gex_data.get("total_net_gex", 0.0) or 0.0
        total_dex = gex_data.get("total_net_dex", 0.0) or 0.0
        gex_environment = "Positive" if total_gex >= 0 else "Negative"

        key_levels = gex_data.get("key_levels") or {}
        call_res = key_levels.get("call_resistance") or {}
        put_sup = key_levels.get("put_support") or {}

        call_resistance_strike = call_res.get("strike") or 0.0
        call_resistance_gex = call_res.get("net_gex") or 0.0
        put_support_strike = put_sup.get("strike") or 0.0
        put_support_gex = put_sup.get("net_gex") or 0.0
        hvl_strike = key_levels.get("hvl") or 0.0

        # Vol surface
        atm_iv = vol_data.get("atm_iv") or 0.0
        skew_data = vol_data.get("skew_25d") or {}
        skew_25d = skew_data.get("skew") or 0.0
        put_25d_iv = skew_data.get("put_25d_iv") or 0.0
        call_25d_iv = skew_data.get("call_25d_iv") or 0.0

        pc_moneyness = vol_data.get("pc_by_moneyness") or {}
        pc_atm = (pc_moneyness.get("atm") or {}).get("ratio") or 0.0
        pc_near_otm = (pc_moneyness.get("near_otm") or {}).get("ratio") or 0.0
        pc_far_otm = (pc_moneyness.get("far_otm") or {}).get("ratio") or 0.0

        second_order = vol_data.get("second_order_greeks") or {}
        net_vanna = second_order.get("net_vanna") or 0.0
        net_charm = second_order.get("net_charm") or 0.0

        # Flow
        flow_bias = flow_data.get("bias_interpretation") or "Mixed/Neutral"
        flow_trend = flow_data.get("flow_trend") or "Mixed/Neutral Flow"
        top_buy_strikes = flow_data.get("top_buy_strikes") or []
        top_sell_strikes = flow_data.get("top_sell_strikes") or []

        return ExpiryMetrics(
            expiry=expiration,
            dte=dte,
            total_oi=int(total_oi),
            notional=notional,
            max_pain=float(max_pain),
            pc_ratio=float(pc_ratio),
            volume_pc_ratio=volume_pc_ratio,
            total_gex=total_gex,
            total_dex=total_dex,
            gex_environment=gex_environment,
            call_resistance_strike=float(call_resistance_strike),
            call_resistance_gex=float(call_resistance_gex),
            put_support_strike=float(put_support_strike),
            put_support_gex=float(put_support_gex),
            hvl_strike=float(hvl_strike),
            atm_iv=float(atm_iv),
            skew_25d=float(skew_25d),
            put_25d_iv=float(put_25d_iv),
            call_25d_iv=float(call_25d_iv),
            vwap_iv=0.0,  # Not captured in calculate() — only in generate_report_section()
            mark_iv=0.0,
            pc_atm=float(pc_atm),
            pc_near_otm=float(pc_near_otm),
            pc_far_otm=float(pc_far_otm),
            net_vanna=float(net_vanna),
            net_charm=float(net_charm),
            flow_bias=flow_bias,
            flow_trend=flow_trend,
            top_buy_strikes=list(top_buy_strikes),
            top_sell_strikes=list(top_sell_strikes),
        )

    @staticmethod
    def build_market_wide(analyzer: Any) -> MarketWideMetrics:
        """Build MarketWideMetrics from analyzer.market_wide_structured."""
        mw = analyzer.market_wide_structured

        # RV values from calculator are decimals (e.g. 0.585 = 58.5%).
        # dvol and vrp are in percentage points (e.g. 58.7, -7.6).
        # Multiply RV by 100 here so all vol fields share the same scale.
        rv_10d = (mw.get("rv_10d") or 0.0) * 100
        rv_20d = (mw.get("rv_20d") or 0.0) * 100
        rv_30d = (mw.get("rv_30d") or 0.0) * 100

        # API funding values are also decimals (e.g. -0.000201 = -0.0201%).
        # Multiply by 100 so score_funding thresholds (5/10/20%) work correctly.
        funding_rate = (mw.get("funding_rate") or 0.0) * 100
        funding_8h = (mw.get("funding_8h") or 0.0) * 100

        return MarketWideMetrics(
            spot_price=mw.get("spot_price") or analyzer.underlying_price,
            dvol=mw.get("dvol") or 0.0,
            iv_percentile_365d=mw.get("iv_percentile_365d") or 0.0,
            funding_rate=funding_rate,
            funding_8h=funding_8h,
            term_structure_shape=mw.get("shape") or "FLAT",
            term_structure_spread=mw.get("spread") or 0.0,
            term_structure_spread_signed=mw.get("spread_signed") or 0.0,
            iv_by_dte=mw.get("iv_by_dte") or {},
            rv_10d=rv_10d,
            rv_20d=rv_20d,
            rv_30d=rv_30d,
            vrp=mw.get("vrp") or 0.0,
            cone_10d_pctile=mw.get("cone_10d_pctile") or 0.0,
            cone_20d_pctile=mw.get("cone_20d_pctile") or 0.0,
            cone_30d_pctile=mw.get("cone_30d_pctile") or 0.0,
            futures_basis=mw.get("futures_basis") or {},
            perp_oi=mw.get("perp_oi") or 0.0,
            perp_funding_trend=mw.get("perp_funding_trend") or "Stable",
            btc_eth_price_corr=mw.get("btc_eth_price_corr") or 0.0,
            btc_eth_dvol_corr=mw.get("btc_eth_dvol_corr") or 0.0,
            block_trades=mw.get("block_trades") or [],
        )

    @classmethod
    def build_all(cls, analyzer: Any) -> Tuple[MarketWideMetrics, List[ExpiryMetrics]]:
        """Build complete input for SynthesisEngine from a fully-run analyzer."""
        market = cls.build_market_wide(analyzer)
        expiries = [
            m for exp in analyzer.get_expirations()
            if (m := cls.build_expiry_metrics(analyzer, exp)) is not None
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
        block_trades=[
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
            volume_pc_ratio=3.23,
            total_gex=-12_402_566, total_dex=-390.66,
            gex_environment="Negative",
            call_resistance_strike=66000, call_resistance_gex=265785,
            put_support_strike=65000, put_support_gex=-4_578_703,
            hvl_strike=66000,
            atm_iv=30.3, skew_25d=11.7, put_25d_iv=37.3, call_25d_iv=25.6,
            vwap_iv=50.3, mark_iv=74.5,
            pc_atm=2.60, pc_near_otm=2.37, pc_far_otm=0.0,
            net_vanna=0.000062, net_charm=59.96,
            flow_bias="Heavy Buying", flow_trend="Decelerating Buy Pressure",
        ),
        ExpiryMetrics(
            expiry="6MAR26", dte=6, total_oi=23883,
            notional=1_569_282_663, max_pain=67000, pc_ratio=1.23,
            volume_pc_ratio=1.05,
            total_gex=-7_885_127, total_dex=-1496.14,
            gex_environment="Negative",
            call_resistance_strike=70000, call_resistance_gex=2_263_058,
            put_support_strike=58000, put_support_gex=-4_456_432,
            hvl_strike=65500,
            atm_iv=49.1, skew_25d=8.9, put_25d_iv=55.3, call_25d_iv=46.5,
            vwap_iv=61.1, mark_iv=56.2,
            pc_atm=2.45, pc_near_otm=0.82, pc_far_otm=1.72,
            net_vanna=0.000349, net_charm=93.19,
            flow_bias="Heavy Buying", flow_trend="Reversing to Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="13MAR26", dte=13, total_oi=8785,
            notional=577_248_276, max_pain=66000, pc_ratio=0.93,
            volume_pc_ratio=0.75,
            total_gex=-13_297, total_dex=-146.97,
            gex_environment="Negative",
            call_resistance_strike=75000, call_resistance_gex=1_200_177,
            put_support_strike=55000, put_support_gex=-1_147_244,
            hvl_strike=66000,
            atm_iv=49.0, skew_25d=10.4, put_25d_iv=57.2, call_25d_iv=46.8,
            vwap_iv=50.2, mark_iv=53.5,
            pc_atm=1.44, pc_near_otm=0.60, pc_far_otm=1.75,
            net_vanna=0.000179, net_charm=22.69,
            flow_bias="Moderate Buying", flow_trend="Reversing to Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="27MAR26", dte=27, total_oi=149488,
            notional=9_822_511_753, max_pain=80000, pc_ratio=0.70,
            volume_pc_ratio=0.64,
            total_gex=-14_812_074, total_dex=-26914.66,
            gex_environment="Negative",
            call_resistance_strike=80000, call_resistance_gex=3_116_454,
            put_support_strike=60000, put_support_gex=-6_600_975,
            hvl_strike=67000,
            atm_iv=49.2, skew_25d=9.2, put_25d_iv=55.8, call_25d_iv=46.7,
            vwap_iv=76.8, mark_iv=76.5,
            pc_atm=1.38, pc_near_otm=1.81, pc_far_otm=0.51,
            net_vanna=0.001561, net_charm=94.23,
            flow_bias="Heavy Selling", flow_trend="Decelerating Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="24APR26", dte=55, total_oi=39117,
            notional=2_570_273_003, max_pain=70000, pc_ratio=0.68,
            volume_pc_ratio=0.47,
            total_gex=3_072_631, total_dex=-1179.31,
            gex_environment="Positive",
            call_resistance_strike=75000, call_resistance_gex=2_095_712,
            put_support_strike=60000, put_support_gex=-3_372_438,
            hvl_strike=84000,
            atm_iv=48.0, skew_25d=8.6, put_25d_iv=53.9, call_25d_iv=45.3,
            vwap_iv=49.7, mark_iv=51.3,
            pc_atm=2.00, pc_near_otm=0.95, pc_far_otm=0.41,
            net_vanna=0.000944, net_charm=27.06,
            flow_bias="Heavy Buying", flow_trend="Mixed/Neutral Flow",
        ),
        ExpiryMetrics(
            expiry="26JUN26", dte=118, total_oi=70893,
            notional=4_658_212_431, max_pain=85000, pc_ratio=0.91,
            volume_pc_ratio=0.67,
            total_gex=-8_447_524, total_dex=-11001.86,
            gex_environment="Negative",
            call_resistance_strike=90000, call_resistance_gex=489_690,
            put_support_strike=60000, put_support_gex=-2_930_370,
            hvl_strike=72000,
            atm_iv=48.7, skew_25d=7.4, put_25d_iv=53.7, call_25d_iv=46.3,
            vwap_iv=48.4, mark_iv=61.1,
            pc_atm=1.54, pc_near_otm=3.37, pc_far_otm=0.64,
            net_vanna=0.001305, net_charm=17.56,
            flow_bias="Moderate Selling", flow_trend="Steady Sell Pressure",
        ),
        ExpiryMetrics(
            expiry="25DEC26", dte=300, total_oi=45475,
            notional=2_988_048_812, max_pain=80000, pc_ratio=0.60,
            volume_pc_ratio=0.20,
            total_gex=2_890_951, total_dex=352.39,
            gex_environment="Positive",
            call_resistance_strike=120000, call_resistance_gex=2_422_244,
            put_support_strike=60000, put_support_gex=-1_777_383,
            hvl_strike=120000,
            atm_iv=50.3, skew_25d=4.8, put_25d_iv=52.8, call_25d_iv=48.0,
            vwap_iv=54.4, mark_iv=51.8,
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
