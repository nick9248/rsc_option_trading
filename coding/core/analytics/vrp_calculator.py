"""
Volatility Risk Premium (VRP) calculator.

Computes VRP = IV - RV where:
- IV (Implied Volatility): Forward-looking volatility priced into options
- RV (Realized Volatility): Actual historical volatility of the underlying

VRP indicates whether options are expensive (high IV) or cheap (low IV) relative
to realized volatility. Positive VRP suggests options are expensive, negative VRP
suggests options are cheap.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class VRPCalculator:
    """
    Calculate Volatility Risk Premium from options and price data.

    VRP = IV - RV
    - Positive VRP: Options overpriced relative to realized vol (sell vol)
    - Negative VRP: Options underpriced relative to realized vol (buy vol)
    """

    def __init__(
        self,
        currency: str,
        lookback_days: int = 30
    ):
        """
        Initialize VRP calculator.

        Args:
            currency: Currency symbol (BTC or ETH).
            lookback_days: Days to look back for realized volatility calculation.
        """
        self.currency = currency
        self.lookback_days = lookback_days

    def calculate_realized_volatility(
        self,
        price_history: List[Dict[str, float]],
        window_days: Optional[int] = None
    ) -> float:
        """
        Calculate realized volatility from price history.

        Uses log returns and annualized standard deviation.

        Args:
            price_history: List of dicts with 'timestamp' and 'close' keys.
            window_days: Optional window in days (uses lookback_days if not specified).

        Returns:
            Annualized realized volatility as percentage (e.g., 0.80 for 80%).
        """
        if not price_history or len(price_history) < 2:
            logger.warning("Insufficient price history for RV calculation")
            return 0.0

        window_days = window_days or self.lookback_days

        # Filter to window
        cutoff_time = datetime.now() - timedelta(days=window_days)
        filtered_prices = [
            p for p in price_history
            if datetime.fromtimestamp(p.get("timestamp", 0)) >= cutoff_time
        ]

        if len(filtered_prices) < 2:
            logger.warning(f"Only {len(filtered_prices)} prices in window, need at least 2")
            return 0.0

        # Calculate log returns
        prices = [float(p["close"]) for p in filtered_prices]
        log_returns = [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
        ]

        if not log_returns:
            return 0.0

        # Calculate standard deviation
        std_dev = np.std(log_returns)

        # Annualize (assuming daily data)
        # RV = std_dev * sqrt(365)
        annualized_rv = std_dev * math.sqrt(365)

        return annualized_rv

    def calculate_average_iv(
        self,
        options_data: List[Dict[str, any]],
        moneyness_filter: Optional[Tuple[float, float]] = (0.9, 1.1)
    ) -> float:
        """
        Calculate average implied volatility from options data.

        Args:
            options_data: List of option dicts with 'mark_iv', 'strike', 'underlying_price'.
            moneyness_filter: Optional tuple (min, max) for moneyness filtering.
                            Moneyness = strike / spot. Default: (0.9, 1.1) for ±10% ATM.

        Returns:
            Average IV as decimal (e.g., 0.80 for 80%).
        """
        if not options_data:
            logger.warning("No options data provided for IV calculation")
            return 0.0

        # Filter by moneyness if specified
        if moneyness_filter:
            min_moneyness, max_moneyness = moneyness_filter
            filtered_options = []

            for opt in options_data:
                strike = opt.get("strike")
                spot = opt.get("underlying_price")
                iv = opt.get("mark_iv")

                if strike and spot and iv and spot > 0:
                    moneyness = strike / spot
                    if min_moneyness <= moneyness <= max_moneyness:
                        filtered_options.append(iv)
        else:
            filtered_options = [
                opt.get("mark_iv")
                for opt in options_data
                if opt.get("mark_iv") is not None
            ]

        if not filtered_options:
            logger.warning("No options passed moneyness filter")
            return 0.0

        # Calculate average IV
        avg_iv = np.mean(filtered_options)

        return avg_iv

    def calculate_vrp(
        self,
        implied_vol: float,
        realized_vol: float
    ) -> Dict[str, float]:
        """
        Calculate VRP metrics.

        Args:
            implied_vol: Implied volatility as decimal (e.g., 0.80 for 80%).
            realized_vol: Realized volatility as decimal.

        Returns:
            Dict with vrp_absolute, vrp_percentage, iv, rv.
        """
        vrp_absolute = implied_vol - realized_vol

        if realized_vol > 0:
            vrp_percentage = (vrp_absolute / realized_vol) * 100
        else:
            vrp_percentage = 0.0

        return {
            "vrp_absolute": vrp_absolute,
            "vrp_percentage": vrp_percentage,
            "implied_volatility": implied_vol,
            "realized_volatility": realized_vol,
            "signal": self._interpret_vrp(vrp_absolute, vrp_percentage)
        }

    def _interpret_vrp(self, vrp_abs: float, vrp_pct: float) -> str:
        """
        Interpret VRP signal.

        Args:
            vrp_abs: Absolute VRP (IV - RV).
            vrp_pct: VRP as percentage of RV.

        Returns:
            Signal string.
        """
        if vrp_pct > 50:
            return "VERY_EXPENSIVE"  # Options significantly overpriced
        elif vrp_pct > 20:
            return "EXPENSIVE"  # Options moderately overpriced
        elif vrp_pct > -10:
            return "NEUTRAL"  # Fair pricing
        elif vrp_pct > -30:
            return "CHEAP"  # Options moderately underpriced
        else:
            return "VERY_CHEAP"  # Options significantly underpriced

    def calculate_iv_percentile(
        self,
        current_iv: float,
        iv_history: List[float],
        lookback_days: int = 30
    ) -> float:
        """
        Calculate IV percentile (rank).

        Shows where current IV sits relative to historical range.

        Args:
            current_iv: Current implied volatility.
            iv_history: Historical IV values.
            lookback_days: Days to look back (not used if history provided).

        Returns:
            Percentile as 0-100 (e.g., 75 means current IV is higher than 75% of historical IVs).
        """
        if not iv_history:
            return 50.0  # Default to median

        # Count how many historical IVs are below current IV
        below_count = sum(1 for iv in iv_history if iv < current_iv)

        # Percentile = (count below / total count) * 100
        percentile = (below_count / len(iv_history)) * 100

        return percentile

    def generate_report_section(
        self,
        vrp_data: Dict[str, float],
        iv_percentile: Optional[float] = None
    ) -> str:
        """
        Generate formatted VRP report section.

        Args:
            vrp_data: VRP calculation results from calculate_vrp().
            iv_percentile: Optional IV percentile rank.

        Returns:
            Formatted string for inclusion in analysis report.
        """
        lines = []
        separator = "-" * 80

        lines.append("VOLATILITY RISK PREMIUM (VRP) ANALYSIS")
        lines.append(separator)

        # Core metrics
        iv = vrp_data["implied_volatility"] * 100  # Convert to percentage
        rv = vrp_data["realized_volatility"] * 100
        vrp_abs = vrp_data["vrp_absolute"] * 100
        vrp_pct = vrp_data["vrp_percentage"]
        signal = vrp_data["signal"]

        lines.append(f"Implied Volatility (IV): {iv:.2f}%")
        lines.append(f"Realized Volatility (RV): {rv:.2f}%")
        lines.append(f"VRP (IV - RV): {vrp_abs:+.2f}% ({vrp_pct:+.1f}%)")
        lines.append("")

        # Signal interpretation
        lines.append(f"Signal: {signal}")

        if signal == "VERY_EXPENSIVE":
            lines.append("  - Options are significantly overpriced relative to realized vol")
            lines.append("  - Consider selling volatility (spreads, iron condors)")
        elif signal == "EXPENSIVE":
            lines.append("  - Options are moderately overpriced")
            lines.append("  - Favor selling strategies over buying")
        elif signal == "NEUTRAL":
            lines.append("  - Options are fairly priced")
            lines.append("  - No strong bias toward buying or selling vol")
        elif signal == "CHEAP":
            lines.append("  - Options are moderately underpriced")
            lines.append("  - Favor buying strategies (long calls/puts, debit spreads)")
        elif signal == "VERY_CHEAP":
            lines.append("  - Options are significantly underpriced")
            lines.append("  - Strong buying opportunity for directional trades")

        lines.append("")

        # IV Percentile if provided
        if iv_percentile is not None:
            lines.append(f"IV Percentile (30-day): {iv_percentile:.1f}%")

            if iv_percentile >= 80:
                lines.append("  - IV is in the top 20% of recent range (very high)")
            elif iv_percentile >= 60:
                lines.append("  - IV is above average")
            elif iv_percentile >= 40:
                lines.append("  - IV is around average")
            elif iv_percentile >= 20:
                lines.append("  - IV is below average")
            else:
                lines.append("  - IV is in the bottom 20% of recent range (very low)")

        lines.append("")
        return "\n".join(lines)


class VRPHistoricalAnalyzer:
    """
    Analyze VRP over time to identify patterns and signals.
    """

    def __init__(self, repository):
        """
        Initialize historical VRP analyzer.

        Args:
            repository: Database repository for querying historical data.
        """
        self.repository = repository

    def calculate_vrp_timeseries(
        self,
        currency: str,
        expiration: str,
        lookback_days: int = 30
    ) -> List[Dict[str, any]]:
        """
        Calculate VRP timeseries for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            lookback_days: Days to look back.

        Returns:
            List of dicts with timestamp, iv, rv, vrp.
        """
        # This would query historical IV data from database
        # and calculate RV for each timestamp
        # Implementation depends on available historical data tables

        # Placeholder for now
        logger.warning("VRP timeseries calculation not yet implemented")
        return []

    def detect_vrp_regime_change(
        self,
        vrp_history: List[Dict[str, float]]
    ) -> Optional[str]:
        """
        Detect regime changes in VRP.

        Args:
            vrp_history: List of historical VRP calculations.

        Returns:
            Regime change signal or None.
        """
        if len(vrp_history) < 10:
            return None

        # Get recent VRP values
        recent_vrp = [v["vrp_absolute"] for v in vrp_history[-10:]]
        older_vrp = [v["vrp_absolute"] for v in vrp_history[-30:-10]]

        # Check for regime shift (mean reversion)
        recent_mean = np.mean(recent_vrp)
        older_mean = np.mean(older_vrp)

        if recent_mean > 0.15 and older_mean < 0:
            return "IV_SPIKE"  # Transition from cheap to expensive
        elif recent_mean < -0.10 and older_mean > 0:
            return "IV_CRUSH"  # Transition from expensive to cheap

        return None
