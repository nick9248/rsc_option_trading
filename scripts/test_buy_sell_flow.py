"""
Manual verification script for buy/sell flow analysis.

Tests the BuySellFlowAnalyzer with real database data.
"""

import logging
from coding.core.logging.logging_setup import init_logging
from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.database.repository import DatabaseRepository

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_buy_sell_flow():
    """Test buy/sell flow analysis with real data."""
    logger.info("=" * 80)
    logger.info("BUY/SELL FLOW ANALYSIS TEST")
    logger.info("=" * 80)

    # Initialize repository
    repository = DatabaseRepository()

    # Test with BTC
    currency = "BTC"
    expiration = "14FEB26"  # Use a near-term expiration
    spot_price = 68937.0

    logger.info(f"\nAnalyzing {currency} {expiration}...")
    logger.info(f"Spot Price: ${spot_price:,.2f}")

    # Create analyzer
    analyzer = BuySellFlowAnalyzer(
        repository=repository,
        currency=currency,
        expiration=expiration,
        spot_price=spot_price,
        lookback_hours=24
    )

    # Generate report
    report = analyzer.generate_report_section()

    logger.info("\n" + report)

    # Get detailed results
    result = analyzer.calculate()

    logger.info("\n" + "=" * 80)
    logger.info("VERIFICATION")
    logger.info("=" * 80)
    logger.info(f"Trades analyzed: {result['trade_count']}")
    logger.info(f"Unique strikes: {len(result['flow_data'])}")
    logger.info(f"Top buying strikes: {len(result['top_buy_strikes'])}")
    logger.info(f"Top selling strikes: {len(result['top_sell_strikes'])}")
    logger.info(f"Bias: {result['bias_interpretation']}")
    logger.info(f"Trend: {result['flow_trend']}")

    # Verify expiration totals
    totals = result["expiration_totals"]
    logger.info("\nExpiration Totals:")
    logger.info(f"  Call Buy Volume: {totals['call_buy_volume']:,.1f}")
    logger.info(f"  Call Sell Volume: {totals['call_sell_volume']:,.1f}")
    logger.info(f"  Put Buy Volume: {totals['put_buy_volume']:,.1f}")
    logger.info(f"  Put Sell Volume: {totals['put_sell_volume']:,.1f}")

    # Manual verification: Query a sample strike
    if result['top_buy_strikes']:
        top_strike = result['top_buy_strikes'][0]
        logger.info(f"\nManual Verification for top buy strike:")
        logger.info(f"  Strike: {top_strike['strike']:,.0f}")
        logger.info(f"  Option Type: {top_strike['option_type']}")
        logger.info(f"  Net Flow: {top_strike['net_flow']:+,.1f}")

        # Query database directly to verify
        query = """
            SELECT
                direction,
                SUM(amount) as total_volume,
                COUNT(*) as trade_count
            FROM historical_trades
            WHERE currency = %s
                AND expiration = %s
                AND strike = %s
                AND option_type = %s
                AND trade_timestamp >= %s
            GROUP BY direction
            ORDER BY direction
        """

        from datetime import datetime, timedelta
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)
        start_ts = int(start_time.timestamp() * 1000)

        with repository._db_cursor() as cursor:
            cursor.execute(
                query,
                (currency, expiration, top_strike['strike'], top_strike['option_type'], start_ts)
            )
            rows = cursor.fetchall()

            logger.info("\n  Database verification:")
            buy_vol = 0.0
            sell_vol = 0.0
            for row in rows:
                direction, volume, count = row
                logger.info(f"    {direction}: {volume:,.1f} volume ({count} trades)")
                if direction == "buy":
                    buy_vol = float(volume)
                else:
                    sell_vol = float(volume)

            db_net_flow = buy_vol - sell_vol
            logger.info(f"    Net Flow (DB): {db_net_flow:+,.1f}")
            logger.info(f"    Net Flow (Analyzer): {top_strike['net_flow']:+,.1f}")

            if abs(db_net_flow - top_strike['net_flow']) < 0.01:
                logger.info("    ✓ VERIFIED - Values match!")
            else:
                logger.warning("    ✗ MISMATCH - Values don't match!")

    logger.info("\n" + "=" * 80)
    logger.info("TEST COMPLETE")
    logger.info("=" * 80)


if __name__ == "__main__":
    test_buy_sell_flow()
