"""
Option strategy core package.

This package contains strategy definitions, models, and scoring logic.
"""

from .definitions import (
    BaseStrategy,
    BearPutSpread,
    BullCallSpread,
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
    "BullCallSpread",
    "BearPutSpread",
    "create_strategy",
    "get_available_strategies",
]
