"""
Test script for VRP (Volatility Risk Premium) calculation.

Validates that VRP service fetches data and computes metrics correctly.
"""

import logging
from coding.service.analytics.vrp_service import VRPService
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_vrp_calculation():
    """
    Test VRP calculation for BTC and ETH.
    """
    logger.info("=" * 70)
    logger.info("TESTING VRP (VOLATILITY RISK PREMIUM) CALCULATION")
    logger.info("=" * 70)

    with DeribitApiService() as api:
        vrp_service = VRPService(api_service=api)

        # Test BTC VRP
        logger.info("\n" + "=" * 70)
        logger.info("BTC VRP ANALYSIS")
        logger.info("=" * 70)

        # Get available expirations
        instruments = api.get_instruments(currency="BTC", kind="option")

        if not instruments:
            logger.error("No BTC options found")
            return

        # Extract unique expirations
        expirations = set()
        for inst in instruments:
            name = inst.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) >= 2:
                expirations.add(parts[1])

        expirations_list = sorted(list(expirations))[:3]  # Take first 3

        logger.info(f"Testing with expirations: {', '.join(expirations_list)}")

        for expiration in expirations_list:
            logger.info(f"\n--- VRP for BTC {expiration} ---")

            try:
                # Generate full report
                report = vrp_service.generate_report(
                    currency="BTC",
                    expiration=expiration,
                    lookback_days=30
                )

                print(report)
                print()

            except Exception as e:
                logger.error(f"Error calculating VRP for {expiration}: {e}")
                import traceback
                traceback.print_exc()

        # Test ETH VRP
        logger.info("\n" + "=" * 70)
        logger.info("ETH VRP ANALYSIS")
        logger.info("=" * 70)

        instruments = api.get_instruments(currency="ETH", kind="option")

        if not instruments:
            logger.error("No ETH options found")
            return

        # Extract unique expirations
        expirations = set()
        for inst in instruments:
            name = inst.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) >= 2:
                expirations.add(parts[1])

        expirations_list = sorted(list(expirations))[:2]  # Take first 2

        logger.info(f"Testing with expirations: {', '.join(expirations_list)}")

        for expiration in expirations_list:
            logger.info(f"\n--- VRP for ETH {expiration} ---")

            try:
                # Generate full report
                report = vrp_service.generate_report(
                    currency="ETH",
                    expiration=expiration,
                    lookback_days=30
                )

                print(report)
                print()

            except Exception as e:
                logger.error(f"Error calculating VRP for {expiration}: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    test_vrp_calculation()
    logger.info("\n" + "=" * 70)
    logger.info("VRP TEST COMPLETE")
    logger.info("=" * 70)
