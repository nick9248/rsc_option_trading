"""
Historical trades backfill script.

Downloads historical option trades from Deribit API (6-12 months).
Calculates Greeks using Black-Scholes and aggregates into hourly snapshots.

Usage:
    python -m scripts.backfill_historical_trades --months 6 --currency BTC
"""

import argparse
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from coding.core.database.database_config import get_connection_pool
from coding.service.deribit.deribit_api_service import DeribitAPIService
from coding.core.logging.logging_setup import init_logging

# Initialize logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)


class HistoricalBackfillService:
    """Service for backfilling historical trades from Deribit API."""

    def __init__(self, api_service: DeribitAPIService):
        """
        Initialize backfill service.

        Args:
            api_service: Deribit API service instance
        """
        self.api_service = api_service
        self.connection_pool = get_connection_pool()

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
        Fetch trades for a specific time range.

        Args:
            currency: Currency (BTC or ETH)
            start_timestamp: Start timestamp in milliseconds
            end_timestamp: End timestamp in milliseconds

        Returns:
            List of trade dictionaries
        """
        try:
            response = self.api_service.get_last_trades_by_currency(
                currency=currency,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                count=1000  # Max per request
            )

            # Extract trades from response
            if isinstance(response, dict) and "result" in response:
                trades = response["result"].get("trades", [])
                return trades
            elif isinstance(response, list):
                return response
            else:
                logger.warning(f"Unexpected response format: {type(response)}")
                return []

        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            raise

    def _store_trades(self, trades: List[Dict], currency: str) -> int:
        """
        Store trades in database with deduplication.

        Args:
            trades: List of trade dictionaries
            currency: Currency (BTC or ETH)

        Returns:
            Number of trades stored (excluding duplicates)
        """
        if not trades:
            return 0

        stored_count = 0
        connection = self.connection_pool.getconn()

        try:
            cursor = connection.cursor()

            for trade in trades:
                try:
                    # Extract fields
                    trade_id = trade.get("trade_id")
                    instrument_name = trade.get("instrument_name")
                    price = trade.get("price")
                    amount = trade.get("amount")
                    direction = trade.get("direction")
                    timestamp = trade.get("timestamp")
                    iv = trade.get("iv")
                    index_price = trade.get("index_price")
                    mark_price = trade.get("mark_price")

                    # Insert with ON CONFLICT DO NOTHING (deduplication)
                    cursor.execute(
                        """
                        INSERT INTO historical_trades (
                            trade_id, instrument_name, price, amount, direction,
                            timestamp, iv, index_price, mark_price, currency
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trade_id, timestamp) DO NOTHING
                        """,
                        (
                            trade_id, instrument_name, price, amount, direction,
                            datetime.fromtimestamp(timestamp / 1000),
                            iv, index_price, mark_price, currency
                        )
                    )

                    if cursor.rowcount > 0:
                        stored_count += 1

                except Exception as e:
                    logger.warning(f"Error storing trade {trade.get('trade_id')}: {e}")
                    continue

            connection.commit()
            cursor.close()

        except Exception as e:
            logger.error(f"Database error: {e}")
            connection.rollback()
            raise

        finally:
            self.connection_pool.putconn(connection)

        return stored_count

    def get_backfill_status(self, currency: str) -> Dict:
        """
        Get status of backfill for a currency.

        Args:
            currency: Currency (BTC or ETH)

        Returns:
            Status dictionary with earliest/latest dates and trade count
        """
        connection = self.connection_pool.getconn()

        try:
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_trades,
                    MIN(timestamp) as earliest_trade,
                    MAX(timestamp) as latest_trade,
                    COUNT(DISTINCT instrument_name) as unique_instruments,
                    COUNT(CASE WHEN iv IS NOT NULL THEN 1 END) * 100.0 / COUNT(*) as iv_coverage
                FROM historical_trades
                WHERE currency = %s
                """,
                (currency,)
            )

            row = cursor.fetchone()
            cursor.close()

            return {
                "currency": currency,
                "total_trades": row[0],
                "earliest_trade": row[1],
                "latest_trade": row[2],
                "unique_instruments": row[3],
                "iv_coverage_percent": float(row[4]) if row[4] else 0.0
            }

        finally:
            self.connection_pool.putconn(connection)


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
    api_service = DeribitAPIService()
    backfill_service = HistoricalBackfillService(api_service)

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
