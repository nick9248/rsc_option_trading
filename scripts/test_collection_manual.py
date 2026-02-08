"""
Test manual data collection (before setting up auto-start).

This runs a SINGLE collection cycle to verify everything works.
"""

import logging
from datetime import datetime, timedelta

from coding.core.logging.logging_setup import init_logging
from coding.service.data_collection.prospective_collector import ProspectiveCollector


def test_manual_collection():
    """Run a single collection cycle for testing."""

    # Initialize logging
    init_logging(level="INFO")
    logger = logging.getLogger(__name__)

    logger.info(f"\n{'='*60}")
    logger.info(f"MANUAL COLLECTION TEST")
    logger.info(f"{'='*60}")
    logger.info(f"Time: {datetime.now()}")
    logger.info(f"{'='*60}\n")

    # Create collector
    collector = ProspectiveCollector()

    # Run single collection for last hour
    logger.info(f"Running collection for last hour (BTC + ETH)...")

    result = collector.collect_hour(currencies=["BTC", "ETH"])

    # Print results
    logger.info(f"\n{'='*60}")
    logger.info(f"COLLECTION RESULTS")
    logger.info(f"{'='*60}")
    logger.info(f"Status: {result.get('status')}")
    logger.info(f"Trades collected: {result.get('trades_collected', 0)}")
    logger.info(f"Instruments: {result.get('instruments_collected', 0)}")
    logger.info(f"Duration: {result.get('duration_seconds', 0):.2f}s")

    if result.get("errors"):
        logger.warning(f"\nErrors ({len(result['errors'])}):")
        for err in result['errors'][:5]:  # Show first 5
            logger.warning(f"  • {err}")

    if result.get("details"):
        logger.info(f"\nDetails:")
        for currency, details in result['details'].items():
            logger.info(f"  {currency}: {details.get('count', 0)} trades")

    logger.info(f"{'='*60}\n")

    if result["status"] == "success":
        logger.info(f"[PASS] TEST PASSED - Collection working correctly!")
        logger.info(f"\nNext steps:")
        logger.info(f"1. Check database: SELECT COUNT(*) FROM historical_trades;")
        logger.info(f"2. Setup auto-start (see SETUP_AUTO_START.md)")
        logger.info(f"3. Let it run for 90 days!")
        return True
    else:
        logger.error(f"[FAIL] TEST FAILED - Check errors above")
        return False


if __name__ == "__main__":
    success = test_manual_collection()
    exit(0 if success else 1)
