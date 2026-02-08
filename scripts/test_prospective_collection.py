"""
Test prospective data collection manually.

Nobel-level testing: Start small, validate, then automate.

Steps:
1. Check database connection
2. Apply migration (create tables)
3. Run ONE collection (current hour)
4. Validate data quality
5. If successful, set up automation
"""

import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from coding.core.logging.logging_setup import init_logging
from coding.service.data_collection.prospective_collector import ProspectiveCollector
from coding.service.deribit.deribit_api_service import DeribitApiService


logger = logging.getLogger(__name__)


def test_collection():
    """
    Test collection pipeline with ONE hour of data.
    """
    logger.info(f"{'='*60}")
    logger.info(f"PROSPECTIVE COLLECTION TEST")
    logger.info(f"{'='*60}\n")

    # Step 1: Test API connectivity
    logger.info("Step 1: Testing API connectivity...")
    try:
        api = DeribitApiService()
        connectivity = api.check_connectivity()
        logger.info(f"✅ API connected: {connectivity}")
    except Exception as e:
        logger.error(f"❌ API connection failed: {e}")
        return

    # Step 2: Initialize collector
    logger.info("\nStep 2: Initializing collector...")
    try:
        collector = ProspectiveCollector(api_service=api)
        logger.info(f"✅ Collector initialized")
    except Exception as e:
        logger.error(f"❌ Collector initialization failed: {e}")
        return

    # Step 3: Collect current hour (small test)
    logger.info("\nStep 3: Collecting data for current hour...")
    logger.info("(This is a SMALL TEST - just 1 hour, not full backfill)")

    try:
        result = collector.collect_hour(currencies=["BTC"])  # Just BTC for now

        logger.info(f"\n{'='*60}")
        logger.info(f"COLLECTION RESULT:")
        logger.info(f"{'='*60}")
        logger.info(f"Status: {result['status']}")
        logger.info(f"Trades collected: {result['trades_collected']}")
        logger.info(f"Instruments collected: {result['instruments_collected']}")
        logger.info(f"Duration: {result.get('duration_seconds', 0)}s")

        if result['errors']:
            logger.warning(f"\nErrors encountered:")
            for error in result['errors']:
                logger.warning(f"  - {error}")

        if result['status'] == 'success':
            logger.info(f"\n✅ TEST PASSED: Collection successful!")
            logger.info(f"\nNext steps:")
            logger.info(f"  1. Review data in database")
            logger.info(f"  2. Add Greeks calculation")
            logger.info(f"  3. Set up hourly scheduler")
            logger.info(f"  4. Test ETH collection")
        elif result['status'] == 'partial':
            logger.warning(f"\n⚠️  TEST PARTIAL: Some data collected but errors occurred")
            logger.warning(f"Review errors above and fix issues")
        else:
            logger.error(f"\n❌ TEST FAILED: Collection failed")
            logger.error(f"Review errors above")

    except Exception as e:
        logger.error(f"❌ Collection failed with exception: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    init_logging(level="INFO")

    logger.info("Starting prospective collection test...")
    logger.info("NOTE: This will attempt to insert data into database")
    logger.info("Make sure PostgreSQL is running and migration is applied\n")

    # Check if user wants to proceed
    response = input("Proceed with test? (y/n): ")
    if response.lower() != 'y':
        logger.info("Test cancelled")
        sys.exit(0)

    test_collection()
