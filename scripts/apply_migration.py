"""
Apply database migration using Python.

Temporary script to apply the prospective collection migration.
"""

import psycopg2
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def apply_migration():
    """Apply migration 006."""

    # Database connection parameters (from coding/core/database/config.py)
    conn_params = {
        "host": "localhost",
        "port": 5433,
        "database": "option_trading",
        "user": "postgres",
        "password": "Asdf/1234"
    }

    migration_file = Path(__file__).parent.parent / "migrations" / "006_add_prospective_collection_tables.sql"

    try:
        logger.info(f"Connecting to database...")
        logger.info(f"  Host: {conn_params['host']}")
        logger.info(f"  Database: {conn_params['database']}")
        logger.info(f"  User: {conn_params['user']}")

        conn = psycopg2.connect(**conn_params)
        conn.autocommit = False
        cursor = conn.cursor()

        logger.info(f"Reading migration file: {migration_file}")
        sql = migration_file.read_text()

        logger.info(f"Applying migration...")
        cursor.execute(sql)

        conn.commit()
        logger.info(f"✅ Migration applied successfully!")

        # Verify tables exist
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('historical_trades', 'historical_instruments', 'collection_logs')
            ORDER BY table_name
        """)

        tables = cursor.fetchall()
        logger.info(f"\nVerified tables:")
        for table in tables:
            logger.info(f"  ✅ {table[0]}")

        cursor.close()
        conn.close()

    except psycopg2.OperationalError as e:
        logger.error(f"❌ Connection failed: {e}")
        logger.error(f"\nTroubleshooting:")
        logger.error(f"1. Check PostgreSQL service is running")
        logger.error(f"2. Verify database 'deribit_options_data' exists")
        logger.error(f"3. Check connection parameters (host, port, user, password)")
        logger.error(f"4. PostgreSQL might not be configured to accept TCP connections")
        raise

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        if 'conn' in locals():
            conn.rollback()
        raise


if __name__ == "__main__":
    apply_migration()
