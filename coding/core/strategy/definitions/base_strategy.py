"""
Base strategy definitions for option strategies.

This module provides the abstract base class and data structures for all option strategies.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class StrategyLeg:
    """
    Represents a single leg of an option strategy.

    Attributes:
        action: "buy" or "sell"
        option_type: "call" or "put"
        strike: Strike price
        quantity: Number of contracts (positive for buy, negative for sell)
        cost: Total cost of the leg (positive for debit, negative for credit)
        greeks: Dictionary of greek values (delta, gamma, theta, vega, etc.)
        instrument_name: Deribit instrument name (e.g., "BTC-31JAN25-100000-C")
    """
    action: str  # "buy" or "sell"
    option_type: str  # "call" or "put"
    strike: float
    quantity: int
    cost: float
    greeks: Dict[str, float] = field(default_factory=dict)
    instrument_name: str = ""

    def __post_init__(self):
        """Validate leg attributes."""
        if self.action not in ["buy", "sell"]:
            raise ValueError(f"Invalid action: {self.action}. Must be 'buy' or 'sell'")
        if self.option_type not in ["call", "put"]:
            raise ValueError(f"Invalid option_type: {self.option_type}. Must be 'call' or 'put'")
        if self.strike <= 0:
            raise ValueError(f"Strike must be positive, got: {self.strike}")


class BaseStrategy(ABC):
    """
    Abstract base class for all option strategies.

    Subclasses must implement:
    - name: Strategy name
    - strategy_type: Type classification (e.g., "directional_bullish", "neutral", etc.)
    - build_legs: Construct strategy legs based on market data

    Provides concrete methods for:
    - Risk/reward calculations
    - Breakeven analysis
    - Greek aggregation
    - Position cost calculation
    """

    def __init__(
        self,
        currency: str,
        expiration: str,
        underlying_price: float,
        take_profit_percentage: Optional[float] = None
    ):
        """
        Initialize strategy.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH")
            expiration: Expiration date string (e.g., "31JAN25")
            underlying_price: Current underlying asset price
            take_profit_percentage: Optional take profit target as % gain (e.g., 50 for 50% gain)
        """
        self.currency = currency
        self.expiration = expiration
        self.underlying_price = underlying_price
        self.take_profit_percentage = take_profit_percentage
        self.legs: List[StrategyLeg] = []

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name (e.g., 'Long Call', 'Bull Call Spread')."""
        pass

    @property
    @abstractmethod
    def strategy_type(self) -> str:
        """
        Strategy type classification.

        Types:
        - directional_bullish: Profits from upward price movement
        - directional_bearish: Profits from downward price movement
        - neutral: Profits from sideways movement or time decay
        - volatility_long: Profits from increased volatility
        - volatility_short: Profits from decreased volatility
        """
        pass

    @classmethod
    def get_default_config(cls) -> Dict[str, any]:
        """
        Get default configuration for this strategy.

        Subclasses should override this to provide strategy-specific defaults.

        Returns:
            Dictionary with default configuration parameters.
            Keys vary by strategy type (single-leg vs spread).

        Example for single-leg strategies:
            {
                "target_delta": 0.30,
                "max_loss_percentage": 5.0
            }

        Example for spread strategies:
            {
                "long_target_delta": 0.45,
                "short_target_delta": 0.25,
                "min_profit_debit_ratio": 0.5,
                "max_loss_percentage": 5.0
            }
        """
        # Base implementation returns generic defaults
        return {
            "target_delta": 0.30,
            "max_loss_percentage": 5.0
        }

    @abstractmethod
    def build_legs(self, ticker_data: Dict[str, Dict], **kwargs) -> None:
        """
        Build strategy legs based on market data.

        Args:
            ticker_data: Dictionary mapping instrument names to ticker data
            **kwargs: Strategy-specific parameters (e.g., target_delta, moneyness_pct)

        Raises:
            ValueError: If unable to construct valid legs
        """
        pass

    def get_max_risk(self) -> float:
        """
        Calculate maximum risk (loss) for the strategy.

        Returns:
            Maximum possible loss (positive value). Returns float('inf') for unlimited risk.
        """
        if not self.legs:
            logger.warning(f"{self.name}: No legs defined, cannot calculate max risk")
            return 0.0

        # For simple strategies (single long option), max risk is the cost
        total_cost = self.get_total_cost()

        # For single long positions, max risk is the debit paid
        if len(self.legs) == 1 and self.legs[0].action == "buy":
            return abs(total_cost)

        # For spreads and complex strategies, calculate based on structure
        # Default implementation - subclasses should override for complex strategies
        return abs(total_cost)

    def get_max_profit(self) -> Optional[float]:
        """
        Calculate maximum profit for the strategy.

        Returns:
            Maximum possible profit, or None for unlimited profit potential.
        """
        if not self.legs:
            logger.warning(f"{self.name}: No legs defined, cannot calculate max profit")
            return 0.0

        # For single long call/put, profit is theoretically unlimited (call) or strike-cost (put)
        # Subclasses should override this for accurate calculations
        return None  # Unlimited profit by default

    def get_breakeven_points(self) -> List[float]:
        """
        Calculate breakeven price points at expiration.

        Returns:
            List of breakeven prices (can be empty, one, or multiple points)
        """
        if not self.legs:
            return []

        # Simple implementation for single long option
        if len(self.legs) == 1:
            leg = self.legs[0]
            cost_per_contract = abs(leg.cost / leg.quantity) if leg.quantity != 0 else 0

            if leg.option_type == "call":
                # Breakeven = strike + premium paid
                return [leg.strike + cost_per_contract]
            else:  # put
                # Breakeven = strike - premium paid
                return [leg.strike - cost_per_contract]

        # Complex strategies should override this method
        return []

    def get_net_greeks(self) -> Dict[str, float]:
        """
        Calculate net position greeks by aggregating all legs.

        Returns:
            Dictionary with net delta, gamma, theta, vega
        """
        net_greeks = {
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0
        }

        for leg in self.legs:
            multiplier = leg.quantity  # Already accounts for buy (+) vs sell (-)

            for greek in net_greeks:
                if greek in leg.greeks:
                    net_greeks[greek] += leg.greeks[greek] * multiplier

        return net_greeks

    def get_total_cost(self) -> float:
        """
        Calculate total cost of the strategy.

        Returns:
            Total cost (positive for debit, negative for credit)
        """
        return sum(leg.cost for leg in self.legs)

    def get_max_loss_percentage(self) -> float:
        """
        Calculate maximum loss as percentage of underlying price.

        Returns:
            Maximum loss as percentage (e.g., 2.5 for 2.5% loss)
        """
        if self.underlying_price <= 0:
            logger.warning(f"{self.name}: Invalid underlying_price, cannot calculate loss %")
            return 0.0

        max_risk = self.get_max_risk()

        # Handle unlimited risk
        if max_risk == float('inf'):
            return float('inf')

        return (max_risk / self.underlying_price) * 100.0

    def validate_legs(self) -> bool:
        """
        Validate that strategy legs are properly constructed.

        Returns:
            True if legs are valid, False otherwise
        """
        if not self.legs:
            logger.error(f"{self.name}: No legs defined")
            return False

        for i, leg in enumerate(self.legs):
            try:
                # Validation happens in StrategyLeg.__post_init__
                # Just check that we have necessary data
                if not leg.greeks:
                    logger.warning(f"{self.name}: Leg {i} has no greeks data")

                if leg.cost == 0:
                    logger.warning(f"{self.name}: Leg {i} has zero cost")

            except Exception as e:
                logger.error(f"{self.name}: Leg {i} validation failed: {e}")
                return False

        return True

    def to_dict(self) -> Dict:
        """
        Convert strategy to dictionary representation.

        Returns:
            Dictionary with strategy details
        """
        return {
            "name": self.name,
            "strategy_type": self.strategy_type,
            "currency": self.currency,
            "expiration": self.expiration,
            "underlying_price": self.underlying_price,
            "take_profit_percentage": self.take_profit_percentage,
            "legs": [
                {
                    "action": leg.action,
                    "option_type": leg.option_type,
                    "strike": leg.strike,
                    "quantity": leg.quantity,
                    "cost": leg.cost,
                    "greeks": leg.greeks,
                    "instrument_name": leg.instrument_name
                }
                for leg in self.legs
            ],
            "max_risk": self.get_max_risk(),
            "max_profit": self.get_max_profit(),
            "total_cost": self.get_total_cost(),
            "breakeven_points": self.get_breakeven_points(),
            "net_greeks": self.get_net_greeks(),
            "max_loss_percentage": self.get_max_loss_percentage()
        }

    def __repr__(self) -> str:
        """String representation of strategy."""
        return (
            f"{self.name}("
            f"currency={self.currency}, "
            f"expiration={self.expiration}, "
            f"legs={len(self.legs)}, "
            f"cost={self.get_total_cost():.2f})"
        )
