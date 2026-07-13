"""
Unified Hourly Aggregation Service.

Single source of truth for aggregating historical trades into hourly snapshots.
Used by both the collection daemon (ProspectiveCollector) and the standalone
aggregation script (scripts/aggregate_hourly_snapshots.py).

Produces hourly snapshots with:
- VWAP and volume statistics from trades
- Bid/ask estimates from trade directions
- Greeks calculated via Black-Scholes from average IV
- Open interest enriched from the snapshots (book_summary) table
- Strike, expiration, option_type parsed from instrument name
- Pydantic validation on every snapshot
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.database.repository import DatabaseRepository
from coding.core.schemas.snapshot_models import GreeksData, HourlySnapshotData

logger = logging.getLogger(__name__)


class HourlyAggregationService:
    """
    Aggregates historical trades into hourly snapshots with Greeks.

    This is the single implementation used by both the daemon's
    ProspectiveCollector and the standalone aggregation script.
    """

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Initialize aggregation service.

        Args:
            repository: Database repository instance. Creates new if None.
        """
        self.repo = repository or DatabaseRepository()
        self.bs_calculator = BlackScholesCalculator()

    def aggregate_unaggregated_hours(self, currency: str) -> Dict:
        """
        Find and aggregate all hours that have trades but no hourly snapshots.

        This is the core fix: instead of only aggregating the current hour,
        it discovers ALL gaps and fills them.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            Statistics dictionary with hours_processed, snapshots_created, errors.
        """
        hours = self.repo.get_unaggregated_hours(currency)

        stats = {
            "hours_found": len(hours),
            "hours_processed": 0,
            "snapshots_created": 0,
            "errors": []
        }

        if not hours:
            logger.info(f"No unaggregated hours found for {currency}")
            return stats

        logger.info(f"Found {len(hours)} unaggregated hours for {currency}")

        for hour_start in hours:
            try:
                snapshots = self.aggregate_hour(currency, hour_start)
                stats["hours_processed"] += 1
                stats["snapshots_created"] += len(snapshots)
            except Exception as e:
                logger.error(f"Error aggregating {currency} hour {hour_start}: {e}")
                stats["errors"].append(f"{hour_start}: {str(e)}")

        logger.info(
            f"Aggregation complete for {currency}: "
            f"{stats['hours_processed']} hours, "
            f"{stats['snapshots_created']} snapshots"
        )
        return stats

    def aggregate_hour(self, currency: str, hour_start: datetime) -> List[Dict]:
        """
        Aggregate all trades for one hour into hourly snapshots.

        Steps:
        1. Fetch trades from repository
        2. Group by instrument
        3. Aggregate each instrument (VWAP, bid/ask, Greeks)
        4. Enrich with OI from snapshots table
        5. Validate with Pydantic
        6. Store via repository

        Args:
            currency: Currency symbol.
            hour_start: Start of the hour bucket (naive UTC).

        Returns:
            List of stored snapshot dictionaries.
        """
        # Make hour_start naive if it has timezone info
        if hour_start.tzinfo is not None:
            hour_start = hour_start.replace(tzinfo=None)

        hour_end = hour_start + timedelta(hours=1)

        # 1. Fetch trades
        trades = self.repo.get_trades_for_hour(currency, hour_start, hour_end)

        if not trades:
            return []

        # 2. Group by instrument
        instruments = {}
        for trade in trades:
            instrument_name = trade[0]
            if instrument_name not in instruments:
                instruments[instrument_name] = []
            instruments[instrument_name].append(trade)

        # 3. Get OI from snapshots table for enrichment
        oi_map = self.repo.get_latest_snapshot_oi(currency, hour_start)

        # 4. Aggregate each instrument
        snapshots = []
        for instrument_name, instrument_trades in instruments.items():
            snapshot = self._aggregate_instrument(
                currency, instrument_name, instrument_trades,
                hour_start, oi_map.get(instrument_name)
            )
            if snapshot:
                snapshots.append(snapshot)

        # 5. Store via repository
        if snapshots:
            self.repo.save_hourly_snapshots(snapshots)

        logger.info(
            f"Aggregated {len(snapshots)} instruments for {currency} "
            f"hour {hour_start}"
        )
        return snapshots

    def aggregate_date_range(
        self,
        currency: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """
        Aggregate a date range (for standalone script / backfill).

        If start_date/end_date are not provided, uses the full range
        of trades in the database.

        Args:
            currency: Currency symbol.
            start_date: Start of range (None = earliest trade).
            end_date: End of range (None = latest trade).

        Returns:
            Statistics dictionary.
        """
        # Get date range from database if not specified
        if not start_date or not end_date:
            date_range = self._get_trade_date_range(currency)
            start_date = start_date or date_range["earliest"]
            end_date = end_date or date_range["latest"]

        if not start_date or not end_date:
            logger.error(f"No trades found for {currency}")
            return {"snapshots_created": 0, "hours_processed": 0}

        logger.info(f"Aggregating {currency} from {start_date} to {end_date}")

        stats = {
            "snapshots_created": 0,
            "hours_processed": 0,
            "start_time": datetime.now()
        }

        current_hour = start_date.replace(minute=0, second=0, microsecond=0)
        end_hour = end_date.replace(minute=0, second=0, microsecond=0)

        while current_hour <= end_hour:
            try:
                snapshots = self.aggregate_hour(currency, current_hour)
                stats["snapshots_created"] += len(snapshots)
                stats["hours_processed"] += 1

                # Log progress every 24 hours
                if stats["hours_processed"] % 24 == 0:
                    elapsed = (datetime.now() - stats["start_time"]).total_seconds()
                    rate = stats["snapshots_created"] / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"Progress: {stats['hours_processed']} hours, "
                        f"{stats['snapshots_created']} snapshots, "
                        f"{rate:.1f} snapshots/sec"
                    )

            except Exception as e:
                logger.error(f"Error processing hour {current_hour}: {e}")

            current_hour += timedelta(hours=1)

        elapsed = (datetime.now() - stats["start_time"]).total_seconds()
        logger.info(
            f"Aggregation complete: {stats['snapshots_created']} snapshots "
            f"in {elapsed:.1f}s ({stats['hours_processed']} hours)"
        )
        return stats

    def get_aggregation_status(self, currency: str) -> Dict:
        """
        Get status of hourly aggregation for a currency.

        Args:
            currency: Currency symbol.

        Returns:
            Status dictionary with counts, date range, and coverage.
        """
        conn = self.repo._get_connection()

        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_snapshots,
                    MIN(snapshot_hour) as earliest,
                    MAX(snapshot_hour) as latest,
                    COUNT(DISTINCT instrument_name) as unique_instruments,
                    AVG(trade_count) as avg_trades_per_hour,
                    CASE
                        WHEN COUNT(*) > 0 THEN
                            COUNT(CASE WHEN avg_delta IS NOT NULL THEN 1 END)
                            * 100.0 / COUNT(*)
                        ELSE 0
                    END as greeks_coverage
                FROM hourly_snapshots
                WHERE currency = %s
            """, (currency,))

            row = cursor.fetchone()

            return {
                "currency": currency,
                "total_snapshots": row[0],
                "earliest": row[1],
                "latest": row[2],
                "unique_instruments": row[3],
                "avg_trades_per_hour": float(row[4]) if row[4] else 0.0,
                "greeks_coverage_percent": float(row[5]) if row[5] else 0.0
            }

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _aggregate_instrument(
        self,
        currency: str,
        instrument_name: str,
        trades: List[tuple],
        hour_start: datetime,
        open_interest: Optional[float] = None
    ) -> Optional[Dict]:
        """
        Aggregate trades for a single instrument into one hourly snapshot.

        Calculates VWAP, bid/ask estimates, Greeks via Black-Scholes,
        and validates with Pydantic before returning.

        Args:
            currency: Currency symbol.
            instrument_name: Deribit instrument name (e.g., ETH-27MAR26-3400-C).
            trades: List of trade tuples from the database.
            hour_start: Hour bucket timestamp.
            open_interest: OI from snapshots table (may be None).

        Returns:
            Snapshot dictionary ready for database insertion, or None on failure.
        """
        if not trades:
            return None

        # Parse instrument name for strike/expiration/option_type
        parts = instrument_name.split("-")
        if len(parts) < 4:
            return None

        expiration = parts[1]
        try:
            strike = float(parts[2])
        except ValueError:
            return None
        option_type = parts[3][0].upper()

        # Calculate VWAP
        total_value = sum(float(t[1]) * float(t[2]) for t in trades)
        total_volume = sum(float(t[2]) for t in trades)
        vwap = total_value / total_volume if total_volume > 0 else 0.0

        # Average IV and index price
        ivs = [float(t[4]) for t in trades if t[4] is not None]
        avg_iv = sum(ivs) / len(ivs) if ivs else None

        index_prices = [float(t[5]) for t in trades if t[5] is not None]
        avg_index_price = sum(index_prices) / len(index_prices) if index_prices else None

        # Most recent mark price
        mark_price = float(trades[-1][6]) if trades[-1][6] is not None else vwap

        # Estimate bid/ask from trade directions
        buy_trades = [t for t in trades if t[3] == "buy"]
        sell_trades = [t for t in trades if t[3] == "sell"]

        ask_estimate = max((float(t[1]) for t in buy_trades), default=vwap * 1.005)
        bid_estimate = min((float(t[1]) for t in sell_trades), default=vwap * 0.995)

        # Ensure bid <= ask
        if bid_estimate > ask_estimate:
            bid_estimate, ask_estimate = ask_estimate, bid_estimate

        # Calculate Greeks using Black-Scholes
        greeks = None
        if avg_iv and avg_index_price:
            parsed = self.bs_calculator.parse_instrument_name(instrument_name)
            if parsed:
                hour_start_naive = (
                    hour_start.replace(tzinfo=None)
                    if hour_start.tzinfo else hour_start
                )
                time_to_expiry = self.bs_calculator.calculate_time_to_expiry(
                    hour_start_naive, parsed["expiry_time"]
                )
                if time_to_expiry > 0:
                    greeks_raw = self.bs_calculator.calculate_greeks(
                        spot_price=avg_index_price,
                        strike_price=parsed["strike"],
                        time_to_expiry=time_to_expiry,
                        implied_volatility=avg_iv / 100.0,
                        option_type=parsed["option_type"]
                    )
                    try:
                        greeks = GreeksData(**greeks_raw)
                    except Exception as e:
                        logger.warning(
                            f"Greeks validation failed for {instrument_name}: {e}"
                        )
                        greeks = None

        # Validate with Pydantic
        try:
            validated = HourlySnapshotData(
                currency=currency,
                instrument_name=instrument_name,
                timestamp=hour_start,
                mark_price=mark_price if mark_price > 0 else vwap,
                bid_price=bid_estimate if bid_estimate > 0 else vwap * 0.995,
                ask_price=ask_estimate if ask_estimate > 0 else vwap * 1.005,
                mark_iv=avg_iv,
                underlying_price=avg_index_price,
                volume=total_volume,
                trade_count=len(trades),
                delta=greeks.delta if greeks else None,
                gamma=greeks.gamma if greeks else None,
                theta=greeks.theta if greeks else None,
                vega=greeks.vega if greeks else None
            )
        except Exception as e:
            logger.warning(f"Snapshot validation failed for {instrument_name}: {e}")
            return None

        # Build the full snapshot dict matching all 22 database columns
        return {
            "snapshot_hour": hour_start,
            "captured_at": datetime.now(),
            "instrument_name": instrument_name,
            "currency": currency,
            "strike": strike,
            "expiration": expiration,
            "option_type": option_type,
            "trade_count": validated.trade_count,
            "total_volume": validated.volume,
            "vwap": vwap,
            "bid_price": validated.bid_price,
            "ask_price": validated.ask_price,
            "mark_price": validated.mark_price,
            "mark_iv": validated.mark_iv,
            "open_interest": open_interest,
            "index_price": validated.underlying_price,
            "futures_price": None,
            "basis": None,
            "avg_delta": validated.delta,
            "avg_gamma": validated.gamma,
            "avg_theta": validated.theta,
            "avg_vega": validated.vega
        }

    def _get_trade_date_range(self, currency: str) -> Dict[str, Optional[datetime]]:
        """
        Get earliest and latest trade timestamps for a currency.

        Args:
            currency: Currency symbol.

        Returns:
            Dictionary with 'earliest' and 'latest' datetimes.
        """
        conn = self.repo._get_connection()

        try:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    MIN(to_timestamp(trade_timestamp / 1000.0)),
                    MAX(to_timestamp(trade_timestamp / 1000.0))
                FROM historical_trades
                WHERE currency = %s
            """, (currency,))

            row = cursor.fetchone()
            return {
                "earliest": row[0],
                "latest": row[1]
            }

        finally:
            cursor.close()
            self.repo._return_connection(conn)
