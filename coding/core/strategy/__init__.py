"""
Option strategy core package.

This package contains strategy definitions, models, and scoring logic.
"""

from .definitions import (
    BaseStrategy,
    LongCall,
    LongPut,
    StrategyLeg,
    create_strategy,
    get_available_strategies,
)

__all__ = [
    "BaseStrategy",
    "StrategyLeg",
    "LongCall",
    "LongPut",
    "create_strategy",
    "get_available_strategies",
]
