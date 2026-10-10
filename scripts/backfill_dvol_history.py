#!/usr/bin/env python
"""
Refresh the whole DVOL history from Deribit into dvol_history.

dvol_history holds one row per (asset, hour): the candle's start (UTC) and its
FINAL close. This script refetches the full history, inserts hours that are
missing and corrects rows whose value differs (for example values captured while
a candle was still forming). Only completed candles are written. Safe to re-run.

Usage:
    python scripts/backfill_dvol_history.py
    python scripts/backfill_dvol_history.py --assets BTC   (BTC only)
"""
import logging
import os
import sys
import argparse
from datetime import datetime, timezone

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from coding.core.logging.logging_setup import init_logging
init_logging(level="INFO")

import psycopg2
from coding.core.database.config import DatabaseConfig
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.dvol_fetcher import DVOLFetcher

logger = logging.getLogger(__name__)


def get_connection():
    cfg = DatabaseConfig()
    return psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.database,
        user=cfg.user, password=cfg.password
    )


def backfill_asset(asset: str, months: int = 40) -> int:
    """Refetch and store DVOL history for one asset. Returns rows inserted or corrected."""
    fetcher = DVOLFetcher()
    logger.info("Fetching %d months of DVOL history for %s...", months, asset)
    rows = fetcher.fetch_history(asset, months=months)
    if not rows:
        logger.error("No data returned for %s — aborting", asset)
        return 0

    # The candle still forming has a provisional close: never store it.
    current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    completed = [row for row in rows if row[0] < current_hour]
    logger.info("Received %d rows for %s (%d completed candles)", len(rows), asset, len(completed))

    changed = DatabaseRepository().save_dvol_candles(asset, completed)
    logger.info("%s: %d rows inserted or corrected, %d unchanged", asset, changed, len(completed) - changed)
    return changed


def main():
    parser = argparse.ArgumentParser(description="Refresh DVOL history from Deribit")
    parser.add_argument("--assets", nargs="+", choices=["BTC", "ETH"],
                        default=["BTC", "ETH"])
    args = parser.parse_args()

    total = 0
    for asset in args.assets:
        total += backfill_asset(asset)

    logger.info("Refresh complete. Total rows inserted or corrected: %d", total)

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
