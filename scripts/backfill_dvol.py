"""
Backfill DVOL (Deribit Volatility Index)

Fetches historical DVOL data from Deribit API.
Saves to volatility_index_history table.
"""

import logging
from datetime import datetime, timedelta

from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def backfill_dvol(currency: str, days: int = 90):
    """
    Backfill DVOL data for a currency.

    Args:
        currency: Currency symbol (BTC, ETH)
        days: Number of days to backfill
    """
    logger.info(f"Starting DVOL backfill for {currency}")
    logger.info(f"Target: {days} days of historical data")

    api = DeribitApiService()
    repo = DatabaseRepository()

    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    start_timestamp = int(start_time.timestamp() * 1000)
    end_timestamp = int(end_time.timestamp() * 1000)

    logger.info(f"Fetching DVOL from {start_time} to {end_time}")

    try:
        # Fetch DVOL data
        dvol_result = api.get_volatility_index_data(
            currency=currency,
            resolution=3600,  # 1 hour resolution
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp
        )

        if not dvol_result or "data" not in dvol_result:
            logger.error("Failed to fetch DVOL data")
            return 0

        dvol_data = dvol_result["data"]
        logger.info(f"Fetched {len(dvol_data)} DVOL data points")

        if not dvol_data:
            logger.warning("No DVOL data returned")
            return 0

        # Save to database
        saved_count = 0
        failed_count = 0

        index_name = f"{currency}DVOL"

        for item in dvol_data:
            try:
                # DVOL data format: [timestamp, open, high, low, close]
                if len(item) < 5:
                    logger.warning(f"Incomplete DVOL data: {item}")
                    failed_count += 1
                    continue

                dvol_timestamp = item[0]
                dvol_value = item[4]  # Close price

                # Convert timestamp to datetime
                date = datetime.fromtimestamp(dvol_timestamp / 1000)

                # Save to database
                repo.save_dvol(
                    currency=currency,
                    index_name=index_name,
                    timestamp=dvol_timestamp,
                    date=date,
                    dvol=dvol_value
                )

                saved_count += 1

                if saved_count % 100 == 0:
                    logger.info(f"Saved {saved_count}/{len(dvol_data)} DVOL entries")

            except Exception as e:
                logger.error(f"Failed to save DVOL for {item}: {e}")
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
    print("DVOL Backfill")
    print("=" * 60)

    total_saved = 0

    # Backfill BTC
    print("\n[1/2] Backfilling BTC DVOL...")
    btc_saved = backfill_dvol("BTC", days=90)
    total_saved += btc_saved

    # Backfill ETH
    print("\n[2/2] Backfilling ETH DVOL...")
    eth_saved = backfill_dvol("ETH", days=90)
    total_saved += eth_saved

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"Total DVOL entries saved: {total_saved}")
    print(f"  BTC: {btc_saved}")
    print(f"  ETH: {eth_saved}")
