"""
Master script to run full historical backfill pipeline.

Executes all three stages:
1. Download historical trades from Deribit API
2. Calculate Greeks using Black-Scholes
3. Aggregate into hourly snapshots

Usage:
    python -m scripts.run_full_backfill --currency BTC --months 6
"""

import argparse
import logging
from datetime import datetime
import subprocess
import sys

from coding.core.logging.logging_setup import init_logging

# Initialize logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)


def run_command(description: str, command: list) -> bool:
    """
    Run a command and log results.

    Args:
        description: Human-readable description
        command: Command list for subprocess

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"=== {description} ===")

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True
        )

        # Log stdout
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                logger.info(line)

        logger.info(f"✓ {description} completed successfully")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"✗ {description} failed")
        if e.stdout:
            logger.error(f"STDOUT: {e.stdout}")
        if e.stderr:
            logger.error(f"STDERR: {e.stderr}")
        return False


def main():
    """Execute full backfill pipeline."""
    parser = argparse.ArgumentParser(
        description="Run full historical backfill pipeline"
    )
    parser.add_argument(
        "--currency",
        type=str,
        required=True,
        choices=["BTC", "ETH", "both"],
        help="Currency to backfill (or 'both')"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="Number of months to backfill (default: 6)"
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download step (use existing trades)"
    )
    parser.add_argument(
        "--skip-aggregation",
        action="store_true",
        help="Skip aggregation step"
    )

    args = parser.parse_args()

    # Determine currencies
    currencies = ["BTC", "ETH"] if args.currency == "both" else [args.currency]

    logger.info(f"")
    logger.info(f"╔════════════════════════════════════════════════════════╗")
    logger.info(f"║   HISTORICAL DATA BACKFILL PIPELINE                    ║")
    logger.info(f"╠════════════════════════════════════════════════════════╣")
    logger.info(f"║ Currencies: {', '.join(currencies):<42} ║")
    logger.info(f"║ Period:     {args.months} months{' ' * 36} ║")
    logger.info(f"║ Started:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<42} ║")
    logger.info(f"╚════════════════════════════════════════════════════════╝")
    logger.info(f"")

    pipeline_start = datetime.now()
    success_count = 0
    total_steps = 0

    for currency in currencies:
        logger.info(f"")
        logger.info(f"{'='*60}")
        logger.info(f"Processing {currency}")
        logger.info(f"{'='*60}")
        logger.info(f"")

        # Stage 1: Download historical trades
        if not args.skip_download:
            total_steps += 1
            success = run_command(
                f"Stage 1: Download {currency} trades",
                [
                    sys.executable, "-m", "scripts.backfill_historical_trades",
                    "--currency", currency,
                    "--months", str(args.months)
                ]
            )
            if success:
                success_count += 1
            else:
                logger.error(f"Skipping remaining stages for {currency}")
                continue

            # Show download status
            total_steps += 1
            success = run_command(
                f"Stage 1 Status: {currency} trades",
                [
                    sys.executable, "-m", "scripts.backfill_historical_trades",
                    "--currency", currency,
                    "--status-only"
                ]
            )
            if success:
                success_count += 1

        # Stage 2: Aggregate into hourly snapshots
        if not args.skip_aggregation:
            total_steps += 1
            success = run_command(
                f"Stage 2: Aggregate {currency} hourly snapshots",
                [
                    sys.executable, "-m", "scripts.aggregate_hourly_snapshots",
                    "--currency", currency
                ]
            )
            if success:
                success_count += 1
            else:
                logger.error(f"Aggregation failed for {currency}")
                continue

            # Show aggregation status
            total_steps += 1
            success = run_command(
                f"Stage 2 Status: {currency} snapshots",
                [
                    sys.executable, "-m", "scripts.aggregate_hourly_snapshots",
                    "--currency", currency,
                    "--status-only"
                ]
            )
            if success:
                success_count += 1

    # Final summary
    pipeline_end = datetime.now()
    elapsed = (pipeline_end - pipeline_start).total_seconds()

    logger.info(f"")
    logger.info(f"╔════════════════════════════════════════════════════════╗")
    logger.info(f"║   PIPELINE COMPLETE                                    ║")
    logger.info(f"╠════════════════════════════════════════════════════════╣")
    logger.info(f"║ Status:     {success_count}/{total_steps} steps successful{' ' * (30 - len(str(success_count)) - len(str(total_steps)))} ║")
    logger.info(f"║ Duration:   {elapsed/60:.1f} minutes{' ' * 37} ║")
    logger.info(f"║ Completed:  {pipeline_end.strftime('%Y-%m-%d %H:%M:%S'):<42} ║")
    logger.info(f"╚════════════════════════════════════════════════════════╝")
    logger.info(f"")

    if success_count == total_steps:
        logger.info("✓ All stages completed successfully!")
        sys.exit(0)
    else:
        logger.error(f"✗ {total_steps - success_count} stages failed")
        logger.error("Check logs above for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
