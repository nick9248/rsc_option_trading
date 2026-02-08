"""Verify prospective collection tables exist."""

import psycopg2
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def verify_tables():
    """Check all prospective collection tables exist."""

    conn_params = {
        "host": "localhost",
        "port": 5433,
        "database": "option_trading",
        "user": "postgres",
        "password": "Asdf/1234"
    }

    try:
        conn = psycopg2.connect(**conn_params)
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)

        tables = cursor.fetchall()
        logger.info(f"\nAll tables in database:")
        for table in tables:
            logger.info(f"  • {table[0]}")

        # Check specific prospective collection tables
        expected_tables = ['historical_trades', 'historical_instruments', 'collection_logs']

        logger.info(f"\nProspective collection tables:")
        for expected in expected_tables:
            cursor.execute("""
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = %s
            """, (expected,))

            exists = cursor.fetchone()[0] > 0
            status = "✅" if exists else "❌"
            logger.info(f"  {status} {expected}")

        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"Error: {e}")
        raise


if __name__ == "__main__":
    verify_tables()
