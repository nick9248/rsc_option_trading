"""
On-Chain Volatility Metrics Reconstruction Backfill Script

Reconstructs volatility-surface, VRP, IV-percentile, and expected-move metrics
that the live GUI report computes on-the-fly but never persisted historically
(see TASKS.md Track B). Re-runs the existing calculator classes
(VolatilitySurfaceCalculator, VRPCalculator) against hourly_snapshots /
historical_trades / dvol_history / ohlcv_history and saves the results into
onchain_volatility_snapshots — a companion table joined to
onchain_analysis_snapshots on (snapshot_hour, currency, expiration).

Defaults to the clean 83-day window Track A identified (2026-03-16 -> 2026-06-06):
the only span with verified, gap-free 24/24-hour coverage in
onchain_analysis_snapshots — see TASKS.md Track A4 for why the 19 pre-VPS-migration
days (2026-02-07 -> 2026-03-15) are excluded from the backtest dataset.

Safe to re-run — save_volatility_snapshot uses ON CONFLICT DO UPDATE.

Usage:
    python -m scripts.backfill_volatility_reconstruction
    python -m scripts.backfill_volatility_reconstruction --currency BTC
    python -m scripts.backfill_volatility_reconstruction --start 2026-03-16 --end 2026-06-06
"""
import argparse
import logging
from datetime import datetime

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.volatility_reconstruction_service import VolatilityReconstructionService

init_logging(level="INFO")
logger = logging.getLogger(__name__)

# Clean, gap-free window confirmed in TASKS.md Track A (83 consecutive days,
# exactly 24/24 hours every day, zero exceptions — verified 2026-06-08).
DEFAULT_START = datetime(2026, 3, 16, 0, 0, 0)
DEFAULT_END = datetime(2026, 6, 6, 23, 0, 0)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill reconstructed on-chain volatility metrics into onchain_volatility_snapshots"
    )
    parser.add_argument("--currency", type=str, default=None, help="Single currency (BTC or ETH). Default: both.")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD (default: 2026-03-16)")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (default: 2026-06-06)")
    args = parser.parse_args()

    currencies = [args.currency.upper()] if args.currency else ["BTC", "ETH"]
    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else DEFAULT_START
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23) if args.end else DEFAULT_END

    logger.info("=" * 70)
    logger.info("VOLATILITY METRICS RECONSTRUCTION BACKFILL STARTING")
    logger.info(f"  Currencies: {currencies}")
    logger.info(f"  Range: {start} -> {end}")
    logger.info("=" * 70)

    repo = DatabaseRepository()
    service = VolatilityReconstructionService(repo)

    grand_totals = {"pairs_found": 0, "rows_saved": 0, "rows_skipped": 0, "percentile_updated": 0}
    for currency in currencies:
        logger.info(f"--- {currency} ---")
        result = service.reconstruct_range(
            currency=currency,
            start=start,
            end=end,
            progress_callback=logger.info,
        )
        for key in grand_totals:
            grand_totals[key] += result[key]

    logger.info("=" * 70)
    logger.info("BACKFILL COMPLETE")
    logger.info(f"  (hour, expiration) slices found: {grand_totals['pairs_found']}")
    logger.info(f"  Rows saved:                      {grand_totals['rows_saved']}")
    logger.info(f"  Rows skipped (no instrument data): {grand_totals['rows_skipped']}")
    logger.info(f"  Rows updated with iv_percentile_expiry: {grand_totals['percentile_updated']}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
