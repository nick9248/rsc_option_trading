"""
Strategy factory for creating strategy instances.

This module provides a registry and factory function for creating strategy instances
dynamically based on strategy name.
"""

import logging
from typing import Dict, List, Optional, Type

from .base_strategy import BaseStrategy
from .long_call import LongCall
from .long_put import LongPut

logger = logging.getLogger(__name__)


# Strategy registry mapping strategy names to classes
STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "Long Call": LongCall,
    "Long Put": LongPut,
}


def create_strategy(
    name: str,
    currency: str,
    expiration: str,
    underlying_price: float,
    take_profit_percentage: Optional[float] = None
) -> BaseStrategy:
    """
    Create a strategy instance by name.

    Args:
        name: Strategy name (e.g., "Long Call", "Long Put")
        currency: Currency symbol (e.g., "BTC", "ETH")
        expiration: Expiration date string (e.g., "31JAN25")
        underlying_price: Current underlying asset price
        take_profit_percentage: Optional take profit target as % gain

    Returns:
        Strategy instance

    Raises:
        ValueError: If strategy name is not registered
    """
    if name not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise ValueError(
            f"Unknown strategy: {name}. Available strategies: {available}"
        )

    strategy_class = STRATEGY_REGISTRY[name]

    logger.debug(
        f"Creating strategy: {name} for {currency}-{expiration} "
        f"at underlying_price={underlying_price}"
    )

    return strategy_class(
        currency=currency,
        expiration=expiration,
        underlying_price=underlying_price,
        take_profit_percentage=take_profit_percentage
    )


def get_available_strategies() -> List[str]:
    """
    Get list of available strategy names.

    Returns:
        List of strategy names that can be created
    """
    return list(STRATEGY_REGISTRY.keys())


def get_strategy_metadata() -> List[Dict[str, str]]:
    """
    Get metadata about all available strategies.

    Returns:
        List of dictionaries containing strategy metadata
    """
    metadata = []

    for name, strategy_class in STRATEGY_REGISTRY.items():
        # Create a temporary instance to get properties
        # We use dummy values for initialization
        temp_instance = strategy_class(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100000.0
        )

        metadata.append({
            "name": name,
            "strategy_type": temp_instance.strategy_type,
            "class_name": strategy_class.__name__
        })

    return metadata


def register_strategy(name: str, strategy_class: Type[BaseStrategy]) -> None:
    """
    Register a new strategy in the registry.

    This allows for dynamic strategy registration, useful for plugins or extensions.

    Args:
        name: Strategy name
        strategy_class: Strategy class (must inherit from BaseStrategy)

    Raises:
        TypeError: If strategy_class doesn't inherit from BaseStrategy
        ValueError: If strategy name already registered
    """
    if not issubclass(strategy_class, BaseStrategy):
        raise TypeError(
            f"Strategy class must inherit from BaseStrategy, got {strategy_class}"
        )

    if name in STRATEGY_REGISTRY:
        logger.warning(f"Overwriting existing strategy registration: {name}")

    STRATEGY_REGISTRY[name] = strategy_class
    logger.info(f"Registered strategy: {name} -> {strategy_class.__name__}")
