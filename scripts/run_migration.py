"""
Apply database migration.

Usage:
    python scripts/run_migration.py migrations/003_add_regime_detection_tables.sql
"""

import os
import psycopg2
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def apply_migration(migration_file: str):
    """Apply a specific migration file."""

    # Database connection parameters
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    load_dotenv(dotenv_path=_Path(__file__).parents[1] / ".env")

    conn_params = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5433")),
        "database": os.getenv("DB_NAME", "option_trading"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }

    migration_path = Path(migration_file)

    if not migration_path.exists():
        logger.error(f"Migration file not found: {migration_path}")
        return 1

    try:
        logger.info(f"Connecting to database...")
        logger.info(f"  Host: {conn_params['host']}")
        logger.info(f"  Database: {conn_params['database']}")

        conn = psycopg2.connect(**conn_params)
        conn.autocommit = False
        cursor = conn.cursor()

        logger.info(f"\nReading migration: {migration_path.name}")
        sql = migration_path.read_text()

        logger.info(f"Applying migration...")
        cursor.execute(sql)

        conn.commit()
        logger.info(f"OK: Migration applied successfully!")

        cursor.close()
        conn.close()

        return 0

    except Exception as e:
        logger.error(f"ERROR: Migration failed: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python scripts/run_migration.py <migration_file>")
        sys.exit(1)

    migration_file = sys.argv[1]
    sys.exit(apply_migration(migration_file))
