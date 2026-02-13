"""
VRP (Volatility Risk Premium) service.

Orchestrates data fetching and VRP calculation for options analysis.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from coding.core.analytics.vrp_calculator import VRPCalculator
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class VRPService:
    """
    Service for calculating and analyzing Volatility Risk Premium.

    Fetches price history, options data, and computes VRP metrics.
    """

    def __init__(self, api_service: DeribitApiService):
        """
        Initialize VRP service.

        Args:
            api_service: Deribit API service for data fetching.
        """
        self.api_service = api_service

    def calculate_vrp(
        self,
        currency: str,
        expiration: str,
        lookback_days: int = 30,
        moneyness_filter: Optional[tuple] = (0.9, 1.1)
    ) -> Dict[str, any]:
        """
        Calculate VRP for a currency and expiration.

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string.
            lookback_days: Days to look back for RV calculation.
            moneyness_filter: Tuple (min, max) for ATM filtering (default: ±10% ATM).

        Returns:
            Dict with VRP metrics, IV percentile, and constituent data.
        """
        logger.info(f"Calculating VRP for {currency} {expiration}")

        # Initialize calculator
        calculator = VRPCalculator(currency=currency, lookback_days=lookback_days)

        # Fetch price history
        price_history = self._fetch_price_history(currency, lookback_days)

        if not price_history:
            logger.error("Failed to fetch price history")
            return self._empty_result()

        # Calculate realized volatility
        realized_vol = calculator.calculate_realized_volatility(price_history)

        logger.info(f"Realized Volatility ({lookback_days}d): {realized_vol * 100:.2f}%")

        # Fetch options data for expiration
        options_data = self._fetch_options_data(currency, expiration)

        if not options_data:
            logger.error(f"Failed to fetch options data for {expiration}")
            return self._empty_result()

        # Calculate average IV
        implied_vol = calculator.calculate_average_iv(
            options_data,
            moneyness_filter=moneyness_filter
        )

        logger.info(f"Implied Volatility (ATM): {implied_vol * 100:.2f}%")

        # Calculate VRP
        vrp_result = calculator.calculate_vrp(implied_vol, realized_vol)

        logger.info(
            f"VRP: {vrp_result['vrp_absolute'] * 100:+.2f}% "
            f"({vrp_result['vrp_percentage']:+.1f}%) - Signal: {vrp_result['signal']}"
        )

        # Calculate IV percentile (using recent options data)
        iv_history = self._fetch_iv_history(currency, expiration, lookback_days)
        iv_percentile = calculator.calculate_iv_percentile(implied_vol, iv_history)

        return {
            **vrp_result,
            "iv_percentile": iv_percentile,
            "currency": currency,
            "expiration": expiration,
            "lookback_days": lookback_days,
            "options_count": len(options_data),
            "price_history_count": len(price_history),
        }

    def _fetch_price_history(
        self,
        currency: str,
        lookback_days: int
    ) -> List[Dict[str, float]]:
        """
        Fetch price history for the underlying.

        Args:
            currency: Currency symbol.
            lookback_days: Days to look back.

        Returns:
            List of price dicts with 'timestamp' and 'close' keys.
        """
        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(days=lookback_days)

        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        try:
            # Fetch OHLCV data from TradingView endpoint
            instrument_name = f"{currency}-PERPETUAL"

            result = self.api_service.get_tradingview_chart_data(
                instrument_name=instrument_name,
                resolution="1D",  # Daily candles
                start_timestamp=start_ts,
                end_timestamp=end_ts
            )

            if result.get("status") != "ok":
                logger.error(f"TradingView API returned status: {result.get('status')}")
                return []

            # TradingView returns separate arrays for each field
            ticks = result.get("ticks", [])  # Timestamps
            closes = result.get("close", [])  # Close prices

            if not ticks or not closes:
                logger.warning("No price data returned")
                return []

            if len(ticks) != len(closes):
                logger.warning(f"Ticks and closes length mismatch: {len(ticks)} vs {len(closes)}")
                return []

            # Convert to price history format
            price_history = []
            for i in range(len(ticks)):
                price_history.append({
                    "timestamp": ticks[i] / 1000,  # Convert ms to seconds
                    "close": closes[i]
                })

            logger.info(f"Fetched {len(price_history)} daily candles for {currency}")

            return price_history

        except Exception as e:
            logger.error(f"Error fetching price history: {e}")
            return []

    def _fetch_options_data(
        self,
        currency: str,
        expiration: str
    ) -> List[Dict[str, any]]:
        """
        Fetch options data for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.

        Returns:
            List of option dicts with mark_iv, strike, underlying_price.
        """
        try:
            # Fetch book summary for currency (all expirations)
            book_summary = self.api_service.get_book_summary(
                currency=currency,
                kind="option"
            )

            if not book_summary:
                logger.warning(f"No book summary data for {currency}")
                return []

            # Filter to specific expiration
            expiration_options = [
                item for item in book_summary
                if expiration in item.get("instrument_name", "")
            ]

            if not expiration_options:
                logger.warning(f"No options found for expiration {expiration}")
                return []

            # Extract relevant fields
            options_data = []
            for item in expiration_options:
                instrument_name = item.get("instrument_name", "")
                parts = instrument_name.split("-")

                if len(parts) < 4:
                    continue

                try:
                    strike = float(parts[2])
                except ValueError:
                    continue

                # Extract IV and underlying price
                mark_iv = item.get("mark_iv")
                underlying_price = item.get("underlying_price")

                if mark_iv is not None and underlying_price is not None:
                    options_data.append({
                        "instrument_name": instrument_name,
                        "strike": strike,
                        "option_type": parts[3],
                        "mark_iv": mark_iv / 100.0,  # Convert from percentage to decimal
                        "underlying_price": underlying_price,
                        "open_interest": item.get("open_interest", 0),
                        "volume": item.get("volume", 0)
                    })

            logger.info(f"Found {len(options_data)} options for {expiration}")

            return options_data

        except Exception as e:
            logger.error(f"Error fetching options data: {e}")
            return []

    def _fetch_iv_history(
        self,
        currency: str,
        expiration: str,
        lookback_days: int
    ) -> List[float]:
        """
        Fetch historical IV values for percentile calculation.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            lookback_days: Days to look back.

        Returns:
            List of historical IV values.
        """
        # This would query historical snapshots from database
        # For now, use current options as proxy

        options_data = self._fetch_options_data(currency, expiration)

        if not options_data:
            return []

        # Extract all IVs
        iv_values = [opt["mark_iv"] for opt in options_data if opt.get("mark_iv")]

        return iv_values

    def _empty_result(self) -> Dict[str, any]:
        """Return empty VRP result."""
        return {
            "vrp_absolute": 0.0,
            "vrp_percentage": 0.0,
            "implied_volatility": 0.0,
            "realized_volatility": 0.0,
            "signal": "NO_DATA",
            "iv_percentile": 0.0,
            "currency": "",
            "expiration": "",
            "lookback_days": 0,
            "options_count": 0,
            "price_history_count": 0,
        }

    def generate_report(
        self,
        currency: str,
        expiration: str,
        lookback_days: int = 30
    ) -> str:
        """
        Generate full VRP report.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            lookback_days: Days to look back.

        Returns:
            Formatted VRP report string.
        """
        # Calculate VRP
        vrp_data = self.calculate_vrp(currency, expiration, lookback_days)

        # Use calculator to generate report section
        calculator = VRPCalculator(currency=currency, lookback_days=lookback_days)

        report = calculator.generate_report_section(
            vrp_data=vrp_data,
            iv_percentile=vrp_data.get("iv_percentile")
        )

        # Add data quality info
        report += f"\nData Quality:\n"
        report += f"  Options analyzed: {vrp_data['options_count']}\n"
        report += f"  Price history days: {vrp_data['price_history_count']}\n"

        return report
