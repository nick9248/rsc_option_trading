"""Test script for regime detection."""

import logging
from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.regime.regime_detection_service import RegimeDetectionService
from coding.core.database.repository import DatabaseRepository

# Initialize logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)

def main():
    """Test regime detection for BTC and ETH."""
    logger.info("Starting regime detection test")

    with DeribitApiService() as api_service:
        repository = DatabaseRepository()
        service = RegimeDetectionService(
            api_service=api_service,
            repository=repository
        )

        # Test BTC
        logger.info("=" * 80)
        logger.info("Testing BTC regime detection")
        logger.info("=" * 80)
        btc_result = service.detect_regime("BTC")

        if "error" in btc_result:
            logger.error(f"BTC detection failed: {btc_result['error']}")
        else:
            print("\n" + "=" * 80)
            print("BTC REGIME DETECTION RESULTS")
            print("=" * 80)
            print(f"Regime: {btc_result['regime']}")
            print(f"Confidence: {btc_result['confidence']:.1f}%")
            print(f"Composite Score: {btc_result['composite_score']:.1f}")
            print(f"Current Price: ${btc_result['current_price']:,.2f}")
            print(f"\nComponent Scores:")
            for component, score in btc_result['component_scores'].items():
                print(f"  {component.capitalize():12s}: {score:6.1f}")
            print(f"\nReasoning:")
            print(f"  {btc_result['reasoning']}")
            print("=" * 80)

        # Test ETH
        print("\n")
        logger.info("=" * 80)
        logger.info("Testing ETH regime detection")
        logger.info("=" * 80)
        eth_result = service.detect_regime("ETH")

        if "error" in eth_result:
            logger.error(f"ETH detection failed: {eth_result['error']}")
        else:
            print("\n" + "=" * 80)
            print("ETH REGIME DETECTION RESULTS")
            print("=" * 80)
            print(f"Regime: {eth_result['regime']}")
            print(f"Confidence: {eth_result['confidence']:.1f}%")
            print(f"Composite Score: {eth_result['composite_score']:.1f}")
            print(f"Current Price: ${eth_result['current_price']:,.2f}")
            print(f"\nComponent Scores:")
            for component, score in eth_result['component_scores'].items():
                print(f"  {component.capitalize():12s}: {score:6.1f}")
            print(f"\nReasoning:")
            print(f"  {eth_result['reasoning']}")
            print("=" * 80)

    logger.info("Regime detection test completed")

if __name__ == "__main__":
    main()
