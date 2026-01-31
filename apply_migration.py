"""
Apply migration 004 to increase greeks precision.
"""
import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from coding.core.database.config import DatabaseConfig, ConnectionPool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Run migration 004."""
    # Read migration file
    migration_file = Path(__file__).parent / "migrations" / "004_increase_greeks_precision.sql"

    if not migration_file.exists():
        logger.error(f"Migration file not found: {migration_file}")
        return False

    with open(migration_file, 'r') as f:
        migration_sql = f.read()

    # Initialize database connection pool
    config = DatabaseConfig()
    pool = ConnectionPool()
    pool.initialize(config)

    try:
        logger.info("Applying migration 004: Increase greeks precision for BTC options...")

        conn = pool.get_connection()
        cursor = conn.cursor()

        try:
            # Execute migration
            cursor.execute(migration_sql)
            conn.commit()

            logger.info("✅ Migration 004 applied successfully!")
            logger.info("   - net_delta: DECIMAL(12,6) - supports up to 999,999")
            logger.info("   - net_gamma: DECIMAL(12,8)")
            logger.info("   - net_theta: DECIMAL(12,6)")
            logger.info("   - net_vega: DECIMAL(12,6)")

            return True

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Migration failed: {e}")
            return False

        finally:
            cursor.close()
            pool.return_connection(conn)

    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False
    finally:
        pool.close_all()

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
