"""
Data models for strategy signals and configurations.

This module contains dataclasses for strategy evaluation results, configurations,
and signal representations.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """
    Represents a scored strategy signal.

    This is the primary output of the strategy evaluation system, containing
    all information needed to understand and execute a strategy.
    """
    # Identification
    strategy_name: str
    currency: str
    expiration: str
    generated_at: datetime

    # Structure
    legs: List[Dict]  # Serialized StrategyLeg objects

    # Scores (0-10 scale)
    intrinsic_score: float
    on_chain_score: float
    composite_score: float

    # Score breakdowns
    intrinsic_breakdown: Dict[str, float]
    on_chain_breakdown: Dict[str, float]

    # Market context
    underlying_price: float
    implied_volatility: Optional[float] = None
    max_pain_strike: Optional[float] = None

    # Risk metrics
    max_risk: float = 0.0
    max_profit: Optional[float] = None
    total_cost: float = 0.0
    breakeven_points: List[float] = field(default_factory=list)
    max_loss_percentage: float = 0.0  # Max loss as % of underlying

    # Exit management
    take_profit_percentage: Optional[float] = None  # Optional take profit target

    # Market regime
    market_regime: Optional[str] = None  # "bullish", "bearish", "neutral"

    # Greeks
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0

    # Ranking
    rank: Optional[int] = None

    # Chart path
    chart_path: Optional[str] = None

    def __post_init__(self):
        """Validate signal data."""
        # Validate scores are in 0-10 range
        for score_name, score_value in [
            ("intrinsic_score", self.intrinsic_score),
            ("on_chain_score", self.on_chain_score),
            ("composite_score", self.composite_score)
        ]:
            if not 0 <= score_value <= 10:
                logger.warning(
                    f"{score_name} out of range: {score_value}. "
                    f"Expected 0-10. Clamping to valid range."
                )
                # Clamp to valid range
                if score_value < 0:
                    setattr(self, score_name, 0.0)
                elif score_value > 10:
                    setattr(self, score_name, 10.0)

    def to_dict(self) -> Dict:
        """
        Convert signal to dictionary for database storage.

        Returns:
            Dictionary representation of signal
        """
        return {
            "strategy_name": self.strategy_name,
            "currency": self.currency,
            "expiration": self.expiration,
            "generated_at": self.generated_at,
            "legs": self.legs,
            "intrinsic_score": self.intrinsic_score,
            "on_chain_score": self.on_chain_score,
            "composite_score": self.composite_score,
            "intrinsic_breakdown": self.intrinsic_breakdown,
            "on_chain_breakdown": self.on_chain_breakdown,
            "underlying_price": self.underlying_price,
            "implied_volatility": self.implied_volatility,
            "max_pain_strike": self.max_pain_strike,
            "max_risk": self.max_risk,
            "max_profit": self.max_profit,
            "total_cost": self.total_cost,
            "breakeven_points": self.breakeven_points,
            "max_loss_percentage": self.max_loss_percentage,
            "take_profit_percentage": self.take_profit_percentage,
            "market_regime": self.market_regime,
            "net_delta": self.net_delta,
            "net_gamma": self.net_gamma,
            "net_theta": self.net_theta,
            "net_vega": self.net_vega,
            "rank": self.rank,
            "chart_path": self.chart_path
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "StrategySignal":
        """
        Create signal from dictionary (e.g., from database).

        Args:
            data: Dictionary with signal data

        Returns:
            StrategySignal instance
        """
        return cls(**data)

    def __repr__(self) -> str:
        """String representation of signal."""
        return (
            f"StrategySignal("
            f"strategy={self.strategy_name}, "
            f"currency={self.currency}, "
            f"expiration={self.expiration}, "
            f"composite_score={self.composite_score:.2f}, "
            f"rank={self.rank})"
        )


@dataclass
class StrikeConfig:
    """
    Configuration for strike selection.

    Defines how to select strikes for a strategy.
    """
    method: str  # "by_delta", "by_moneyness", "by_strike"
    target_delta: Optional[float] = None  # For "by_delta" method
    moneyness_pct: Optional[float] = None  # For "by_moneyness" method
    specific_strike: Optional[float] = None  # For "by_strike" method
    quantity: int = 1  # Number of contracts

    def __post_init__(self):
        """Validate strike configuration."""
        if self.method not in ["by_delta", "by_moneyness", "by_strike"]:
            raise ValueError(
                f"Invalid method: {self.method}. "
                f"Must be 'by_delta', 'by_moneyness', or 'by_strike'"
            )

        # Validate required parameters based on method
        if self.method == "by_delta" and self.target_delta is None:
            raise ValueError("target_delta required for by_delta method")

        if self.method == "by_moneyness" and self.moneyness_pct is None:
            raise ValueError("moneyness_pct required for by_moneyness method")

        if self.method == "by_strike" and self.specific_strike is None:
            raise ValueError("specific_strike required for by_strike method")

        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "method": self.method,
            "target_delta": self.target_delta,
            "moneyness_pct": self.moneyness_pct,
            "specific_strike": self.specific_strike,
            "quantity": self.quantity
        }


@dataclass
class StrategyConfig:
    """
    Configuration for strategy evaluation.

    Defines which strategies to evaluate, how to select strikes,
    scoring weights, and filters.
    """
    # Strategy selection
    strategy_names: List[str]  # List of strategy names to evaluate
    expirations: List[str]  # List of expirations to evaluate

    # Strike configuration (per strategy)
    strike_configs: Dict[str, StrikeConfig] = field(default_factory=dict)

    # Scoring weights (must sum to 1.0)
    intrinsic_weight: float = 0.5
    on_chain_weight: float = 0.5

    # Filters
    min_intrinsic_score: float = 0.0  # Minimum intrinsic score (0-10)
    min_on_chain_score: float = 0.0  # Minimum on-chain score (0-10)
    min_composite_score: float = 0.0  # Minimum composite score (0-10)
    max_loss_filter: Optional[float] = None  # Max loss % (e.g., 2.0 for 2%)
    max_budget: Optional[float] = None  # Max budget constraint for spreads (USD)

    # Exit management
    take_profit_percentage: Optional[float] = None  # Optional take profit target

    # Market regime
    market_regime: Optional[str] = None  # "bullish", "bearish", "neutral", None

    # Results
    top_n: int = 10  # Number of top signals to return

    def __post_init__(self):
        """Validate configuration."""
        # Validate weights sum to 1.0
        total_weight = self.intrinsic_weight + self.on_chain_weight
        if not 0.99 <= total_weight <= 1.01:  # Allow small floating point error
            logger.warning(
                f"Weights don't sum to 1.0 (sum={total_weight}). Normalizing..."
            )
            # Normalize weights
            self.intrinsic_weight /= total_weight
            self.on_chain_weight /= total_weight

        # Validate score filters
        for filter_name, filter_value in [
            ("min_intrinsic_score", self.min_intrinsic_score),
            ("min_on_chain_score", self.min_on_chain_score),
            ("min_composite_score", self.min_composite_score)
        ]:
            if not 0 <= filter_value <= 10:
                raise ValueError(
                    f"{filter_name} must be 0-10, got {filter_value}"
                )

        # Validate max_loss_filter
        if self.max_loss_filter is not None and self.max_loss_filter <= 0:
            raise ValueError(
                f"max_loss_filter must be positive, got {self.max_loss_filter}"
            )

        # Validate market_regime
        if self.market_regime is not None:
            valid_regimes = ["bullish", "bearish", "neutral"]
            if self.market_regime not in valid_regimes:
                raise ValueError(
                    f"market_regime must be one of {valid_regimes} or None, "
                    f"got {self.market_regime}"
                )

        # Validate top_n
        if self.top_n <= 0:
            raise ValueError(f"top_n must be positive, got {self.top_n}")

        # Validate strategy_names
        if not self.strategy_names:
            raise ValueError("strategy_names cannot be empty")

        # Validate expirations
        if not self.expirations:
            raise ValueError("expirations cannot be empty")

    def get_strike_config(self, strategy_name: str) -> Optional[StrikeConfig]:
        """
        Get strike configuration for a strategy.

        Args:
            strategy_name: Strategy name

        Returns:
            StrikeConfig if configured, None otherwise
        """
        return self.strike_configs.get(strategy_name)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "strategy_names": self.strategy_names,
            "expirations": self.expirations,
            "strike_configs": {
                name: config.to_dict()
                for name, config in self.strike_configs.items()
            },
            "intrinsic_weight": self.intrinsic_weight,
            "on_chain_weight": self.on_chain_weight,
            "min_intrinsic_score": self.min_intrinsic_score,
            "min_on_chain_score": self.min_on_chain_score,
            "min_composite_score": self.min_composite_score,
            "max_loss_filter": self.max_loss_filter,
            "take_profit_percentage": self.take_profit_percentage,
            "market_regime": self.market_regime,
            "top_n": self.top_n
        }


@dataclass
class EvaluationResult:
    """
    Result of strategy evaluation.

    Contains success status, signals, and any errors encountered.
    """
    success: bool
    signals: List[StrategySignal] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)  # {strategy_name, error_msg}
    evaluation_time_seconds: float = 0.0

    def add_error(self, strategy_name: str, error_msg: str) -> None:
        """
        Add an error for a strategy.

        Args:
            strategy_name: Strategy that failed
            error_msg: Error message
        """
        self.errors.append({
            "strategy_name": strategy_name,
            "error": error_msg
        })
        logger.error(f"Strategy evaluation error ({strategy_name}): {error_msg}")

    def has_errors(self) -> bool:
        """Check if evaluation has any errors."""
        return len(self.errors) > 0

    def get_successful_count(self) -> int:
        """Get count of successfully evaluated strategies."""
        return len(self.signals)

    def get_failed_count(self) -> int:
        """Get count of failed strategy evaluations."""
        return len(self.errors)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "signals": [signal.to_dict() for signal in self.signals],
            "errors": self.errors,
            "evaluation_time_seconds": self.evaluation_time_seconds,
            "successful_count": self.get_successful_count(),
            "failed_count": self.get_failed_count()
        }

    def __repr__(self) -> str:
        """String representation of result."""
        return (
            f"EvaluationResult("
            f"success={self.success}, "
            f"signals={len(self.signals)}, "
            f"errors={len(self.errors)}, "
            f"time={self.evaluation_time_seconds:.2f}s)"
        )
