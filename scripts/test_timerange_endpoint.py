"""
Test script for the newly integrated time-range trade endpoint.

Validates that the endpoint integration works correctly and returns expected data.
"""

import logging
from datetime import datetime, timedelta
from coding.service.deribit.deribit_api_service import DeribitApiService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_time_range_endpoint():
    """
    Test the integrated get_last_trades_by_currency_and_time endpoint.
    """
    logger.info("="*70)
    logger.info("TESTING TIME-RANGE TRADE ENDPOINT INTEGRATION")
    logger.info("="*70)

    # Test with last 10 minutes (should have recent trades)
    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=10)

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)

    logger.info(f"Time range: {start_time} to {end_time}")
    logger.info(f"Timestamps: {start_ts} to {end_ts}")

    try:
        with DeribitApiService() as api:
            logger.info("Fetching BTC option trades...")

            result = api.get_last_trades_by_currency_and_time(
                currency="BTC",
                kind="option",
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                count=100  # Small batch for testing
            )

            # Check response structure
            if "trades" in result:
                trades = result["trades"]
                has_more = result.get("has_more", False)

                logger.info(f"\n✅ SUCCESS: Endpoint working")
                logger.info(f"Trades returned: {len(trades)}")
                logger.info(f"Has more data: {has_more}")

                if trades:
                    # Analyze first trade
                    first_trade = trades[0]
                    logger.info(f"\nFirst trade structure:")
                    logger.info(f"  Instrument: {first_trade.get('instrument_name')}")
                    logger.info(f"  Price: {first_trade.get('price')}")
                    logger.info(f"  Amount: {first_trade.get('amount')}")
                    logger.info(f"  Direction: {first_trade.get('direction')}")
                    logger.info(f"  IV: {first_trade.get('iv')}")
                    logger.info(f"  Timestamp: {datetime.fromtimestamp(first_trade.get('timestamp', 0)/1000)}")

                    # Validate critical fields
                    critical_fields = ["direction", "iv", "trade_id", "price", "amount"]
                    missing = [f for f in critical_fields if first_trade.get(f) is None]

                    if not missing:
                        logger.info(f"\n✅ All critical fields present")
                    else:
                        logger.warning(f"\n⚠️  Missing fields: {missing}")

                    # Test pagination if has_more is True
                    if has_more:
                        logger.info(f"\n📄 Pagination available - can fetch more batches")
                        logger.info(f"   Use last trade timestamp as new start_timestamp")
                else:
                    logger.warning("No trades returned in this time window")
                    logger.info("Try a different time range or check if market was active")

            else:
                logger.error("Unexpected response structure")
                logger.error(f"Response keys: {result.keys()}")

    except ValueError as e:
        logger.error(f"❌ Validation error: {e}")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def test_error_handling():
    """
    Test that error handling works correctly.
    """
    logger.info("\n" + "="*70)
    logger.info("TESTING ERROR HANDLING")
    logger.info("="*70)

    try:
        with DeribitApiService() as api:
            # Test missing required parameters
            logger.info("Testing missing timestamps...")
            try:
                api.get_last_trades_by_currency_and_time(
                    currency="BTC",
                    start_timestamp=None,
                    end_timestamp=None
                )
                logger.error("❌ Should have raised ValueError")
            except ValueError as e:
                logger.info(f"✅ Correctly raised ValueError: {e}")

    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    # Run tests
    test_time_range_endpoint()
    test_error_handling()

    logger.info("\n" + "="*70)
    logger.info("ENDPOINT INTEGRATION TEST COMPLETE")
    logger.info("="*70)
    logger.info("✅ If successful, proceed to Task #2: Continuous collector")
