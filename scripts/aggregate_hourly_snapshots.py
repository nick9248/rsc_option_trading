"""
Aggregate historical trades into hourly snapshots.

Thin CLI wrapper around HourlyAggregationService.
Creates hourly snapshots from historical trades for ML training.

Usage:
    python -m scripts.aggregate_hourly_snapshots --currency BTC
    python -m scripts.aggregate_hourly_snapshots --currency ETH --status-only
"""

import argparse
import logging

from coding.core.logging.logging_setup import init_logging
from coding.service.data_collection.hourly_aggregation_service import HourlyAggregationService

# Initialize logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)


def main():
    """Main aggregation execution."""
    parser = argparse.ArgumentParser(
        description="Aggregate historical trades into hourly snapshots"
    )
    parser.add_argument(
        "--currency",
        type=str,
        required=True,
        choices=["BTC", "ETH"],
        help="Currency to aggregate"
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only show current aggregation status"
    )

    args = parser.parse_args()

    # Initialize service
    service = HourlyAggregationService()

    if args.status_only:
        # Show status only
        status = service.get_aggregation_status(args.currency)
        logger.info(f"Aggregation status for {args.currency}:")
        logger.info(f"  Total snapshots: {status['total_snapshots']:,}")
        logger.info(f"  Earliest: {status['earliest']}")
        logger.info(f"  Latest: {status['latest']}")
        logger.info(f"  Unique instruments: {status['unique_instruments']}")
        logger.info(f"  Avg trades/hour: {status['avg_trades_per_hour']:.1f}")
        logger.info(f"  Greeks coverage: {status['greeks_coverage_percent']:.2f}%")
        return

    # Run aggregation
    logger.info(f"=== Hourly Aggregation ===")
    logger.info(f"Currency: {args.currency}")
    logger.info(f"===========================")

    stats = service.aggregate_date_range(currency=args.currency)

    # Show final status
    status = service.get_aggregation_status(args.currency)
    logger.info(f"=== Final Status ===")
    logger.info(f"Total snapshots: {status['total_snapshots']:,}")
    logger.info(f"Greeks coverage: {status['greeks_coverage_percent']:.2f}%")
    logger.info(f"Avg trades/hour: {status['avg_trades_per_hour']:.1f}")


if __name__ == "__main__":
    main()
