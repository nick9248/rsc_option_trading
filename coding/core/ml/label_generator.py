"""
Label Generator for ML Training Data.

Creates economically grounded labels from market data:
1. Realized Volatility (24h, 7d)
2. Trend Strength (ADX + price slope)
3. Drawdown State (from recent high)
4. IV Surface State (term structure)

These labels enable supervised learning for:
- Market regime detection
- Volatility forecasting
- Strategy signal generation
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from decimal import Decimal

import numpy as np
from pydantic import BaseModel, Field

from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class MarketLabels(BaseModel):
    """
    Validated market labels for ML training.

    All labels are economically meaningful and derived from market data.
    """

    # Timestamp
    timestamp: datetime = Field(..., description="Label timestamp (hour)")
    currency: str = Field(..., min_length=3, max_length=10, description="Currency")

    # Realized Volatility (annualized %)
    realized_vol_24h: Optional[float] = Field(None, ge=0, le=500, description="24h realized vol (%)")
    realized_vol_7d: Optional[float] = Field(None, ge=0, le=500, description="7d realized vol (%)")

    # Trend Strength (0-100)
    trend_strength: Optional[float] = Field(None, ge=0, le=100, description="Trend strength (0-100)")
    trend_direction: Optional[str] = Field(None, pattern="^(bullish|bearish|neutral)$", description="Trend direction")

    # Drawdown State
    drawdown_pct: Optional[float] = Field(None, ge=-100, le=0, description="Drawdown from recent high (%)")
    days_since_high: Optional[int] = Field(None, ge=0, description="Days since recent high")

    # IV Surface State
    iv_percentile: Optional[float] = Field(None, ge=0, le=100, description="IV percentile (30d)")
    term_structure: Optional[str] = Field(None, pattern="^(contango|backwardation|flat)$", description="IV term structure")

    # Market Regime (derived from above)
    market_regime: Optional[str] = Field(
        None,
        pattern="^(bullish|bearish|sideways|high_vol|low_vol)$",
        description="Overall market regime"
    )

    model_config = {"frozen": False, "validate_assignment": True}


class LabelGenerator:
    """
    Generate economically grounded labels for ML training.

    Uses actual market data to create meaningful labels:
    - Realized volatility from price movements
    - Trend strength from price action
    - Drawdown from recent highs
    - IV surface from options data
    """

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Initialize label generator.

        Args:
            repository: Database repository (creates new if None)
        """
        self.repo = repository or DatabaseRepository()

    def generate_labels(
        self,
        currency: str,
        timestamp: datetime,
        lookback_days: int = 30
    ) -> Optional[MarketLabels]:
        """
        Generate all labels for a given timestamp.

        Args:
            currency: Currency (BTC or ETH)
            timestamp: Timestamp to generate labels for
            lookback_days: Days of history to use for calculations

        Returns:
            MarketLabels or None if insufficient data
        """
        logger.info(f"Generating labels for {currency} at {timestamp}")

        try:
            # Get historical price data
            prices = self._get_price_history(currency, timestamp, lookback_days)

            if not prices or len(prices) < 3:
                logger.warning(f"Insufficient price data for {currency} at {timestamp} (need at least 3 hours)")
                return None

            # Calculate realized volatility
            # rv_24h is FORWARD-looking: vol from [timestamp, timestamp+24h]
            forward_prices = self._get_forward_prices(currency, timestamp, forward_hours=24)
            rv_24h = self._calculate_realized_vol(forward_prices, window_hours=24)
            rv_7d = self._calculate_realized_vol(prices, window_hours=168)  # 7*24

            # Calculate trend strength
            trend_strength, trend_direction = self._calculate_trend_strength(prices)

            # Calculate drawdown
            drawdown_pct, days_since_high = self._calculate_drawdown(prices)

            # Calculate IV surface metrics
            iv_percentile, term_structure = self._calculate_iv_metrics(
                currency, timestamp, lookback_days
            )

            # Derive overall market regime
            market_regime = self._derive_market_regime(
                rv_24h, trend_strength, trend_direction, iv_percentile
            )

            # Create validated labels
            labels = MarketLabels(
                timestamp=timestamp,
                currency=currency,
                realized_vol_24h=rv_24h,
                realized_vol_7d=rv_7d,
                trend_strength=trend_strength,
                trend_direction=trend_direction,
                drawdown_pct=drawdown_pct,
                days_since_high=days_since_high,
                iv_percentile=iv_percentile,
                term_structure=term_structure,
                market_regime=market_regime
            )

            logger.info(f"Labels generated: regime={market_regime}, vol={rv_24h:.1f}%, trend={trend_strength:.1f}")
            return labels

        except Exception as e:
            logger.error(f"Error generating labels: {e}")
            return None

    def _get_price_history(
        self,
        currency: str,
        timestamp: datetime,
        lookback_days: int
    ) -> List[Dict]:
        """
        Get historical price data from hourly snapshots.

        Args:
            currency: Currency
            timestamp: End timestamp
            lookback_days: Days to look back

        Returns:
            List of price records (timestamp, price)
        """
        connection = self.repo._get_connection()

        try:
            cursor = connection.cursor()

            start_time = timestamp - timedelta(days=lookback_days)

            # Get perpetual prices from historical_trades
            # Use index_price as underlying price
            cursor.execute(
                """
                SELECT
                    DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000.0)) as hour,
                    AVG(index_price) as avg_price
                FROM historical_trades
                WHERE currency = %s
                  AND TO_TIMESTAMP(trade_timestamp / 1000.0) >= %s
                  AND TO_TIMESTAMP(trade_timestamp / 1000.0) <= %s
                  AND index_price IS NOT NULL
                GROUP BY hour
                ORDER BY hour
                """,
                (currency, start_time, timestamp)
            )

            rows = cursor.fetchall()
            cursor.close()

            prices = [
                {"timestamp": row[0], "price": float(row[1])}
                for row in rows
            ]

            logger.debug(f"Retrieved {len(prices)} price points for {currency}")
            return prices

        finally:
            self.repo._return_connection(connection)

    def _get_forward_prices(
        self,
        currency: str,
        timestamp: datetime,
        forward_hours: int = 24
    ) -> list:
        """
        Fetch hourly average prices AFTER timestamp for forward vol calculation.

        Args:
            currency: Currency symbol.
            timestamp: Start of the forward window (inclusive).
            forward_hours: Number of hours to look forward.

        Returns:
            List of {'timestamp': datetime, 'price': float} sorted ascending.
        """
        connection = self.repo._get_connection()
        try:
            cursor = connection.cursor()
            end_time = timestamp + timedelta(hours=forward_hours)
            cursor.execute(
                """
                SELECT
                    DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000.0)) AS hour,
                    AVG(index_price) AS avg_price
                FROM historical_trades
                WHERE currency = %s
                  AND TO_TIMESTAMP(trade_timestamp / 1000.0) >= %s
                  AND TO_TIMESTAMP(trade_timestamp / 1000.0) <= %s
                  AND index_price IS NOT NULL
                GROUP BY hour
                ORDER BY hour
                """,
                (currency, timestamp, end_time)
            )
            rows = cursor.fetchall()
            cursor.close()
            return [{"timestamp": row[0], "price": float(row[1])} for row in rows]
        finally:
            self.repo._return_connection(connection)

    def _calculate_realized_vol(
        self,
        prices: List[Dict],
        window_hours: int
    ) -> Optional[float]:
        """
        Calculate realized volatility (annualized).

        Uses log returns and annualizes to yearly %.

        Args:
            prices: List of price records
            window_hours: Window size in hours

        Returns:
            Annualized realized volatility (%) or None
        """
        # Need at least 3 prices for meaningful calculation
        if len(prices) < 3:
            return None

        # Use available data if less than window_hours
        # This allows testing with sparse data
        window_hours_actual = min(window_hours, len(prices))

        # Get last N prices
        recent_prices = prices[-window_hours_actual:]
        price_array = np.array([p["price"] for p in recent_prices])

        # Calculate log returns
        log_returns = np.diff(np.log(price_array))

        # Calculate standard deviation
        vol = np.std(log_returns)

        # Annualize (assuming hourly data)
        # vol_annual = vol_hourly * sqrt(hours_per_year)
        hours_per_year = 24 * 365
        vol_annualized = vol * np.sqrt(hours_per_year)

        # Convert to percentage
        vol_pct = vol_annualized * 100

        return float(vol_pct)

    def _calculate_trend_strength(
        self,
        prices: List[Dict]
    ) -> tuple[Optional[float], Optional[str]]:
        """
        Calculate trend strength using linear regression slope.

        Combines slope magnitude and R-squared for trend strength.

        Args:
            prices: List of price records

        Returns:
            (trend_strength 0-100, direction "bullish"/"bearish"/"neutral")
        """
        # Need at least 3 prices for linear regression
        if len(prices) < 3:
            return None, None

        # Use all available data (prefer 7 days if available)
        recent_prices = prices[-168:] if len(prices) >= 168 else prices
        price_array = np.array([p["price"] for p in recent_prices])

        # Linear regression
        x = np.arange(len(price_array))
        coeffs = np.polyfit(x, price_array, 1)
        slope = coeffs[0]

        # Calculate R-squared (goodness of fit)
        y_pred = coeffs[0] * x + coeffs[1]
        ss_res = np.sum((price_array - y_pred) ** 2)
        ss_tot = np.sum((price_array - np.mean(price_array)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # Trend strength = R-squared * 100 (how much variance explained by trend)
        trend_strength = float(r_squared * 100)

        # Determine direction
        # Normalize slope by price (% change per hour)
        slope_pct = (slope / np.mean(price_array)) * 100

        if abs(slope_pct) < 0.01:  # Less than 0.01% per hour = neutral
            direction = "neutral"
        elif slope_pct > 0:
            direction = "bullish"
        else:
            direction = "bearish"

        return trend_strength, direction

    def _calculate_drawdown(
        self,
        prices: List[Dict]
    ) -> tuple[Optional[float], Optional[int]]:
        """
        Calculate drawdown from recent high.

        Args:
            prices: List of price records

        Returns:
            (drawdown_pct, days_since_high)
        """
        # Need at least 2 prices
        if len(prices) < 2:
            return None, None

        # Use all available data (prefer 30 days if available)
        recent_prices = prices[-720:] if len(prices) >= 720 else prices  # 30*24
        price_array = np.array([p["price"] for p in recent_prices])

        # Find recent high
        max_price = np.max(price_array)
        max_idx = np.argmax(price_array)

        # Current price
        current_price = price_array[-1]

        # Drawdown
        drawdown_pct = float(((current_price - max_price) / max_price) * 100)

        # Days since high
        hours_since_high = len(price_array) - max_idx - 1
        days_since_high = hours_since_high // 24

        return drawdown_pct, int(days_since_high)

    def _calculate_iv_metrics(
        self,
        currency: str,
        timestamp: datetime,
        lookback_days: int
    ) -> tuple[Optional[float], Optional[str]]:
        """
        Calculate IV surface metrics.

        Args:
            currency: Currency
            timestamp: Timestamp
            lookback_days: Lookback period

        Returns:
            (iv_percentile, term_structure)
        """
        connection = self.repo._get_connection()

        try:
            cursor = connection.cursor()

            # Get ATM IV for current hour
            cursor.execute(
                """
                SELECT AVG(mark_iv) as avg_iv
                FROM hourly_snapshots
                WHERE currency = %s
                  AND snapshot_hour = %s
                  AND mark_iv IS NOT NULL
                """,
                (currency, timestamp)
            )

            row = cursor.fetchone()
            current_iv = float(row[0]) if row and row[0] else None

            if not current_iv:
                cursor.close()
                return None, None

            # Get historical IV for percentile calculation
            start_time = timestamp - timedelta(days=lookback_days)

            cursor.execute(
                """
                SELECT mark_iv
                FROM hourly_snapshots
                WHERE currency = %s
                  AND snapshot_hour >= %s
                  AND snapshot_hour <= %s
                  AND mark_iv IS NOT NULL
                """,
                (currency, start_time, timestamp)
            )

            historical_ivs = [float(row[0]) for row in cursor.fetchall()]

            if len(historical_ivs) < 10:
                cursor.close()
                return None, None

            # Calculate IV percentile
            iv_percentile = float((np.sum(np.array(historical_ivs) <= current_iv) / len(historical_ivs)) * 100)

            # Analyze term structure (compare short-term vs long-term IV)
            # Get average IV for near-term (< 30 days) vs far-term (> 60 days)
            # TODO: Implement term structure analysis with expiration data
            # For now, return "flat" as placeholder
            term_structure = "flat"

            cursor.close()
            return iv_percentile, term_structure

        except Exception as e:
            logger.error(f"Error calculating IV metrics: {e}")
            return None, None

        finally:
            self.repo._return_connection(connection)

    def _derive_market_regime(
        self,
        realized_vol: Optional[float],
        trend_strength: Optional[float],
        trend_direction: Optional[str],
        iv_percentile: Optional[float]
    ) -> Optional[str]:
        """
        Derive overall market regime from component labels.

        Logic:
        - High vol if realized_vol > 80% or iv_percentile > 80
        - Low vol if realized_vol < 40% and iv_percentile < 40
        - Bullish if trend_direction=bullish and trend_strength > 50
        - Bearish if trend_direction=bearish and trend_strength > 50
        - Sideways otherwise

        Args:
            realized_vol: Realized volatility (%)
            trend_strength: Trend strength (0-100)
            trend_direction: Trend direction
            iv_percentile: IV percentile

        Returns:
            Market regime string
        """
        # Need at least vol and trend data
        if realized_vol is None or trend_strength is None or trend_direction is None:
            return None

        # Check volatility regime first
        if realized_vol > 80 or (iv_percentile and iv_percentile > 80):
            return "high_vol"

        if realized_vol < 40 and (iv_percentile is None or iv_percentile < 40):
            return "low_vol"

        # Check trend regime
        if trend_strength > 50:
            if trend_direction == "bullish":
                return "bullish"
            elif trend_direction == "bearish":
                return "bearish"

        # Default to sideways
        return "sideways"

    def generate_labels_batch(
        self,
        currency: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[MarketLabels]:
        """
        Generate labels for multiple timestamps (batch processing).

        Args:
            currency: Currency
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List of MarketLabels (one per hour)
        """
        logger.info(f"Generating labels for {currency} from {start_time} to {end_time}")

        # Normalize timestamps to the nearest hour (remove minutes, seconds, microseconds)
        current_time = start_time.replace(minute=0, second=0, microsecond=0)
        end_time_normalized = end_time.replace(minute=0, second=0, microsecond=0)

        labels_list = []

        while current_time <= end_time_normalized:
            labels = self.generate_labels(currency, current_time)

            if labels:
                labels_list.append(labels)

            # Move to next hour
            current_time += timedelta(hours=1)

        logger.info(f"Generated {len(labels_list)} label sets")
        return labels_list
