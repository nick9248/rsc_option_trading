"""
Strategy definitions package.

Contains strategy class implementations.
"""

from .base_strategy import BaseStrategy, StrategyLeg
from .bear_put_spread import BearPutSpread
from .bull_call_spread import BullCallSpread
from .long_call import LongCall
from .long_put import LongPut
from .strategy_factory import (
    STRATEGY_REGISTRY,
    create_strategy,
    get_available_strategies,
    get_strategy_metadata,
    is_spread_strategy,
    register_strategy,
)

__all__ = [
    "BaseStrategy",
    "StrategyLeg",
    "LongCall",
    "LongPut",
    "BullCallSpread",
    "BearPutSpread",
    "STRATEGY_REGISTRY",
    "create_strategy",
    "get_available_strategies",
    "get_strategy_metadata",
    "is_spread_strategy",
    "register_strategy",
]
