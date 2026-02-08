"""
Check data collection status.

Queries database to verify:
1. Latest collection timestamps
2. Collection frequency
3. Data gaps
"""

import logging
from datetime import datetime, timedelta
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def check_collection_status():
    """Check status of data collection in database."""

    logger.info("="*60)
    logger.info("DATA COLLECTION STATUS CHECK")
    logger.info("="*60)
    logger.info(f"Current time: {datetime.now()}")
    logger.info("")

    repo = DatabaseRepository()

    # Check each table for latest data
    tables_to_check = [
        ("snapshots", "captured_at"),
        ("max_pain", "captured_at"),
        ("open_interest", "captured_at"),
        ("volume", "captured_at"),
        ("gex_dex", "captured_at"),
        ("levels", "captured_at")
    ]

    conn = repo._get_connection()

    try:
        cursor = conn.cursor()

        for table_name, time_column in tables_to_check:
            logger.info(f"\n{table_name.upper()} Table:")
            logger.info("-" * 50)

            # Get latest timestamp
            cursor.execute(f"""
                SELECT
                    MAX({time_column}) as latest_time,
                    COUNT(*) as total_records
                FROM {table_name}
            """)

            result = cursor.fetchone()
            latest_time, total_records = result

            if latest_time:
                time_diff = datetime.now() - latest_time
                hours_ago = time_diff.total_seconds() / 3600

                logger.info(f"  Latest collection: {latest_time}")
                logger.info(f"  Time since last:   {hours_ago:.2f} hours ago")
                logger.info(f"  Total records:     {total_records}")

                # Check if data is recent (within 1 hour)
                if hours_ago < 1:
                    logger.info(f"  Status: [OK] RECENT DATA")
                elif hours_ago < 2:
                    logger.warning(f"  Status: [!!] DATA SLIGHTLY STALE (>1h)")
                else:
                    logger.error(f"  Status: [!!] DATA STALE (>{hours_ago:.1f}h)")
            else:
                logger.error(f"  Status: [!!] NO DATA FOUND")

        # Check for BTC and ETH specifically
        logger.info("\n" + "="*60)
        logger.info("CURRENCY-SPECIFIC CHECK")
        logger.info("="*60)

        for currency in ["BTC", "ETH"]:
            logger.info(f"\n{currency}:")
            logger.info("-" * 50)

            cursor.execute("""
                SELECT
                    MAX(captured_at) as latest_time,
                    COUNT(DISTINCT expiration) as num_expirations,
                    COUNT(*) as num_records
                FROM snapshots
                WHERE currency = %s
            """, (currency,))

            result = cursor.fetchone()
            if result[0]:
                latest_time, num_expirations, num_records = result
                time_diff = datetime.now() - latest_time
                hours_ago = time_diff.total_seconds() / 3600

                logger.info(f"  Latest collection: {latest_time}")
                logger.info(f"  Time since last:   {hours_ago:.2f} hours ago")
                logger.info(f"  Expirations:       {num_expirations}")
                logger.info(f"  Total records:     {num_records}")

                # Get sample of recent data
                cursor.execute("""
                    SELECT
                        captured_at,
                        COUNT(*) as instruments
                    FROM snapshots
                    WHERE currency = %s
                    GROUP BY captured_at
                    ORDER BY captured_at DESC
                    LIMIT 10
                """, (currency,))

                recent_collections = cursor.fetchall()
                if len(recent_collections) > 1:
                    logger.info(f"\n  Recent collections:")
                    for i, (cap_time, inst_count) in enumerate(recent_collections):
                        logger.info(f"    {i+1}. {cap_time} - {inst_count} instruments")

                    # Check collection intervals
                    if len(recent_collections) >= 2:
                        first_time = recent_collections[0][0]
                        second_time = recent_collections[1][0]
                        interval_minutes = (first_time - second_time).total_seconds() / 60
                        logger.info(f"\n  Latest interval: {interval_minutes:.1f} minutes")

                        if 25 <= interval_minutes <= 35:
                            logger.info(f"  Collection frequency: [OK] ON SCHEDULE (~30 min)")
                        else:
                            logger.warning(f"  Collection frequency: [!!] IRREGULAR ({interval_minutes:.1f} min)")
            else:
                logger.error(f"  Status: [!!] NO DATA")

        # Final summary
        logger.info("\n" + "="*60)
        logger.info("SUMMARY")
        logger.info("="*60)

        cursor.execute("""
            SELECT MAX(captured_at) FROM snapshots
        """)
        latest_snapshot = cursor.fetchone()[0]

        if latest_snapshot:
            hours_since = (datetime.now() - latest_snapshot).total_seconds() / 3600

            if hours_since < 0.75:  # Less than 45 minutes
                logger.info("[OK] Data collection is ACTIVE and UP-TO-DATE")
                logger.info(f"  Last collection was {hours_since*60:.1f} minutes ago")
            elif hours_since < 1.5:  # Less than 90 minutes
                logger.warning("[!!] Data collection may be DELAYED")
                logger.info(f"  Last collection was {hours_since:.2f} hours ago")
                logger.info("  Expected: every 30 minutes")
            else:
                logger.error("[!!] Data collection appears STOPPED")
                logger.info(f"  Last collection was {hours_since:.2f} hours ago")
                logger.info("  Check if daemon is running")
        else:
            logger.error("[!!] NO DATA in database - collection never ran")

        logger.info("="*60)

    finally:
        cursor.close()
        repo._return_connection(conn)


if __name__ == "__main__":
    check_collection_status()
