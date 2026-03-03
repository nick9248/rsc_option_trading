"""
Test script for flow-based GEX/DEX calculator.

Validates that the calculator works with trade data and produces meaningful results.
"""

import logging
from coding.core.analytics.flow_based_gex_calculator import FlowBasedGexCalculator
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_flow_based_gex():
    """
    Test flow-based GEX calculator with real trade data.
    """
    logger.info("=" * 70)
    logger.info("TESTING FLOW-BASED GEX/DEX CALCULATOR")
    logger.info("=" * 70)

    # Initialize repository
    repository = DatabaseRepository()

    # Get current spot price from API
    with DeribitApiService() as api:
        ticker = api.get_ticker(instrument_name="BTC-PERPETUAL")
        spot_price = ticker.get("index_price", 0)

    logger.info(f"Current BTC spot price: ${spot_price:,.2f}")

    # Test with BTC and an expiration with recent trades
    # Query expirations with recent trade activity (last 24 hours)
    from datetime import datetime, timedelta
    start_time = datetime.now() - timedelta(hours=24)
    start_ts = int(start_time.timestamp() * 1000)

    with repository._db_cursor() as cursor:
        cursor.execute("""
            SELECT expiration, COUNT(*) as trade_count
            FROM historical_trades
            WHERE currency = %s
                AND expiration IS NOT NULL
                AND trade_timestamp >= %s
            GROUP BY expiration
            ORDER BY trade_count DESC
            LIMIT 10
        """, ("BTC", start_ts))

        expiration_stats = cursor.fetchall()

    if not expiration_stats:
        logger.warning("No expirations with recent trades found in historical_trades table")
        logger.info("Make sure the trade collector has been running to populate data")
        return

    # Use the expiration with most recent trades
    expiration = expiration_stats[0][0]
    trade_count = expiration_stats[0][1]

    logger.info(f"Using expiration: {expiration} ({trade_count:,} trades in last 24h)")
    logger.info(
        f"Top expirations by trade activity: "
        f"{', '.join([f'{exp} ({cnt})' for exp, cnt in expiration_stats[:5]])}"
    )

    # Test with different lookback windows
    for lookback_hours in [1, 6, 24]:
        logger.info(f"\n{'=' * 70}")
        logger.info(f"Testing with {lookback_hours}-hour lookback window")
        logger.info(f"{'=' * 70}")

        try:
            calculator = FlowBasedGexCalculator(
                repository=repository,
                currency="BTC",
                expiration=expiration,
                spot_price=spot_price,
                lookback_hours=lookback_hours
            )

            result = calculator.calculate()

            # Print summary
            logger.info(f"\nResults for {lookback_hours}-hour window:")
            logger.info(f"  Trades analyzed: {result['trade_count']}")
            logger.info(f"  Unique strikes: {len(result['strike_data'])}")
            logger.info(f"  Total Net GEX: {result['total_net_gex']:+,.2f}")
            logger.info(f"  Total Net DEX: {result['total_net_dex']:+,.2f}")

            # Key levels
            key_levels = result["key_levels"]
            if key_levels["call_resistance"]:
                cr = key_levels["call_resistance"]
                logger.info(
                    f"  Call Resistance: ${cr['strike']:,.0f} "
                    f"(GEX: {cr['net_gex']:+,.2f})"
                )

            if key_levels["put_support"]:
                ps = key_levels["put_support"]
                logger.info(
                    f"  Put Support: ${ps['strike']:,.0f} "
                    f"(GEX: {ps['net_gex']:+,.2f})"
                )

            if key_levels["hvl"]:
                logger.info(f"  HVL (Zero Gamma): ${key_levels['hvl']:,.0f}")

            # Show top 5 strikes by absolute GEX
            if result["strike_data"]:
                logger.info(f"\n  Top 5 strikes by absolute Net GEX:")
                sorted_strikes = sorted(
                    result["strike_data"].keys(),
                    key=lambda s: abs(result["strike_data"][s]["net_gex"]),
                    reverse=True
                )[:5]

                for strike in sorted_strikes:
                    data = result["strike_data"][strike]
                    logger.info(
                        f"    ${strike:,.0f}: Net GEX={data['net_gex']:+,.2f}, "
                        f"Net DEX={data['net_dex']:+,.4f}, "
                        f"Call Vol={data.get('call_volume', 0):.1f}, "
                        f"Put Vol={data.get('put_volume', 0):.1f}"
                    )

        except Exception as e:
            logger.error(f"Error testing {lookback_hours}-hour window: {e}")
            import traceback
            traceback.print_exc()

    # Generate full report for 24-hour window
    logger.info(f"\n{'=' * 70}")
    logger.info("FULL REPORT (24-hour window)")
    logger.info(f"{'=' * 70}\n")

    calculator = FlowBasedGexCalculator(
        repository=repository,
        currency="BTC",
        expiration=expiration,
        spot_price=spot_price,
        lookback_hours=24
    )

    report = calculator.generate_report_section()
    print(report)


if __name__ == "__main__":
    test_flow_based_gex()
    logger.info("\n" + "=" * 70)
    logger.info("FLOW-BASED GEX TEST COMPLETE")
    logger.info("=" * 70)
