"""
Market regime detection service.

Orchestrates data fetching, indicator calculation, and regime detection.
"""

import logging
import time
from datetime import datetime
from typing import Dict, Optional

from coding.core.analytics.market_regime_detector import MarketRegimeDetector
from coding.core.analytics.technical_indicator_calculator import TechnicalIndicatorCalculator
from coding.core.api.external_apis import ExternalMetricsFetcher
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class RegimeDetectionService:
    """
    Service for detecting market regime using multiple data sources.

    Coordinates:
    1. Historical OHLCV data fetching
    2. Technical indicator calculation
    3. On-chain metrics extraction
    4. External sentiment metrics
    5. Regime detection algorithm
    6. Database persistence
    """

    def __init__(
        self,
        api_service,
        repository: DatabaseRepository
    ):
        """
        Initialize regime detection service.

        Args:
            api_service: Deribit API service instance.
            repository: Database repository instance.
        """
        self.api_service = api_service
        self.repository = repository
        self.indicator_calculator = TechnicalIndicatorCalculator()
        self.regime_detector = MarketRegimeDetector()
        self.external_fetcher = ExternalMetricsFetcher()

        logger.info("RegimeDetectionService initialized")

    def detect_regime(self, currency: str) -> Dict:
        """
        Detect current market regime for a currency.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            Dictionary with regime analysis and all component data.
        """
        start_time = time.time()
        logger.info(f"Starting regime detection for {currency}")

        try:
            # Step 1: Fetch historical OHLCV data
            logger.info("Fetching historical OHLCV data")
            instrument_name = f"{currency}-PERPETUAL"
            ohlcv_result = self.api_service.get_tradingview_chart_data(
                instrument_name=instrument_name,
                resolution="1D",
                start_timestamp=None,  # Will fetch 200 days by default
                end_timestamp=None
            )

            if not ohlcv_result or "ticks" not in ohlcv_result:
                logger.error("Failed to fetch OHLCV data")
                return {"error": "Failed to fetch OHLCV data"}

            # Transform columnar format to row format
            # API returns: {ticks: [...], open: [...], high: [...], low: [...], close: [...], volume: [...]}
            # Need: [[timestamp, open, high, low, close, volume], ...]
            timestamps = ohlcv_result["ticks"]
            opens = ohlcv_result.get("open", [])
            highs = ohlcv_result.get("high", [])
            lows = ohlcv_result.get("low", [])
            closes = ohlcv_result.get("close", [])
            volumes = ohlcv_result.get("volume", [])

            ohlcv_data = []
            for i in range(len(timestamps)):
                ohlcv_data.append([
                    timestamps[i],
                    opens[i] if i < len(opens) else 0,
                    highs[i] if i < len(highs) else 0,
                    lows[i] if i < len(lows) else 0,
                    closes[i] if i < len(closes) else 0,
                    volumes[i] if i < len(volumes) else 0,
                ])

            logger.info(f"Fetched {len(ohlcv_data)} OHLCV data points")

            # Step 2: Calculate technical indicators
            logger.info("Calculating technical indicators")
            indicators_df = self.indicator_calculator.calculate_all_indicators(ohlcv_data)

            if indicators_df.empty:
                logger.error("Failed to calculate technical indicators")
                return {"error": "Failed to calculate indicators"}

            latest_indicators = self.indicator_calculator.get_latest_indicators(indicators_df)
            current_price = latest_indicators.get("close")

            if not current_price:
                logger.error("No current price in indicators")
                return {"error": "No current price available"}

            logger.info(f"Current price: ${current_price:,.2f}")

            # Step 3: Get on-chain metrics
            logger.info("Fetching on-chain metrics")
            onchain_metrics = self._get_onchain_metrics(currency)

            # Step 4: Get external sentiment metrics
            logger.info("Fetching external sentiment metrics")
            external_metrics = self.external_fetcher.fetch_all_metrics()

            # Step 5: Detect regime
            logger.info("Running regime detection algorithm")
            regime_result = self.regime_detector.detect_regime(
                technical_indicators=latest_indicators,
                onchain_metrics=onchain_metrics,
                external_metrics=external_metrics,
                current_price=current_price
            )

            # Step 6: Build comprehensive result
            result = {
                "currency": currency,
                "detected_at": datetime.now(),
                "current_price": current_price,
                "regime": regime_result["regime"],
                "confidence": regime_result["confidence"],
                "composite_score": regime_result["composite_score"],
                "component_scores": {
                    "trend": regime_result["trend_score"],
                    "volatility": regime_result["volatility_score"],
                    "momentum": regime_result["momentum_score"],
                    "onchain": regime_result["onchain_score"],
                    "sentiment": regime_result["sentiment_score"],
                },
                "technical_indicators": latest_indicators,
                "onchain_metrics": onchain_metrics,
                "external_metrics": external_metrics,
                "reasoning": regime_result["reasoning"],
                "detection_time_seconds": time.time() - start_time,
            }

            # Step 7: Save to database (if repository method exists)
            try:
                self._save_regime_detection(result)
            except Exception as e:
                logger.warning(f"Failed to save regime detection to database: {e}")

            logger.info(
                f"Regime detection complete: {result['regime']} "
                f"(confidence={result['confidence']:.1f}%, time={result['detection_time_seconds']:.2f}s)"
            )

            return result

        except Exception as e:
            logger.error(f"Regime detection failed: {e}", exc_info=True)
            return {"error": str(e)}

    def _get_onchain_metrics(self, currency: str) -> Dict:
        """
        Get on-chain metrics for regime detection.

        Args:
            currency: Currency symbol.

        Returns:
            Dictionary with funding rate, put/call ratio, etc.
        """
        metrics = {}

        try:
            # Get funding rate from perpetual contract
            instrument_name = f"{currency}-PERPETUAL"
            ticker = self.api_service.get_ticker(instrument_name)

            if ticker:
                # Funding rate (8h)
                funding_8h = ticker.get("funding_8h")
                if funding_8h is not None:
                    # Convert from percentage to decimal
                    metrics["funding_rate"] = funding_8h / 100
                    logger.info(f"Funding rate: {funding_8h:.4f}%")

                # Current funding rate
                current_funding = ticker.get("current_funding")
                if current_funding is not None:
                    metrics["current_funding"] = current_funding

                # Interest rate
                interest_rate = ticker.get("interest_rate")
                if interest_rate is not None:
                    metrics["interest_rate"] = interest_rate

                # Index price (underlying)
                index_price = ticker.get("index_price")
                if index_price is not None:
                    metrics["index_price"] = index_price

            # Get DVOL if available
            try:
                dvol_result = self.api_service.get_volatility_index_data(
                    currency=currency,
                    resolution=3600,  # 1 hour
                    start_timestamp=None,
                    end_timestamp=None
                )
                if dvol_result and "data" in dvol_result and dvol_result["data"]:
                    # Get latest DVOL value (last close)
                    latest_dvol = dvol_result["data"][-1]
                    if len(latest_dvol) >= 5:
                        dvol_value = latest_dvol[4]  # Close price
                        metrics["dvol"] = dvol_value
                        logger.info(f"DVOL: {dvol_value:.2f}")
            except Exception as e:
                logger.warning(f"Failed to fetch DVOL: {e}")

            # Get put/call ratio from current book summary
            try:
                book_summary = self.api_service.get_book_summary(
                    currency=currency,
                    kind="option"
                )

                if book_summary:
                    from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer

                    analyzer = OnChainAnalyzer(book_summary, currency)
                    analyzer.parse_instruments()

                    # Get all active strikes (combine all expirations for overall market sentiment)
                    all_strikes = {}
                    for exp_data in analyzer.parsed_data.values():
                        for instrument in exp_data:
                            strike = instrument.get("strike")
                            option_type = instrument.get("option_type")
                            oi = instrument.get("open_interest", 0)

                            if strike and option_type and oi > 0:
                                if strike not in all_strikes:
                                    all_strikes[strike] = {"call_oi": 0, "put_oi": 0}

                                if option_type == "C":
                                    all_strikes[strike]["call_oi"] += oi
                                elif option_type == "P":
                                    all_strikes[strike]["put_oi"] += oi

                    # Calculate total P/C ratio
                    total_call_oi = sum(data["call_oi"] for data in all_strikes.values())
                    total_put_oi = sum(data["put_oi"] for data in all_strikes.values())

                    if total_call_oi > 0:
                        put_call_ratio = total_put_oi / total_call_oi
                        metrics["put_call_ratio"] = put_call_ratio
                        logger.info(f"Put/Call Ratio: {put_call_ratio:.2f}")

            except Exception as e:
                logger.warning(f"Failed to calculate Put/Call ratio: {e}")

        except Exception as e:
            logger.error(f"Failed to fetch on-chain metrics: {e}", exc_info=True)

        return metrics

    def _save_regime_detection(self, result: Dict) -> None:
        """
        Save regime detection result to database.

        Args:
            result: Regime detection result dictionary.
        """
        # This would require adding a method to DatabaseRepository
        # For now, just log
        logger.debug(f"Regime detection result: {result['regime']}")

    def get_regime_history(
        self,
        currency: str,
        days: int = 30
    ) -> list:
        """
        Get historical regime detections.

        Args:
            currency: Currency symbol.
            days: Number of days of history to retrieve.

        Returns:
            List of regime detection results.
        """
        # This would require adding a repository method
        # For now, return empty list
        logger.info(f"Fetching regime history for {currency} (last {days} days)")
        return []
