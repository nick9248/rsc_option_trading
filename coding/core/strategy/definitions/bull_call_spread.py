"""
Bull Call Spread strategy implementation.

A bull call spread is a bullish vertical spread where the trader:
- Buys a lower strike call (long leg)
- Sells a higher strike call (short leg)

This limits both maximum profit and maximum risk compared to a long call.
"""

import logging
from typing import Dict, List, Optional, Tuple

from ..models.spread_config import SpreadStrikeConfig
from .base_strategy import BaseStrategy, StrategyLeg

logger = logging.getLogger(__name__)


class BullCallSpread(BaseStrategy):
    """
    Bull Call Spread strategy.

    Characteristics:
    - Strategy Type: Directional Bullish
    - Max Risk: Strike width - net debit (limited)
    - Max Profit: Net debit paid (limited)
    - Breakeven: Long strike + net debit per contract
    - Legs: 2 (buy lower strike call, sell higher strike call)

    Strike Selection Methods:
    1. skew_aware: Dynamic optimization using volatility skew analysis
       - profit_debit_ratio: Find spread with best risk/reward ratio
       - max_width_for_budget: Find widest spread within budget constraint
    2. by_delta: Manual selection using long/short deltas
    3. by_moneyness: Manual selection using % OTM from current price
    4. by_strike: Manual selection using specific strike values

    Example Usage (Skew-Aware):
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            min_profit_debit_ratio=0.5,
            quantity=1
        )
        strategy.build_legs(ticker_data=data, spread_config=config)

    Example Usage (Traditional):
        config = SpreadStrikeConfig(
            method="by_delta",
            long_target_delta=0.50,
            short_target_delta=0.30,
            quantity=1
        )
        strategy.build_legs(ticker_data=data, spread_config=config)
    """

    @property
    def name(self) -> str:
        """Strategy name."""
        return "Bull Call Spread"

    @property
    def strategy_type(self) -> str:
        """Strategy type classification."""
        return "directional_bullish"

    def build_legs(
        self,
        ticker_data: Dict[str, Dict],
        spread_config: SpreadStrikeConfig
    ) -> None:
        """
        Build bull call spread legs based on strike selection method.

        Args:
            ticker_data: Dictionary mapping instrument names to ticker data
                        Expected format: {"BTC-31JAN25-100000-C": {...}, ...}
            spread_config: SpreadStrikeConfig Pydantic model with method and parameters

        Raises:
            ValueError: If unable to find suitable strikes or invalid parameters
        """
        # Filter for calls matching this expiration and currency
        call_instruments = {
            name: data
            for name, data in ticker_data.items()
            if self._is_matching_call(name, data)
        }

        if not call_instruments:
            raise ValueError(
                f"No call options found for {self.currency}-{self.expiration}"
            )

        # Select strikes based on method
        if spread_config.method == "skew_aware":
            long_instrument, short_instrument = self._select_skew_aware(
                call_instruments, spread_config
            )

        elif spread_config.method == "by_delta":
            long_instrument, short_instrument = self._select_by_dual_delta(
                call_instruments, spread_config
            )

        elif spread_config.method == "by_moneyness":
            long_instrument, short_instrument = self._select_by_dual_moneyness(
                call_instruments, spread_config
            )

        elif spread_config.method == "by_strike":
            long_instrument, short_instrument = self._select_by_dual_strike(
                call_instruments, spread_config
            )

        else:
            raise ValueError(
                f"Invalid method: {spread_config.method}. "
                f"Must be 'skew_aware', 'by_delta', 'by_moneyness', or 'by_strike'"
            )

        # Extract strikes
        long_strike = self._extract_strike_from_name(long_instrument)
        short_strike = self._extract_strike_from_name(short_instrument)

        # Validate spread structure
        self._validate_spread(long_strike, short_strike)

        # Build legs
        long_leg = self._build_long_leg(
            ticker_data[long_instrument],
            long_instrument,
            long_strike,
            spread_config.quantity
        )

        short_leg = self._build_short_leg(
            ticker_data[short_instrument],
            short_instrument,
            short_strike,
            spread_config.quantity
        )

        self.legs = [long_leg, short_leg]

        # Log summary
        net_debit = abs(self.get_total_cost())
        strike_width = short_strike - long_strike
        max_profit = strike_width - net_debit
        profit_debit_ratio = max_profit / net_debit if net_debit > 0 else 0

        logger.info(
            f"Built {self.name}: {long_strike}/{short_strike} "
            f"(width={strike_width:.0f}), "
            f"debit=${net_debit:.2f}, max_profit=${max_profit:.2f}, "
            f"profit/debit={profit_debit_ratio:.2f}"
        )

    def get_max_risk(self) -> float:
        """
        Calculate maximum risk (loss) for bull call spread.

        Max risk = strike_width - net_debit

        Returns:
            Maximum possible loss
        """
        if len(self.legs) != 2:
            logger.warning(f"{self.name}: Expected 2 legs, got {len(self.legs)}")
            return abs(self.get_total_cost())

        # Extract strikes
        long_strike = self.legs[0].strike
        short_strike = self.legs[1].strike

        strike_width = short_strike - long_strike
        net_debit = abs(self.get_total_cost())

        # Max risk = strike width - net debit
        max_risk = strike_width - net_debit

        return max(max_risk, 0.0)  # Cannot be negative

    def get_max_profit(self) -> Optional[float]:
        """
        Calculate maximum profit for bull call spread.

        Max profit = net debit paid

        Returns:
            Maximum possible profit
        """
        # Max profit is the net debit (cost paid)
        return abs(self.get_total_cost())

    def get_breakeven_points(self) -> List[float]:
        """
        Calculate breakeven price at expiration.

        Breakeven = long_strike + net_debit_per_contract

        Returns:
            List with single breakeven point
        """
        if len(self.legs) != 2:
            return []

        long_strike = self.legs[0].strike
        net_debit = abs(self.get_total_cost())
        quantity = abs(self.legs[0].quantity)

        debit_per_contract = net_debit / quantity if quantity > 0 else 0

        breakeven = long_strike + debit_per_contract

        return [breakeven]

    # ==================== Strike Selection Methods ====================

    def _select_skew_aware(
        self,
        call_instruments: Dict[str, Dict],
        config: SpreadStrikeConfig
    ) -> Tuple[str, str]:
        """
        Select optimal spread using volatility skew analysis.

        This is the professional approach: scan all possible spreads,
        calculate metrics, and select the optimal one based on criteria.

        Algorithm:
        1. Extract IV from greeks for all strikes
        2. Generate all valid spread combinations
        3. For each spread, calculate:
           - Net debit (long cost - short credit)
           - Strike width
           - Profit/debit ratio: (width - debit) / debit
           - IV skew slope: (short_IV - long_IV) / (short_strike - long_strike)
        4. Apply filters (min ratio, target width, max budget)
        5. Rank by optimization criteria
        6. Return optimal long/short instrument names

        Args:
            call_instruments: Filtered call options
            config: SpreadStrikeConfig with optimization parameters

        Returns:
            Tuple of (long_instrument_name, short_instrument_name)

        Raises:
            ValueError: If no valid spreads found
        """
        spreads = []

        for long_name, long_data in call_instruments.items():
            long_strike = self._extract_strike_from_name(long_name)
            long_iv = long_data.get("greeks", {}).get("iv", 0)
            long_cost = self._calculate_leg_cost(long_data, config.quantity, is_buy=True)

            for short_name, short_data in call_instruments.items():
                short_strike = self._extract_strike_from_name(short_name)

                # Skip if not valid spread structure (short must be > long)
                if short_strike <= long_strike:
                    continue

                short_iv = short_data.get("greeks", {}).get("iv", 0)
                short_credit = self._calculate_leg_cost(short_data, config.quantity, is_buy=False)

                # Calculate metrics
                net_debit = long_cost - short_credit
                strike_width = short_strike - long_strike
                max_profit = strike_width - net_debit
                profit_debit_ratio = max_profit / net_debit if net_debit > 0 else 0
                iv_skew_slope = (short_iv - long_iv) / strike_width if strike_width > 0 else 0

                # Skip if debit is negative (we're getting paid - not a debit spread)
                if net_debit <= 0:
                    continue

                # Apply filters
                if config.min_profit_debit_ratio and profit_debit_ratio < config.min_profit_debit_ratio:
                    continue

                if config.max_budget and net_debit > config.max_budget:
                    continue

                if config.target_width_pct:
                    target_width = self.underlying_price * (config.target_width_pct / 100)
                    tolerance = target_width * 0.2  # 20% tolerance
                    if abs(strike_width - target_width) > tolerance:
                        continue

                spreads.append({
                    "long_name": long_name,
                    "short_name": short_name,
                    "long_strike": long_strike,
                    "short_strike": short_strike,
                    "net_debit": net_debit,
                    "strike_width": strike_width,
                    "profit_debit_ratio": profit_debit_ratio,
                    "iv_skew_slope": iv_skew_slope,
                    "max_profit": max_profit
                })

        if not spreads:
            raise ValueError(
                "No valid spreads found with given criteria. "
                "Try relaxing filters (lower min_profit_debit_ratio or increase max_budget)"
            )

        # Sort by optimization criteria
        if config.optimize_for == "profit_debit_ratio":
            spreads.sort(key=lambda s: s["profit_debit_ratio"], reverse=True)
        elif config.optimize_for == "max_width_for_budget":
            spreads.sort(key=lambda s: s["strike_width"], reverse=True)

        optimal = spreads[0]

        logger.info(
            f"Skew-aware selection ({config.optimize_for}): "
            f"{optimal['long_strike']:.0f}/{optimal['short_strike']:.0f}, "
            f"profit/debit={optimal['profit_debit_ratio']:.2f}, "
            f"width={optimal['strike_width']:.0f}, "
            f"debit=${optimal['net_debit']:.2f}, "
            f"IV_skew={optimal['iv_skew_slope']:.6f}"
        )

        return optimal["long_name"], optimal["short_name"]

    def _select_by_dual_delta(
        self,
        call_instruments: Dict[str, Dict],
        config: SpreadStrikeConfig
    ) -> Tuple[str, str]:
        """
        Select strikes using dual delta specification.

        Traditional method: user specifies exact deltas for both legs.

        Args:
            call_instruments: Filtered call options
            config: SpreadStrikeConfig with long_target_delta and short_target_delta

        Returns:
            Tuple of (long_instrument_name, short_instrument_name)

        Raises:
            ValueError: If no instruments with delta data found
        """
        # Filter instruments with delta data
        instruments_with_delta = {
            name: data
            for name, data in call_instruments.items()
            if data.get("greeks", {}).get("delta") is not None
        }

        if not instruments_with_delta:
            raise ValueError("No call options with delta data found")

        # Find long leg (higher delta, closer to ATM)
        long_instrument = min(
            instruments_with_delta.items(),
            key=lambda item: abs(item[1].get("greeks", {}).get("delta", 0) - config.long_target_delta)
        )[0]

        # Find short leg (lower delta, further OTM)
        short_instrument = min(
            instruments_with_delta.items(),
            key=lambda item: abs(item[1].get("greeks", {}).get("delta", 0) - config.short_target_delta)
        )[0]

        long_delta = instruments_with_delta[long_instrument].get("greeks", {}).get("delta", 0)
        short_delta = instruments_with_delta[short_instrument].get("greeks", {}).get("delta", 0)

        logger.info(
            f"Selected by delta: long={config.long_target_delta:.2f} (actual={long_delta:.2f}), "
            f"short={config.short_target_delta:.2f} (actual={short_delta:.2f})"
        )

        return long_instrument, short_instrument

    def _select_by_dual_moneyness(
        self,
        call_instruments: Dict[str, Dict],
        config: SpreadStrikeConfig
    ) -> Tuple[str, str]:
        """
        Select strikes using dual moneyness specification (% OTM).

        Traditional method: user specifies % OTM for both legs.

        Args:
            call_instruments: Filtered call options
            config: SpreadStrikeConfig with long_moneyness_pct and short_moneyness_pct

        Returns:
            Tuple of (long_instrument_name, short_instrument_name)

        Raises:
            ValueError: If no suitable strikes found
        """
        # Calculate target strikes
        long_target_strike = self.underlying_price * (1 + config.long_moneyness_pct / 100)
        short_target_strike = self.underlying_price * (1 + config.short_moneyness_pct / 100)

        # Find closest strikes
        long_instrument = min(
            call_instruments.keys(),
            key=lambda name: abs(self._extract_strike_from_name(name) - long_target_strike)
        )

        short_instrument = min(
            call_instruments.keys(),
            key=lambda name: abs(self._extract_strike_from_name(name) - short_target_strike)
        )

        long_actual = self._extract_strike_from_name(long_instrument)
        short_actual = self._extract_strike_from_name(short_instrument)

        logger.info(
            f"Selected by moneyness: long={config.long_moneyness_pct}% OTM (strike={long_actual:.0f}), "
            f"short={config.short_moneyness_pct}% OTM (strike={short_actual:.0f})"
        )

        return long_instrument, short_instrument

    def _select_by_dual_strike(
        self,
        call_instruments: Dict[str, Dict],
        config: SpreadStrikeConfig
    ) -> Tuple[str, str]:
        """
        Select specific strike values.

        Traditional method: user specifies exact strikes for both legs.

        Args:
            call_instruments: Filtered call options
            config: SpreadStrikeConfig with long_specific_strike and short_specific_strike

        Returns:
            Tuple of (long_instrument_name, short_instrument_name)

        Raises:
            ValueError: If specific strikes not found
        """
        # Find long strike (exact or closest)
        long_matches = [
            name for name in call_instruments.keys()
            if self._extract_strike_from_name(name) == config.long_specific_strike
        ]

        if long_matches:
            long_instrument = long_matches[0]
        else:
            logger.warning(
                f"Exact long strike {config.long_specific_strike} not found, selecting closest"
            )
            long_instrument = min(
                call_instruments.keys(),
                key=lambda name: abs(
                    self._extract_strike_from_name(name) - config.long_specific_strike
                )
            )

        # Find short strike (exact or closest)
        short_matches = [
            name for name in call_instruments.keys()
            if self._extract_strike_from_name(name) == config.short_specific_strike
        ]

        if short_matches:
            short_instrument = short_matches[0]
        else:
            logger.warning(
                f"Exact short strike {config.short_specific_strike} not found, selecting closest"
            )
            short_instrument = min(
                call_instruments.keys(),
                key=lambda name: abs(
                    self._extract_strike_from_name(name) - config.short_specific_strike
                )
            )

        long_actual = self._extract_strike_from_name(long_instrument)
        short_actual = self._extract_strike_from_name(short_instrument)

        logger.info(
            f"Selected by strike: long={long_actual:.0f}, short={short_actual:.0f}"
        )

        return long_instrument, short_instrument

    # ==================== Leg Building Methods ====================

    def _build_long_leg(
        self,
        ticker: Dict,
        instrument_name: str,
        strike: float,
        quantity: int
    ) -> StrategyLeg:
        """
        Build long call leg (buy).

        Args:
            ticker: Ticker data for instrument
            instrument_name: Instrument name
            strike: Strike price
            quantity: Number of contracts

        Returns:
            StrategyLeg for long position
        """
        # Use ask price for buying
        ask_price = ticker.get("ask_price", 0)
        if ask_price == 0:
            logger.warning(f"Ask price is 0 for {instrument_name}, using mark_price")
            ask_price = ticker.get("mark_price", 0)

        # Cost per contract in USD
        cost_per_contract = ask_price * self.underlying_price
        total_cost = cost_per_contract * quantity

        # Extract greeks
        greeks = {
            "delta": ticker.get("greeks", {}).get("delta", 0),
            "gamma": ticker.get("greeks", {}).get("gamma", 0),
            "theta": ticker.get("greeks", {}).get("theta", 0),
            "vega": ticker.get("greeks", {}).get("vega", 0),
            "iv": ticker.get("greeks", {}).get("iv", 0),
        }

        return StrategyLeg(
            action="buy",
            option_type="call",
            strike=strike,
            quantity=quantity,  # Positive for buy
            cost=total_cost,  # Positive for debit
            greeks=greeks,
            instrument_name=instrument_name
        )

    def _build_short_leg(
        self,
        ticker: Dict,
        instrument_name: str,
        strike: float,
        quantity: int
    ) -> StrategyLeg:
        """
        Build short call leg (sell).

        Args:
            ticker: Ticker data for instrument
            instrument_name: Instrument name
            strike: Strike price
            quantity: Number of contracts

        Returns:
            StrategyLeg for short position
        """
        # Use bid price for selling
        bid_price = ticker.get("bid_price", 0)
        if bid_price == 0:
            logger.warning(f"Bid price is 0 for {instrument_name}, using mark_price")
            bid_price = ticker.get("mark_price", 0)

        # Credit per contract in USD
        credit_per_contract = bid_price * self.underlying_price
        total_credit = credit_per_contract * quantity

        # Extract greeks
        greeks = {
            "delta": ticker.get("greeks", {}).get("delta", 0),
            "gamma": ticker.get("greeks", {}).get("gamma", 0),
            "theta": ticker.get("greeks", {}).get("theta", 0),
            "vega": ticker.get("greeks", {}).get("vega", 0),
            "iv": ticker.get("greeks", {}).get("iv", 0),
        }

        return StrategyLeg(
            action="sell",
            option_type="call",
            strike=strike,
            quantity=-quantity,  # Negative for sell
            cost=-total_credit,  # Negative for credit
            greeks=greeks,
            instrument_name=instrument_name
        )

    # ==================== Helper Methods ====================

    def _is_matching_call(self, instrument_name: str, ticker_data: Dict) -> bool:
        """
        Check if instrument is a call option matching this strategy's parameters.

        Args:
            instrument_name: Instrument name (e.g., "BTC-31JAN25-100000-C")
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
            option_type == "C"  # Call option
        )

    def _extract_strike_from_name(self, instrument_name: str) -> float:
        """
        Extract strike price from instrument name.

        Args:
            instrument_name: Instrument name (e.g., "BTC-31JAN25-100000-C")

        Returns:
            Strike price as float

        Raises:
            ValueError: If instrument name format is invalid
        """
        parts = instrument_name.split("-")
        if len(parts) != 4:
            raise ValueError(f"Invalid instrument name format: {instrument_name}")

        return float(parts[2])

    def _calculate_leg_cost(
        self,
        ticker: Dict,
        quantity: int,
        is_buy: bool
    ) -> float:
        """
        Calculate leg cost in USD.

        Args:
            ticker: Ticker data
            quantity: Number of contracts
            is_buy: True for buy (use ask), False for sell (use bid)

        Returns:
            Total cost in USD
        """
        if is_buy:
            price = ticker.get("ask_price", 0)
            if price == 0:
                price = ticker.get("mark_price", 0)
        else:
            price = ticker.get("bid_price", 0)
            if price == 0:
                price = ticker.get("mark_price", 0)

        cost_per_contract = price * self.underlying_price
        return cost_per_contract * quantity

    def _validate_spread(self, long_strike: float, short_strike: float) -> None:
        """
        Validate spread structure.

        Args:
            long_strike: Long leg strike
            short_strike: Short leg strike

        Raises:
            ValueError: If spread structure is invalid
        """
        # Critical validation: short strike must be > long strike
        if short_strike <= long_strike:
            raise ValueError(
                f"Invalid spread: short strike ({short_strike}) must be > long strike ({long_strike})"
            )

        # Validate strikes are not equal
        if short_strike == long_strike:
            raise ValueError(
                f"Invalid spread: strikes cannot be equal ({long_strike})"
            )

        # Calculate spread width
        strike_width = short_strike - long_strike
        width_pct = (strike_width / self.underlying_price) * 100

        # Warning if spread is too tight (< 2% of underlying)
        if width_pct < 2.0:
            logger.warning(
                f"Spread width is tight: {strike_width:.0f} ({width_pct:.1f}% of underlying). "
                f"Consider wider spread for better risk/reward."
            )

        # Error if spread is too wide (> 50% of underlying)
        if width_pct > 50.0:
            raise ValueError(
                f"Spread width too wide: {strike_width:.0f} ({width_pct:.1f}% of underlying). "
                f"Maximum allowed: 50% of underlying."
            )

        logger.debug(
            f"Spread validation passed: {long_strike}/{short_strike} "
            f"(width={strike_width:.0f}, {width_pct:.1f}% of underlying)"
        )
