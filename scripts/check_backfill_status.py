"""
Check backfill status - historical trades and hourly snapshots.
"""

import logging
from datetime import datetime
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def check_backfill_status():
    """Check status of historical_trades and hourly_snapshots tables."""

    repo = DatabaseRepository()
    conn = repo._get_connection()

    try:
        cursor = conn.cursor()

        logger.info("")
        logger.info("="*60)
        logger.info("BACKFILL STATUS CHECK")
        logger.info("="*60)

        # Check historical_trades
        try:
            cursor.execute("SELECT COUNT(*) FROM historical_trades")
            historical_count = cursor.fetchone()[0]

            logger.info(f"\nHistorical Trades: {historical_count:,}")

            if historical_count > 0:
                # Get date range
                cursor.execute(
                    """
                    SELECT
                        MIN(captured_at) as earliest,
                        MAX(captured_at) as latest,
                        COUNT(DISTINCT currency) as currencies,
                        COUNT(DISTINCT instrument_name) as instruments
                    FROM historical_trades
                    """
                )
                earliest, latest, currencies, instruments = cursor.fetchone()
                logger.info(f"  Date range: {earliest} to {latest}")
                logger.info(f"  Currencies: {currencies}")
                logger.info(f"  Unique instruments: {instruments}")

                # By currency
                cursor.execute(
                    """
                    SELECT currency, COUNT(*)
                    FROM historical_trades
                    GROUP BY currency
                    ORDER BY currency
                    """
                )
                logger.info("  By currency:")
                for currency, count in cursor.fetchall():
                    logger.info(f"    {currency}: {count:,}")

        except Exception as e:
            logger.error(f"Error checking historical_trades: {e}")

        # Check hourly_snapshots
        try:
            cursor.execute("SELECT COUNT(*) FROM hourly_snapshots")
            snapshot_count = cursor.fetchone()[0]

            logger.info(f"\nHourly Snapshots: {snapshot_count:,}")

            if snapshot_count > 0:
                # Get date range
                cursor.execute(
                    """
                    SELECT
                        MIN(snapshot_hour) as earliest,
                        MAX(snapshot_hour) as latest,
                        COUNT(DISTINCT currency) as currencies,
                        COUNT(DISTINCT instrument_name) as instruments
                    FROM hourly_snapshots
                    """
                )
                earliest, latest, currencies, instruments = cursor.fetchone()
                logger.info(f"  Date range: {earliest} to {latest}")
                logger.info(f"  Currencies: {currencies}")
                logger.info(f"  Unique instruments: {instruments}")

                # Greeks coverage
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) as total,
                        COUNT(avg_delta) as with_greeks,
                        COUNT(avg_delta) * 100.0 / COUNT(*) as coverage
                    FROM hourly_snapshots
                    """
                )
                total, with_greeks, coverage = cursor.fetchone()
                logger.info(f"  Greeks coverage: {coverage:.2f}% ({with_greeks:,}/{total:,})")

        except Exception as e:
            logger.error(f"Error checking hourly_snapshots: {e}")

        cursor.close()
        logger.info("")
        logger.info("="*60)

    finally:
        repo._return_connection(conn)


if __name__ == "__main__":
    check_backfill_status()
