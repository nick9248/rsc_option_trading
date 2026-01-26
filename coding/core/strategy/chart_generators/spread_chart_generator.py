"""
Chart generator for spread strategies (Bull Call Spread, Bear Put Spread, etc.).

Inherits all functionality from BaseStrategyChartGenerator.
Currently no customizations, but provides a place for spread-specific features.

Future enhancements could include:
- Visual annotations for max profit/max loss zones
- Spread width indicators
- Risk/reward ratio callouts
- Different color schemes for spreads
"""

import logging

from .base_chart_generator import BaseStrategyChartGenerator

logger = logging.getLogger(__name__)


class SpreadChartGenerator(BaseStrategyChartGenerator):
    """
    Chart generator for spread strategies (Bull Call Spread, etc.).

    Currently inherits all behavior from BaseStrategyChartGenerator.
    The generic implementation handles spreads correctly via multi-leg support.

    Design rationale:
    - Base implementation already handles multiple legs generically
    - Bull Call Spread charts work correctly as-is
    - If future spread-specific features are needed (annotations, zones, etc.), override methods here
    - Separation ensures single-leg strategies don't get spread-specific clutter

    Future customization examples:
    - _add_max_profit_zone(): Highlight the flat profit zone for spreads
    - _add_spread_width_annotation(): Show strike width and ratios
    - _customize_color_scheme(): Different colors for defined-risk strategies
    """

    # No overrides currently - inherits everything from base class
    # The generic implementation handles spreads perfectly via StrategyLeg objects
    # Override methods here when spread-specific customizations are needed
    pass
