"""
Strategy data models package.

Contains dataclasses for signals, configurations, and results.
"""

from .spread_config import SpreadStrikeConfig
from .strategy_signal import (
    EvaluationResult,
    StrategyConfig,
    StrategySignal,
    StrikeConfig,
)

__all__ = [
    "StrategySignal",
    "StrategyConfig",
    "StrikeConfig",
    "EvaluationResult",
    "SpreadStrikeConfig",
]
