"""
Market regime detection algorithm.

Combines multiple market metrics to classify the current market regime:
- Strong Bullish, Weak Bullish, Sideways, Weak Bearish, Strong Bearish
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """
    Detect market regime using multi-factor analysis.

    Combines trend, volatility, momentum, on-chain, and sentiment metrics
    to classify the current market state.
    """

    # Component weights (must sum to 1.0)
    WEIGHTS = {
        "trend": 0.30,
        "volatility": 0.15,
        "momentum": 0.25,
        "onchain": 0.20,
        "sentiment": 0.10,
    }

    # Regime thresholds (based on composite score -100 to +100)
    REGIME_THRESHOLDS = {
        "Strong Bullish": 60,
        "Weak Bullish": 30,
        "Sideways": -30,
        "Weak Bearish": -60,
        "Strong Bearish": -100,
    }

    def __init__(self):
        """Initialize the regime detector."""
        logger.info("Initialized MarketRegimeDetector")

    def detect_regime(
        self,
        technical_indicators: Dict,
        onchain_metrics: Dict,
        external_metrics: Dict,
        current_price: float
    ) -> Dict:
        """
        Detect current market regime.

        Args:
            technical_indicators: Dict with SMA, EMA, ADX, ATR, RSI, MACD values.
            onchain_metrics: Dict with funding_rate, put_call_ratio, oi_trend.
            external_metrics: Dict with fear_greed, btc_dominance.
            current_price: Current underlying price.

        Returns:
            Dict with regime classification, scores, confidence, and reasoning.
        """
        # Calculate component scores
        trend_score = self._score_trend_component(technical_indicators, current_price)
        volatility_score = self._score_volatility_component(technical_indicators, onchain_metrics)
        momentum_score = self._score_momentum_component(technical_indicators)
        onchain_score = self._score_onchain_component(onchain_metrics)
        sentiment_score = self._score_sentiment_component(external_metrics)

        # Calculate weighted composite score
        composite_score = (
            trend_score * self.WEIGHTS["trend"] +
            volatility_score * self.WEIGHTS["volatility"] +
            momentum_score * self.WEIGHTS["momentum"] +
            onchain_score * self.WEIGHTS["onchain"] +
            sentiment_score * self.WEIGHTS["sentiment"]
        )

        # Classify regime
        regime = self._classify_regime(composite_score)

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

        logger.info(
            f"Detected regime: {regime} (composite={composite_score:.1f}, "
            f"confidence={confidence:.1f}%)"
        )

        return result

    def _score_trend_component(self, indicators: Dict, current_price: float) -> float:
        """
        Score the trend component (-100 to +100).

        Considers:
        - Price position relative to moving averages
        - MA slope/alignment (golden/death cross)
        - ADX strength
        """
        score = 0.0
        sma_50 = indicators.get("sma_50")
        sma_200 = indicators.get("sma_200")
        adx = indicators.get("adx")

        # MA position (50% of trend score)
        if sma_50 and sma_200:
            # Price above both MAs = bullish
            if current_price > sma_50 and current_price > sma_200:
                score += 30
            # Price below both MAs = bearish
            elif current_price < sma_50 and current_price < sma_200:
                score -= 30
            # Mixed = neutral
            else:
                score += 0

            # MA alignment (golden cross = bullish, death cross = bearish)
            if sma_50 > sma_200:
                score += 20  # Golden cross structure
            elif sma_50 < sma_200:
                score -= 20  # Death cross structure

        # ADX strength multiplier (50% of trend score)
        # Strong trend amplifies the signal, weak trend dampens it
        if adx:
            if adx > 40:
                # Very strong trend - amplify score
                multiplier = 1.5
            elif adx > 25:
                # Strong trend
                multiplier = 1.0
            elif adx > 20:
                # Weak trend - dampen score
                multiplier = 0.5
            else:
                # No trend - heavily dampen
                multiplier = 0.2
                score = score * multiplier
        else:
            # No ADX data, use moderate multiplier
            score = score * 0.7

        # Cap at -100 to +100
        return max(-100, min(100, score))

    def _score_volatility_component(self, indicators: Dict, onchain: Dict) -> float:
        """
        Score the volatility component (-100 to +100).

        High volatility is slightly negative (uncertain/risky).
        Low volatility is slightly positive (stable).
        """
        score = 0.0
        atr_percentile = indicators.get("atr_percentile")

        if atr_percentile is not None:
            # LOW volatility (< 25th percentile) = slightly bullish (stability)
            if atr_percentile < 25:
                score += 20
            # NORMAL volatility (25-50th) = neutral
            elif atr_percentile < 50:
                score += 5
            # HIGH volatility (50-75th) = slightly bearish (risk)
            elif atr_percentile < 75:
                score -= 10
            # EXTREME volatility (> 75th) = bearish (high risk)
            else:
                score -= 30

        # DVOL consideration (if available in onchain metrics)
        dvol = onchain.get("dvol")
        if dvol:
            # High DVOL = uncertainty
            if dvol > 80:
                score -= 10
            elif dvol < 40:
                score += 10

        return max(-100, min(100, score))

    def _score_momentum_component(self, indicators: Dict) -> float:
        """
        Score the momentum component (-100 to +100).

        Considers RSI, MACD, and price momentum.
        """
        score = 0.0
        rsi = indicators.get("rsi")
        macd = indicators.get("macd")
        macd_signal = indicators.get("macd_signal")
        macd_histogram = indicators.get("macd_histogram")

        # RSI scoring (50% of momentum score)
        if rsi is not None:
            if rsi > 70:
                # Overbought - weak bullish (potential reversal)
                score += 20
            elif rsi > 60:
                # Strong bullish momentum
                score += 40
            elif rsi > 50:
                # Bullish
                score += 20
            elif rsi > 40:
                # Neutral/slight bearish
                score -= 10
            elif rsi > 30:
                # Bearish
                score -= 30
            else:
                # Oversold - weak bearish (potential bounce)
                score -= 20

        # MACD scoring (50% of momentum score)
        if macd is not None and macd_signal is not None:
            # MACD above signal = bullish
            if macd > macd_signal:
                score += 30
            else:
                score -= 30

            # MACD histogram direction (confirmation)
            if macd_histogram is not None:
                if macd_histogram > 0:
                    score += 20
                else:
                    score -= 20

        return max(-100, min(100, score))

    def _score_onchain_component(self, onchain: Dict) -> float:
        """
        Score the on-chain component (-100 to +100).

        Considers funding rate, put/call ratio, OI trends.
        """
        score = 0.0
        funding_rate = onchain.get("funding_rate")
        put_call_ratio = onchain.get("put_call_ratio")

        # Funding rate scoring (60% of on-chain score)
        if funding_rate is not None:
            # Positive funding = longs paying shorts = bullish
            # Negative funding = shorts paying longs = bearish
            # Scale: typical funding is -0.1% to +0.1% (0.001)
            if funding_rate > 0.0005:
                # High positive funding (> 0.05%)
                score += 50
            elif funding_rate > 0:
                # Positive funding
                score += 30
            elif funding_rate > -0.0005:
                # Slightly negative
                score -= 30
            else:
                # High negative funding
                score -= 50

        # Put/Call ratio scoring (40% of on-chain score)
        if put_call_ratio is not None:
            # High P/C ratio = fear/bearish
            # Low P/C ratio = greed/bullish
            if put_call_ratio > 1.5:
                # Heavy put bias = bearish
                score -= 40
            elif put_call_ratio > 1.0:
                # Moderate put bias = slightly bearish
                score -= 20
            elif put_call_ratio > 0.7:
                # Balanced
                score += 0
            elif put_call_ratio > 0.5:
                # Call bias = bullish
                score += 20
            else:
                # Heavy call bias = very bullish
                score += 40

        return max(-100, min(100, score))

    def _score_sentiment_component(self, external: Dict) -> float:
        """
        Score the sentiment component (-100 to +100).

        Considers Fear & Greed Index and BTC dominance.
        """
        score = 0.0

        # Fear & Greed Index (70% of sentiment score)
        fear_greed_data = external.get("fear_greed")
        if fear_greed_data and isinstance(fear_greed_data, dict):
            value = fear_greed_data.get("value")
            if value is not None:
                # 0-25: Extreme Fear (contrarian bullish)
                # 25-45: Fear (slightly bearish)
                # 45-55: Neutral
                # 55-75: Greed (bullish)
                # 75-100: Extreme Greed (contrarian bearish)
                if value < 25:
                    score += 30  # Extreme fear = buy signal
                elif value < 45:
                    score -= 20  # Fear
                elif value < 55:
                    score += 0  # Neutral
                elif value < 75:
                    score += 40  # Greed
                else:
                    score += 20  # Extreme greed = potential top

        # BTC Dominance (30% of sentiment score)
        btc_dom = external.get("btc_dominance")
        if btc_dom is not None:
            # Rising BTC dominance = flight to safety (bearish for alts)
            # Falling BTC dominance = risk-on (bullish for alts)
            # For BTC itself, rising dominance is bullish
            # For now, treat neutrally or check trend
            if btc_dom > 50:
                score += 10  # BTC strong
            else:
                score -= 10  # Alt season potential

        return max(-100, min(100, score))

    def _classify_regime(self, composite_score: float) -> str:
        """
        Classify regime based on composite score.

        Args:
            composite_score: Weighted average score (-100 to +100).

        Returns:
            Regime classification string.
        """
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
        Calculate confidence score based on alignment of components.

        High confidence when all components agree.
        Low confidence when components are mixed.

        Args:
            component_scores: List of component scores.

        Returns:
            Confidence percentage (0-100).
        """
        if not component_scores:
            return 0.0

        # Count how many components are bullish (> 0) vs bearish (< 0)
        bullish_count = sum(1 for score in component_scores if score > 20)
        bearish_count = sum(1 for score in component_scores if score < -20)
        neutral_count = len(component_scores) - bullish_count - bearish_count

        # High confidence when most components agree
        max_agreement = max(bullish_count, bearish_count)
        total_components = len(component_scores)

        # Alignment percentage
        alignment = (max_agreement / total_components) * 100

        # Penalize for neutral components (uncertainty)
        neutral_penalty = (neutral_count / total_components) * 20

        confidence = alignment - neutral_penalty

        return max(0, min(100, confidence))

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
        reasons = [f"Market Regime: {regime} (Score: {composite_score:.1f})"]

        # Trend analysis
        sma_50 = indicators.get("sma_50")
        sma_200 = indicators.get("sma_200")
        adx = indicators.get("adx")

        if sma_50 and sma_200:
            if sma_50 > sma_200:
                reasons.append(f"Trend: Golden Cross structure (50 SMA > 200 SMA), ADX={adx:.1f}")
            else:
                reasons.append(f"Trend: Death Cross structure (50 SMA < 200 SMA), ADX={adx:.1f}")
        else:
            reasons.append(f"Trend: Insufficient MA data, ADX={adx:.1f if adx else 'N/A'}")

        # Momentum analysis
        rsi = indicators.get("rsi")
        macd_histogram = indicators.get("macd_histogram")
        if rsi:
            if rsi > 70:
                reasons.append(f"Momentum: Overbought (RSI={rsi:.1f})")
            elif rsi < 30:
                reasons.append(f"Momentum: Oversold (RSI={rsi:.1f})")
            else:
                reasons.append(f"Momentum: RSI={rsi:.1f}, MACD={'Bullish' if macd_histogram and macd_histogram > 0 else 'Bearish'}")

        # Volatility analysis
        atr_percentile = indicators.get("atr_percentile")
        if atr_percentile:
            if atr_percentile < 25:
                vol_regime = "LOW"
            elif atr_percentile < 50:
                vol_regime = "NORMAL"
            elif atr_percentile < 75:
                vol_regime = "HIGH"
            else:
                vol_regime = "EXTREME"
            reasons.append(f"Volatility: {vol_regime} regime (ATR Percentile={atr_percentile:.1f})")

        # On-chain analysis
        funding_rate = onchain.get("funding_rate")
        put_call_ratio = onchain.get("put_call_ratio")
        if funding_rate is not None:
            funding_pct = funding_rate * 100
            reasons.append(f"On-Chain: Funding={funding_pct:.3f}%, P/C Ratio={put_call_ratio:.2f if put_call_ratio else 'N/A'}")

        # Sentiment analysis
        fear_greed_data = external.get("fear_greed")
        if fear_greed_data and isinstance(fear_greed_data, dict):
            fg_value = fear_greed_data.get("value")
            fg_class = fear_greed_data.get("classification")
            reasons.append(f"Sentiment: Fear & Greed={fg_value} ({fg_class})")

        return " | ".join(reasons)
