"""
Test Black-Scholes calculator with real market data.
"""

import logging
from datetime import datetime
from coding.core.database.repository import DatabaseRepository
from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_black_scholes_with_real_data():
    """Test Black-Scholes calculator with real trades from database."""

    logger.info("")
    logger.info("="*60)
    logger.info("BLACK-SCHOLES CALCULATOR TEST")
    logger.info("="*60)

    repo = DatabaseRepository()
    conn = repo._get_connection()
    bs_calc = BlackScholesCalculator()

    try:
        cursor = conn.cursor()

        # Get a sample of real trades with IV
        cursor.execute(
            """
            SELECT
                instrument_name,
                price,
                iv,
                index_price,
                trade_timestamp,
                direction
            FROM historical_trades
            WHERE iv IS NOT NULL
              AND index_price IS NOT NULL
            ORDER BY captured_at DESC
            LIMIT 5
            """
        )

        trades = cursor.fetchall()

        if not trades:
            logger.error("No trades with IV found in database!")
            return

        logger.info(f"\nTesting with {len(trades)} real trades:")
        logger.info("")

        test_passed = 0
        test_failed = 0

        for trade in trades:
            instrument_name, price, iv, index_price, trade_timestamp, direction = trade

            logger.info(f"Testing: {instrument_name}")
            logger.info(f"  Trade price: ${price:.4f}")
            logger.info(f"  IV: {iv:.2f}%")
            logger.info(f"  Index price: ${index_price:.2f}")

            # Parse instrument
            parsed = bs_calc.parse_instrument_name(instrument_name)

            if not parsed:
                logger.error(f"  ERROR: Failed to parse instrument name")
                test_failed += 1
                continue

            logger.info(f"  Strike: ${parsed['strike']:.0f}")
            logger.info(f"  Type: {parsed['option_type']}")
            logger.info(f"  Expiry: {parsed['expiry_time']}")

            # Calculate time to expiry
            trade_time = datetime.fromtimestamp(trade_timestamp / 1000)
            time_to_expiry = bs_calc.calculate_time_to_expiry(
                trade_time,
                parsed['expiry_time']
            )

            logger.info(f"  Time to expiry: {time_to_expiry:.6f} years ({time_to_expiry*365:.1f} days)")

            # Calculate Greeks
            try:
                greeks = bs_calc.calculate_greeks(
                    spot_price=float(index_price),
                    strike_price=float(parsed['strike']),
                    time_to_expiry=time_to_expiry,
                    implied_volatility=float(iv) / 100.0,  # Convert to decimal
                    option_type=parsed['option_type']
                )

                logger.info("  Calculated Greeks:")
                logger.info(f"    Delta:  {greeks['delta']:8.6f}")
                logger.info(f"    Gamma:  {greeks['gamma']:8.6f}")
                logger.info(f"    Theta:  {greeks['theta']:8.6f}")
                logger.info(f"    Vega:   {greeks['vega']:8.6f}")
                logger.info(f"    Rho:    {greeks['rho']:8.6f}")

                # Validate Greeks are in reasonable ranges
                valid = True

                if not (-1 <= greeks['delta'] <= 1):
                    logger.error(f"    ERROR: Delta out of range!")
                    valid = False

                if greeks['gamma'] < 0:
                    logger.error(f"    ERROR: Gamma should be positive!")
                    valid = False

                if greeks['vega'] < 0:
                    logger.error(f"    ERROR: Vega should be positive!")
                    valid = False

                if valid:
                    logger.info("  PASS: Greeks in valid ranges")
                    test_passed += 1
                else:
                    logger.error("  FAIL: Greeks validation failed")
                    test_failed += 1

            except Exception as e:
                logger.error(f"  FAIL: Greeks calculation error: {e}")
                test_failed += 1

            logger.info("")

        # Summary
        logger.info("="*60)
        logger.info(f"Test Results: {test_passed} passed, {test_failed} failed")

        if test_failed == 0:
            logger.info("SUCCESS: All Black-Scholes tests passed!")
        else:
            logger.error(f"FAILURE: {test_failed} tests failed")

        logger.info("="*60)
        logger.info("")

        cursor.close()

    finally:
        repo._return_connection(conn)


if __name__ == "__main__":
    test_black_scholes_with_real_data()
