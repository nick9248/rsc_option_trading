"""
Chart generator for single-leg strategies (Long Call, Long Put).

Inherits all functionality from BaseStrategyChartGenerator with no modifications.
This ensures existing Long Call and Long Put charts continue to work exactly as before.

Future: Can override specific methods here if single-leg strategies need customization.
"""

import logging

from .base_chart_generator import BaseStrategyChartGenerator

logger = logging.getLogger(__name__)


class SingleLegChartGenerator(BaseStrategyChartGenerator):
    """
    Chart generator for single-leg strategies (Long Call, Long Put).

    Currently inherits all behavior from BaseStrategyChartGenerator.
    No customization needed - the base implementation works perfectly.

    Design rationale:
    - Long Call and Long Put have been working correctly
    - Don't change what works
    - If future single-leg customizations are needed, override methods here
    - Separation ensures spread strategies don't affect single-leg strategies
    """

    # No overrides - inherits everything from base class
    # This is intentional - existing behavior is correct
    pass
