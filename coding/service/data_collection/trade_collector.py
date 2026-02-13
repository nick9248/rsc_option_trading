"""
Continuous trade collector daemon for Deribit options.

Collects trade data every 1-5 minutes to ensure comprehensive coverage
with minimal gaps. Uses time-range endpoint for reliable data capture.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class TradeCollector:
    """
    Continuous collector for trade data with gap detection and recovery.
    """

    def __init__(
        self,
        api_service: DeribitApiService,
        repository: DatabaseRepository,
        collection_interval_seconds: int = 60,  # 1 minute default
        lookback_minutes: int = 5  # Collect last 5 minutes to handle any gaps
    ):
        """
        Initialize trade collector.

        Args:
            api_service: Deribit API service instance.
            repository: Database repository for storing trades.
            collection_interval_seconds: How often to collect (default 60s).
            lookback_minutes: How far back to look each collection (default 5 min).
        """
        self.api_service = api_service
        self.repository = repository
        self.collection_interval = collection_interval_seconds
        self.lookback_minutes = lookback_minutes
        self.running = False
        self.stats = {
            "total_collections": 0,
            "total_trades_collected": 0,
            "total_trades_stored": 0,
            "last_collection_time": None,
            "errors": 0
        }

    def start(self, currencies: List[str] = ["BTC", "ETH"], duration_hours: Optional[int] = None):
        """
        Start the continuous collection daemon.

        Args:
            currencies: List of currencies to collect (default: BTC and ETH).
            duration_hours: Optional duration to run in hours. If None, runs indefinitely.
        """
        self.running = True
        start_time = datetime.now()
        end_time = start_time + timedelta(hours=duration_hours) if duration_hours else None

        logger.info("="*70)
        logger.info("TRADE COLLECTOR DAEMON STARTING")
        logger.info("="*70)
        logger.info(f"Currencies: {', '.join(currencies)}")
        logger.info(f"Collection interval: {self.collection_interval}s")
        logger.info(f"Lookback window: {self.lookback_minutes} minutes")
        logger.info(f"Duration: {'Indefinite' if not end_time else f'{duration_hours} hours'}")
        logger.info("="*70)

        try:
            while self.running:
                # Check if duration limit reached
                if end_time and datetime.now() >= end_time:
                    logger.info("Duration limit reached. Stopping collector.")
                    break

                # Collect for each currency
                for currency in currencies:
                    try:
                        self._collect_currency(currency)
                    except Exception as e:
                        logger.error(f"Error collecting {currency}: {e}")
                        self.stats["errors"] += 1

                # Log stats periodically
                if self.stats["total_collections"] % 10 == 0:
                    self._log_stats()

                # Sleep until next collection
                time.sleep(self.collection_interval)

        except KeyboardInterrupt:
            logger.info("\nKeyboard interrupt received. Stopping collector...")
        finally:
            self.stop()

    def stop(self):
        """Stop the collector and log final stats."""
        self.running = False
        logger.info("="*70)
        logger.info("TRADE COLLECTOR DAEMON STOPPED")
        logger.info("="*70)
        self._log_stats()

    def _collect_currency(self, currency: str):
        """
        Collect trades for a single currency.

        Args:
            currency: Currency symbol (BTC or ETH).
        """
        # Calculate time window
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=self.lookback_minutes)

        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        logger.debug(f"Collecting {currency} trades from {start_time} to {end_time}")

        # Fetch trades
        result = self.api_service.get_last_trades_by_currency_and_time(
            currency=currency,
            kind="option",
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            count=1000,
            include_old=True
        )

        trades = result.get("trades", [])
        has_more = result.get("has_more", False)

        # Handle pagination if needed
        all_trades = trades.copy()
        while has_more and len(trades) > 0:
            # Use last trade timestamp as new start
            last_ts = trades[-1]["timestamp"]
            logger.debug(f"Pagination: fetching more trades after {datetime.fromtimestamp(last_ts/1000)}")

            result = self.api_service.get_last_trades_by_currency_and_time(
                currency=currency,
                kind="option",
                start_timestamp=last_ts + 1,  # +1 to avoid duplicate
                end_timestamp=end_ts,
                count=1000,
                include_old=True
            )

            trades = result.get("trades", [])
            has_more = result.get("has_more", False)
            all_trades.extend(trades)

        # Store trades
        stored_count = self._store_trades(all_trades, currency)

        # Update stats
        self.stats["total_collections"] += 1
        self.stats["total_trades_collected"] += len(all_trades)
        self.stats["total_trades_stored"] += stored_count
        self.stats["last_collection_time"] = datetime.now()

        logger.info(f"{currency}: Collected {len(all_trades)} trades, stored {stored_count} new")

    def _store_trades(self, trades: List[Dict], currency: str) -> int:
        """
        Store trades in database with deduplication.

        Args:
            trades: List of trade dictionaries from API.
            currency: Currency symbol.

        Returns:
            Number of trades actually stored (excludes duplicates).
        """
        if not trades:
            return 0

        stored_count = 0

        for trade in trades:
            try:
                # Parse instrument name
                instrument_name = trade.get("instrument_name", "")
                parts = instrument_name.split("-")

                expiration = parts[1] if len(parts) > 1 else None
                strike = float(parts[2]) if len(parts) > 2 else None
                option_type = parts[3] if len(parts) > 3 else None

                # Prepare trade data
                trade_data = {
                    "trade_id": trade.get("trade_id"),
                    "trade_seq": trade.get("trade_seq"),
                    "trade_timestamp": trade.get("timestamp"),
                    "instrument_name": instrument_name,
                    "currency": currency,
                    "expiration": expiration,
                    "strike": strike,
                    "option_type": option_type,
                    "price": trade.get("price"),
                    "amount": trade.get("amount"),
                    "direction": trade.get("direction"),
                    "iv": trade.get("iv"),
                    "mark_price": trade.get("mark_price"),
                    "index_price": trade.get("index_price")
                }

                # Insert with ON CONFLICT DO NOTHING (deduplication by trade_id)
                query = """
                    INSERT INTO historical_trades (
                        trade_id, trade_seq, trade_timestamp, instrument_name,
                        currency, expiration, strike, option_type,
                        price, amount, direction, iv, mark_price, index_price
                    ) VALUES (
                        %(trade_id)s, %(trade_seq)s, %(trade_timestamp)s, %(instrument_name)s,
                        %(currency)s, %(expiration)s, %(strike)s, %(option_type)s,
                        %(price)s, %(amount)s, %(direction)s, %(iv)s, %(mark_price)s, %(index_price)s
                    )
                    ON CONFLICT (trade_id) DO NOTHING
                    RETURNING trade_id
                """

                result = self.repository.execute_query(query, trade_data)
                if result:  # If INSERT returned a row, it was new
                    stored_count += 1

            except Exception as e:
                logger.error(f"Error storing trade {trade.get('trade_id')}: {e}")
                continue

        return stored_count

    def _log_stats(self):
        """Log current collector statistics."""
        logger.info("-"*70)
        logger.info("COLLECTOR STATISTICS")
        logger.info(f"  Total collections: {self.stats['total_collections']}")
        logger.info(f"  Total trades collected: {self.stats['total_trades_collected']}")
        logger.info(f"  Total trades stored (new): {self.stats['total_trades_stored']}")
        logger.info(f"  Duplicate rate: {self._calculate_duplicate_rate():.1f}%")
        logger.info(f"  Errors: {self.stats['errors']}")
        logger.info(f"  Last collection: {self.stats['last_collection_time']}")
        logger.info("-"*70)

    def _calculate_duplicate_rate(self) -> float:
        """Calculate percentage of duplicate trades."""
        if self.stats["total_trades_collected"] == 0:
            return 0.0

        duplicates = self.stats["total_trades_collected"] - self.stats["total_trades_stored"]
        return (duplicates / self.stats["total_trades_collected"]) * 100


def main():
    """
    Run the trade collector as a standalone daemon.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Continuous trade collector for Deribit options")
    parser.add_argument("--currencies", nargs="+", default=["BTC", "ETH"], help="Currencies to collect")
    parser.add_argument("--interval", type=int, default=60, help="Collection interval in seconds")
    parser.add_argument("--lookback", type=int, default=5, help="Lookback window in minutes")
    parser.add_argument("--duration", type=int, default=None, help="Duration to run in hours (default: indefinite)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize services
    with DeribitApiService() as api_service:
        repository = DatabaseRepository()
        collector = TradeCollector(
            api_service=api_service,
            repository=repository,
            collection_interval_seconds=args.interval,
            lookback_minutes=args.lookback
        )

        # Start collecting
        collector.start(currencies=args.currencies, duration_hours=args.duration)


if __name__ == "__main__":
    main()
