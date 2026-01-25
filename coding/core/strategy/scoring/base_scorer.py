"""
Base scorer abstract class for strategy evaluation.

All scorers must inherit from BaseScorer and implement the scoring logic.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict

logger = logging.getLogger(__name__)


class BaseScorer(ABC):
    """
    Abstract base class for strategy scorers.

    Scorers evaluate strategies based on specific criteria and return a score
    on a 0-10 scale along with a breakdown of component scores.

    All subclasses must implement:
    - calculate_score: Return overall score (0-10)
    - get_breakdown: Return component scores as dictionary
    """

    @abstractmethod
    def calculate_score(self, strategy, market_context: Dict) -> float:
        """
        Calculate overall score for a strategy.

        Args:
            strategy: Strategy instance (BaseStrategy subclass)
            market_context: Dictionary with market data (on-chain metrics, volatility, etc.)

        Returns:
            Score from 0 to 10 (higher is better)
        """
        pass

    @abstractmethod
    def get_breakdown(self, strategy, market_context: Dict) -> Dict[str, float]:
        """
        Get detailed breakdown of component scores.

        Args:
            strategy: Strategy instance (BaseStrategy subclass)
            market_context: Dictionary with market data

        Returns:
            Dictionary mapping component names to scores (0-10 scale)
        """
        pass

    def normalize_score(self, score: float, min_score: float = 0.0, max_score: float = 10.0) -> float:
        """
        Normalize a score to 0-10 range.

        Args:
            score: Raw score value
            min_score: Minimum expected score (maps to 0)
            max_score: Maximum expected score (maps to 10)

        Returns:
            Normalized score (0-10)
        """
        if max_score == min_score:
            logger.warning(f"max_score equals min_score ({max_score}), returning 5.0")
            return 5.0

        normalized = 10.0 * (score - min_score) / (max_score - min_score)

        # Clamp to 0-10 range
        return max(0.0, min(10.0, normalized))

    def clamp_score(self, score: float) -> float:
        """
        Clamp score to valid 0-10 range.

        Args:
            score: Score value

        Returns:
            Clamped score (0-10)
        """
        return max(0.0, min(10.0, score))

    def weighted_average(self, components: Dict[str, float], weights: Dict[str, float]) -> float:
        """
        Calculate weighted average of component scores.

        Args:
            components: Dictionary of component scores
            weights: Dictionary of component weights (should sum to 1.0)

        Returns:
            Weighted average score
        """
        if not components:
            logger.warning("No components provided for weighted average")
            return 0.0

        # Validate all components have weights
        missing_weights = set(components.keys()) - set(weights.keys())
        if missing_weights:
            logger.warning(f"Missing weights for components: {missing_weights}")

        # Calculate weighted sum
        total_score = 0.0
        total_weight = 0.0

        for component_name, component_score in components.items():
            weight = weights.get(component_name, 0.0)
            total_score += component_score * weight
            total_weight += weight

        # Normalize if weights don't sum to 1.0
        if total_weight > 0 and abs(total_weight - 1.0) > 0.01:
            logger.debug(f"Weights sum to {total_weight}, normalizing")
            total_score /= total_weight

        return total_score

    def __repr__(self) -> str:
        """String representation of scorer."""
        return f"{self.__class__.__name__}()"
