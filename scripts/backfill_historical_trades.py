"""
Historical trades backfill script.

Downloads historical option trades from Deribit API for specified time periods.
Uses time-range endpoint with pagination for reliable data capture.

Usage:
    python -m scripts.backfill_historical_trades --months 6 --currency BTC
"""

import argparse
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List

from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

# Initialize logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)


class HistoricalBackfillService:
    """Service for backfilling historical trades from Deribit API."""

    def __init__(self, api_service: DeribitApiService, repository: DatabaseRepository):
        """
        Initialize backfill service.

        Args:
            api_service: Deribit API service instance
            repository: Database repository for storing trades
        """
        self.api_service = api_service
        self.repository = repository

    def backfill_trades(
        self,
        currency: str,
        start_date: datetime,
        end_date: datetime,
        batch_hours: int = 1
    ) -> Dict[str, int]:
        """
        Backfill historical trades for a currency within date range.

        Args:
            currency: Currency (BTC or ETH)
            start_date: Start of backfill period
            end_date: End of backfill period
            batch_hours: Number of hours per API call (default: 1)

        Returns:
            Statistics dict with trades_fetched, trades_stored, batches_processed
        """
        logger.info(f"Starting backfill for {currency}: {start_date} to {end_date}")

        stats = {
            "trades_fetched": 0,
            "trades_stored": 0,
            "batches_processed": 0,
            "batches_failed": 0,
            "start_time": datetime.now()
        }

        # Calculate time range in milliseconds
        current_time = start_date
        batch_delta = timedelta(hours=batch_hours)

        while current_time < end_date:
            batch_end = min(current_time + batch_delta, end_date)

            try:
                # Fetch trades for this time range
                trades = self._fetch_trades_batch(
                    currency=currency,
                    start_timestamp=int(current_time.timestamp() * 1000),
                    end_timestamp=int(batch_end.timestamp() * 1000)
                )

                if trades:
                    # Store trades in database
                    stored_count = self._store_trades(trades, currency)

                    stats["trades_fetched"] += len(trades)
                    stats["trades_stored"] += stored_count

                    logger.info(
                        f"Batch {stats['batches_processed'] + 1}: "
                        f"{current_time} - {batch_end} | "
                        f"Fetched: {len(trades)}, Stored: {stored_count}"
                    )
                else:
                    logger.debug(f"No trades found for {current_time} - {batch_end}")

                stats["batches_processed"] += 1

                # Rate limiting (Deribit has credit-based rate limits)
                # Wait 0.5s between requests to be safe
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error processing batch {current_time} - {batch_end}: {e}")
                stats["batches_failed"] += 1
                # Continue to next batch on error
                time.sleep(2)  # Longer wait on error

            # Move to next batch
            current_time = batch_end

            # Log progress every 24 batches (1 day if batch_hours=1)
            if stats["batches_processed"] % 24 == 0:
                elapsed = (datetime.now() - stats["start_time"]).total_seconds()
                rate = stats["trades_stored"] / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Progress: {stats['batches_processed']} batches, "
                    f"{stats['trades_stored']} trades stored, "
                    f"{rate:.1f} trades/sec"
                )

        # Final statistics
        elapsed = (datetime.now() - stats["start_time"]).total_seconds()
        logger.info(
            f"Backfill complete for {currency}: "
            f"{stats['trades_stored']} trades stored in {elapsed:.1f}s "
            f"({stats['batches_processed']} batches, {stats['batches_failed']} failed)"
        )

        return stats

    def _fetch_trades_batch(
        self,
        currency: str,
        start_timestamp: int,
        end_timestamp: int
    ) -> List[Dict]:
        """
        Fetch trades for a specific time range with pagination.

        Args:
            currency: Currency (BTC or ETH)
            start_timestamp: Start timestamp in milliseconds
            end_timestamp: End timestamp in milliseconds

        Returns:
            List of trade dictionaries
        """
        try:
            # Initial fetch
            result = self.api_service.get_last_trades_by_currency_and_time(
                currency=currency,
                kind="option",
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                count=1000,  # Max per request
                include_old=True
            )

            trades = result.get("trades", [])
            has_more = result.get("has_more", False)

            # Handle pagination
            all_trades = trades.copy()
            while has_more and len(trades) > 0:
                # Use last trade timestamp as new start
                last_ts = trades[-1]["timestamp"]
                logger.debug(f"Pagination: fetching more trades after {datetime.fromtimestamp(last_ts/1000)}")

                result = self.api_service.get_last_trades_by_currency_and_time(
                    currency=currency,
                    kind="option",
                    start_timestamp=last_ts + 1,  # +1 to avoid duplicate
                    end_timestamp=end_timestamp,
                    count=1000,
                    include_old=True
                )

                trades = result.get("trades", [])
                has_more = result.get("has_more", False)
                all_trades.extend(trades)

            return all_trades

        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            raise

    def _store_trades(self, trades: List[Dict], currency: str) -> int:
        """
        Store trades in database with deduplication.

        Args:
            trades: List of trade dictionaries from API
            currency: Currency symbol

        Returns:
            Number of trades actually stored (excludes duplicates)
        """
        if not trades:
            return 0

        stored_count = 0

        with self.repository._db_cursor() as cursor:
            for trade in trades:
                try:
                    # Parse instrument name
                    instrument_name = trade.get("instrument_name", "")
                    parts = instrument_name.split("-")

                    expiration = parts[1] if len(parts) > 1 else None
                    strike = float(parts[2]) if len(parts) > 2 else None
                    option_type = parts[3] if len(parts) > 3 else None

                    # Insert with ON CONFLICT DO NOTHING (deduplication by trade_id)
                    cursor.execute(
                        """
                        INSERT INTO historical_trades (
                            trade_id, trade_seq, trade_timestamp, instrument_name,
                            currency, expiration, strike, option_type,
                            price, amount, direction, iv, mark_price, index_price
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (trade_id) DO NOTHING
                        RETURNING trade_id
                        """,
                        (
                            trade.get("trade_id"),
                            trade.get("trade_seq"),
                            trade.get("timestamp"),
                            instrument_name,
                            currency,
                            expiration,
                            strike,
                            option_type,
                            trade.get("price"),
                            trade.get("amount"),
                            trade.get("direction"),
                            trade.get("iv"),
                            trade.get("mark_price"),
                            trade.get("index_price")
                        )
                    )

                    # Check if a row was returned (meaning it was inserted)
                    if cursor.fetchone() is not None:
                        stored_count += 1

                except Exception as e:
                    logger.error(f"Error storing trade {trade.get('trade_id')}: {e}")
                    continue

        return stored_count

    def get_backfill_status(self, currency: str) -> Dict:
        """
        Get status of backfill for a currency.

        Args:
            currency: Currency (BTC or ETH)

        Returns:
            Status dictionary with earliest/latest dates and trade count
        """
        with self.repository._db_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_trades,
                    MIN(trade_timestamp) as earliest_trade,
                    MAX(trade_timestamp) as latest_trade,
                    COUNT(DISTINCT instrument_name) as unique_instruments,
                    COUNT(CASE WHEN iv IS NOT NULL THEN 1 END) * 100.0 / COUNT(*) as iv_coverage
                FROM historical_trades
                WHERE currency = %s
                """,
                (currency,)
            )

            row = cursor.fetchone()

            if row and row[0] > 0:
                return {
                    "currency": currency,
                    "total_trades": row[0],
                    "earliest_trade": datetime.fromtimestamp(row[1] / 1000) if row[1] else None,
                    "latest_trade": datetime.fromtimestamp(row[2] / 1000) if row[2] else None,
                    "unique_instruments": row[3],
                    "iv_coverage_percent": float(row[4]) if row[4] else 0.0
                }
            else:
                return {
                    "currency": currency,
                    "total_trades": 0,
                    "earliest_trade": None,
                    "latest_trade": None,
                    "unique_instruments": 0,
                    "iv_coverage_percent": 0.0
                }


def main():
    """Main backfill execution."""
    parser = argparse.ArgumentParser(description="Backfill historical options trades from Deribit")
    parser.add_argument(
        "--currency",
        type=str,
        required=True,
        choices=["BTC", "ETH"],
        help="Currency to backfill"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="Number of months to backfill (default: 6)"
    )
    parser.add_argument(
        "--batch-hours",
        type=int,
        default=1,
        help="Hours per batch (default: 1)"
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only show current backfill status"
    )

    args = parser.parse_args()

    # Initialize services
    with DeribitApiService() as api_service:
        repository = DatabaseRepository()
        backfill_service = HistoricalBackfillService(api_service, repository)

        if args.status_only:
            # Show status only
            status = backfill_service.get_backfill_status(args.currency)
            logger.info(f"Backfill status for {args.currency}:")
            logger.info(f"  Total trades: {status['total_trades']:,}")
            logger.info(f"  Earliest: {status['earliest_trade']}")
            logger.info(f"  Latest: {status['latest_trade']}")
            logger.info(f"  Unique instruments: {status['unique_instruments']}")
            logger.info(f"  IV coverage: {status['iv_coverage_percent']:.2f}%")
            return

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.months * 30)

        logger.info(f"=== Historical Backfill ===")
        logger.info(f"Currency: {args.currency}")
        logger.info(f"Period: {start_date.date()} to {end_date.date()}")
        logger.info(f"Batch size: {args.batch_hours} hour(s)")
        logger.info(f"===========================")

        # Run backfill
        stats = backfill_service.backfill_trades(
            currency=args.currency,
            start_date=start_date,
            end_date=end_date,
            batch_hours=args.batch_hours
        )

        # Show final status
        status = backfill_service.get_backfill_status(args.currency)
        logger.info(f"=== Final Status ===")
        logger.info(f"Total trades: {status['total_trades']:,}")
        logger.info(f"IV coverage: {status['iv_coverage_percent']:.2f}%")
        logger.info(f"Date range: {status['earliest_trade']} to {status['latest_trade']}")


if __name__ == "__main__":
    main()
