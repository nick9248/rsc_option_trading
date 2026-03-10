"""
Buy/Sell Flow Analyzer for options trading.

Analyzes trade direction (buy/sell aggressor side) to identify conviction signals,
large trade activity, and regime changes. Complements OI-based metrics by showing
the direction of recent market activity.

Metrics:
- Per-strike buy/sell volume, notional, and counts
- Net flow and buy/sell ratios
- Multi-window trend detection (1h, 4h, 24h)
- Top strikes by buying/selling pressure
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class BuySellFlowAnalyzer:
    """
    Analyze buy/sell flow from trade direction data.

    Uses actual trade direction to identify directional conviction,
    detect regime changes, and track large participant activity.
    """

    def __init__(
        self,
        repository: DatabaseRepository,
        currency: str,
        expiration: str,
        spot_price: float,
        lookback_hours: int = 24,
        trade_filter: str = "all",
    ):
        """
        Initialize buy/sell flow analyzer.

        Args:
            repository: Database repository for querying trades.
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "27MAR26").
            spot_price: Current underlying spot price.
            lookback_hours: Hours to look back for trade data (default: 24).
            trade_filter: Trade size filter — "all" (no filter), "block"
                (notional >= $100k), or "non_block" (notional < $100k).
        """
        self.repository = repository
        self.currency = currency
        self.expiration = expiration
        self.spot_price = spot_price
        self.lookback_hours = lookback_hours
        self.trade_filter = trade_filter

        # Per-strike flow data: {strike: {option_type: {buy_count, sell_count, ...}}}
        self.flow_data: Dict[float, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {
                "buy_count": 0.0,
                "sell_count": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            })
        )

        # Expiration-level aggregates
        self.expiration_totals = {
            "call_buy_volume": 0.0,
            "call_sell_volume": 0.0,
            "put_buy_volume": 0.0,
            "put_sell_volume": 0.0,
        }

    def calculate(self) -> Dict[str, Any]:
        """
        Calculate all buy/sell flow metrics.

        Returns:
            Dict with per-strike data, expiration totals, and top strikes.
        """
        # Reset data structures
        self.flow_data = defaultdict(
            lambda: defaultdict(lambda: {
                "buy_count": 0.0,
                "sell_count": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            })
        )
        self.expiration_totals = {
            "call_buy_volume": 0.0,
            "call_sell_volume": 0.0,
            "put_buy_volume": 0.0,
            "put_sell_volume": 0.0,
        }

        # Fetch trades from database
        trades = self._fetch_trades(self.lookback_hours)

        if not trades:
            logger.warning(f"No trades found for {self.currency} {self.expiration}")
            return self._empty_result()

        # Process each trade
        for trade in trades:
            self._process_trade(trade)

        # Calculate derived metrics (net flow, ratios)
        self._calculate_derived_metrics()

        # Detect flow trends (multi-window comparison)
        flow_trend = self._detect_flow_trend()

        # Find top strikes by buying/selling pressure
        top_buy_strikes = self._find_top_strikes_by_buying()
        top_sell_strikes = self._find_top_strikes_by_selling()

        # Determine expiration-level bias
        bias_interpretation = self._interpret_flow_bias()

        return {
            "flow_data": dict(self.flow_data),
            "expiration_totals": self.expiration_totals,
            "bias_interpretation": bias_interpretation,
            "flow_trend": flow_trend,
            "top_buy_strikes": top_buy_strikes,
            "top_sell_strikes": top_sell_strikes,
            "trade_count": len(trades),
            "spot_price": self.spot_price,
        }

    def _fetch_trades(self, lookback_hours: int) -> List[Dict[str, Any]]:
        """
        Fetch trades from database for the specified time window.

        Args:
            lookback_hours: Hours to look back for trade data.

        Returns:
            List of trade dictionaries.
        """
        # Calculate time window
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=lookback_hours)

        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        filter_clause = {
            "block":     "AND (amount * index_price) >= 100000",
            "non_block": "AND (amount * index_price) < 100000",
        }.get(self.trade_filter, "")

        query = f"""
            SELECT
                trade_id, trade_timestamp, instrument_name, strike,
                option_type, price, amount, direction, index_price
            FROM historical_trades
            WHERE currency = %s
                AND expiration = %s
                AND trade_timestamp >= %s
                AND trade_timestamp <= %s
                AND strike IS NOT NULL
                AND direction IS NOT NULL
                {filter_clause}
            ORDER BY trade_timestamp ASC
        """

        with self.repository._db_cursor() as cursor:
            cursor.execute(query, (self.currency, self.expiration, start_ts, end_ts))

            columns = [
                "trade_id", "trade_timestamp", "instrument_name", "strike",
                "option_type", "price", "amount", "direction", "index_price"
            ]

            trades = [dict(zip(columns, row)) for row in cursor.fetchall()]

        logger.info(
            f"Fetched {len(trades)} trades for {self.currency} {self.expiration} "
            f"from {start_time} to {end_time} ({lookback_hours}h window)"
        )

        return trades

    def _process_trade(self, trade: Dict[str, Any]) -> None:
        """
        Process a single trade and update flow data.

        Args:
            trade: Trade dictionary from database.
        """
        # Convert Decimal types from database to float
        strike = float(trade["strike"]) if trade["strike"] is not None else 0.0
        option_type = trade["option_type"]  # "C" or "P"
        amount = float(trade["amount"]) if trade["amount"] is not None else 0.0
        direction = trade["direction"]  # "buy" or "sell"
        index_price = float(trade.get("index_price")) if trade.get("index_price") is not None else self.spot_price

        # Calculate notional value
        notional = amount * index_price

        # Get or initialize strike data
        strike_data = self.flow_data[strike][option_type]

        # Update counts and volumes based on direction
        if direction == "buy":
            strike_data["buy_count"] += 1
            strike_data["buy_volume"] += amount
            strike_data["buy_notional"] += notional

            # Update expiration totals
            if option_type == "C":
                self.expiration_totals["call_buy_volume"] += amount
            else:
                self.expiration_totals["put_buy_volume"] += amount
        else:  # direction == "sell"
            strike_data["sell_count"] += 1
            strike_data["sell_volume"] += amount
            strike_data["sell_notional"] += notional

            # Update expiration totals
            if option_type == "C":
                self.expiration_totals["call_sell_volume"] += amount
            else:
                self.expiration_totals["put_sell_volume"] += amount

    def _calculate_derived_metrics(self) -> None:
        """
        Calculate derived metrics (net flow, buy/sell ratio) for each strike.
        """
        for strike, option_types in self.flow_data.items():
            for option_type, data in option_types.items():
                buy_vol = data["buy_volume"]
                sell_vol = data["sell_volume"]

                # Net flow: positive = net buying, negative = net selling
                data["net_flow"] = buy_vol - sell_vol

                # Buy/sell ratio: avoid division by zero
                if sell_vol > 0:
                    data["buy_sell_ratio"] = buy_vol / sell_vol
                else:
                    data["buy_sell_ratio"] = float("inf") if buy_vol > 0 else 0.0

    def _detect_flow_trend(self) -> str:
        """
        Detect flow trend by comparing rates across multiple time windows.

        Compares 1h, 4h, and 24h windows to identify acceleration/deceleration.

        Returns:
            Trend label string.
        """
        # Fetch trades for each window
        trades_1h = self._fetch_trades(1)
        trades_4h = self._fetch_trades(4)
        trades_24h = self._fetch_trades(24)

        # Calculate net flow for each window
        def calc_net_flow(trades: List[Dict[str, Any]]) -> float:
            """Calculate net flow (buy - sell volume) from trades."""
            net = 0.0
            for trade in trades:
                amount = float(trade["amount"]) if trade["amount"] is not None else 0.0
                direction = trade["direction"]
                if direction == "buy":
                    net += amount
                else:
                    net -= amount
            return net

        net_1h = calc_net_flow(trades_1h)
        net_4h = calc_net_flow(trades_4h)
        net_24h = calc_net_flow(trades_24h)

        # Normalize to per-hour rates
        rate_1h = net_1h / 1 if len(trades_1h) > 0 else 0.0
        rate_4h = net_4h / 4 if len(trades_4h) > 0 else 0.0
        rate_24h = net_24h / 24 if len(trades_24h) > 0 else 0.0

        # Detect trend patterns
        # Accelerating buy: 1h >> 4h >> 24h (all positive, increasing rate)
        if rate_1h > 0 and rate_4h > 0 and rate_24h > 0:
            if rate_1h > rate_4h * 1.5 and rate_4h > rate_24h * 1.2:
                return "Accelerating Buy Pressure"
            elif rate_1h < rate_4h * 0.7:
                return "Decelerating Buy Pressure"
            else:
                return "Steady Buy Pressure"

        # Accelerating sell: 1h << 4h << 24h (all negative, increasing magnitude)
        elif rate_1h < 0 and rate_4h < 0 and rate_24h < 0:
            if abs(rate_1h) > abs(rate_4h) * 1.5 and abs(rate_4h) > abs(rate_24h) * 1.2:
                return "Accelerating Sell Pressure"
            elif abs(rate_1h) < abs(rate_4h) * 0.7:
                return "Decelerating Sell Pressure"
            else:
                return "Steady Sell Pressure"

        # Reversing to sell: 1h negative but 24h positive
        elif rate_1h < 0 < rate_24h:
            return "Reversing to Sell Pressure"

        # Reversing to buy: 1h positive but 24h negative
        elif rate_1h > 0 > rate_24h:
            return "Reversing to Buy Pressure"

        # Mixed or neutral
        else:
            return "Mixed/Neutral Flow"

    def _find_top_strikes_by_buying(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Find top strikes by buying pressure (net buying volume).

        Args:
            top_n: Number of top strikes to return.

        Returns:
            List of dicts with strike, option_type, net_flow, buy_volume.
        """
        all_strikes = []
        for strike, option_types in self.flow_data.items():
            for option_type, data in option_types.items():
                if data["net_flow"] > 0:  # Only positive net flow (net buying)
                    all_strikes.append({
                        "strike": strike,
                        "option_type": option_type,
                        "net_flow": data["net_flow"],
                        "buy_volume": data["buy_volume"],
                        "buy_notional": data["buy_notional"],
                    })

        # Sort by net flow descending
        all_strikes.sort(key=lambda x: x["net_flow"], reverse=True)
        return all_strikes[:top_n]

    def _find_top_strikes_by_selling(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Find top strikes by selling pressure (net selling volume).

        Args:
            top_n: Number of top strikes to return.

        Returns:
            List of dicts with strike, option_type, net_flow, sell_volume.
        """
        all_strikes = []
        for strike, option_types in self.flow_data.items():
            for option_type, data in option_types.items():
                if data["net_flow"] < 0:  # Only negative net flow (net selling)
                    all_strikes.append({
                        "strike": strike,
                        "option_type": option_type,
                        "net_flow": data["net_flow"],
                        "sell_volume": data["sell_volume"],
                        "sell_notional": data["sell_notional"],
                    })

        # Sort by net flow ascending (most negative first)
        all_strikes.sort(key=lambda x: x["net_flow"])
        return all_strikes[:top_n]

    def _interpret_flow_bias(self) -> str:
        """
        Interpret expiration-level flow bias.

        Returns:
            Bias interpretation string.
        """
        total_buy = (
            self.expiration_totals["call_buy_volume"] +
            self.expiration_totals["put_buy_volume"]
        )
        total_sell = (
            self.expiration_totals["call_sell_volume"] +
            self.expiration_totals["put_sell_volume"]
        )

        net_flow = total_buy - total_sell

        # Thresholds based on ratio
        if total_sell > 0:
            buy_sell_ratio = total_buy / total_sell
        else:
            buy_sell_ratio = float("inf") if total_buy > 0 else 1.0

        if buy_sell_ratio > 1.3:
            return "Heavy Buying"
        elif buy_sell_ratio > 1.1:
            return "Moderate Buying"
        elif buy_sell_ratio > 0.9:
            return "Balanced"
        elif buy_sell_ratio > 0.7:
            return "Moderate Selling"
        else:
            return "Heavy Selling"

    def _empty_result(self) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            "flow_data": {},
            "expiration_totals": {
                "call_buy_volume": 0.0,
                "call_sell_volume": 0.0,
                "put_buy_volume": 0.0,
                "put_sell_volume": 0.0,
            },
            "bias_interpretation": "No Data",
            "flow_trend": "No Data",
            "top_buy_strikes": [],
            "top_sell_strikes": [],
            "trade_count": 0,
            "spot_price": self.spot_price,
        }

    def generate_report_section(self) -> str:
        """
        Generate formatted text report section for buy/sell flow.

        Returns:
            Formatted string for inclusion in analysis report.
        """
        result = self.calculate()
        lines = []
        separator = "-" * 80

        lines.append("BUY/SELL FLOW ANALYSIS (Trade Direction-Based)")
        lines.append(separator)
        lines.append(f"Spot Price: ${self.spot_price:,.2f}")
        lines.append(f"Lookback Window: {self.lookback_hours} hours")
        lines.append(f"Trades Analyzed: {result['trade_count']}")
        lines.append("")

        # Expiration-level summary
        totals = result["expiration_totals"]
        lines.append("EXPIRATION-LEVEL FLOW:")
        lines.append(f"  Calls:  Buy: {totals['call_buy_volume']:>10,.1f}  Sell: {totals['call_sell_volume']:>10,.1f}")
        lines.append(f"  Puts:   Buy: {totals['put_buy_volume']:>10,.1f}  Sell: {totals['put_sell_volume']:>10,.1f}")
        lines.append(f"  Bias: {result['bias_interpretation']}")
        lines.append(f"  Trend: {result['flow_trend']}")
        lines.append("")

        # Top buying pressure strikes
        lines.append("TOP 5 STRIKES BY BUYING PRESSURE:")
        lines.append(separator)
        if result["top_buy_strikes"]:
            lines.append(
                f"{'Strike':>10}  {'Type':>6}  {'Net Flow':>12}  {'Buy Vol':>12}  {'Buy Notional':>15}"
            )
            lines.append(
                f"{'------':>10}  {'----':>6}  {'---------':>12}  {'--------':>12}  {'-------------':>15}"
            )
            for item in result["top_buy_strikes"]:
                lines.append(
                    f"{item['strike']:>10,.0f}  {item['option_type']:>6}  "
                    f"{item['net_flow']:>+12,.1f}  {item['buy_volume']:>12,.1f}  "
                    f"${item['buy_notional']:>14,.2f}"
                )
        else:
            lines.append("  No net buying detected")
        lines.append("")

        # Top selling pressure strikes
        lines.append("TOP 5 STRIKES BY SELLING PRESSURE:")
        lines.append(separator)
        if result["top_sell_strikes"]:
            lines.append(
                f"{'Strike':>10}  {'Type':>6}  {'Net Flow':>12}  {'Sell Vol':>12}  {'Sell Notional':>15}"
            )
            lines.append(
                f"{'------':>10}  {'----':>6}  {'---------':>12}  {'---------':>12}  {'--------------':>15}"
            )
            for item in result["top_sell_strikes"]:
                lines.append(
                    f"{item['strike']:>10,.0f}  {item['option_type']:>6}  "
                    f"{item['net_flow']:>+12,.1f}  {item['sell_volume']:>12,.1f}  "
                    f"${item['sell_notional']:>14,.2f}"
                )
        else:
            lines.append("  No net selling detected")
        lines.append("")

        return "\n".join(lines)
