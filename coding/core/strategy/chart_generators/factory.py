"""
Factory for creating strategy-specific chart generators.

Selects the appropriate chart generator based on strategy type:
- Single-leg strategies → SingleLegChartGenerator
- Spread strategies → SpreadChartGenerator

This ensures:
- Each strategy type gets the right generator
- Easy to add new strategy types
- Type-specific customizations don't affect other types
"""

import logging
from pathlib import Path
from typing import Optional

from .single_leg_chart_generator import SingleLegChartGenerator
from .spread_chart_generator import SpreadChartGenerator
from .base_chart_generator import BaseStrategyChartGenerator

logger = logging.getLogger(__name__)

# Strategy type to generator mapping
STRATEGY_GENERATORS = {
    # Single-leg strategies
    "Long Call": SingleLegChartGenerator,
    "Long Put": SingleLegChartGenerator,

    # Spread strategies
    "Bull Call Spread": SpreadChartGenerator,
    "Bear Put Spread": SpreadChartGenerator,
    "Bull Put Spread": SpreadChartGenerator,
    "Bear Call Spread": SpreadChartGenerator,

    # Future strategies can be added here
    # "Iron Condor": ComplexChartGenerator,
    # "Butterfly": ComplexChartGenerator,
}


def get_chart_generator(
    strategy_name: str,
    output_base_dir: Optional[Path] = None,
    repository=None
) -> BaseStrategyChartGenerator:
    """
    Get the appropriate chart generator for a strategy.

    Args:
        strategy_name: Name of the strategy (e.g., "Long Call", "Bull Call Spread")
        output_base_dir: Base directory for chart output
        repository: Database repository for trend analysis

    Returns:
        Appropriate chart generator instance for the strategy type

    Raises:
        ValueError: If strategy_name is not recognized

    Examples:
        >>> gen = get_chart_generator("Long Call")
        >>> isinstance(gen, SingleLegChartGenerator)
        True

        >>> gen = get_chart_generator("Bull Call Spread")
        >>> isinstance(gen, SpreadChartGenerator)
        True
    """
    generator_class = STRATEGY_GENERATORS.get(strategy_name)

    if not generator_class:
        logger.warning(
            f"No specific generator found for '{strategy_name}'. "
            f"Using SingleLegChartGenerator as fallback."
        )
        generator_class = SingleLegChartGenerator

    logger.debug(f"Using {generator_class.__name__} for {strategy_name}")

    return generator_class(output_base_dir=output_base_dir, repository=repository)
