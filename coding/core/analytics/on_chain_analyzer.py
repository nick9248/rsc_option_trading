"""
On-chain analytics for options market data.

Calculates max pain, put/call ratios, support/resistance levels,
and generates formatted text reports per expiration.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OnChainAnalyzer:
    """
    Calculate on-chain analytics from option book summary data.

    Analyzes open interest distribution to calculate:
    - Max pain price per expiration
    - Put/Call ratios
    - Support and resistance levels
    - Open interest by strike
    - GEX/DEX exposure (when Greeks data is provided)
    """

    def __init__(self, data: List[Dict[str, Any]], currency: str):
        """
        Initialize analyzer with book summary data.

        Args:
            data: List of book summary items from Deribit API.
            currency: Currency symbol (ETH, BTC).
        """
        self.raw_data = data
        self.currency = currency
        self.underlying_price: float = 0.0
        self.parsed_data: Dict[str, List[Dict]] = {}
        self.gex_dex_data: Dict[str, str] = {}  # Stores GEX/DEX report per expiration
        self.buy_sell_flow_data: Dict[str, str] = {}  # Stores buy/sell flow report per expiration
        self.market_metrics: Dict[str, Any] = {}  # Stores DVOL, funding rate, etc.

        # Extract underlying price using most common value (mode)
        # Different instruments may have slightly different underlying_price values
        # depending on when their data was last updated. The mode gives us
        # the most current price since most instruments share it.
        if data:
            self.underlying_price = self._extract_underlying_price(data)

        logger.info(f"Initialized OnChainAnalyzer with {len(data)} instruments")

    def _extract_underlying_price(self, data: List[Dict[str, Any]]) -> float:
        """
        Extract the most accurate underlying price from data.

        Uses the underlying_price from the highest volume instrument,
        as actively traded instruments have the most recently updated
        price data. The book_summary endpoint caches underlying_price
        per instrument, so stale instruments may have outdated values.

        Args:
            data: List of book summary items.

        Returns:
            Underlying price from highest volume instrument, or 0 if none found.
        """
        # Filter to instruments with volume and valid price
        active_instruments = [
            item for item in data
            if (item.get("volume") or 0) > 0 and item.get("underlying_price")
        ]

        if not active_instruments:
            # Fallback: use any instrument with a price
            for item in data:
                if item.get("underlying_price"):
                    return item.get("underlying_price")
            return 0.0

        # Get the instrument with highest volume (most recently active)
        highest_volume_item = max(active_instruments, key=lambda x: x.get("volume", 0))
        price = highest_volume_item.get("underlying_price", 0)

        logger.debug(
            f"Underlying price: {price} "
            f"(from {highest_volume_item.get('instrument_name')} "
            f"with volume {highest_volume_item.get('volume')})"
        )

        return price

    def parse_instruments(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Parse instrument names and group by expiration.

        Instrument format: ETH-27DEC24-3000-C
        - Parts[0]: Currency (ETH)
        - Parts[1]: Expiration (27DEC24)
        - Parts[2]: Strike price (3000)
        - Parts[3]: Option type (C=Call, P=Put)

        Returns:
            Dict mapping expiration -> list of parsed instruments.
        """
        grouped: Dict[str, List[Dict]] = {}

        for item in self.raw_data:
            instrument_name = item.get("instrument_name", "")
            parts = instrument_name.split("-")

            if len(parts) < 4:
                logger.warning(f"Skipping invalid instrument: {instrument_name}")
                continue

            expiration = parts[1]
            try:
                strike = float(parts[2])
            except ValueError:
                logger.warning(f"Invalid strike price in: {instrument_name}")
                continue

            option_type = parts[3].upper()
            if option_type not in ("C", "P"):
                logger.warning(f"Invalid option type in: {instrument_name}")
                continue

            parsed_item = {
                "instrument_name": instrument_name,
                "expiration": expiration,
                "strike": strike,
                "option_type": option_type,
                "open_interest": item.get("open_interest", 0) or 0,
                "volume": item.get("volume", 0) or 0,
                "volume_usd": item.get("volume_usd", 0) or 0,
                "mark_price": item.get("mark_price", 0) or 0,
            }

            if expiration not in grouped:
                grouped[expiration] = []
            grouped[expiration].append(parsed_item)

        self.parsed_data = grouped
        logger.info(f"Parsed {len(grouped)} expirations")
        return grouped

    def group_by_strike(
        self, instruments: List[Dict[str, Any]]
    ) -> Dict[float, Dict[str, float]]:
        """
        Group instruments by strike price.

        Args:
            instruments: List of parsed instrument dicts for one expiration.

        Returns:
            Dict mapping strike -> {call_oi, put_oi, call_volume, put_volume}.
        """
        grouped: Dict[float, Dict[str, float]] = {}

        for item in instruments:
            strike = item["strike"]
            option_type = item["option_type"]
            oi = item["open_interest"]
            volume = item["volume"]

            if strike not in grouped:
                grouped[strike] = {
                    "call_oi": 0.0,
                    "put_oi": 0.0,
                    "call_volume": 0.0,
                    "put_volume": 0.0,
                }

            if option_type == "C":
                grouped[strike]["call_oi"] += oi
                grouped[strike]["call_volume"] += volume
            else:  # P
                grouped[strike]["put_oi"] += oi
                grouped[strike]["put_volume"] += volume

        return grouped

    def calculate_max_pain(
        self, strike_data: Dict[float, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Calculate max pain strike price.

        Max pain is the strike where option writers (sellers) pay the minimum
        to option buyers. It's where the most options expire worthless.

        Formula:
        For each candidate strike K:
          - Call loss at strike S: max(0, K - S) * call_OI
          - Put loss at strike S: max(0, S - K) * put_OI
          - Total pain = sum of all call + put losses
        Max Pain = K with minimum total pain

        Args:
            strike_data: Dict mapping strike -> {call_oi, put_oi}.

        Returns:
            Dict with max_pain_strike, pain_by_strike, and min_pain_value.
        """
        if not strike_data:
            return {
                "max_pain_strike": None,
                "pain_by_strike": {},
                "min_pain_value": 0,
            }

        strikes = sorted(strike_data.keys())
        pain_by_strike: Dict[float, float] = {}

        for candidate in strikes:
            total_pain = 0.0

            for strike, oi_data in strike_data.items():
                call_oi = oi_data["call_oi"]
                put_oi = oi_data["put_oi"]

                # Call intrinsic value if underlying settles at candidate
                # Calls are ITM when underlying > strike
                call_pain = max(0, candidate - strike) * call_oi

                # Put intrinsic value if underlying settles at candidate
                # Puts are ITM when underlying < strike
                put_pain = max(0, strike - candidate) * put_oi

                total_pain += call_pain + put_pain

            pain_by_strike[candidate] = total_pain

        max_pain_strike = min(pain_by_strike.keys(), key=lambda k: pain_by_strike[k])

        return {
            "max_pain_strike": max_pain_strike,
            "pain_by_strike": pain_by_strike,
            "min_pain_value": pain_by_strike[max_pain_strike],
        }

    def calculate_put_call_ratio(
        self, strike_data: Dict[float, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Calculate put/call ratio from open interest.

        Args:
            strike_data: Dict mapping strike -> {call_oi, put_oi}.

        Returns:
            Dict with total_call_oi, total_put_oi, ratio, and bias.
        """
        total_call_oi = sum(data["call_oi"] for data in strike_data.values())
        total_put_oi = sum(data["put_oi"] for data in strike_data.values())

        if total_call_oi > 0:
            ratio = total_put_oi / total_call_oi
        else:
            ratio = float("inf") if total_put_oi > 0 else 0

        # Determine bias
        if ratio < 0.7:
            bias = "Strong Bullish"
        elif ratio < 1.0:
            bias = "Bullish"
        elif ratio == 1.0:
            bias = "Neutral"
        elif ratio < 1.3:
            bias = "Bearish"
        else:
            bias = "Strong Bearish"

        return {
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "ratio": ratio,
            "bias": bias,
        }

    def calculate_volume_stats(
        self, strike_data: Dict[float, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Calculate volume statistics.

        Args:
            strike_data: Dict mapping strike -> {call_volume, put_volume, ...}.

        Returns:
            Dict with total volumes and volume ratio.
        """
        total_call_volume = sum(data["call_volume"] for data in strike_data.values())
        total_put_volume = sum(data["put_volume"] for data in strike_data.values())
        total_volume = total_call_volume + total_put_volume

        if total_call_volume > 0:
            volume_ratio = total_put_volume / total_call_volume
        else:
            volume_ratio = float("inf") if total_put_volume > 0 else 0

        return {
            "total_call_volume": total_call_volume,
            "total_put_volume": total_put_volume,
            "total_volume": total_volume,
            "volume_ratio": volume_ratio,
        }

    def analyze_moneyness(
        self,
        instruments: List[Dict[str, Any]],
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Analyze open interest by moneyness (ITM/OTM) with notional values.

        Matches Deribit's classification (no ATM category):
        - ITM Call: strike < current_price
        - OTM Call: strike >= current_price
        - ITM Put: strike > current_price
        - OTM Put: strike <= current_price

        Notional Value = OI × underlying_price

        Args:
            instruments: List of parsed instrument dicts.
            current_price: Current underlying price.

        Returns:
            Dict with OI and notional value breakdown by moneyness.
        """
        # Initialize counters for OI
        call_itm_oi = 0.0
        call_otm_oi = 0.0
        put_itm_oi = 0.0
        put_otm_oi = 0.0

        # Initialize counters for notional value
        call_itm_notional = 0.0
        call_otm_notional = 0.0
        put_itm_notional = 0.0
        put_otm_notional = 0.0

        for item in instruments:
            strike = item["strike"]
            option_type = item["option_type"]
            oi = item["open_interest"]
            # Notional value = OI × underlying price
            notional = oi * current_price

            if option_type == "C":
                if strike < current_price:  # ITM call
                    call_itm_oi += oi
                    call_itm_notional += notional
                else:  # OTM call (includes ATM)
                    call_otm_oi += oi
                    call_otm_notional += notional
            else:  # Put
                if strike > current_price:  # ITM put
                    put_itm_oi += oi
                    put_itm_notional += notional
                else:  # OTM put (includes ATM)
                    put_otm_oi += oi
                    put_otm_notional += notional

        # Calculate call totals
        total_call_oi = call_itm_oi + call_otm_oi
        total_call_notional = call_itm_notional + call_otm_notional

        # Calculate put totals
        total_put_oi = put_itm_oi + put_otm_oi
        total_put_notional = put_itm_notional + put_otm_notional

        # Calculate overall totals
        total_itm_oi = call_itm_oi + put_itm_oi
        total_otm_oi = call_otm_oi + put_otm_oi
        total_oi = total_itm_oi + total_otm_oi

        total_itm_notional = call_itm_notional + put_itm_notional
        total_otm_notional = call_otm_notional + put_otm_notional
        total_notional = total_itm_notional + total_otm_notional

        # Calculate percentages (based on notional value like Deribit)
        call_itm_pct = (call_itm_notional / total_call_notional * 100) if total_call_notional > 0 else 0
        call_otm_pct = (call_otm_notional / total_call_notional * 100) if total_call_notional > 0 else 0
        put_itm_pct = (put_itm_notional / total_put_notional * 100) if total_put_notional > 0 else 0
        put_otm_pct = (put_otm_notional / total_put_notional * 100) if total_put_notional > 0 else 0

        total_itm_pct = (total_itm_notional / total_notional * 100) if total_notional > 0 else 0
        total_otm_pct = (total_otm_notional / total_notional * 100) if total_notional > 0 else 0

        # Determine OI skew interpretation
        if total_otm_pct > 70:
            oi_skew = "Heavy OTM (Speculative)"
        elif total_itm_pct > 40:
            oi_skew = "Heavy ITM (Hedging)"
        else:
            oi_skew = "Balanced"

        return {
            "calls": {
                "itm_oi": call_itm_oi,
                "otm_oi": call_otm_oi,
                "total_oi": total_call_oi,
                "itm_notional": call_itm_notional,
                "otm_notional": call_otm_notional,
                "total_notional": total_call_notional,
                "itm_pct": call_itm_pct,
                "otm_pct": call_otm_pct,
            },
            "puts": {
                "itm_oi": put_itm_oi,
                "otm_oi": put_otm_oi,
                "total_oi": total_put_oi,
                "itm_notional": put_itm_notional,
                "otm_notional": put_otm_notional,
                "total_notional": total_put_notional,
                "itm_pct": put_itm_pct,
                "otm_pct": put_otm_pct,
            },
            "totals": {
                "itm_oi": total_itm_oi,
                "otm_oi": total_otm_oi,
                "total_oi": total_oi,
                "itm_notional": total_itm_notional,
                "otm_notional": total_otm_notional,
                "total_notional": total_notional,
                "itm_pct": total_itm_pct,
                "otm_pct": total_otm_pct,
            },
            "oi_skew": oi_skew,
        }

    def find_support_resistance(
        self,
        strike_data: Dict[float, Dict[str, float]],
        current_price: float,
        top_n: int = 3,
    ) -> Dict[str, Any]:
        """
        Find support and resistance levels based on open interest.

        - Resistance: Strikes with highest Call OI (price magnets above)
        - Support: Strikes with highest Put OI (price magnets below)
        - Short-term: Nearest high-OI strikes to current price

        Args:
            strike_data: Dict mapping strike -> {call_oi, put_oi}.
            current_price: Current underlying price.
            top_n: Number of top levels to return.

        Returns:
            Dict with resistance_levels, support_levels, and short_term_levels.
        """
        if not strike_data:
            return {
                "resistance_levels": [],
                "support_levels": [],
                "short_term_resistance": None,
                "short_term_support": None,
            }

        # Sort by Call OI for resistance (descending)
        call_oi_sorted = sorted(
            [(strike, data["call_oi"]) for strike, data in strike_data.items()],
            key=lambda x: x[1],
            reverse=True,
        )
        resistance_levels = [
            {"strike": strike, "call_oi": oi}
            for strike, oi in call_oi_sorted[:top_n]
            if oi > 0
        ]

        # Sort by Put OI for support (descending)
        put_oi_sorted = sorted(
            [(strike, data["put_oi"]) for strike, data in strike_data.items()],
            key=lambda x: x[1],
            reverse=True,
        )
        support_levels = [
            {"strike": strike, "put_oi": oi}
            for strike, oi in put_oi_sorted[:top_n]
            if oi > 0
        ]

        # Find nearest high-OI strikes to current price
        # Short-term resistance: nearest strike above current price with significant call OI
        strikes_above = [
            (strike, data["call_oi"])
            for strike, data in strike_data.items()
            if strike > current_price and data["call_oi"] > 0
        ]
        if strikes_above:
            # Sort by proximity to current price, then by OI
            strikes_above.sort(key=lambda x: (x[0] - current_price, -x[1]))
            short_term_resistance = {
                "strike": strikes_above[0][0],
                "call_oi": strikes_above[0][1],
            }
        else:
            short_term_resistance = None

        # Short-term support: nearest strike below current price with significant put OI
        strikes_below = [
            (strike, data["put_oi"])
            for strike, data in strike_data.items()
            if strike < current_price and data["put_oi"] > 0
        ]
        if strikes_below:
            # Sort by proximity to current price (descending), then by OI
            strikes_below.sort(key=lambda x: (current_price - x[0], -x[1]))
            short_term_support = {
                "strike": strikes_below[0][0],
                "put_oi": strikes_below[0][1],
            }
        else:
            short_term_support = None

        return {
            "resistance_levels": resistance_levels,
            "support_levels": support_levels,
            "short_term_resistance": short_term_resistance,
            "short_term_support": short_term_support,
        }

    def analyze_expiration(self, expiration: str) -> Dict[str, Any]:
        """
        Perform full analysis for a single expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            Dict with all analysis results for this expiration.
        """
        if expiration not in self.parsed_data:
            logger.warning(f"Expiration {expiration} not found in data")
            return {}

        instruments = self.parsed_data[expiration]
        strike_data = self.group_by_strike(instruments)

        # Count calls and puts
        call_count = sum(1 for i in instruments if i["option_type"] == "C")
        put_count = sum(1 for i in instruments if i["option_type"] == "P")

        # Calculate analytics
        max_pain = self.calculate_max_pain(strike_data)
        put_call_ratio = self.calculate_put_call_ratio(strike_data)
        volume_stats = self.calculate_volume_stats(strike_data)
        moneyness = self.analyze_moneyness(instruments, self.underlying_price)
        support_resistance = self.find_support_resistance(
            strike_data, self.underlying_price
        )

        return {
            "expiration": expiration,
            "underlying_price": self.underlying_price,
            "total_instruments": len(instruments),
            "call_count": call_count,
            "put_count": put_count,
            "strike_data": strike_data,
            "max_pain": max_pain,
            "put_call_ratio": put_call_ratio,
            "volume_stats": volume_stats,
            "moneyness": moneyness,
            "support_resistance": support_resistance,
        }

    def generate_report(self) -> str:
        """
        Generate a formatted text report for all expirations.

        Returns:
            Formatted text report string.
        """
        if not self.parsed_data:
            self.parse_instruments()

        lines = []
        separator = "=" * 80
        sub_separator = "-" * 80

        # Header
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(separator)
        lines.append("ON CHAIN ANALYSIS REPORT")
        lines.append(f"Generated: {timestamp}")
        lines.append(f"Currency: {self.currency}")
        lines.append(f"Current Underlying Price: ${self.underlying_price:,.2f}")
        lines.append(separator)
        lines.append("")

        # Market Metrics (DVOL, Funding Rate) - if available
        if self.market_metrics:
            lines.append("MARKET METRICS")
            lines.append(sub_separator)

            dvol = self.market_metrics.get("dvol")
            iv_percentile = self.market_metrics.get("iv_percentile")
            current_funding = self.market_metrics.get("current_funding")
            funding_8h = self.market_metrics.get("funding_8h")

            if dvol is not None:
                lines.append(f"DVOL (Volatility Index): {dvol:.2f}")
            if iv_percentile is not None:
                lines.append(f"IV Percentile (365d): {iv_percentile:.1f}%")
            if current_funding is not None:
                # Convert to percentage and annualized
                funding_pct = current_funding * 100
                funding_annualized = current_funding * 3 * 365 * 100  # 3 funding periods per day
                lines.append(
                    f"Current Funding Rate: {funding_pct:.4f}% "
                    f"({funding_annualized:.2f}% annualized)"
                )
            if funding_8h is not None:
                funding_8h_pct = funding_8h * 100
                lines.append(f"8h Funding Rate: {funding_8h_pct:.4f}%")

            lines.append("")
            lines.append(separator)
            lines.append("")

        # Sort expirations chronologically
        expirations = sorted(self.parsed_data.keys())

        for expiration in expirations:
            analysis = self.analyze_expiration(expiration)
            if not analysis:
                continue

            lines.append(f"EXPIRATION: {expiration}")
            lines.append(sub_separator)

            # Summary
            lines.append(
                f"Total Instruments: {analysis['total_instruments']} "
                f"({analysis['call_count']} Calls, {analysis['put_count']} Puts)"
            )
            lines.append("")

            # Max Pain
            max_pain = analysis["max_pain"]
            max_pain_strike = max_pain["max_pain_strike"]
            lines.append("MAX PAIN ANALYSIS")
            lines.append(sub_separator)
            if max_pain_strike is not None:
                lines.append(f"Max Pain Strike: ${max_pain_strike:,.0f}")
                diff = self.underlying_price - max_pain_strike
                diff_pct = (diff / max_pain_strike * 100) if max_pain_strike else 0
                lines.append(f"Distance from Current: ${diff:+,.2f} ({diff_pct:+.2f}%)")
            else:
                lines.append("Max Pain Strike: N/A")
            lines.append("")

            # Put/Call Ratio
            pcr = analysis["put_call_ratio"]
            lines.append("PUT/CALL RATIO (Open Interest)")
            lines.append(sub_separator)
            lines.append(f"Total Call OI: {pcr['total_call_oi']:,.0f}")
            lines.append(f"Total Put OI: {pcr['total_put_oi']:,.0f}")
            if pcr["ratio"] != float("inf"):
                lines.append(f"P/C Ratio: {pcr['ratio']:.2f} ({pcr['bias']})")
            else:
                lines.append(f"P/C Ratio: N/A (No Call OI)")
            lines.append("")

            # Volume Stats
            vol = analysis["volume_stats"]
            lines.append("VOLUME STATISTICS")
            lines.append(sub_separator)
            lines.append(f"Total Call Volume: {vol['total_call_volume']:,.2f}")
            lines.append(f"Total Put Volume: {vol['total_put_volume']:,.2f}")
            lines.append(f"Total Volume: {vol['total_volume']:,.2f}")
            if vol["volume_ratio"] != float("inf"):
                lines.append(f"Volume P/C Ratio: {vol['volume_ratio']:.2f}")
            else:
                lines.append("Volume P/C Ratio: N/A (No Call Volume)")
            lines.append("")

            # ITM/OTM Analysis (Deribit-style, no ATM)
            money = analysis["moneyness"]
            totals = money["totals"]
            calls = money["calls"]
            puts = money["puts"]

            lines.append("MONEYNESS ANALYSIS (ITM/OTM)")
            lines.append(sub_separator)
            lines.append(f"OI Skew: {money['oi_skew']}")
            lines.append("")

            # Calls breakdown
            lines.append("CALLS:")
            lines.append(
                f"  ITM: {calls['itm_oi']:>8,.0f} OI    "
                f"Notional: ${calls['itm_notional']:>14,.2f}    ({calls['itm_pct']:>5.2f}%)"
            )
            lines.append(
                f"  OTM: {calls['otm_oi']:>8,.0f} OI    "
                f"Notional: ${calls['otm_notional']:>14,.2f}    ({calls['otm_pct']:>5.2f}%)"
            )
            lines.append(
                f"  Total: {calls['total_oi']:>6,.0f} OI    "
                f"Notional: ${calls['total_notional']:>14,.2f}"
            )
            lines.append("")

            # Puts breakdown
            lines.append("PUTS:")
            lines.append(
                f"  ITM: {puts['itm_oi']:>8,.0f} OI    "
                f"Notional: ${puts['itm_notional']:>14,.2f}    ({puts['itm_pct']:>5.2f}%)"
            )
            lines.append(
                f"  OTM: {puts['otm_oi']:>8,.0f} OI    "
                f"Notional: ${puts['otm_notional']:>14,.2f}    ({puts['otm_pct']:>5.2f}%)"
            )
            lines.append(
                f"  Total: {puts['total_oi']:>6,.0f} OI    "
                f"Notional: ${puts['total_notional']:>14,.2f}"
            )
            lines.append("")

            # Combined totals
            lines.append("COMBINED TOTALS:")
            lines.append(
                f"  ITM: {totals['itm_oi']:>8,.0f} OI    "
                f"Notional: ${totals['itm_notional']:>14,.2f}    ({totals['itm_pct']:>5.2f}%)"
            )
            lines.append(
                f"  OTM: {totals['otm_oi']:>8,.0f} OI    "
                f"Notional: ${totals['otm_notional']:>14,.2f}    ({totals['otm_pct']:>5.2f}%)"
            )
            lines.append(
                f"  Total: {totals['total_oi']:>6,.0f} OI    "
                f"Notional: ${totals['total_notional']:>14,.2f}"
            )
            lines.append("")

            # Open Interest and Volume by Strike
            lines.append("OPEN INTEREST & VOLUME BY STRIKE")
            lines.append(sub_separator)
            lines.append(
                f"{'Strike':>10}  {'Call OI':>10}  {'Put OI':>10}  "
                f"{'Call Vol':>10}  {'Put Vol':>10}  Notes"
            )
            lines.append(
                f"{'------':>10}  {'--------':>10}  {'-------':>10}  "
                f"{'--------':>10}  {'-------':>10}  -----"
            )

            strike_data = analysis["strike_data"]
            sr = analysis["support_resistance"]

            # Get top OI strikes for annotations
            top_call_strikes = set(
                level["strike"] for level in sr["resistance_levels"]
            )
            top_put_strikes = set(level["strike"] for level in sr["support_levels"])

            for strike in sorted(strike_data.keys()):
                data = strike_data[strike]

                notes = []
                if strike == max_pain_strike:
                    notes.append("<< MAX PAIN")
                if strike in top_call_strikes:
                    notes.append("Resistance")
                if strike in top_put_strikes:
                    notes.append("Support")

                notes_str = " | ".join(notes) if notes else ""

                lines.append(
                    f"{strike:>10,.0f}  {data['call_oi']:>10,.0f}  "
                    f"{data['put_oi']:>10,.0f}  {data['call_volume']:>10,.2f}  "
                    f"{data['put_volume']:>10,.2f}  {notes_str}"
                )
            lines.append("")

            # Support/Resistance Levels
            lines.append("SUPPORT/RESISTANCE LEVELS")
            lines.append(sub_separator)

            lines.append("RESISTANCE (Top 3 Call OI):")
            for i, level in enumerate(sr["resistance_levels"], 1):
                lines.append(
                    f"  {i}. ${level['strike']:,.0f} - Call OI: {level['call_oi']:,.0f}"
                )
            if not sr["resistance_levels"]:
                lines.append("  None found")
            lines.append("")

            lines.append("SUPPORT (Top 3 Put OI):")
            for i, level in enumerate(sr["support_levels"], 1):
                lines.append(
                    f"  {i}. ${level['strike']:,.0f} - Put OI: {level['put_oi']:,.0f}"
                )
            if not sr["support_levels"]:
                lines.append("  None found")
            lines.append("")

            lines.append(
                f"SHORT-TERM LEVELS (nearest to current price ${self.underlying_price:,.2f}):"
            )
            if sr["short_term_resistance"]:
                lines.append(
                    f"  Nearest Resistance: ${sr['short_term_resistance']['strike']:,.0f} "
                    f"(Call OI: {sr['short_term_resistance']['call_oi']:,.0f})"
                )
            else:
                lines.append("  Nearest Resistance: None found above current price")

            if sr["short_term_support"]:
                lines.append(
                    f"  Nearest Support: ${sr['short_term_support']['strike']:,.0f} "
                    f"(Put OI: {sr['short_term_support']['put_oi']:,.0f})"
                )
            else:
                lines.append("  Nearest Support: None found below current price")

            lines.append("")

            # GEX/DEX section (if available for this expiration)
            if expiration in self.gex_dex_data:
                lines.append(self.gex_dex_data[expiration])

            # Buy/Sell Flow section (if available for this expiration)
            if expiration in self.buy_sell_flow_data:
                lines.append(self.buy_sell_flow_data[expiration])

            lines.append(separator)
            lines.append("")

        return "\n".join(lines)

    def get_expirations(self) -> List[str]:
        """
        Get list of available expirations.

        Returns:
            Sorted list of expiration date strings.
        """
        if not self.parsed_data:
            self.parse_instruments()
        return sorted(self.parsed_data.keys())

    def set_gex_dex_data(self, expiration: str, report_text: str) -> None:
        """
        Store GEX/DEX report text for an expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").
            report_text: Formatted GEX/DEX report section text.
        """
        self.gex_dex_data[expiration] = report_text

    def set_buy_sell_flow_data(self, expiration: str, report_text: str) -> None:
        """
        Store buy/sell flow report text for an expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").
            report_text: Formatted buy/sell flow report section text.
        """
        self.buy_sell_flow_data[expiration] = report_text

    def set_market_metrics(
        self,
        dvol: Optional[float] = None,
        iv_percentile: Optional[float] = None,
        current_funding: Optional[float] = None,
        funding_8h: Optional[float] = None,
    ) -> None:
        """
        Store market-wide metrics (DVOL, funding rate).

        These metrics are currency-wide, not from the book summary data.

        Args:
            dvol: Current DVOL (Deribit Volatility Index) value.
            iv_percentile: IV percentile based on past 365 days.
            current_funding: Current funding rate from perpetual.
            funding_8h: 8-hour funding rate from perpetual.
        """
        self.market_metrics = {
            "dvol": dvol,
            "iv_percentile": iv_percentile,
            "current_funding": current_funding,
            "funding_8h": funding_8h,
        }
