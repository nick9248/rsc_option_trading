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

    def __init__(
        self,
        currency: str,
        expiration: str,
        underlying_price: float,
        take_profit_percentage: Optional[float] = None
    ):
        """
        Initialize Bull Call Spread strategy.

        Args:
            currency: Currency symbol
            expiration: Expiration date string
            underlying_price: Current underlying price
            take_profit_percentage: Optional take profit percentage
        """
        super().__init__(currency, expiration, underlying_price, take_profit_percentage)
        self._top_spread_variations: List[Dict] = []  # Stores top N spreads for multi-signal generation

    @property
    def name(self) -> str:
        """Strategy name."""
        return "Bull Call Spread"

    @property
    def strategy_type(self) -> str:
        """Strategy type classification."""
        return "directional_bullish"

    @classmethod
    def get_default_config(cls) -> Dict[str, any]:
        """
        Get default configuration for Bull Call Spread.

        Optimized for capital-efficient directional plays:
        - Long leg: 0.45 delta (ATM for directional exposure)
        - Short leg: 0.25 delta (OTM to maximize spread width)
        - Min profit/debit ratio: 0.5 (50% return on capital minimum)
        - Max loss: 5% of account

        Returns:
            Dictionary with Bull Call Spread defaults
        """
        return {
            "long_target_delta": 0.45,
            "short_target_delta": 0.25,
            "long_moneyness_pct": 15.0,  # 15% OTM for long leg
            "short_moneyness_pct": 25.0,  # 25% OTM for short leg
            "min_profit_debit_ratio": 0.5,
            "max_loss_percentage": 5.0,
            "optimize_for": "profit_debit_ratio"
        }

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
        long_strike = self.extract_strike_from_name(long_instrument)
        short_strike = self.extract_strike_from_name(short_instrument)

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

        Max risk = net debit paid (what you lose if price stays below long strike)

        Returns:
            Maximum possible loss
        """
        # Max risk is the net debit (cost paid upfront)
        # If price stays below long strike, both options expire worthless
        return abs(self.get_total_cost())

    def get_max_profit(self) -> Optional[float]:
        """
        Calculate maximum profit for bull call spread.

        Max profit = strike_width - net_debit (what you gain if price goes above short strike)

        Returns:
            Maximum possible profit
        """
        if len(self.legs) != 2:
            logger.warning(f"{self.name}: Expected 2 legs, got {len(self.legs)}")
            return abs(self.get_total_cost())

        # Extract strikes
        long_strike = self.legs[0].strike
        short_strike = self.legs[1].strike

        strike_width = short_strike - long_strike
        net_debit = abs(self.get_total_cost())

        # Max profit = strike width - net debit
        # At expiration above short strike: (short_strike - long_strike) - debit_paid
        max_profit = strike_width - net_debit

        return max(max_profit, 0.0)  # Cannot be negative

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
        1. Filter strikes to reasonable range (0.8x to 1.5x underlying)
        2. Extract IV from greeks for all strikes
        3. Generate all valid spread combinations
        4. For each spread, calculate:
           - Net debit (long cost - short credit)
           - Strike width
           - Profit/debit ratio: (width - debit) / debit
           - IV skew slope: (short_IV - long_IV) / (short_strike - long_strike)
        5. Apply filters (min ratio, target width, max budget, max spread width)
        6. Rank by optimization criteria
        7. Return optimal long/short instrument names

        Args:
            call_instruments: Filtered call options
            config: SpreadStrikeConfig with optimization parameters

        Returns:
            Tuple of (long_instrument_name, short_instrument_name)

        Raises:
            ValueError: If no valid spreads found
        """
        # Filter to reasonable strike range for bull call spreads
        # Long strike: 0.90x to 1.12x (ATM to moderately OTM - max 12% OTM)
        # Short strike: 0.95x to 1.20x (slightly OTM to moderately OTM - max 20% OTM)
        # These tighter ranges prevent lottery ticket trades
        min_long_strike = self.underlying_price * 0.90
        max_long_strike = self.underlying_price * 1.12  # Changed from 1.20 (max 12% OTM)
        min_short_strike = self.underlying_price * 0.95
        max_short_strike = self.underlying_price * 1.20  # Changed from 1.30 (max 20% OTM)

        # Filter long leg candidates (closer to ATM)
        long_candidates = {
            name: data
            for name, data in call_instruments.items()
            if min_long_strike <= self.extract_strike_from_name(name) <= max_long_strike
        }

        # Filter short leg candidates (further OTM)
        short_candidates = {
            name: data
            for name, data in call_instruments.items()
            if min_short_strike <= self.extract_strike_from_name(name) <= max_short_strike
        }

        if not long_candidates or not short_candidates:
            logger.warning(
                f"Limited strikes in optimal range. "
                f"Long: {len(long_candidates)}, Short: {len(short_candidates)}. "
                f"Expanding range to 0.85x-1.15x for long, 0.90x-1.25x for short"
            )
            # Expand range slightly if needed, but still keep it reasonable
            min_long_strike = self.underlying_price * 0.85
            max_long_strike = self.underlying_price * 1.15  # Still cap at 15% OTM
            min_short_strike = self.underlying_price * 0.90
            max_short_strike = self.underlying_price * 1.25  # Still cap at 25% OTM

            long_candidates = {
                name: data
                for name, data in call_instruments.items()
                if min_long_strike <= self.extract_strike_from_name(name) <= max_long_strike
            }

            short_candidates = {
                name: data
                for name, data in call_instruments.items()
                if min_short_strike <= self.extract_strike_from_name(name) <= max_short_strike
            }

        if not long_candidates or not short_candidates:
            raise ValueError(
                f"No suitable call options found for bull call spread. "
                f"Long candidates: {len(long_candidates)}, Short candidates: {len(short_candidates)}. "
                f"Underlying: {self.underlying_price:.2f}"
            )

        logger.info(
            f"Filtered to {len(long_candidates)} long candidates "
            f"({min_long_strike:.0f}-{max_long_strike:.0f}) and "
            f"{len(short_candidates)} short candidates "
            f"({min_short_strike:.0f}-{max_short_strike:.0f}) "
            f"for underlying={self.underlying_price:.2f}"
        )

        spreads = []

        for long_name, long_data in long_candidates.items():
            long_strike = self.extract_strike_from_name(long_name)
            long_delta = abs(long_data.get("greeks", {}).get("delta", 0))
            long_iv = long_data.get("greeks", {}).get("iv", 0)

            # Skip if long leg delta is too low (< 0.20 = too far OTM)
            if long_delta < 0.20:
                continue

            long_cost = self._calculate_leg_cost(long_data, config.quantity, is_buy=True)

            for short_name, short_data in short_candidates.items():
                short_strike = self.extract_strike_from_name(short_name)
                short_delta = abs(short_data.get("greeks", {}).get("delta", 0))

                # Skip if short leg delta is too low (< 0.10 = lottery ticket)
                if short_delta < 0.10:
                    continue

                # Skip if not valid spread structure (short must be > long)
                if short_strike <= long_strike:
                    continue

                # Calculate strike width first
                strike_width = short_strike - long_strike
                width_pct = (strike_width / self.underlying_price) * 100

                # Skip spreads that are too wide (> 15% of underlying for bull calls)
                # Reduced from 25% to prevent excessive spreads
                if width_pct > 15.0:
                    continue

                # Skip spreads that are too narrow (< 3% of underlying)
                # Prevents tiny profit potential like $50 spreads on $3000 underlying
                if width_pct < 3.0:
                    continue

                short_iv = short_data.get("greeks", {}).get("iv", 0)
                short_credit = self._calculate_leg_cost(short_data, config.quantity, is_buy=False)

                # Calculate metrics
                net_debit = long_cost - short_credit
                max_profit = strike_width - net_debit
                profit_debit_ratio = max_profit / net_debit if net_debit > 0 else 0
                iv_skew_slope = (short_iv - long_iv) / strike_width if strike_width > 0 else 0

                # Skip if debit is negative (we're getting paid - not a debit spread)
                if net_debit <= 0:
                    continue

                # Calculate breakeven and distance from current price
                debit_per_contract = net_debit / config.quantity if config.quantity > 0 else net_debit
                breakeven = long_strike + debit_per_contract
                breakeven_distance_pct = ((breakeven - self.underlying_price) / self.underlying_price) * 100

                # Skip if breakeven requires excessive move (> 12% for bull call spread)
                # This prevents lottery ticket trades that need 20%+ moves
                max_breakeven_distance_pct = 12.0
                if breakeven_distance_pct > max_breakeven_distance_pct:
                    continue

                # Calculate probability-adjusted score
                # Penalize spreads that are far OTM even if they have good profit/debit ratios
                long_otm_pct = ((long_strike - self.underlying_price) / self.underlying_price) * 100

                # Probability weight: decreases as strikes move further OTM
                # 1.0 for ATM, 0.5 for 10% OTM, 0.2 for 15% OTM, etc.
                if long_otm_pct <= 0:
                    probability_weight = 1.0  # ITM or ATM
                elif long_otm_pct <= 5:
                    probability_weight = 0.9
                elif long_otm_pct <= 8:
                    probability_weight = 0.7
                elif long_otm_pct <= 12:
                    probability_weight = 0.5
                else:
                    probability_weight = 0.3  # Very OTM

                # Risk-adjusted score: combines profit/debit with probability
                risk_adjusted_score = profit_debit_ratio * probability_weight

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
                    "long_delta": long_delta,
                    "short_delta": short_delta,
                    "net_debit": net_debit,
                    "strike_width": strike_width,
                    "profit_debit_ratio": profit_debit_ratio,
                    "iv_skew_slope": iv_skew_slope,
                    "max_profit": max_profit,
                    "breakeven": breakeven,
                    "breakeven_distance_pct": breakeven_distance_pct,
                    "long_otm_pct": long_otm_pct,
                    "probability_weight": probability_weight,
                    "risk_adjusted_score": risk_adjusted_score
                })

        if not spreads:
            raise ValueError(
                "No valid spreads found with given criteria. "
                "Try relaxing filters (lower min_profit_debit_ratio or increase max_budget)"
            )

        # Sort by optimization criteria
        if config.optimize_for == "profit_debit_ratio":
            # Use risk-adjusted score instead of raw profit/debit ratio
            # This prevents selecting lottery ticket trades with high ratios but low probability
            spreads.sort(key=lambda s: s["risk_adjusted_score"], reverse=True)
        elif config.optimize_for == "max_width_for_budget":
            spreads.sort(key=lambda s: s["strike_width"], reverse=True)

        # Get top N spreads (for multi-signal generation)
        num_variations = min(config.return_top_n, len(spreads))
        top_spreads = spreads[:num_variations]

        # Log all top variations
        logger.info(
            f"Found {len(spreads)} valid spreads, returning top {num_variations} variations "
            f"(sorted by {'risk-adjusted score' if config.optimize_for == 'profit_debit_ratio' else 'width'})"
        )

        for i, spread in enumerate(top_spreads, 1):
            logger.info(
                f"  #{i}: {spread['long_strike']:.0f}(Δ{spread['long_delta']:.2f}, "
                f"{spread['long_otm_pct']:+.1f}% OTM)/"
                f"{spread['short_strike']:.0f}(Δ{spread['short_delta']:.2f}), "
                f"profit/debit={spread['profit_debit_ratio']:.2f}, "
                f"risk-adj={spread['risk_adjusted_score']:.2f}, "
                f"breakeven={spread['breakeven']:.0f} ({spread['breakeven_distance_pct']:+.1f}%), "
                f"width={spread['strike_width']:.0f}, "
                f"debit=${spread['net_debit']:.2f}"
            )

        # Store all top spreads for multi-signal generation
        self._top_spread_variations = top_spreads

        # Return optimal spread (backward compatible)
        optimal = top_spreads[0]
        return optimal["long_name"], optimal["short_name"]

    def get_all_spread_variations(self) -> List[Tuple[str, str]]:
        """
        Get all top spread variations as instrument name pairs.

        This method should be called AFTER build_legs() has been called with skew_aware method.
        Returns the top N spreads that were identified during skew-aware optimization.

        Returns:
            List of (long_instrument_name, short_instrument_name) tuples

        Raises:
            ValueError: If build_legs() hasn't been called yet or no variations available

        Example:
            strategy.build_legs(ticker_data=data, spread_config=config)
            variations = strategy.get_all_spread_variations()
            # [(ETH-27MAR26-3100-C, ETH-27MAR26-3500-C), (ETH-27MAR26-3150-C, ETH-27MAR26-3550-C), ...]
        """
        if not self._top_spread_variations:
            raise ValueError(
                "No spread variations available. "
                "Call build_legs() with skew_aware method first."
            )

        return [
            (spread["long_name"], spread["short_name"])
            for spread in self._top_spread_variations
        ]

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
            key=lambda name: abs(self.extract_strike_from_name(name) - long_target_strike)
        )

        short_instrument = min(
            call_instruments.keys(),
            key=lambda name: abs(self.extract_strike_from_name(name) - short_target_strike)
        )

        long_actual = self.extract_strike_from_name(long_instrument)
        short_actual = self.extract_strike_from_name(short_instrument)

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
            if self.extract_strike_from_name(name) == config.long_specific_strike
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
                    self.extract_strike_from_name(name) - config.long_specific_strike
                )
            )

        # Find short strike (exact or closest)
        short_matches = [
            name for name in call_instruments.keys()
            if self.extract_strike_from_name(name) == config.short_specific_strike
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
                    self.extract_strike_from_name(name) - config.short_specific_strike
                )
            )

        long_actual = self.extract_strike_from_name(long_instrument)
        short_actual = self.extract_strike_from_name(short_instrument)

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
        # Use best ask price for buying
        best_ask_price = ticker.get("best_ask_price", 0)
        if best_ask_price == 0:
            logger.warning(f"Best ask price is 0 for {instrument_name}, using mark_price")
            best_ask_price = ticker.get("mark_price", 0)

        # Cost per contract in USD
        cost_per_contract = best_ask_price * self.underlying_price
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
        # Use best bid price for selling
        best_bid_price = ticker.get("best_bid_price", 0)
        if best_bid_price == 0:
            logger.warning(f"Best bid price is 0 for {instrument_name}, using mark_price")
            best_bid_price = ticker.get("mark_price", 0)

        # Credit per contract in USD
        credit_per_contract = best_bid_price * self.underlying_price
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
            price = ticker.get("best_ask_price", 0)
            if price == 0:
                price = ticker.get("mark_price", 0)
        else:
            price = ticker.get("best_bid_price", 0)
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
