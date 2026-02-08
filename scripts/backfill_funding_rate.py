"""
Backfill Funding Rate

Fetches historical funding rate data from Deribit API.
Saves to funding_rate_history table.
"""

import logging
from datetime import datetime

from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def backfill_funding_rate(currency: str):
    """
    Backfill funding rate data for a currency.

    Note: Deribit API provides funding rate history via get_funding_chart_data
    with length parameter (8h, 24h, 1m). We'll use "1m" for ~1 month of data.

    Args:
        currency: Currency symbol (BTC, ETH)
    """
    logger.info(f"Starting funding rate backfill for {currency}")

    api = DeribitApiService()
    repo = DatabaseRepository()

    instrument_name = f"{currency}-PERPETUAL"

    try:
        # Fetch funding rate chart data (1 month)
        logger.info(f"Fetching funding rate data for {instrument_name}")

        funding_result = api.get_funding_chart_data(
            instrument_name=instrument_name,
            length="1m"  # 1 month of data
        )

        if not funding_result or "data" not in funding_result:
            logger.error("Failed to fetch funding rate data")
            return 0

        funding_data = funding_result["data"]
        logger.info(f"Fetched {len(funding_data)} funding rate data points")

        if not funding_data:
            logger.warning("No funding rate data returned")
            return 0

        # Save to database
        saved_count = 0
        failed_count = 0

        for item in funding_data:
            try:
                # Funding rate data format varies by API response
                # Typically: timestamp and interest_8h or funding rate value

                if isinstance(item, dict):
                    # If data is dict format
                    funding_timestamp = item.get("timestamp")
                    funding_rate = item.get("interest_8h") or item.get("funding_rate")

                    if funding_timestamp is None or funding_rate is None:
                        logger.warning(f"Incomplete funding data: {item}")
                        failed_count += 1
                        continue

                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    # If data is array format [timestamp, rate]
                    funding_timestamp = item[0]
                    funding_rate = item[1]
                else:
                    logger.warning(f"Unknown funding data format: {item}")
                    failed_count += 1
                    continue

                # Convert timestamp to datetime
                date = datetime.fromtimestamp(funding_timestamp / 1000)

                # Save to database
                # Note: Funding rate from API is typically in percentage form
                # Convert to decimal (divide by 100)
                repo.save_funding_rate(
                    currency=currency,
                    instrument_name=instrument_name,
                    timestamp=funding_timestamp,
                    date=date,
                    funding_rate=funding_rate / 100 if funding_rate > 1 else funding_rate
                )

                saved_count += 1

                if saved_count % 100 == 0:
                    logger.info(f"Saved {saved_count}/{len(funding_data)} funding rate entries")

            except Exception as e:
                logger.error(f"Failed to save funding rate for {item}: {e}")
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
    print("Funding Rate Backfill")
    print("=" * 60)
    print("\nNote: Fetching ~1 month of funding rate data per currency")

    total_saved = 0

    # Backfill BTC
    print("\n[1/2] Backfilling BTC funding rate...")
    btc_saved = backfill_funding_rate("BTC")
    total_saved += btc_saved

    # Backfill ETH
    print("\n[2/2] Backfilling ETH funding rate...")
    eth_saved = backfill_funding_rate("ETH")
    total_saved += eth_saved

    print("\n" + "=" * 60)
    print("BACKFILL COMPLETE")
    print("=" * 60)
    print(f"Total funding rate entries saved: {total_saved}")
    print(f"  BTC: {btc_saved}")
    print(f"  ETH: {eth_saved}")
