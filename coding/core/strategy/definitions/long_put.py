"""
Long Put strategy implementation.

A long put is a bearish strategy where the trader buys a put option,
expecting the underlying price to fall below the strike price.
"""

import logging
from typing import Dict, List, Optional

from ..models.long_put_config import LongPutConfig
from .base_strategy import BaseStrategy, StrategyLeg

logger = logging.getLogger(__name__)


class LongPut(BaseStrategy):
    """
    Long Put strategy.

    Characteristics:
    - Strategy Type: Directional Bearish
    - Max Risk: Premium paid
    - Max Profit: Strike - premium (limited to zero underlying price)
    - Breakeven: Strike - premium paid

    Strike Selection Methods:
    1. by_delta: Select strike closest to target delta (e.g., -0.30, use absolute value 0.30)
    2. by_moneyness: Select strike at X% OTM from current price (e.g., 5% below)
    3. by_strike: Use specific strike value

    Example Usage (by_delta):
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,  # Absolute value, sign handled by strategy
            quantity=1
        )
        strategy.build_legs(ticker_data=data, config=config)

    Example Usage (by_moneyness):
        config = LongPutConfig(
            method="by_moneyness",
            moneyness_pct=10.0,  # 10% below current price
            quantity=1
        )
        strategy.build_legs(ticker_data=data, config=config)

    Example Usage (by_strike):
        config = LongPutConfig(
            method="by_strike",
            specific_strike=95000.0,
            quantity=1
        )
        strategy.build_legs(ticker_data=data, config=config)
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "Long Put"

    @property
    def strategy_type(self) -> str:
        """Strategy type classification."""
        return "directional_bearish"

    @classmethod
    def get_default_config(cls) -> Dict[str, any]:
        """
        Get default configuration for Long Put.

        Optimized for pure directional speculation:
        - Delta: -0.30 (OTM for leverage and lower cost)
        - Max loss: 5% of account

        Returns:
            Dictionary with Long Put defaults
        """
        return {
            "target_delta": 0.30,  # Absolute value, sign handled by strategy
            "max_loss_percentage": 5.0
        }

    def build_legs(
        self,
        ticker_data: Dict[str, Dict],
        config: LongPutConfig
    ) -> None:
        """
        Build long put leg based on strike selection method.

        Args:
            ticker_data: Dictionary mapping instrument names to ticker data
                        Expected format: {"BTC-31JAN25-90000-P": {...}, ...}
            config: LongPutConfig Pydantic model with method and parameters

        Raises:
            ValueError: If unable to find suitable strike or invalid parameters
        """
        # Filter for puts matching this expiration and currency
        put_instruments = {
            name: data
            for name, data in ticker_data.items()
            if self._is_matching_put(name, data)
        }

        if not put_instruments:
            raise ValueError(
                f"No put options found for {self.currency}-{self.expiration}"
            )

        # Select strike based on method
        if config.method == "by_delta":
            selected_instrument = self._select_by_delta(put_instruments, config.target_delta)

        elif config.method == "by_moneyness":
            selected_instrument = self._select_by_moneyness(put_instruments, config.moneyness_pct)

        elif config.method == "by_strike":
            selected_instrument = self._select_by_strike(put_instruments, config.specific_strike)

        else:
            raise ValueError(
                f"Invalid method: {config.method}. "
                f"Must be 'by_delta', 'by_moneyness', or 'by_strike'"
            )

        # Extract strike from instrument name (e.g., "BTC-31JAN25-90000-P" -> 90000)
        strike = self._extract_strike_from_name(selected_instrument)

        # Get ticker data for selected instrument
        ticker = ticker_data[selected_instrument]

        # Calculate cost (best ask price * quantity, converted to currency units)
        best_ask_price = ticker.get("best_ask_price", 0)
        if best_ask_price == 0:
            logger.warning(f"Best ask price is 0 for {selected_instrument}, using mark_price")
            best_ask_price = ticker.get("mark_price", 0)

        # Cost per contract in USD
        cost_per_contract = best_ask_price * self.underlying_price

        total_cost = cost_per_contract * config.quantity

        # Extract greeks (including IV like Bull Call Spread)
        greeks = {
            "delta": ticker.get("greeks", {}).get("delta", 0),
            "gamma": ticker.get("greeks", {}).get("gamma", 0),
            "theta": ticker.get("greeks", {}).get("theta", 0),
            "vega": ticker.get("greeks", {}).get("vega", 0),
            "iv": ticker.get("greeks", {}).get("iv", 0),  # Added IV
        }

        # Create the leg
        leg = StrategyLeg(
            action="buy",
            option_type="put",
            strike=strike,
            quantity=config.quantity,
            cost=total_cost,
            greeks=greeks,
            instrument_name=selected_instrument
        )

        self.legs = [leg]

        logger.info(
            f"Built {self.name}: strike={strike}, cost=${total_cost:.2f}, "
            f"delta={greeks['delta']:.3f}, iv={greeks['iv']:.2%}, instrument={selected_instrument}"
        )

    def get_max_risk(self) -> float:
        """
        Maximum risk is the premium paid.

        Returns:
            Premium paid for the put option
        """
        return abs(self.get_total_cost())

    def get_max_profit(self) -> Optional[float]:
        """
        Maximum profit is strike - premium (limited by zero underlying price).

        Returns:
            Maximum profit if underlying goes to zero
        """
        if not self.legs:
            return 0.0

        leg = self.legs[0]
        premium_paid = abs(leg.cost / leg.quantity) if leg.quantity != 0 else 0

        # Max profit = strike - premium (when underlying goes to 0)
        max_profit_per_contract = leg.strike - premium_paid

        return max_profit_per_contract * leg.quantity

    def get_breakeven_points(self) -> List[float]:
        """
        Calculate breakeven price at expiration.

        Breakeven = strike - premium_per_contract

        Returns:
            List with single breakeven point
        """
        if not self.legs:
            return []

        leg = self.legs[0]
        strike = leg.strike
        total_cost = abs(leg.cost)
        quantity = abs(leg.quantity)

        premium_per_contract = total_cost / quantity if quantity > 0 else 0

        breakeven = strike - premium_per_contract

        return [breakeven]

    def _is_matching_put(self, instrument_name: str, ticker_data: Dict) -> bool:
        """
        Check if instrument is a put option matching this strategy's parameters.

        Args:
            instrument_name: Instrument name (e.g., "BTC-31JAN25-90000-P")
            ticker_data: Ticker data for the instrument

        Returns:
            True if instrument matches, False otherwise
        """
        parts = instrument_name.split("-")

        if len(parts) != 4:
            return False

        currency, expiration, strike_str, option_type = parts

        return (
            currency == self.currency and
            expiration == self.expiration and
            option_type == "P"  # Put option
        )

    def _extract_strike_from_name(self, instrument_name: str) -> float:
        """
        Extract strike price from instrument name.

        Args:
            instrument_name: Instrument name (e.g., "BTC-31JAN25-90000-P")

        Returns:
            Strike price as float
        """
        parts = instrument_name.split("-")
        if len(parts) != 4:
            raise ValueError(f"Invalid instrument name format: {instrument_name}")

        return float(parts[2])

    def _select_by_delta(
        self,
        put_instruments: Dict[str, Dict],
        target_delta: float
    ) -> str:
        """
        Select strike closest to target delta.

        Note: Put deltas are negative, but config uses absolute value.
        We compare absolute values to find the closest match.

        Args:
            put_instruments: Filtered put options
            target_delta: Target delta value (absolute value, e.g., 0.30)

        Returns:
            Selected instrument name

        Raises:
            ValueError: If no instruments with delta data found
        """
        # Filter instruments with delta data
        instruments_with_delta = {
            name: data
            for name, data in put_instruments.items()
            if data.get("greeks", {}).get("delta") is not None
        }

        if not instruments_with_delta:
            raise ValueError("No put options with delta data found")

        # Find instrument with delta closest to target (compare absolute values)
        # Put deltas are negative, so we compare abs(actual_delta) to abs(target_delta)
        best_instrument = min(
            instruments_with_delta.items(),
            key=lambda item: abs(abs(item[1].get("greeks", {}).get("delta", 0)) - abs(target_delta))
        )

        selected_name = best_instrument[0]
        selected_delta = best_instrument[1].get("greeks", {}).get("delta", 0)

        logger.info(
            f"Selected strike by delta: target={target_delta:.3f}, "
            f"actual={selected_delta:.3f}, instrument={selected_name}"
        )

        return selected_name

    def _select_by_moneyness(
        self,
        put_instruments: Dict[str, Dict],
        moneyness_pct: float
    ) -> str:
        """
        Select strike at X% OTM from current price (below current price for puts).

        Args:
            put_instruments: Filtered put options
            moneyness_pct: Percentage OTM (e.g., 5.0 for 5% below current price)

        Returns:
            Selected instrument name

        Raises:
            ValueError: If no suitable strike found
        """
        # Calculate target strike (current price - moneyness%)
        target_strike = self.underlying_price * (1 - moneyness_pct / 100)

        logger.info(
            f"Selecting strike by moneyness: {moneyness_pct}% OTM, "
            f"target_strike={target_strike:.2f}"
        )

        # Find strike closest to target
        best_instrument = min(
            put_instruments.keys(),
            key=lambda name: abs(self._extract_strike_from_name(name) - target_strike)
        )

        actual_strike = self._extract_strike_from_name(best_instrument)

        logger.info(
            f"Selected strike by moneyness: target={target_strike:.2f}, "
            f"actual={actual_strike:.2f}, instrument={best_instrument}"
        )

        return best_instrument

    def _select_by_strike(
        self,
        put_instruments: Dict[str, Dict],
        specific_strike: float
    ) -> str:
        """
        Select specific strike value.

        Args:
            put_instruments: Filtered put options
            specific_strike: Exact strike to find

        Returns:
            Selected instrument name

        Raises:
            ValueError: If specific strike not found
        """
        # Find exact match or closest strike
        matching_instruments = [
            name for name in put_instruments.keys()
            if self._extract_strike_from_name(name) == specific_strike
        ]

        if matching_instruments:
            selected = matching_instruments[0]
            logger.info(f"Selected exact strike: {specific_strike}, instrument={selected}")
            return selected

        # If no exact match, find closest
        logger.warning(
            f"Exact strike {specific_strike} not found, selecting closest strike"
        )

        best_instrument = min(
            put_instruments.keys(),
            key=lambda name: abs(self._extract_strike_from_name(name) - specific_strike)
        )

        actual_strike = self._extract_strike_from_name(best_instrument)

        logger.info(
            f"Selected closest strike: target={specific_strike:.2f}, "
            f"actual={actual_strike:.2f}, instrument={best_instrument}"
        )

        return best_instrument
