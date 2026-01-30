"""
PnL calculator for option strategies.

Provides general-purpose P&L calculation at any price point for single-leg
and multi-leg option strategies.
"""

import logging
from typing import Dict, List, Tuple

from coding.core.strategy.definitions.base_strategy import StrategyLeg

logger = logging.getLogger(__name__)


class StrategyPnLCalculator:
    """
    General-purpose PnL calculator for option strategies.

    Calculates P&L at any price point for single-leg and multi-leg strategies.
    Uses intrinsic value calculation for at-expiry P&L or greek-based
    approximation for pre-expiry estimates.
    """

    def __init__(self, legs: List[StrategyLeg], underlying_price: float):
        """
        Initialize calculator with strategy legs.

        Args:
            legs: List of strategy legs (each containing action, type, strike, cost, greeks)
            underlying_price: Current underlying asset price
        """
        if not legs:
            raise ValueError("Strategy must have at least one leg")

        self.legs = legs
        self.underlying_price = underlying_price

    def calculate_pnl_at_price(self, price: float, at_expiry: bool = True) -> float:
        """
        Calculate strategy PnL at given price point.

        Args:
            price: Underlying price to evaluate
            at_expiry: If True, use intrinsic value. If False, estimate using greeks.

        Returns:
            PnL in USD (positive = profit, negative = loss)
        """
        if at_expiry:
            return self._calculate_expiry_pnl(price)
        else:
            return self._estimate_preexpiry_pnl(price)

    def _calculate_expiry_pnl(self, price: float) -> float:
        """
        Calculate P&L at expiration using intrinsic value.

        Args:
            price: Price at expiration

        Returns:
            Total P&L across all legs
        """
        total_pnl = 0.0

        for leg in self.legs:
            # Calculate intrinsic value
            if leg.option_type == "call":
                intrinsic_value = max(0, price - leg.strike)
            else:  # put
                intrinsic_value = max(0, leg.strike - price)

            # Calculate leg P&L based on action (buy/sell)
            if leg.action == "buy":
                # Long position: profit when intrinsic > cost
                leg_pnl = (intrinsic_value - leg.cost) * abs(leg.quantity)
            else:  # sell
                # Short position: profit when premium received > intrinsic owed
                # leg.cost is negative (credit), so negate it to get credit received
                leg_pnl = (-leg.cost - intrinsic_value) * abs(leg.quantity)

            total_pnl += leg_pnl

        return total_pnl

    def _estimate_preexpiry_pnl(self, price: float) -> float:
        """
        Estimate P&L before expiration using greek approximation.

        Uses Taylor series expansion:
        PnL ≈ Delta × ΔS + 0.5 × Gamma × ΔS² - premium

        Args:
            price: Current price to evaluate

        Returns:
            Estimated P&L
        """
        total_pnl = 0.0
        price_change = price - self.underlying_price

        for leg in self.legs:
            delta = leg.greeks.get("delta", 0)
            gamma = leg.greeks.get("gamma", 0)

            # Greek-based P&L approximation
            delta_pnl = delta * price_change
            gamma_pnl = 0.5 * gamma * (price_change ** 2)
            greek_value = delta_pnl + gamma_pnl

            # Account for buy/sell direction
            if leg.action == "buy":
                leg_pnl = (greek_value - leg.cost) * abs(leg.quantity)
            else:  # sell
                # leg.cost is negative (credit), so negate it
                leg_pnl = (-leg.cost - greek_value) * abs(leg.quantity)

            total_pnl += leg_pnl

        return total_pnl

    def calculate_pnl_profile(
        self,
        price_range: Tuple[float, float],
        num_points: int = 100,
        at_expiry: bool = True
    ) -> Dict[float, float]:
        """
        Calculate P&L across a price range.

        Args:
            price_range: (min_price, max_price) tuple
            num_points: Number of price points to calculate
            at_expiry: Whether to use expiry or pre-expiry calculation

        Returns:
            Dict mapping price -> P&L
        """
        min_price, max_price = price_range

        if min_price >= max_price:
            raise ValueError(f"Invalid price range: {price_range}")

        if num_points < 2:
            raise ValueError(f"num_points must be >= 2, got {num_points}")

        profile = {}
        price_step = (max_price - min_price) / (num_points - 1)

        for i in range(num_points):
            price = min_price + (i * price_step)
            pnl = self.calculate_pnl_at_price(price, at_expiry=at_expiry)
            profile[price] = pnl

        return profile

    def get_max_profit(self, price_range: Tuple[float, float]) -> float:
        """
        Find maximum profit within price range.

        Args:
            price_range: (min_price, max_price) to search

        Returns:
            Maximum profit (or float('inf') for unlimited profit strategies)
        """
        # Check if any leg has unlimited profit potential
        for leg in self.legs:
            if leg.action == "buy" and leg.option_type == "call":
                # Long call has unlimited upside
                return float('inf')
            if leg.action == "buy" and leg.option_type == "put":
                # Long put has upside to strike
                # Check if this dominates
                pass

        # Calculate max profit across range
        profile = self.calculate_pnl_profile(price_range, num_points=100)
        return max(profile.values())

    def get_max_loss(self, price_range: Tuple[float, float]) -> float:
        """
        Find maximum loss within price range.

        Args:
            price_range: (min_price, max_price) to search

        Returns:
            Maximum loss (negative value, or -inf for unlimited loss)
        """
        # Check if any leg has unlimited loss potential
        for leg in self.legs:
            if leg.action == "sell" and leg.option_type == "call":
                # Short call has unlimited downside
                return float('-inf')

        # Calculate max loss across range
        profile = self.calculate_pnl_profile(price_range, num_points=100)
        return min(profile.values())

    def get_breakeven_points(self, price_range: Tuple[float, float], tolerance: float = 0.01) -> List[float]:
        """
        Find breakeven points where P&L crosses zero.

        Args:
            price_range: (min_price, max_price) to search
            tolerance: How close to zero counts as breakeven

        Returns:
            List of breakeven prices
        """
        breakeven_points = []
        profile = self.calculate_pnl_profile(price_range, num_points=500)

        prices = sorted(profile.keys())
        prev_pnl = None

        for price in prices:
            pnl = profile[price]

            # Check if P&L is very close to zero
            if abs(pnl) < tolerance:
                breakeven_points.append(price)
            # Check if P&L crossed zero
            elif prev_pnl is not None:
                if (prev_pnl < 0 and pnl > 0) or (prev_pnl > 0 and pnl < 0):
                    # Interpolate to find more exact breakeven
                    breakeven = self._interpolate_breakeven(prices[prices.index(price) - 1], prev_pnl, price, pnl)
                    breakeven_points.append(breakeven)

            prev_pnl = pnl

        # Remove duplicates (within tolerance)
        unique_points = []
        for point in breakeven_points:
            if not any(abs(point - existing) < tolerance * 10 for existing in unique_points):
                unique_points.append(point)

        return sorted(unique_points)

    def _interpolate_breakeven(self, price1: float, pnl1: float, price2: float, pnl2: float) -> float:
        """
        Linear interpolation to find exact breakeven point.

        Args:
            price1, pnl1: First point
            price2, pnl2: Second point

        Returns:
            Interpolated breakeven price
        """
        # Linear interpolation: price = price1 + (price2 - price1) * (-pnl1) / (pnl2 - pnl1)
        if pnl2 == pnl1:
            return (price1 + price2) / 2

        return price1 + (price2 - price1) * (-pnl1) / (pnl2 - pnl1)
