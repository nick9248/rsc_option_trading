"""
On-chain scorer for strategy evaluation.

Scores strategies based on on-chain market data:
- Max pain alignment
- GEX/DEX support
- Open interest levels
- Put/Call ratio
- Volume profile
- Market regime detection (technical, on-chain, sentiment analysis)
"""

import logging
from typing import Dict, List, Optional

from .base_scorer import BaseScorer

logger = logging.getLogger(__name__)


class OnChainScorer(BaseScorer):
    """
    Scores strategies based on on-chain metrics.

    Components (weights):
    1. Max Pain Alignment (20%): Strategy alignment with max pain
    2. GEX/DEX Support (20%): Gamma/delta exposure support
    3. OI Levels (15%): Open interest at strikes
    4. Put/Call Ratio (15%): Market sentiment
    5. Volume Profile (15%): Trading volume
    6. Market Regime (15%): Multi-factor regime detection with alignment scoring

    All scores are on 0-10 scale, higher is better.
    """

    # Component weights (must sum to 1.0)
    WEIGHTS = {
        "max_pain_alignment": 0.20,
        "gex_dex_support": 0.20,
        "oi_levels": 0.15,
        "put_call_ratio": 0.15,
        "volume_profile": 0.15,
        "market_regime": 0.15
    }

    def __init__(self, repository=None):
        """
        Initialize on-chain scorer.

        Args:
            repository: Optional database repository for trend queries.
                       If None, trend analysis will return neutral score (5.0)
        """
        self.repository = repository

    def calculate_score(self, strategy, market_context: Dict) -> float:
        """
        Calculate overall on-chain score.

        Args:
            strategy: Strategy instance
            market_context: Dictionary with on-chain metrics

        Returns:
            Overall on-chain score (0-10)
        """
        components = self.get_breakdown(strategy, market_context)
        return self.weighted_average(components, self.WEIGHTS)

    def get_breakdown(self, strategy, market_context: Dict) -> Dict[str, float]:
        """
        Get breakdown of all on-chain components.

        Args:
            strategy: Strategy instance
            market_context: Dictionary with on-chain metrics
                Expected keys:
                - max_pain_strike
                - gex_total, dex_total
                - total_oi, call_oi, put_oi
                - put_call_ratio
                - total_volume

        Returns:
            Dictionary with component scores (0-10)
        """
        return {
            "max_pain_alignment": self._score_max_pain_alignment(strategy, market_context),
            "gex_dex_support": self._score_gex_dex_support(strategy, market_context),
            "oi_levels": self._score_oi_levels(strategy, market_context),
            "put_call_ratio": self._score_put_call_ratio(strategy, market_context),
            "volume_profile": self._score_volume_profile(strategy, market_context),
            "market_regime": self._score_market_regime(strategy, market_context)
        }

    def _score_max_pain_alignment(self, strategy, market_context: Dict) -> float:
        """
        Score based on max pain alignment.

        - Bullish strategies score higher when price > max pain
        - Bearish strategies score higher when price < max pain
        - Distance from max pain also matters

        Args:
            strategy: Strategy instance
            market_context: Market data with max_pain_strike

        Returns:
            Score (0-10)
        """
        max_pain = market_context.get("max_pain_strike")

        if max_pain is None or max_pain == 0:
            logger.warning("Max pain not available, returning neutral score")
            return 5.0

        current_price = strategy.underlying_price
        distance_pct = ((current_price - max_pain) / current_price) * 100

        strategy_type = strategy.strategy_type

        if strategy_type == "directional_bullish":
            # Bullish: want price > max pain (positive distance)
            # +5% above max pain = 10, at max pain = 5, -5% below = 0
            score = self.normalize_score(
                score=distance_pct,
                min_score=-5.0,
                max_score=5.0
            )

            logger.debug(
                f"{strategy.name}: Bullish, distance from max pain={distance_pct:.2f}%, "
                f"alignment_score={score:.2f}"
            )
            return score

        elif strategy_type == "directional_bearish":
            # Bearish: want price < max pain (negative distance)
            # -5% below max pain = 10, at max pain = 5, +5% above = 0
            score = self.normalize_score(
                score=-distance_pct,
                min_score=-5.0,
                max_score=5.0
            )

            logger.debug(
                f"{strategy.name}: Bearish, distance from max pain={distance_pct:.2f}%, "
                f"alignment_score={score:.2f}"
            )
            return score

        else:
            # Neutral strategies: want price close to max pain
            # At max pain = 10, 5% away = 0
            score = self.normalize_score(
                score=5.0 - abs(distance_pct),
                min_score=0.0,
                max_score=5.0
            )

            logger.debug(
                f"{strategy.name}: Neutral, distance from max pain={abs(distance_pct):.2f}%, "
                f"alignment_score={score:.2f}"
            )
            return score

    def _score_gex_dex_support(self, strategy, market_context: Dict) -> float:
        """
        Score based on GEX/DEX support.

        - Negative GEX (dealers short gamma) = volatile, supports large moves
        - Positive GEX (dealers long gamma) = stable, suppresses moves
        - Positive DEX = bullish bias, Negative DEX = bearish bias

        Args:
            strategy: Strategy instance
            market_context: Market data with gex_total, dex_total

        Returns:
            Score (0-10)
        """
        gex_total = market_context.get("gex_total", 0)
        dex_total = market_context.get("dex_total", 0)

        if gex_total == 0 and dex_total == 0:
            logger.warning("GEX/DEX not available, returning neutral score")
            return 5.0

        strategy_type = strategy.strategy_type

        if strategy_type == "directional_bullish":
            # Bullish wants: Positive DEX (dealer delta supports upside)
            # Negative GEX is bonus (more volatile = bigger moves)

            # DEX component (primary)
            if dex_total > 0:
                dex_score = 10.0
            elif dex_total < 0:
                dex_score = 0.0
            else:
                dex_score = 5.0

            # GEX component (secondary) - negative GEX helps volatility
            if gex_total < 0:
                gex_score = 8.0  # Bonus for negative GEX
            else:
                gex_score = 5.0

            score = (dex_score * 0.7) + (gex_score * 0.3)

            logger.debug(
                f"{strategy.name}: Bullish, DEX={dex_total:.2f}, GEX={gex_total:.2f}, "
                f"support_score={score:.2f}"
            )
            return score

        elif strategy_type == "directional_bearish":
            # Bearish wants: Negative DEX (dealer delta supports downside)

            if dex_total < 0:
                dex_score = 10.0
            elif dex_total > 0:
                dex_score = 0.0
            else:
                dex_score = 5.0

            # GEX component - negative GEX helps
            if gex_total < 0:
                gex_score = 8.0
            else:
                gex_score = 5.0

            score = (dex_score * 0.7) + (gex_score * 0.3)

            logger.debug(
                f"{strategy.name}: Bearish, DEX={dex_total:.2f}, GEX={gex_total:.2f}, "
                f"support_score={score:.2f}"
            )
            return score

        elif strategy_type == "volatility_long":
            # Vol long wants negative GEX (high volatility)
            if gex_total < 0:
                score = 10.0
            elif gex_total > 0:
                score = 2.0
            else:
                score = 5.0

            logger.debug(
                f"{strategy.name}: Vol long, GEX={gex_total:.2f}, support_score={score:.2f}"
            )
            return score

        else:
            # Neutral or other types
            logger.debug(f"{strategy.name}: Neutral strategy type, returning 5.0")
            return 5.0

    def _score_oi_levels(self, strategy, market_context: Dict) -> float:
        """
        Score based on open interest levels.

        Higher OI = more liquid, more significant strikes.

        Args:
            strategy: Strategy instance
            market_context: Market data with total_oi

        Returns:
            Score (0-10)
        """
        total_oi = market_context.get("total_oi", 0)

        if total_oi == 0:
            logger.warning("OI not available, returning low score")
            return 3.0

        # Normalize OI - higher is better
        # This is currency-dependent, so normalize relative to typical values
        # BTC: 10k+ OI = high, ETH: 50k+ OI = high

        if strategy.currency == "BTC":
            # BTC typical OI ranges
            score = self.normalize_score(
                score=total_oi,
                min_score=1000,
                max_score=20000
            )
        elif strategy.currency == "ETH":
            # ETH typical OI ranges
            score = self.normalize_score(
                score=total_oi,
                min_score=5000,
                max_score=100000
            )
        else:
            # Unknown currency, use generic normalization
            score = self.normalize_score(
                score=total_oi,
                min_score=1000,
                max_score=50000
            )

        logger.debug(
            f"{strategy.name}: Total OI={total_oi:.0f}, oi_score={score:.2f}"
        )

        return score

    def _score_put_call_ratio(self, strategy, market_context: Dict) -> float:
        """
        Score based on put/call ratio.

        - High P/C ratio (>1.0) = bearish sentiment
        - Low P/C ratio (<1.0) = bullish sentiment
        - Ratio near 1.0 = neutral sentiment

        Args:
            strategy: Strategy instance
            market_context: Market data with put_call_ratio

        Returns:
            Score (0-10)
        """
        pc_ratio = market_context.get("put_call_ratio", 1.0)

        if pc_ratio == 0:
            logger.warning("P/C ratio is zero, returning neutral score")
            return 5.0

        strategy_type = strategy.strategy_type

        if strategy_type == "directional_bullish":
            # Bullish wants low P/C ratio (<1.0)
            # P/C = 0.5 = 10, P/C = 1.0 = 5, P/C = 1.5+ = 0
            score = self.normalize_score(
                score=1.5 - pc_ratio,
                min_score=0.0,
                max_score=1.0
            )

            logger.debug(
                f"{strategy.name}: Bullish, P/C={pc_ratio:.2f}, sentiment_score={score:.2f}"
            )
            return score

        elif strategy_type == "directional_bearish":
            # Bearish wants high P/C ratio (>1.0)
            # P/C = 1.5+ = 10, P/C = 1.0 = 5, P/C = 0.5 = 0
            score = self.normalize_score(
                score=pc_ratio,
                min_score=0.5,
                max_score=1.5
            )

            logger.debug(
                f"{strategy.name}: Bearish, P/C={pc_ratio:.2f}, sentiment_score={score:.2f}"
            )
            return score

        else:
            # Neutral strategies want P/C near 1.0
            distance_from_one = abs(pc_ratio - 1.0)
            score = self.normalize_score(
                score=1.0 - distance_from_one,
                min_score=0.0,
                max_score=1.0
            )

            logger.debug(
                f"{strategy.name}: Neutral, P/C={pc_ratio:.2f}, sentiment_score={score:.2f}"
            )
            return score

    def _score_volume_profile(self, strategy, market_context: Dict) -> float:
        """
        Score based on volume profile.

        Higher volume = more liquid = higher score.

        Args:
            strategy: Strategy instance
            market_context: Market data with total_volume

        Returns:
            Score (0-10)
        """
        total_volume = market_context.get("total_volume", 0)

        if total_volume == 0:
            logger.warning("Volume not available, returning low score")
            return 3.0

        # Normalize volume - currency-dependent
        if strategy.currency == "BTC":
            score = self.normalize_score(
                score=total_volume,
                min_score=500,
                max_score=10000
            )
        elif strategy.currency == "ETH":
            score = self.normalize_score(
                score=total_volume,
                min_score=2000,
                max_score=50000
            )
        else:
            score = self.normalize_score(
                score=total_volume,
                min_score=500,
                max_score=20000
            )

        logger.debug(
            f"{strategy.name}: Total volume={total_volume:.0f}, volume_score={score:.2f}"
        )

        return score

    def _score_market_regime(self, strategy, market_context: Dict) -> float:
        """
        Score based on market regime detection.

        Uses sophisticated regime detection combining:
        - Technical indicators (SMA, RSI, MACD, ADX, ATR)
        - On-chain metrics (funding rate, P/C ratio, DVOL)
        - External sentiment (Fear & Greed Index, BTC dominance)

        Args:
            strategy: Strategy instance
            market_context: Market data (may contain pre-computed regime)

        Returns:
            Score (0-10)
        """
        try:
            # Check if regime was provided in market context (from config)
            market_regime = market_context.get("market_regime")
            regime_composite_score = market_context.get("regime_composite_score")

            # If not provided, attempt to detect regime
            if market_regime is None:
                market_regime, regime_composite_score = self._detect_market_regime(strategy)

            if market_regime is None:
                logger.warning("Market regime detection failed, returning neutral score")
                return 5.0

            # Score based on regime alignment with strategy type
            strategy_type = strategy.strategy_type
            score = self._score_regime_alignment(
                strategy_type,
                market_regime,
                regime_composite_score
            )

            logger.debug(
                f"{strategy.name}: Market regime={market_regime}, "
                f"regime_score={regime_composite_score:.1f}, "
                f"strategy_type={strategy_type}, trend_score={score:.2f}"
            )

            return score

        except Exception as e:
            logger.error(f"Error in regime-based trend analysis: {e}", exc_info=True)
            return 5.0  # Neutral score on error

    def _detect_market_regime(self, strategy) -> tuple:
        """
        Detect current market regime using RegimeDetectionService.

        Args:
            strategy: Strategy instance with currency

        Returns:
            Tuple of (regime_name, composite_score) or (None, None) on error
        """
        try:
            from coding.service.regime.regime_detection_service import RegimeDetectionService
            from coding.service.deribit.deribit_api_service import DeribitApiService

            # Use existing API service if available, otherwise create new one
            with DeribitApiService() as api_service:
                regime_service = RegimeDetectionService(
                    api_service=api_service,
                    repository=self.repository
                )

                result = regime_service.detect_regime(strategy.currency)

                if "error" in result:
                    logger.error(f"Regime detection error: {result['error']}")
                    return None, None

                regime = result.get("regime")
                composite_score = result.get("composite_score")

                logger.info(
                    f"Detected market regime for {strategy.currency}: "
                    f"{regime} (score={composite_score:.1f})"
                )

                return regime, composite_score

        except Exception as e:
            logger.error(f"Failed to detect market regime: {e}", exc_info=True)
            return None, None

    def _score_regime_alignment(
        self,
        strategy_type: str,
        regime: str,
        regime_composite_score: float
    ) -> float:
        """
        Score strategy alignment with market regime.

        Args:
            strategy_type: Strategy type (directional_bullish, directional_bearish, etc.)
            regime: Market regime (Strong Bullish, Weak Bullish, Sideways, etc.)
            regime_composite_score: Regime composite score (-100 to +100)

        Returns:
            Alignment score (0-10)
        """
        # Map regime to numeric scale for alignment
        regime_score_map = {
            "Strong Bullish": 10.0,
            "Weak Bullish": 7.0,
            "Sideways": 5.0,
            "Weak Bearish": 3.0,
            "Strong Bearish": 0.0
        }

        base_regime_score = regime_score_map.get(regime, 5.0)

        if strategy_type == "directional_bullish":
            # Bullish strategies align with bullish regimes
            # Strong Bullish = 10, Weak Bullish = 7, Sideways = 5, Weak Bearish = 3, Strong Bearish = 0
            score = base_regime_score

        elif strategy_type == "directional_bearish":
            # Bearish strategies align with bearish regimes (invert the scale)
            # Strong Bearish = 10, Weak Bearish = 7, Sideways = 5, Weak Bullish = 3, Strong Bullish = 0
            score = 10.0 - base_regime_score

        elif strategy_type == "volatility_long":
            # Volatility strategies prefer extreme regimes (strong bull or strong bear)
            # Score based on how far from sideways
            distance_from_neutral = abs(regime_composite_score)
            # 0-30 = sideways (low score), 30-60 = moderate (medium), 60+ = extreme (high)
            score = self.normalize_score(
                score=distance_from_neutral,
                min_score=0,
                max_score=80
            )

        elif strategy_type == "volatility_short":
            # Vol short wants low volatility (sideways regime)
            # Score based on proximity to sideways
            distance_from_neutral = abs(regime_composite_score)
            # Closer to 0 = better
            score = self.normalize_score(
                score=80 - distance_from_neutral,
                min_score=0,
                max_score=80
            )

        else:
            # Neutral strategies (spreads, etc.) prefer sideways markets
            # Score based on proximity to sideways
            distance_from_neutral = abs(regime_composite_score)
            score = self.normalize_score(
                score=60 - distance_from_neutral,
                min_score=0,
                max_score=60
            )

        return score

    def _get_max_pain_trend(self, strategy) -> str:
        """
        Get max pain trend direction.

        Queries last 5 max pain values and compares first vs last.
        - Decreasing max pain = bullish (dealers hedging less puts)
        - Increasing max pain = bearish (dealers hedging more puts)

        Args:
            strategy: Strategy instance

        Returns:
            Trend direction: "increasing", "decreasing", "neutral"
        """
        if not self.repository:
            return "neutral"

        try:
            history = self.repository.get_max_pain_history(
                currency=strategy.currency,
                expiration=strategy.expiration,
                limit=5
            )

            if len(history) < 2:
                logger.debug("Insufficient max pain history for trend analysis")
                return "neutral"

            # Compare most recent vs oldest in window
            recent = history[-1]["max_pain_strike"]  # Last in chronological order
            older = history[0]["max_pain_strike"]    # First in chronological order

            if recent == 0 or older == 0:
                return "neutral"

            pct_change = ((recent - older) / older) * 100

            # Threshold: ±2% change to be significant
            if pct_change < -2:
                logger.debug(
                    f"Max pain trend: DECREASING {pct_change:.2f}% "
                    f"(from {older:.2f} to {recent:.2f})"
                )
                return "decreasing"
            elif pct_change > 2:
                logger.debug(
                    f"Max pain trend: INCREASING {pct_change:.2f}% "
                    f"(from {older:.2f} to {recent:.2f})"
                )
                return "increasing"
            else:
                logger.debug(f"Max pain trend: NEUTRAL {pct_change:.2f}%")
                return "neutral"

        except Exception as e:
            logger.error(f"Error calculating max pain trend: {e}", exc_info=True)
            return "neutral"

    def _get_volume_trend(self, strategy) -> str:
        """
        Get volume trend direction.

        Queries last 5 volume readings and compares first vs last.
        - Increasing volume = stronger conviction
        - Decreasing volume = weakening conviction

        Args:
            strategy: Strategy instance

        Returns:
            Trend direction: "increasing", "decreasing", "neutral"
        """
        if not self.repository:
            return "neutral"

        try:
            history = self.repository.get_volume_history(
                currency=strategy.currency,
                expiration=strategy.expiration,
                limit=5
            )

            if len(history) < 2:
                logger.debug("Insufficient volume history for trend analysis")
                return "neutral"

            # Compare most recent vs oldest in window
            recent = history[-1]["total_volume"]  # Last in chronological order
            older = history[0]["total_volume"]    # First in chronological order

            if recent == 0 or older == 0:
                return "neutral"

            pct_change = ((recent - older) / older) * 100

            # Threshold: ±20% change to be significant (volume is more volatile)
            if pct_change < -20:
                logger.debug(
                    f"Volume trend: DECREASING {pct_change:.2f}% "
                    f"(from {older:.2f} to {recent:.2f})"
                )
                return "decreasing"
            elif pct_change > 20:
                logger.debug(
                    f"Volume trend: INCREASING {pct_change:.2f}% "
                    f"(from {older:.2f} to {recent:.2f})"
                )
                return "increasing"
            else:
                logger.debug(f"Volume trend: NEUTRAL {pct_change:.2f}%")
                return "neutral"

        except Exception as e:
            logger.error(f"Error calculating volume trend: {e}", exc_info=True)
            return "neutral"
