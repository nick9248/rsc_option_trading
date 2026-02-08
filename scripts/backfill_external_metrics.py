"""
Backfill External Metrics

Fetches historical Fear & Greed Index and BTC/ETH dominance data.
Saves to external_metrics table.
"""

import logging
from datetime import datetime

from coding.core.api.external_apis import FearGreedAPI, CoinGeckoAPI
from coding.core.database.repository import DatabaseRepository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def backfill_external_metrics(days: int = 90):
    """
    Backfill external metrics (Fear & Greed, BTC/ETH dominance).

    Args:
        days: Number of days to backfill (max 365 for Fear & Greed)
    """
    logger.info(f"Starting external metrics backfill")
    logger.info(f"Target: {days} days of historical data")

    fear_greed_api = FearGreedAPI()
    coingecko_api = CoinGeckoAPI()
    repo = DatabaseRepository()

    # Fetch historical Fear & Greed Index
    logger.info("Fetching historical Fear & Greed Index")
    try:
        fear_greed_history = fear_greed_api.get_historical(limit=days)

        if not fear_greed_history:
            logger.error("Failed to fetch Fear & Greed history")
            return 0

        logger.info(f"Fetched {len(fear_greed_history)} Fear & Greed data points")

    except Exception as e:
        logger.error(f"Failed to fetch Fear & Greed data: {e}")
        return 0

    # Fetch current BTC/ETH dominance (historical not available via free API)
    logger.info("Fetching BTC/ETH dominance data")
    try:
        global_data = coingecko_api.get_global_market_data()
        current_btc_dominance = global_data.get("btc_dominance")
        current_eth_dominance = global_data.get("eth_dominance")

        logger.info(f"Current BTC Dominance: {current_btc_dominance}%")
        logger.info(f"Current ETH Dominance: {current_eth_dominance}%")

    except Exception as e:
        logger.warning(f"Failed to fetch dominance data: {e}")
        current_btc_dominance = None
        current_eth_dominance = None

    # Save to database
    saved_count = 0
    failed_count = 0

    logger.info("Saving to database")

    for item in fear_greed_history:
        try:
            # Parse timestamp
            timestamp = int(item.get("timestamp", 0))
            date = datetime.fromtimestamp(timestamp)

            # Extract Fear & Greed values
            fear_greed_value = int(item.get("value", 0))
            fear_greed_classification = item.get("value_classification")

            # Use current dominance for all historical dates
            # (Free API doesn't provide historical dominance data)
            # This is a limitation, but better than nothing
            repo.save_external_metrics(
                date=date,
                fear_greed_value=fear_greed_value,
                fear_greed_classification=fear_greed_classification,
                btc_dominance=current_btc_dominance,
                eth_dominance=current_eth_dominance
            )

            saved_count += 1

            if saved_count % 10 == 0:
                logger.info(f"Saved {saved_count}/{len(fear_greed_history)} metrics")

        except Exception as e:
            logger.error(f"Failed to save metrics for {item}: {e}")
            failed_count += 1

    logger.info(f"Backfill complete")
    logger.info(f"  Saved: {saved_count} rows")
    logger.info(f"  Failed: {failed_count} rows")

    return saved_count


if __name__ == "__main__":
    print("=" * 60)
    print("External Metrics Backfill")
    print("=" * 60)
    print("\nNote: BTC/ETH dominance will use current values for all dates")
    print("(Free API doesn't provide historical dominance data)")

    print("\nBackfilling external metrics...")
    saved = backfill_external_metrics(days=90)

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"Total metrics saved: {saved}")
