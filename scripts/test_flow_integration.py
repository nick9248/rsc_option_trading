"""
Test the full buy/sell flow integration.

Tests:
1. Repository save_flow_metrics method
2. Repository get_flow_metrics method
3. Repository get_active_expirations_with_flow method
"""

import logging
from datetime import datetime

from coding.core.database.repository import DatabaseRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_flow_integration():
    """Test the flow metrics repository methods."""
    repo = DatabaseRepository()

    # Test data
    currency = "BTC"
    expiration = "27MAR26"
    underlying_price = 95000.0

    # Sample flow_data structure (matches BuySellFlowAnalyzer output)
    # Uses "C" and "P" as keys (not "calls" and "puts")
    flow_data = {
        90000.0: {
            "C": {
                "buy_count": 10,
                "buy_volume": 5.5,
                "buy_notional": 27500.0,
                "sell_count": 8,
                "sell_volume": 3.2,
                "sell_notional": 16000.0,
            },
            "P": {
                "buy_count": 5,
                "buy_volume": 2.1,
                "buy_notional": 10500.0,
                "sell_count": 12,
                "sell_volume": 6.3,
                "sell_notional": 31500.0,
            }
        },
        95000.0: {
            "C": {
                "buy_count": 25,
                "buy_volume": 15.8,
                "buy_notional": 79000.0,
                "sell_count": 18,
                "sell_volume": 9.2,
                "sell_notional": 46000.0,
            },
            "P": {
                "buy_count": 20,
                "buy_volume": 12.5,
                "buy_notional": 62500.0,
                "sell_count": 15,
                "sell_volume": 8.7,
                "sell_notional": 43500.0,
            }
        },
        100000.0: {
            "C": {
                "buy_count": 30,
                "buy_volume": 18.9,
                "buy_notional": 94500.0,
                "sell_count": 22,
                "sell_volume": 11.3,
                "sell_notional": 56500.0,
            },
            "P": {
                "buy_count": 8,
                "buy_volume": 4.2,
                "buy_notional": 21000.0,
                "sell_count": 6,
                "sell_volume": 3.1,
                "sell_notional": 15500.0,
            }
        }
    }

    # Test 1: Save flow metrics
    logger.info("=" * 60)
    logger.info("TEST 1: Save flow metrics")
    logger.info("=" * 60)

    try:
        rows_saved = repo.save_flow_metrics(
            currency=currency,
            expiration=expiration,
            flow_data=flow_data,
            underlying_price=underlying_price,
            window_hours=24
        )
        logger.info(f"✓ Saved {rows_saved} flow metric rows")
    except Exception as e:
        logger.error(f"✗ Failed to save flow metrics: {e}")
        return

    # Test 2: Get flow metrics
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: Get flow metrics")
    logger.info("=" * 60)

    try:
        result = repo.get_flow_metrics(currency, expiration)

        logger.info(f"✓ Retrieved flow data for {currency} {expiration}")
        logger.info(f"  Spot price: {result['spot_price']}")
        logger.info(f"  Number of strikes: {len(result['flow_data'])}")

        # Verify data structure (should use "C" and "P" keys)
        for strike, option_data in sorted(result['flow_data'].items())[:2]:
            logger.info(f"\n  Strike {strike}:")
            if "C" in option_data:
                calls = option_data["C"]
                logger.info(f"    Calls: buy_vol={calls['buy_volume']:.2f}, sell_vol={calls['sell_volume']:.2f}")
            if "P" in option_data:
                puts = option_data["P"]
                logger.info(f"    Puts: buy_vol={puts['buy_volume']:.2f}, sell_vol={puts['sell_volume']:.2f}")

    except Exception as e:
        logger.error(f"✗ Failed to get flow metrics: {e}")
        return

    # Test 3: Get active expirations with flow
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: Get active expirations with flow")
    logger.info("=" * 60)

    try:
        expirations = repo.get_active_expirations_with_flow(currency)

        logger.info(f"✓ Found {len(expirations)} active expirations with flow data")
        for exp in expirations[:5]:  # Show first 5
            logger.info(f"  - {exp['expiration']}: OI = {exp['total_oi']:,.0f}")

    except Exception as e:
        logger.error(f"✗ Failed to get active expirations: {e}")
        return

    logger.info("\n" + "=" * 60)
    logger.info("✓ ALL TESTS PASSED")
    logger.info("=" * 60)


if __name__ == "__main__":
    test_flow_integration()
