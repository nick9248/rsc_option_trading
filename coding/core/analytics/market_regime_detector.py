"""
Market regime detection algorithm.

Combines multiple market metrics to classify the current market regime:
- Strong Bullish, Weak Bullish, Sideways, Weak Bearish, Strong Bearish
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """
    Detect market regime using multi-factor analysis.

    Combines trend, volatility, momentum, on-chain, and sentiment metrics
    to classify the current market state.
    """

    # Component weights (must sum to 1.0)
    # Order: trend, volatility, momentum, onchain, sentiment
    WEIGHTS = {
        "trend":      0.30,
        "volatility": 0.15,
        "momentum":   0.20,
        "onchain":    0.25,
        "sentiment":  0.10,
    }
    # Ordered list matching detect_regime scoring order (used by _calculate_confidence)
    WEIGHTS_LIST = [0.30, 0.15, 0.20, 0.25, 0.10]

    # Regime thresholds — symmetric around 0
    # Half-open intervals: lower bound inclusive, upper bound exclusive
    REGIME_THRESHOLDS = {
        "Strong Bullish": 55,   # composite >= 55
        "Weak Bullish":   20,   # 20 <= composite < 55
        "Sideways":      -20,   # -20 <= composite < 20
        "Weak Bearish":  -55,   # -55 <= composite < -20
        "Strong Bearish": -100, # composite < -55
    }

    # ADX threshold for trend override in classifier
    ADX_TREND_THRESHOLD = 25

    def __init__(self):
        """Initialize the regime detector."""
        logger.info("Initialized MarketRegimeDetector")

    def detect_regime(
        self,
        technical_indicators: Dict,
        onchain_metrics: Dict,
        external_metrics: Dict,
        current_price: float,
        velocity_indicators: Optional[Dict] = None,
        currency: str = "BTC",
    ) -> Dict:
        """
        Detect current market regime.

        Args:
            technical_indicators: Dict with SMA, EMA, ADX, ATR, RSI, MACD values.
            onchain_metrics: Dict with funding_rate, wings_skew, oi_direction, dvol metrics.
            external_metrics: Dict with fear_greed, btc_dominance, market_cap_change_24h.
            current_price: Current underlying price.
            velocity_indicators: Optional dict with ema_50_velocity, rsi_velocity, macd_histogram_velocity.
            currency: Currency being analyzed (BTC or ETH) — affects sentiment scoring.

        Returns:
            Dict with regime classification, scores, confidence, and reasoning.
        """
        # Calculate component scores
        trend_score = self._score_trend_component(
            technical_indicators, current_price, velocity=velocity_indicators
        )
        volatility_score = self._score_volatility_component(
            technical_indicators, onchain_metrics
        )
        momentum_score = self._score_momentum_component(
            technical_indicators, velocity=velocity_indicators
        )
        onchain_score = self._score_onchain_component(onchain_metrics)

        sentiment_score = self._score_sentiment_component(
            external_metrics,
            currency=currency,
            adx=technical_indicators.get("adx"),
            composite_trend=trend_score,
        )

        # Defensive check: Ensure all scores are valid numbers (not None)
        if any(score is None for score in [trend_score, volatility_score, momentum_score, onchain_score, sentiment_score]):
            logger.error(
                f"One or more component scores is None: trend={trend_score}, "
                f"vol={volatility_score}, mom={momentum_score}, "
                f"onchain={onchain_score}, sentiment={sentiment_score}"
            )
            trend_score = trend_score if trend_score is not None else 0.0
            volatility_score = volatility_score if volatility_score is not None else 0.0
            momentum_score = momentum_score if momentum_score is not None else 0.0
            onchain_score = onchain_score if onchain_score is not None else 0.0
            sentiment_score = sentiment_score if sentiment_score is not None else 0.0

        # Calculate weighted composite score
        composite_score = (
            trend_score * self.WEIGHTS["trend"] +
            volatility_score * self.WEIGHTS["volatility"] +
            momentum_score * self.WEIGHTS["momentum"] +
            onchain_score * self.WEIGHTS["onchain"] +
            sentiment_score * self.WEIGHTS["sentiment"]
        )

        # Classify regime with ADX override
        regime = self._classify_regime(
            composite_score,
            adx=technical_indicators.get("adx"),
            plus_di=technical_indicators.get("plus_di"),
            minus_di=technical_indicators.get("minus_di"),
        )

        # Calculate confidence (based on alignment of components)
        confidence = self._calculate_confidence([
            trend_score, volatility_score, momentum_score, onchain_score, sentiment_score
        ])

        # Generate reasoning
        reasoning = self._generate_reasoning(
            regime, composite_score, trend_score, volatility_score,
            momentum_score, onchain_score, sentiment_score,
            technical_indicators, onchain_metrics, external_metrics
        )

        result = {
            "regime": regime,
            "composite_score": round(composite_score, 2),
            "confidence": round(confidence, 2),
            "trend_score": round(trend_score, 2),
            "volatility_score": round(volatility_score, 2),
            "momentum_score": round(momentum_score, 2),
            "onchain_score": round(onchain_score, 2),
            "sentiment_score": round(sentiment_score, 2),
            "reasoning": reasoning,
        }

        composite_str = f"{composite_score:.1f}" if composite_score is not None else "N/A"
        confidence_str = f"{confidence:.1f}" if confidence is not None else "N/A"
        logger.info(
            f"Detected regime: {regime} (composite={composite_str}, "
            f"confidence={confidence_str}%)"
        )

        return result

    def _score_trend_component(
        self,
        indicators: Dict,
        current_price: float,
        velocity: Optional[Dict] = None,
    ) -> float:
        """
        Score the trend component (-100 to +100).

        Step 1: DI directional signal (uses plus_di / minus_di)
        Step 2: MA structure (4 states: uptrend, pullback, downtrend, bounce)
        Step 3: ADX strength multiplier (applied to Steps 1+2 sum)
        Step 4: EMA velocity (added after multiplier, not ADX-scaled — intentional)
        """
        score = 0.0
        sma_50 = indicators.get("sma_50")
        sma_200 = indicators.get("sma_200")
        adx = indicators.get("adx")
        plus_di = indicators.get("plus_di")
        minus_di = indicators.get("minus_di")

        # Step 1: DI directional signal
        if plus_di is not None and minus_di is not None:
            di_spread = plus_di - minus_di
            if di_spread > 15:
                score += 35
            elif di_spread > 5:
                score += 20
            elif di_spread > -5:
                score += 0
            elif di_spread > -15:
                score -= 20
            else:
                score -= 35

        # Step 2: MA structure (4 states)
        if sma_50 is not None and sma_200 is not None:
            if current_price > sma_50 and sma_50 > sma_200:
                score += 20  # Clean uptrend
            elif sma_50 > sma_200 and current_price < sma_50:
                score += 10  # Pullback in uptrend
            elif current_price < sma_50 and sma_50 < sma_200:
                score -= 20  # Clean downtrend
            elif sma_50 < sma_200 and current_price > sma_50:
                score -= 10  # Bounce in downtrend

        # Step 3: ADX strength multiplier (FIXED — actually applied now)
        if adx is not None:
            if adx > 40:
                multiplier = 1.4
            elif adx > 25:
                multiplier = 1.0
            elif adx > 20:
                multiplier = 0.6
            else:
                multiplier = 0.3
            score = score * multiplier
        else:
            score = score * 0.7  # Missing ADX — moderate dampening

        # Step 4: EMA velocity (added after multiplier, not ADX-scaled — intentional)
        if velocity:
            ema_vel = velocity.get("ema_50_velocity")
            if ema_vel is not None:
                if ema_vel > 0.2:
                    score += 10
                elif ema_vel < -0.2:
                    score -= 10

        return max(-100.0, min(100.0, score))

    def _score_volatility_component(self, indicators: Dict, onchain: Dict) -> float:
        """
        Score the volatility component (-100 to +100).

        Uses DVOL-based signals instead of ATR percentile alone:
        - DVOL 30-day rolling percentile (50% documentary weight)
        - DVOL term structure ratio: current / 30d_avg (30% documentary weight)
        - VRP signal: IV vs realized vol (20% documentary weight)

        Low DVOL + cheap options = bullish regime precursor.
        High DVOL + backwardation = fear/crisis = bearish.
        """
        score = 0.0

        # Sub-signal 1: DVOL 30-day rolling percentile
        dvol_percentile = onchain.get("dvol_percentile")
        if dvol_percentile is not None:
            if dvol_percentile < 20:
                score += 40
            elif dvol_percentile < 40:
                score += 20
            elif dvol_percentile < 60:
                score += 0
            elif dvol_percentile < 80:
                score -= 20
            else:
                score -= 40

        # Sub-signal 2: DVOL term structure ratio (current / 30d_avg)
        dvol_term_ratio = onchain.get("dvol_term_structure_ratio")
        if dvol_term_ratio is not None:
            if dvol_term_ratio < 0.80:
                score += 20   # Contango — near vol cheap, calm
            elif dvol_term_ratio < 0.95:
                score += 10
            elif dvol_term_ratio < 1.10:
                score += 0    # Flat
            elif dvol_term_ratio < 1.25:
                score -= 15   # Mild backwardation — near-term fear
            else:
                score -= 25   # Steep backwardation — crisis premium

        # Sub-signal 3: VRP signal (vrp_percentage = (IV - RV) / RV * 100)
        vrp_percentage = onchain.get("vrp_percentage")
        if vrp_percentage is not None:
            if vrp_percentage > 20:
                score -= 20   # Options very expensive — hedgers paying up
            elif vrp_percentage > 5:
                score -= 10
            elif vrp_percentage > -5:
                score += 0    # Fair pricing
            elif vrp_percentage > -20:
                score += 10   # Cheap options — complacency — bullish
            else:
                score += 20   # Extreme complacency — pre-run environment

        return max(-100.0, min(100.0, score))

    def _score_momentum_component(
        self,
        indicators: Dict,
        velocity: Optional[Dict] = None,
    ) -> float:
        """
        Score the momentum component (-100 to +100).

        RSI (50% documentary): level + 5-day velocity.
        MACD (50% documentary): crossover + histogram MAGNITUDE velocity.

        Key fix: histogram magnitude velocity is independent of crossover direction.
        Previously both were 100% correlated; now MACD can contribute +25/-15 = +10,
        not just forced ±50.
        """
        score = 0.0
        rsi = indicators.get("rsi")
        macd = indicators.get("macd")
        macd_signal = indicators.get("macd_signal")

        # Sub-signal 1: RSI level
        if rsi is not None:
            if rsi > 70:
                score += 25   # Overbought — bullish but less than peak range
            elif rsi > 60:
                score += 35   # Strongest bullish momentum range
            elif rsi > 50:
                score += 15
            elif rsi > 40:
                score -= 10
            elif rsi > 30:
                score -= 30
            else:
                score -= 15   # Oversold — contrarian bounce potential (less bearish than 30-40)

            # RSI velocity (5-day absolute change)
            if velocity:
                rsi_vel = velocity.get("rsi_velocity")
                if rsi_vel is not None:
                    if rsi_vel > 8:
                        score += 10   # Accelerating bullish
                    elif rsi_vel < -8:
                        score -= 10   # Decelerating

        # Sub-signal 2: MACD crossover
        if macd is not None and macd_signal is not None:
            if macd > macd_signal:
                score += 25
            else:
                score -= 25

            # Histogram magnitude velocity — genuinely independent of crossover direction
            if velocity:
                hist_vel = velocity.get("macd_histogram_velocity")
                if hist_vel is not None:
                    if hist_vel > 0:
                        score += 15   # Momentum building
                    elif hist_vel < 0:
                        score -= 15   # Momentum fading

        return max(-100.0, min(100.0, score))

    def _score_onchain_component(self, onchain: Dict) -> float:
        """
        Score the on-chain component (-100 to +100).

        Wings skew (40%): put_iv - call_iv for OTM options.
          Positive = fear/puts bid. Negative = calls bid/bullish.
        Funding rate (40%): raw 8h percentage. UNIT FIXED — no /100.
          Thresholds: ±0.02% neutral zone, ±0.05% strong signal.
        OI direction (20%): pre-computed score in [-20, +20] from service.
        """
        score = 0.0

        # Sub-signal 1: Wings skew (put_iv - call_iv, percentage points)
        wings_skew = onchain.get("wings_skew")
        if wings_skew is not None:
            if wings_skew > 10:
                score -= 40   # Strong fear premium — puts very expensive
            elif wings_skew > 5:
                score -= 20
            elif wings_skew > -5:
                score += 0    # Balanced skew
            elif wings_skew > -10:
                score += 20
            else:
                score += 40   # Strong call premium — upside positioned

        # Sub-signal 2: Funding rate (raw %, NOT divided by 100 — unit is fixed in service)
        # Typical Deribit 8h range: -0.05% to +0.05%
        funding_rate = onchain.get("funding_rate")
        if funding_rate is not None:
            if funding_rate > 0.05:
                score += 30
            elif funding_rate > 0.02:
                score += 20
            elif funding_rate > -0.02:
                score += 0    # Neutral zone (typical market)
            elif funding_rate > -0.05:
                score -= 20
            else:
                score -= 30

        # Sub-signal 3: OI direction (pre-computed in service, range [-20, +20])
        oi_direction = onchain.get("oi_direction", 0)
        if oi_direction:
            score += oi_direction

        return max(-100.0, min(100.0, score))

    def _score_sentiment_component(
        self,
        external: Dict,
        currency: str = "BTC",
        adx: Optional[float] = None,
        composite_trend: Optional[float] = None,
    ) -> float:
        """
        Score the sentiment component (-100 to +100).

        F&G 7-day average (60%): smoothed index with context-aware scoring.
        BTC dominance (25%): currency-aware — sign inverts for ETH.
        Market cap 24h change (15%): broad risk-on/off signal.
        """
        score = 0.0

        in_strong_trend = adx is not None and adx > 25
        trend_is_bearish = composite_trend is not None and composite_trend < -30

        # Sub-signal 1: F&G 7-day average
        # Prefer fear_greed_7d_avg; fall back to spot fear_greed.value
        fear_greed_avg = external.get("fear_greed_7d_avg")
        if fear_greed_avg is None:
            fear_greed_data = external.get("fear_greed")
            if fear_greed_data and isinstance(fear_greed_data, dict):
                fear_greed_avg = fear_greed_data.get("value")

        if fear_greed_avg is not None:
            if fear_greed_avg < 25:
                # Extreme fear — context-aware
                if in_strong_trend and trend_is_bearish:
                    score -= 15   # Confirms bearish — not contrarian
                else:
                    score += 25   # Contrarian buy signal in weak/ranging market
            elif fear_greed_avg < 45:
                score -= 20       # Fear
            elif fear_greed_avg < 55:
                score += 0        # Neutral
            elif fear_greed_avg < 75:
                score += 35       # Greed — bullish
            else:
                score += 15       # Extreme greed — potential top warning

        # Sub-signal 2: BTC dominance (currency-aware — sign inverts for ETH)
        btc_dom = external.get("btc_dominance")
        if btc_dom is not None:
            if currency.upper() == "BTC":
                if btc_dom > 55:
                    score += 10   # BTC strength — flight to BTC
                elif btc_dom < 45:
                    score -= 10   # Alt season — capital leaving BTC
            else:
                # ETH and other alts: dominance sign inverts
                if btc_dom > 55:
                    score -= 10   # Capital in BTC, not alts
                elif btc_dom < 45:
                    score += 10   # Alt season — bullish for ETH

        # Sub-signal 3: Broad market 24h change (already fetched, was unused)
        mc_change = external.get("market_cap_change_24h")
        if mc_change is not None:
            if mc_change > 3:
                score += 10   # Risk-on environment
            elif mc_change < -3:
                score -= 10   # Risk-off

        return max(-100.0, min(100.0, score))

    def _classify_regime(
        self,
        composite_score: float,
        adx: Optional[float] = None,
        plus_di: Optional[float] = None,
        minus_di: Optional[float] = None,
    ) -> str:
        """
        Classify regime from composite score with DI-based ADX override.

        Thresholds are symmetric around 0 (Sideways = -20 to +20).
        ADX override: if ADX > 25 and composite is in Sideways range,
        use raw DI+/DI- spread to force directional classification.
        "DI spread" here refers to raw indicator values, not Step 1 buckets.
        """
        # ADX override — only when composite is in Sideways range
        if adx is not None and adx > self.ADX_TREND_THRESHOLD:
            if self.REGIME_THRESHOLDS["Sideways"] <= composite_score < self.REGIME_THRESHOLDS["Weak Bullish"]:
                if plus_di is not None and minus_di is not None:
                    di_diff = abs(plus_di - minus_di)
                    if di_diff > 5:
                        if plus_di > minus_di:
                            return "Weak Bullish"
                        else:
                            return "Weak Bearish"
                    # DI spread ≤ 5 — trend direction uncertain, keep Sideways

        # Standard threshold classification
        if composite_score >= self.REGIME_THRESHOLDS["Strong Bullish"]:
            return "Strong Bullish"
        elif composite_score >= self.REGIME_THRESHOLDS["Weak Bullish"]:
            return "Weak Bullish"
        elif composite_score >= self.REGIME_THRESHOLDS["Sideways"]:
            return "Sideways"
        elif composite_score >= self.REGIME_THRESHOLDS["Weak Bearish"]:
            return "Weak Bearish"
        else:
            return "Strong Bearish"

    def _calculate_confidence(self, component_scores: list) -> float:
        """
        Calculate confidence as weighted net agreement between components.

        Uses component weights (WEIGHTS_LIST) so a single fringe component
        cannot inflate confidence. Divide by 1.0 (sum of all weights) ensures
        correct scaling across all scenarios.

        Examples:
          All 5 bullish → (1.0 - 0.0) * 100 = 100%
          Only sentiment bullish (0.10 weight) → (0.10 - 0.0) * 100 = 10%
          Perfect split (0.50 vs 0.50) → (0.50 - 0.50) * 100 = 0%
        """
        if not component_scores:
            return 0.0

        weights = self.WEIGHTS_LIST
        bullish_weight = sum(w for s, w in zip(component_scores, weights) if s > 20)
        bearish_weight = sum(w for s, w in zip(component_scores, weights) if s < -20)

        dominant = max(bullish_weight, bearish_weight)
        conflicting = min(bullish_weight, bearish_weight)

        # Divide by 1.0 (= sum of all weights) — not total_active
        confidence = (dominant - conflicting) * 100

        return max(0.0, min(100.0, confidence))

    def _generate_reasoning(
        self,
        regime: str,
        composite_score: float,
        trend_score: float,
        volatility_score: float,
        momentum_score: float,
        onchain_score: float,
        sentiment_score: float,
        indicators: Dict,
        onchain: Dict,
        external: Dict
    ) -> str:
        """
        Generate human-readable reasoning for the regime classification.

        Args:
            regime: Detected regime.
            composite_score: Overall composite score.
            trend_score, volatility_score, momentum_score, onchain_score, sentiment_score: Component scores.
            indicators: Technical indicators.
            onchain: On-chain metrics.
            external: External sentiment metrics.

        Returns:
            Reasoning text.
        """
        composite_str = f"{composite_score:.1f}" if composite_score is not None else "N/A"
        reasons = [f"Market Regime: {regime} (Score: {composite_str})"]

        # Trend analysis
        sma_50 = indicators.get("sma_50")
        sma_200 = indicators.get("sma_200")
        adx = indicators.get("adx")

        if sma_50 and sma_200:
            adx_str = f"{adx:.1f}" if adx is not None else "N/A"
            if sma_50 > sma_200:
                reasons.append(f"Trend: Golden Cross structure (50 SMA > 200 SMA), ADX={adx_str}")
            else:
                reasons.append(f"Trend: Death Cross structure (50 SMA < 200 SMA), ADX={adx_str}")
        else:
            adx_str = f"{adx:.1f}" if adx is not None else "N/A"
            reasons.append(f"Trend: Insufficient MA data, ADX={adx_str}")

        # Momentum analysis
        rsi = indicators.get("rsi")
        macd_histogram = indicators.get("macd_histogram")
        if rsi is not None:
            rsi_str = f"{rsi:.1f}"
            if rsi > 70:
                reasons.append(f"Momentum: Overbought (RSI={rsi_str})")
            elif rsi < 30:
                reasons.append(f"Momentum: Oversold (RSI={rsi_str})")
            else:
                reasons.append(f"Momentum: RSI={rsi_str}, MACD={'Bullish' if macd_histogram and macd_histogram > 0 else 'Bearish'}")

        # Volatility analysis
        dvol_percentile = onchain.get("dvol_percentile")
        if dvol_percentile is not None:
            dvol_str = f"{dvol_percentile:.1f}"
            if dvol_percentile < 20:
                vol_regime = "LOW"
            elif dvol_percentile < 40:
                vol_regime = "BELOW_AVERAGE"
            elif dvol_percentile < 60:
                vol_regime = "NORMAL"
            elif dvol_percentile < 80:
                vol_regime = "HIGH"
            else:
                vol_regime = "EXTREME"
            reasons.append(f"Volatility: {vol_regime} regime (DVOL Percentile={dvol_str})")

        # On-chain analysis
        funding_rate = onchain.get("funding_rate")
        wings_skew = onchain.get("wings_skew")
        if funding_rate is not None:
            funding_pct = funding_rate * 100
            funding_str = f"{funding_pct:.3f}"
            wings_str = f"{wings_skew:.2f}" if wings_skew is not None else "N/A"
            reasons.append(f"On-Chain: Funding={funding_str}%, Wings Skew={wings_str}")

        # Sentiment analysis
        fear_greed_avg = external.get("fear_greed_7d_avg")
        fear_greed_data = external.get("fear_greed")
        if fear_greed_avg is not None:
            reasons.append(f"Sentiment: Fear & Greed 7d avg={fear_greed_avg:.1f}")
        elif fear_greed_data and isinstance(fear_greed_data, dict):
            fg_value = fear_greed_data.get("value")
            fg_class = fear_greed_data.get("classification")
            reasons.append(f"Sentiment: Fear & Greed={fg_value} ({fg_class})")

        return " | ".join(reasons)
