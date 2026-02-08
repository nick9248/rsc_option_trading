"""Check database for collected trades."""

import psycopg2
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_database():
    """Check database for collected data."""

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

        logger.info(f"\n{'='*60}")
        logger.info(f"DATABASE CHECK")
        logger.info(f"{'='*60}\n")

        # Check total trades
        cursor.execute("SELECT COUNT(*) FROM historical_trades")
        total_trades = cursor.fetchone()[0]
        logger.info(f"Total trades: {total_trades}")

        # Check by currency
        cursor.execute("""
            SELECT currency, COUNT(*)
            FROM historical_trades
            GROUP BY currency
            ORDER BY currency
        """)
        logger.info(f"\nTrades by currency:")
        for row in cursor.fetchall():
            logger.info(f"  {row[0]}: {row[1]}")

        # Check most recent trades
        cursor.execute("""
            SELECT
                instrument_name,
                price,
                amount,
                direction,
                iv,
                captured_at
            FROM historical_trades
            ORDER BY captured_at DESC
            LIMIT 5
        """)
        logger.info(f"\nMost recent trades:")
        for row in cursor.fetchall():
            logger.info(f"  {row[0]}: price={row[1]}, amount={row[2]}, direction={row[3]}, iv={row[4]}, captured={row[5]}")

        # Check IV presence
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(iv) as with_iv,
                ROUND(COUNT(iv) * 100.0 / COUNT(*), 2) as iv_pct
            FROM historical_trades
        """)
        row = cursor.fetchone()
        logger.info(f"\nIV Coverage:")
        logger.info(f"  Total trades: {row[0]}")
        logger.info(f"  With IV: {row[1]}")
        logger.info(f"  Coverage: {row[2]}%")

        logger.info(f"\n{'='*60}\n")

        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"Error: {e}")
        raise


if __name__ == "__main__":
    check_database()
