"""
Run migration 009 to create buy_sell_flow_metrics table.
"""

import logging
from pathlib import Path

from coding.core.database.config import DatabaseConfig
from coding.core.database.repository import DatabaseRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_migration():
    """Run the migration script."""
    migration_file = Path("migrations/009_add_buy_sell_flow_metrics.sql")

    if not migration_file.exists():
        logger.error(f"Migration file not found: {migration_file}")
        return

    # Read migration SQL
    with open(migration_file, "r", encoding="utf-8") as f:
        sql = f.read()

    # Execute migration
    config = DatabaseConfig()
    repo = DatabaseRepository(config)

    try:
        import psycopg2
        conn = repo._get_connection()
        cursor = conn.cursor()

        # Execute entire migration as one transaction
        logger.info("Executing migration 009...")
        cursor.execute(sql)

        conn.commit()
        cursor.close()
        repo._return_connection(conn)

        logger.info("✓ Migration 009 completed successfully")
        logger.info("✓ Table buy_sell_flow_metrics created")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    run_migration()
