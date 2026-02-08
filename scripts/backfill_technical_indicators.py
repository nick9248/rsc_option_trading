"""
Backfill Technical Indicators

Fetches historical OHLCV data and calculates technical indicators for the past 90 days.
Saves to technical_indicators table.
"""

import logging
from datetime import datetime, timedelta

from coding.core.analytics.technical_indicator_calculator import TechnicalIndicatorCalculator
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def backfill_technical_indicators(currency: str, days: int = 90):
    """
    Backfill technical indicators for a currency.

    Args:
        currency: Currency symbol (BTC, ETH)
        days: Number of days to backfill (default 90, max ~200)
    """
    logger.info(f"Starting technical indicators backfill for {currency}")
    logger.info(f"Target: {days} days of historical data")

    api = DeribitApiService()
    repo = DatabaseRepository()
    calculator = TechnicalIndicatorCalculator()

    # Fetch historical OHLCV data
    logger.info(f"Fetching historical OHLCV data for {currency}")
    instrument_name = f"{currency}-PERPETUAL"

    try:
        ohlcv_result = api.get_tradingview_chart_data(
            instrument_name=instrument_name,
            resolution="1D",  # Daily candles
            start_timestamp=None,  # Will fetch last ~200 days
            end_timestamp=None
        )

        if not ohlcv_result or "ticks" not in ohlcv_result:
            logger.error("Failed to fetch OHLCV data")
            return 0

        # Transform columnar format to row format
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

        # Calculate technical indicators
        logger.info("Calculating technical indicators")
        indicators_df = calculator.calculate_all_indicators(ohlcv_data)

        if indicators_df.empty:
            logger.error("Failed to calculate technical indicators")
            return 0

        # Filter to last N days
        cutoff_date = datetime.now() - timedelta(days=days)
        indicators_df = indicators_df[
            indicators_df.index >= cutoff_date
        ]

        logger.info(f"Calculated indicators for {len(indicators_df)} days")

        # Save to database
        saved_count = 0
        failed_count = 0

        for idx, row in indicators_df.iterrows():
            try:
                # Convert numpy types to Python native types
                def to_python(val):
                    """Convert numpy types to Python native types."""
                    if val is None or (hasattr(val, '__iter__') and len(val) == 0):
                        return None
                    try:
                        # Handle pandas/numpy types
                        import pandas as pd
                        import numpy as np
                        if pd.isna(val):
                            return None
                        if isinstance(val, (np.integer, np.floating)):
                            return float(val)
                        return float(val) if val is not None else None
                    except:
                        return None

                indicators = {
                    "sma_50": to_python(row.get("sma_50")),
                    "sma_200": to_python(row.get("sma_200")),
                    "ema_50": to_python(row.get("ema_50")),
                    "ema_200": to_python(row.get("ema_200")),
                    "adx": to_python(row.get("adx")),
                    "plus_di": to_python(row.get("plus_di")),
                    "minus_di": to_python(row.get("minus_di")),
                    "atr": to_python(row.get("atr")),
                    "atr_percentile": to_python(row.get("atr_percentile")),
                    "rsi": to_python(row.get("rsi")),
                    "macd": to_python(row.get("macd")),
                    "macd_signal": to_python(row.get("macd_signal")),
                    "macd_histogram": to_python(row.get("macd_histogram")),
                }

                repo.save_technical_indicators(
                    currency=currency,
                    date=idx,
                    indicators=indicators
                )
                saved_count += 1

                if saved_count % 10 == 0:
                    logger.info(f"Saved {saved_count}/{len(indicators_df)} indicators")

            except Exception as e:
                logger.error(f"Failed to save indicators for {idx}: {e}")
                failed_count += 1

        logger.info(f"Backfill complete for {currency}")
        logger.info(f"  Saved: {saved_count} rows")
        logger.info(f"  Failed: {failed_count} rows")

        return saved_count

    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        return 0


if __name__ == "__main__":
    print("=" * 60)
    print("Technical Indicators Backfill")
    print("=" * 60)

    total_saved = 0

    # Backfill BTC
    print("\n[1/2] Backfilling BTC technical indicators...")
    btc_saved = backfill_technical_indicators("BTC", days=90)
    total_saved += btc_saved

    # Backfill ETH
    print("\n[2/2] Backfilling ETH technical indicators...")
    eth_saved = backfill_technical_indicators("ETH", days=90)
    total_saved += eth_saved

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"Total indicators saved: {total_saved}")
    print(f"  BTC: {btc_saved}")
    print(f"  ETH: {eth_saved}")
