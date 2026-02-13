"""
Flow-based GEX (Gamma Exposure) and DEX (Delta Exposure) calculator.

Uses trade-level data with direction (buy/sell aggressor side) to reconstruct
dealer positioning, rather than relying on OI snapshots. This approach is more
accurate for crypto markets where OI heuristics fail.

Theory:
- When customer buys a call/put, dealer sells it and must hedge (buy underlying for calls,
  sell for puts) -> positive gamma exposure for dealer
- When customer sells a call/put, dealer buys it and must hedge oppositely -> negative gamma
- Aggregate all trades by strike to get net dealer positioning per strike
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class FlowBasedGexCalculator:
    """
    Calculate GEX and DEX from trade flow data.

    Uses actual trade direction to infer dealer positioning, providing
    more accurate gamma/delta exposure estimates than OI-based methods.
    """

    def __init__(
        self,
        repository: DatabaseRepository,
        currency: str,
        expiration: str,
        spot_price: float,
        lookback_hours: int = 24,
        risk_free_rate: float = 0.0
    ):
        """
        Initialize flow-based GEX calculator.

        Args:
            repository: Database repository for querying trades.
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "27MAR26").
            spot_price: Current underlying spot price.
            lookback_hours: Hours to look back for trade data (default: 24).
            risk_free_rate: Risk-free rate for Black-Scholes (default: 0.0).
        """
        self.repository = repository
        self.currency = currency
        self.expiration = expiration
        self.spot_price = spot_price
        self.lookback_hours = lookback_hours
        self.bs_calculator = BlackScholesCalculator(risk_free_rate=risk_free_rate)
        self.strike_data: Dict[float, Dict[str, Any]] = {}

    def calculate(self) -> Dict[str, Any]:
        """
        Calculate all flow-based GEX/DEX metrics.

        Returns:
            Dict with per-strike data, cumulative profiles, and key levels.
        """
        # Fetch trades from database
        trades = self._fetch_trades()

        if not trades:
            logger.warning(f"No trades found for {self.currency} {self.expiration}")
            return self._empty_result()

        # Calculate expiration datetime
        exp_datetime = self._parse_expiration(self.expiration)
        if not exp_datetime:
            logger.error(f"Failed to parse expiration: {self.expiration}")
            return self._empty_result()

        # Process each trade
        for trade in trades:
            self._process_trade(trade, exp_datetime)

        # Calculate net GEX/DEX per strike
        self._calculate_gex_dex()

        # Calculate cumulative profiles
        cumulative = self._calculate_cumulative_profiles()

        # Detect key levels
        key_levels = self._detect_key_levels()

        return {
            "strike_data": self.strike_data,
            "cumulative_gex": cumulative["cumulative_gex"],
            "cumulative_dex": cumulative["cumulative_dex"],
            "key_levels": key_levels,
            "spot_price": self.spot_price,
            "total_net_gex": sum(d["net_gex"] for d in self.strike_data.values()),
            "total_net_dex": sum(d["net_dex"] for d in self.strike_data.values()),
            "trade_count": len(trades),
        }

    def _fetch_trades(self) -> List[Dict[str, Any]]:
        """
        Fetch trades from database for the specified time window.

        Returns:
            List of trade dictionaries.
        """
        # Calculate time window
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=self.lookback_hours)

        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        query = """
            SELECT
                trade_id, trade_timestamp, instrument_name, strike,
                option_type, price, amount, direction, iv, index_price
            FROM historical_trades
            WHERE currency = %s
                AND expiration = %s
                AND trade_timestamp >= %s
                AND trade_timestamp <= %s
                AND strike IS NOT NULL
                AND iv IS NOT NULL
            ORDER BY trade_timestamp ASC
        """

        with self.repository._db_cursor() as cursor:
            cursor.execute(query, (self.currency, self.expiration, start_ts, end_ts))

            columns = [
                "trade_id", "trade_timestamp", "instrument_name", "strike",
                "option_type", "price", "amount", "direction", "iv", "index_price"
            ]

            trades = [dict(zip(columns, row)) for row in cursor.fetchall()]

        logger.info(
            f"Fetched {len(trades)} trades for {self.currency} {self.expiration} "
            f"from {start_time} to {end_time}"
        )

        return trades

    def _process_trade(self, trade: Dict[str, Any], exp_datetime: datetime) -> None:
        """
        Process a single trade and update strike data.

        Args:
            trade: Trade dictionary from database.
            exp_datetime: Expiration datetime.
        """
        # Convert Decimal types from database to float
        strike = float(trade["strike"]) if trade["strike"] is not None else 0.0
        option_type = trade["option_type"]
        amount = float(trade["amount"]) if trade["amount"] is not None else 0.0
        direction = trade["direction"]  # "buy" or "sell"
        iv = float(trade["iv"]) if trade["iv"] is not None else 0.0
        trade_time = datetime.fromtimestamp(int(trade["trade_timestamp"]) / 1000)

        # Calculate time to expiry in years
        time_to_expiry = (exp_datetime - trade_time).total_seconds() / (365.25 * 24 * 3600)

        if time_to_expiry <= 0:
            return  # Skip expired trades

        # Use index_price at trade time as spot, fallback to current spot
        spot_at_trade = float(trade.get("index_price")) if trade.get("index_price") is not None else self.spot_price

        # Calculate greeks using Black-Scholes
        greeks = self.bs_calculator.calculate_greeks(
            spot_price=spot_at_trade,
            strike_price=strike,
            time_to_expiry=time_to_expiry,
            implied_volatility=iv,
            option_type="call" if option_type == "C" else "put"
        )

        gamma = greeks["gamma"]
        delta = greeks["delta"]

        # Initialize strike data if not exists
        if strike not in self.strike_data:
            self.strike_data[strike] = {
                "dealer_gamma": 0.0,  # Dealer's net gamma position
                "dealer_delta": 0.0,  # Dealer's net delta position
                "call_volume": 0.0,
                "put_volume": 0.0,
                "net_gex": 0.0,
                "net_dex": 0.0,
            }

        # Apply dealer positioning logic based on trade direction
        # direction="buy" means customer bought (dealer sold) -> dealer needs to hedge
        # direction="sell" means customer sold (dealer bought) -> dealer has opposite position

        if direction == "buy":
            # Customer bought, dealer sold
            # Dealer is SHORT the option, needs to hedge by buying underlying
            # This creates POSITIVE gamma exposure for the dealer
            dealer_gamma_contribution = gamma * amount
            dealer_delta_contribution = delta * amount
        else:  # direction == "sell"
            # Customer sold, dealer bought
            # Dealer is LONG the option, needs to hedge by selling underlying
            # This creates NEGATIVE gamma exposure for the dealer
            dealer_gamma_contribution = -gamma * amount
            dealer_delta_contribution = -delta * amount

        # Aggregate by option type for reporting
        if option_type == "C":
            self.strike_data[strike]["call_volume"] += amount
        else:
            self.strike_data[strike]["put_volume"] += amount

        # Accumulate dealer positioning
        self.strike_data[strike]["dealer_gamma"] += dealer_gamma_contribution
        self.strike_data[strike]["dealer_delta"] += dealer_delta_contribution

    def _calculate_gex_dex(self) -> None:
        """
        Calculate Net GEX and Net DEX per strike from dealer positioning.

        Net GEX = Dealer Gamma * Spot Price
        Net DEX = Dealer Delta * Spot Price (normalized)
        """
        for strike, data in self.strike_data.items():
            # Net GEX: Dealer gamma exposure scaled by spot price
            data["net_gex"] = data["dealer_gamma"] * self.spot_price

            # Net DEX: Dealer delta exposure
            data["net_dex"] = data["dealer_delta"]

    def _calculate_cumulative_profiles(self) -> Dict[str, Dict[float, float]]:
        """
        Calculate cumulative GEX and DEX profiles across strikes.

        Returns:
            Dict with cumulative_gex and cumulative_dex mappings.
        """
        sorted_strikes = sorted(self.strike_data.keys())

        cumulative_gex: Dict[float, float] = {}
        cumulative_dex: Dict[float, float] = {}

        running_gex = 0.0
        running_dex = 0.0

        for strike in sorted_strikes:
            running_gex += self.strike_data[strike]["net_gex"]
            running_dex += self.strike_data[strike]["net_dex"]
            cumulative_gex[strike] = running_gex
            cumulative_dex[strike] = running_dex
            # Store in strike data as well
            self.strike_data[strike]["cumulative_gex"] = running_gex
            self.strike_data[strike]["cumulative_dex"] = running_dex

        return {
            "cumulative_gex": cumulative_gex,
            "cumulative_dex": cumulative_dex,
        }

    def _detect_key_levels(self) -> Dict[str, Any]:
        """
        Detect key trading levels from GEX/DEX data.

        Returns:
            Dict with call_resistance, put_support, hvl (zero gamma), and gamma_flip.
        """
        if not self.strike_data:
            return {
                "call_resistance": None,
                "put_support": None,
                "hvl": None,
                "gamma_flip": None,
            }

        sorted_strikes = sorted(self.strike_data.keys())

        # Call Resistance: Strike with maximum positive Net GEX
        max_positive_gex = 0.0
        call_resistance = None
        for strike in sorted_strikes:
            gex = self.strike_data[strike]["net_gex"]
            if gex > max_positive_gex:
                max_positive_gex = gex
                call_resistance = strike

        # Put Support: Strike with maximum negative Net GEX (by absolute value)
        max_negative_gex = 0.0
        put_support = None
        for strike in sorted_strikes:
            gex = self.strike_data[strike]["net_gex"]
            if gex < 0 and abs(gex) > max_negative_gex:
                max_negative_gex = abs(gex)
                put_support = strike

        # HVL / Gamma Flip: Where cumulative GEX crosses zero
        gamma_flip = None
        hvl = None
        prev_cumulative = None

        for strike in sorted_strikes:
            curr_cumulative = self.strike_data[strike]["cumulative_gex"]

            if prev_cumulative is not None:
                # Check for sign change in cumulative GEX
                if prev_cumulative * curr_cumulative < 0:
                    gamma_flip = strike
                    hvl = strike

            prev_cumulative = curr_cumulative

        # Fallback: Find strike closest to zero cumulative GEX
        if hvl is None and sorted_strikes:
            min_abs_cumulative = float("inf")
            for strike in sorted_strikes:
                abs_cumulative = abs(self.strike_data[strike]["cumulative_gex"])
                if abs_cumulative < min_abs_cumulative:
                    min_abs_cumulative = abs_cumulative
                    hvl = strike

        return {
            "call_resistance": {
                "strike": call_resistance,
                "net_gex": max_positive_gex,
            } if call_resistance else None,
            "put_support": {
                "strike": put_support,
                "net_gex": -max_negative_gex,
            } if put_support else None,
            "hvl": hvl,
            "gamma_flip": gamma_flip,
        }

    def _parse_expiration(self, expiration: str) -> Optional[datetime]:
        """
        Parse expiration string to datetime.

        Args:
            expiration: Expiration string (e.g., "27MAR26").

        Returns:
            Expiration datetime at 08:00 UTC (Deribit expiry time) or None if parsing fails.
        """
        try:
            # Deribit format: DDMMMYY (e.g., "27MAR26")
            exp_date = datetime.strptime(expiration, "%d%b%y")
            # Deribit options expire at 08:00 UTC
            exp_datetime = exp_date.replace(hour=8, minute=0, second=0, microsecond=0)
            return exp_datetime
        except ValueError as e:
            logger.error(f"Failed to parse expiration '{expiration}': {e}")
            return None

    def _empty_result(self) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "strike_data": {},
            "cumulative_gex": {},
            "cumulative_dex": {},
            "key_levels": {
                "call_resistance": None,
                "put_support": None,
                "hvl": None,
                "gamma_flip": None,
            },
            "spot_price": self.spot_price,
            "total_net_gex": 0.0,
            "total_net_dex": 0.0,
            "trade_count": 0,
        }

    def generate_report_section(self) -> str:
        """
        Generate formatted text report section for flow-based GEX/DEX.

        Returns:
            Formatted string for inclusion in analysis report.
        """
        result = self.calculate()
        lines = []
        separator = "-" * 80

        lines.append("FLOW-BASED GEX/DEX ANALYSIS (Trade Direction-Based)")
        lines.append(separator)
        lines.append(f"Spot Price: ${self.spot_price:,.2f}")
        lines.append(f"Lookback Window: {self.lookback_hours} hours")
        lines.append(f"Trades Analyzed: {result['trade_count']}")
        lines.append("")

        # Key Levels
        key_levels = result["key_levels"]
        lines.append("KEY LEVELS:")

        if key_levels["call_resistance"]:
            cr = key_levels["call_resistance"]
            lines.append(
                f"  Call Resistance: ${cr['strike']:,.0f} "
                f"(Net GEX: {cr['net_gex']:+,.2f})"
            )
        else:
            lines.append("  Call Resistance: None found")

        if key_levels["put_support"]:
            ps = key_levels["put_support"]
            lines.append(
                f"  Put Support: ${ps['strike']:,.0f} "
                f"(Net GEX: {ps['net_gex']:+,.2f})"
            )
        else:
            lines.append("  Put Support: None found")

        if key_levels["hvl"]:
            lines.append(f"  HVL (Zero Gamma): ${key_levels['hvl']:,.0f}")
        else:
            lines.append("  HVL (Zero Gamma): Not detected")

        lines.append("")

        # Totals
        lines.append("TOTALS:")
        lines.append(f"  Total Net GEX: {result['total_net_gex']:+,.2f}")
        lines.append(f"  Total Net DEX: {result['total_net_dex']:+,.2f}")
        lines.append("")

        # Interpretation
        total_gex = result["total_net_gex"]
        if total_gex > 0:
            gex_interp = "Positive (Dealers long gamma - stabilizing, buy dips/sell rallies)"
        elif total_gex < 0:
            gex_interp = "Negative (Dealers short gamma - amplifying volatility)"
        else:
            gex_interp = "Neutral"
        lines.append(f"  GEX Environment: {gex_interp}")

        total_dex = result["total_net_dex"]
        if total_dex > 0:
            dex_interp = "Positive (Net long delta - bullish pressure)"
        elif total_dex < 0:
            dex_interp = "Negative (Net short delta - bearish pressure)"
        else:
            dex_interp = "Neutral"
        lines.append(f"  DEX Environment: {dex_interp}")
        lines.append("")

        # Per-strike data table (top strikes by absolute GEX)
        if result["strike_data"]:
            lines.append("TOP STRIKES BY ABSOLUTE NET GEX:")
            lines.append(separator)
            lines.append(
                f"{'Strike':>10}  {'Net GEX':>12}  {'Net DEX':>12}  "
                f"{'Call Vol':>10}  {'Put Vol':>10}  Notes"
            )
            lines.append(
                f"{'------':>10}  {'-------':>12}  {'-------':>12}  "
                f"{'--------':>10}  {'-------':>10}  -----"
            )

            # Sort by absolute GEX and take top 20
            sorted_strikes = sorted(
                result["strike_data"].keys(),
                key=lambda s: abs(result["strike_data"][s]["net_gex"]),
                reverse=True
            )[:20]

            for strike in sorted_strikes:
                data = result["strike_data"][strike]

                notes = []
                if key_levels["call_resistance"] and strike == key_levels["call_resistance"]["strike"]:
                    notes.append("Call Resistance")
                if key_levels["put_support"] and strike == key_levels["put_support"]["strike"]:
                    notes.append("Put Support")
                if key_levels["hvl"] and strike == key_levels["hvl"]:
                    notes.append("HVL/Zero Gamma")

                notes_str = " | ".join(notes) if notes else ""

                lines.append(
                    f"{strike:>10,.0f}  {data['net_gex']:>+12,.2f}  {data['net_dex']:>+12,.4f}  "
                    f"{data.get('call_volume', 0):>10,.1f}  {data.get('put_volume', 0):>10,.1f}  {notes_str}"
                )

        lines.append("")
        return "\n".join(lines)
