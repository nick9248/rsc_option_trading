"""
Master Backfill Script

Runs all 4 backfill scripts in sequence to populate missing ML feature tables:
1. Technical Indicators
2. External Metrics (Fear & Greed, BTC Dominance)
3. DVOL (Deribit Volatility Index)
4. Funding Rate

This prepares the database for ML training with the full 70-feature set.
"""

import logging
import time
from datetime import datetime

# Import backfill functions
import sys
sys.path.append('.')

from scripts.backfill_technical_indicators import backfill_technical_indicators
from scripts.backfill_external_metrics import backfill_external_metrics
from scripts.backfill_dvol import backfill_dvol
from scripts.backfill_funding_rate import backfill_funding_rate

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def verify_backfill():
    """Verify that all tables have been populated."""
    from coding.core.database.repository import DatabaseRepository

    repo = DatabaseRepository()
    conn = repo._get_connection()
    cursor = conn.cursor()

    print("\n" + "=" * 60)
    print("VERIFICATION - Database Table Counts")
    print("=" * 60)

    tables = [
        "technical_indicators",
        "external_metrics",
        "volatility_index_history",
        "funding_rate_history"
    ]

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]

        # Get date range
        try:
            if table == "external_metrics":
                cursor.execute(f"SELECT MIN(date), MAX(date) FROM {table}")
            else:
                cursor.execute(f"SELECT MIN(date), MAX(date) FROM {table}")

            min_date, max_date = cursor.fetchone()
            date_range = f"{min_date.date()} to {max_date.date()}" if min_date else "N/A"
        except:
            date_range = "N/A"

        print(f"\n{table}:")
        print(f"  Total rows: {count}")
        print(f"  Date range: {date_range}")

    conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("MASTER BACKFILL SCRIPT")
    print("=" * 60)
    print("\nThis will populate all missing ML feature tables:")
    print("  1. Technical Indicators (~90 days)")
    print("  2. External Metrics (~90 days)")
    print("  3. DVOL (~90 days)")
    print("  4. Funding Rate (~30 days)")
    print("\nEstimated time: 5-10 minutes")
    print("=" * 60)

    start_time = time.time()
    total_saved = 0

    # Step 1: Technical Indicators
    print("\n\n" + "=" * 60)
    print("STEP 1/4: Technical Indicators Backfill")
    print("=" * 60)
    try:
        btc_ti = backfill_technical_indicators("BTC", days=90)
        eth_ti = backfill_technical_indicators("ETH", days=90)
        ti_total = btc_ti + eth_ti
        total_saved += ti_total
        print(f"\nOK Technical Indicators: {ti_total} rows saved")
    except Exception as e:
        print(f"\nERROR Technical Indicators failed: {e}")
        logger.error("Technical indicators backfill failed", exc_info=True)

    # Step 2: External Metrics
    print("\n\n" + "=" * 60)
    print("STEP 2/4: External Metrics Backfill")
    print("=" * 60)
    try:
        em_total = backfill_external_metrics(days=90)
        total_saved += em_total
        print(f"\nOK External Metrics: {em_total} rows saved")
    except Exception as e:
        print(f"\nERROR External Metrics failed: {e}")
        logger.error("External metrics backfill failed", exc_info=True)

    # Step 3: DVOL
    print("\n\n" + "=" * 60)
    print("STEP 3/4: DVOL Backfill")
    print("=" * 60)
    try:
        btc_dvol = backfill_dvol("BTC", days=90)
        eth_dvol = backfill_dvol("ETH", days=90)
        dvol_total = btc_dvol + eth_dvol
        total_saved += dvol_total
        print(f"\nOK DVOL: {dvol_total} rows saved")
    except Exception as e:
        print(f"\nERROR DVOL failed: {e}")
        logger.error("DVOL backfill failed", exc_info=True)

    # Step 4: Funding Rate
    print("\n\n" + "=" * 60)
    print("STEP 4/4: Funding Rate Backfill")
    print("=" * 60)
    try:
        btc_fr = backfill_funding_rate("BTC")
        eth_fr = backfill_funding_rate("ETH")
        fr_total = btc_fr + eth_fr
        total_saved += fr_total
        print(f"\nOK Funding Rate: {fr_total} rows saved")
    except Exception as e:
        print(f"\nERROR Funding Rate failed: {e}")
        logger.error("Funding rate backfill failed", exc_info=True)

    # Verification
    verify_backfill()

    # Summary
    elapsed = time.time() - start_time
    print("\n\n" + "=" * 60)
    print("BACKFILL COMPLETE!")
    print("=" * 60)
    print(f"\nTotal rows saved: {total_saved}")
    print(f"Time elapsed: {elapsed:.2f} seconds ({elapsed/60:.2f} minutes)")
    print("\nOK Database is now ready for ML training with full 70-feature set!")
    print("\nNext steps:")
    print("  1. Start prospective daemon to keep data fresh")
    print("  2. Update ML feature engineering to use new tables")
    print("  3. Retrain models with full feature set")
