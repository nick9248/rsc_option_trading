"""
Strategy definitions package.

Contains strategy class implementations.
"""

from .base_strategy import BaseStrategy, StrategyLeg
from .bull_call_spread import BullCallSpread
from .long_call import LongCall
from .long_put import LongPut
from .strategy_factory import (
    STRATEGY_REGISTRY,
    create_strategy,
    get_available_strategies,
    get_strategy_metadata,
    register_strategy,
)

__all__ = [
    "BaseStrategy",
    "StrategyLeg",
    "LongCall",
    "LongPut",
    "BullCallSpread",
    "STRATEGY_REGISTRY",
    "create_strategy",
    "get_available_strategies",
    "get_strategy_metadata",
    "register_strategy",
]
