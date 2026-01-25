"""
Composite scorer combining intrinsic and on-chain scoring.

The composite scorer is the central scoring engine that combines multiple
scoring dimensions with configurable weights and applies market regime adjustments.
"""

import logging
from typing import Dict, Optional, Tuple

from .base_scorer import BaseScorer
from .intrinsic_scorer import IntrinsicScorer
from .on_chain_scorer import OnChainScorer

logger = logging.getLogger(__name__)


class CompositeScorer:
    """
    Composite scorer combining intrinsic and on-chain metrics.

    This is the "brain" of the evaluation system, combining:
    - Intrinsic metrics (risk/reward, cost, greeks, breakeven)
    - On-chain metrics (max pain, GEX/DEX, OI, P/C, volume, trends)

    Features:
    - Configurable weights between intrinsic and on-chain
    - Market regime awareness with penalty system
    - Full score breakdown for transparency
    """

    # Default weights
    DEFAULT_INTRINSIC_WEIGHT = 0.5
    DEFAULT_ON_CHAIN_WEIGHT = 0.5

    # Regime penalty multipliers
    REGIME_PENALTY_STRONG = 0.5  # 50% score reduction for counter-regime strategies
    REGIME_PENALTY_MODERATE = 0.75  # 25% score reduction

    def __init__(
        self,
        intrinsic_scorer: Optional[IntrinsicScorer] = None,
        on_chain_scorer: Optional[OnChainScorer] = None,
        intrinsic_weight: float = DEFAULT_INTRINSIC_WEIGHT,
        on_chain_weight: float = DEFAULT_ON_CHAIN_WEIGHT
    ):
        """
        Initialize composite scorer.

        Args:
            intrinsic_scorer: Intrinsic scorer instance (created if None)
            on_chain_scorer: On-chain scorer instance (created if None)
            intrinsic_weight: Weight for intrinsic score (0-1)
            on_chain_weight: Weight for on-chain score (0-1)

        Raises:
            ValueError: If weights don't sum to 1.0
        """
        self.intrinsic_scorer = intrinsic_scorer or IntrinsicScorer()
        self.on_chain_scorer = on_chain_scorer or OnChainScorer()

        # Validate and normalize weights
        total_weight = intrinsic_weight + on_chain_weight

        if not 0.99 <= total_weight <= 1.01:  # Allow small floating point error
            logger.warning(
                f"Weights don't sum to 1.0 (sum={total_weight}). Normalizing..."
            )
            intrinsic_weight /= total_weight
            on_chain_weight /= total_weight

        self.intrinsic_weight = intrinsic_weight
        self.on_chain_weight = on_chain_weight

        logger.debug(
            f"CompositeScorer initialized: intrinsic_weight={intrinsic_weight:.2f}, "
            f"on_chain_weight={on_chain_weight:.2f}"
        )

    def evaluate_strategy(
        self,
        strategy,
        market_context: Dict,
        market_regime: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Evaluate strategy and return all scores with breakdowns.

        Args:
            strategy: Strategy instance
            market_context: Market data for on-chain scoring
            market_regime: Optional market regime ("bullish", "bearish", "neutral")

        Returns:
            Dictionary with:
            - intrinsic_score (float)
            - on_chain_score (float)
            - composite_score (float)
            - intrinsic_breakdown (dict)
            - on_chain_breakdown (dict)
            - regime_penalty_applied (bool)
            - regime_penalty_multiplier (float)
        """
        # Calculate component scores
        intrinsic_score = self.intrinsic_scorer.calculate_score(strategy, market_context)
        on_chain_score = self.on_chain_scorer.calculate_score(strategy, market_context)

        # Get breakdowns
        intrinsic_breakdown = self.intrinsic_scorer.get_breakdown(strategy, market_context)
        on_chain_breakdown = self.on_chain_scorer.get_breakdown(strategy, market_context)

        # Calculate composite score (weighted average)
        composite_score = (
            (intrinsic_score * self.intrinsic_weight) +
            (on_chain_score * self.on_chain_weight)
        )

        # Apply regime penalty if applicable
        regime_penalty_applied = False
        regime_penalty_multiplier = 1.0

        if market_regime is not None and market_regime != "neutral":
            penalty_multiplier = self.calculate_regime_penalty(
                strategy.strategy_type,
                market_regime
            )

            if penalty_multiplier < 1.0:
                regime_penalty_applied = True
                regime_penalty_multiplier = penalty_multiplier
                composite_score *= penalty_multiplier

                logger.info(
                    f"{strategy.name}: Regime penalty applied. "
                    f"Regime={market_regime}, StrategyType={strategy.strategy_type}, "
                    f"Multiplier={penalty_multiplier:.2f}, "
                    f"Score: {composite_score/(penalty_multiplier):.2f} -> {composite_score:.2f}"
                )

        # Clamp final score to 0-10
        composite_score = max(0.0, min(10.0, composite_score))

        logger.info(
            f"{strategy.name} evaluation: "
            f"intrinsic={intrinsic_score:.2f}, "
            f"on_chain={on_chain_score:.2f}, "
            f"composite={composite_score:.2f}"
        )

        return {
            "intrinsic_score": intrinsic_score,
            "on_chain_score": on_chain_score,
            "composite_score": composite_score,
            "intrinsic_breakdown": intrinsic_breakdown,
            "on_chain_breakdown": on_chain_breakdown,
            "regime_penalty_applied": regime_penalty_applied,
            "regime_penalty_multiplier": regime_penalty_multiplier
        }

    def calculate_regime_penalty(
        self,
        strategy_type: str,
        market_regime: str
    ) -> float:
        """
        Calculate regime penalty multiplier for a strategy.

        Penalizes strategies that are counter to the market regime.

        Args:
            strategy_type: Strategy type (e.g., "directional_bullish")
            market_regime: Market regime ("bullish", "bearish", "neutral")

        Returns:
            Penalty multiplier (0.5-1.0, where 1.0 = no penalty)
        """
        if market_regime == "neutral" or market_regime is None:
            return 1.0  # No penalty for neutral regime

        # Define regime conflicts
        if market_regime == "bearish":
            if strategy_type == "directional_bullish":
                logger.debug(
                    f"Strong penalty: Bullish strategy in bearish regime"
                )
                return self.REGIME_PENALTY_STRONG

            elif strategy_type == "volatility_short":
                # Selling volatility in bearish regime is risky
                logger.debug(
                    f"Moderate penalty: Vol short strategy in bearish regime"
                )
                return self.REGIME_PENALTY_MODERATE

        elif market_regime == "bullish":
            if strategy_type == "directional_bearish":
                logger.debug(
                    f"Strong penalty: Bearish strategy in bullish regime"
                )
                return self.REGIME_PENALTY_STRONG

        # No penalty for aligned or neutral strategies
        return 1.0

    def set_weights(self, intrinsic_weight: float, on_chain_weight: float) -> None:
        """
        Update scorer weights.

        Args:
            intrinsic_weight: New intrinsic weight
            on_chain_weight: New on-chain weight

        Raises:
            ValueError: If weights don't sum to 1.0
        """
        total_weight = intrinsic_weight + on_chain_weight

        if not 0.99 <= total_weight <= 1.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total_weight} "
                f"(intrinsic={intrinsic_weight}, on_chain={on_chain_weight})"
            )

        self.intrinsic_weight = intrinsic_weight
        self.on_chain_weight = on_chain_weight

        logger.info(
            f"Weights updated: intrinsic={intrinsic_weight:.2f}, "
            f"on_chain={on_chain_weight:.2f}"
        )

    def __repr__(self) -> str:
        """String representation of composite scorer."""
        return (
            f"CompositeScorer("
            f"intrinsic_weight={self.intrinsic_weight:.2f}, "
            f"on_chain_weight={self.on_chain_weight:.2f})"
        )
