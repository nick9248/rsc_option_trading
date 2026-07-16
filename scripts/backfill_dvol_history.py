#!/usr/bin/env python
"""
One-time backfill: fetch full DVOL history from Deribit and store in dvol_history table.

Usage:
    python scripts/backfill_dvol_history.py
    python scripts/backfill_dvol_history.py --assets BTC   (BTC only)

Skips rows that already exist (ON CONFLICT DO NOTHING).
"""
import logging
import os
import sys
import argparse
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from coding.core.logging.logging_setup import init_logging
init_logging(level="INFO")

import psycopg2
from coding.core.database.config import DatabaseConfig
from coding.service.deribit.dvol_fetcher import DVOLFetcher

logger = logging.getLogger(__name__)


def get_connection():
    cfg = DatabaseConfig()
    return psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.database,
        user=cfg.user, password=cfg.password
    )


def backfill_asset(asset: str, months: int = 40) -> int:
    """Fetch and store DVOL history for one asset. Returns rows inserted."""
    fetcher = DVOLFetcher()
    logger.info("Fetching %d months of DVOL history for %s...", months, asset)
    rows = fetcher.fetch_history(asset, months=months)
    if not rows:
        logger.error("No data returned for %s — aborting", asset)
        return 0

    logger.info("Received %d rows for %s", len(rows), asset)

    conn = get_connection()
    inserted = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for ts, value in rows:
                    cur.execute(
                        """
                        INSERT INTO dvol_history (asset, timestamp, dvol_value)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (asset, timestamp) DO NOTHING
                        """,
                        (asset, ts, value),
                    )
                    inserted += cur.rowcount
        logger.info("Inserted %d new rows for %s (%d already existed)",
                    inserted, asset, len(rows) - inserted)
    finally:
        conn.close()

    return inserted


def main():
    parser = argparse.ArgumentParser(description="Backfill DVOL history from Deribit")
    parser.add_argument("--assets", nargs="+", choices=["BTC", "ETH"],
                        default=["BTC", "ETH"])
    args = parser.parse_args()

    total = 0
    for asset in args.assets:
        total += backfill_asset(asset)

    logger.info("Backfill complete. Total rows inserted: %d", total)

    # Verification
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT asset, COUNT(*), MIN(timestamp)::date, MAX(timestamp)::date "
            "FROM dvol_history GROUP BY asset ORDER BY asset"
        )
        print("\n-- DVOL History Summary --")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]} rows  |  {row[2]} to {row[3]}")
    conn.close()


if __name__ == "__main__":
    main()
