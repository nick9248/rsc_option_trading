"""
Verify the buy_sell_flow_metrics table structure.
"""

import logging
from coding.core.database.repository import DatabaseRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def verify_table():
    """Verify table exists and show structure."""
    repo = DatabaseRepository()

    try:
        conn = repo._get_connection()
        cursor = conn.cursor()

        # Check table exists
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'buy_sell_flow_metrics'
        """)

        if cursor.fetchone():
            logger.info("✓ Table buy_sell_flow_metrics exists")

            # Show column structure
            cursor.execute("""
                SELECT column_name, data_type, character_maximum_length
                FROM information_schema.columns
                WHERE table_name = 'buy_sell_flow_metrics'
                ORDER BY ordinal_position
            """)

            logger.info("\nTable structure:")
            for row in cursor.fetchall():
                col_name, data_type, max_length = row
                if max_length:
                    logger.info(f"  - {col_name}: {data_type}({max_length})")
                else:
                    logger.info(f"  - {col_name}: {data_type}")

            # Show indexes
            cursor.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'buy_sell_flow_metrics'
            """)

            logger.info("\nIndexes:")
            for row in cursor.fetchall():
                logger.info(f"  - {row[0]}")

        else:
            logger.error("✗ Table buy_sell_flow_metrics does not exist")

        cursor.close()
        repo._return_connection(conn)

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        raise


if __name__ == "__main__":
    verify_table()
