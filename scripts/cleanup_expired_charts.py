"""
Cleanup script for output/charts/ directory.

Fetches active expiration dates from Deribit API for BTC and ETH,
then removes all chart folders whose expiration is no longer available.

Logic:
- Active = listed by Deribit API for BTC OR ETH
- Delete = expiration folder NOT in the active set (either expired or delisted)
- Dry-run mode (default) prints what would be deleted without removing anything
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService

init_logging(level="INFO")
logger = logging.getLogger(__name__)

CHARTS_DIR = Path(__file__).parent.parent / "output" / "charts"
CURRENCIES = ["BTC", "ETH"]


def get_active_expirations(api_service: DeribitApiService) -> set[str]:
    """Fetch active expirations from Deribit for all configured currencies."""
    active = set()
    for currency in CURRENCIES:
        try:
            data = api_service.get_expirations(currency=currency)
            currency_data = data.get(currency.lower(), {})
            option_expirations = currency_data.get("option", [])
            active.update(option_expirations)
            logger.info(f"{currency}: {len(option_expirations)} active expirations")
        except Exception as e:
            logger.error(f"Failed to fetch expirations for {currency}: {e}")
    logger.info(f"Total active expirations across all currencies: {len(active)}")
    return active


def get_chart_type_dirs() -> list[Path]:
    """Return all subdirectories of CHARTS_DIR (each is a chart type like gex_dex, snapshot)."""
    return [d for d in CHARTS_DIR.iterdir() if d.is_dir()]


def cleanup_charts(dry_run: bool = True) -> None:
    """
    Remove chart folders for unavailable expirations.

    Args:
        dry_run: If True, only print what would be deleted. Default True.
    """
    if not CHARTS_DIR.exists():
        logger.error(f"Charts directory not found: {CHARTS_DIR}")
        return

    logger.info(f"Charts directory: {CHARTS_DIR}")
    logger.info(f"Mode: {'DRY RUN (no files deleted)' if dry_run else 'LIVE (files will be deleted)'}")

    api_service = DeribitApiService()
    active_expirations = get_active_expirations(api_service)

    if not active_expirations:
        logger.error("Could not retrieve active expirations from API. Aborting to prevent accidental deletion.")
        return

    chart_type_dirs = get_chart_type_dirs()
    logger.info(f"Chart types found: {[d.name for d in chart_type_dirs]}")

    total_to_delete = 0
    total_size_bytes = 0

    for chart_type_dir in sorted(chart_type_dirs):
        expiration_dirs = [d for d in chart_type_dir.iterdir() if d.is_dir()]
        to_delete = [d for d in expiration_dirs if d.name not in active_expirations]
        to_keep = [d for d in expiration_dirs if d.name in active_expirations]

        logger.info(
            f"\n[{chart_type_dir.name}] "
            f"{len(expiration_dirs)} total | "
            f"{len(to_keep)} keep | "
            f"{len(to_delete)} delete"
        )

        for exp_dir in sorted(to_delete):
            dir_size = sum(f.stat().st_size for f in exp_dir.rglob("*") if f.is_file())
            total_size_bytes += dir_size
            total_to_delete += 1

            size_mb = dir_size / (1024 * 1024)
            logger.info(f"  {'[DRY RUN] Would delete' if dry_run else 'Deleting'}: {exp_dir.name} ({size_mb:.1f} MB)")

            if not dry_run:
                shutil.rmtree(exp_dir)
                logger.info(f"  Deleted: {exp_dir}")

    total_size_gb = total_size_bytes / (1024 ** 3)
    action = "Would free" if dry_run else "Freed"
    logger.info(
        f"\nSummary: {total_to_delete} folders {'would be' if dry_run else ''} deleted | "
        f"{action} ~{total_size_gb:.2f} GB"
    )

    if dry_run and total_to_delete > 0:
        logger.info("\nRun with dry_run=False to execute deletion:")
        logger.info("  python -m scripts.cleanup_expired_charts --execute")


if __name__ == "__main__":
    import sys

    dry_run = "--execute" not in sys.argv
    cleanup_charts(dry_run=dry_run)
