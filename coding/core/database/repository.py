"""
Database repository for on-chain analysis data storage.

Provides methods to save and retrieve data from PostgreSQL tables.
"""

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from coding.core.database.config import ConnectionPool, DatabaseConfig

logger = logging.getLogger(__name__)


class DatabaseRepository:
    """
    Repository for storing and retrieving on-chain analysis data.

    Handles all database operations for snapshots, max pain,
    open interest, volume, and levels tables.
    """

    def __init__(self, config: Optional[DatabaseConfig] = None):
        """
        Initialize repository with database configuration.

        Args:
            config: Database configuration. Uses default if not provided.
        """
        self.config = config or DatabaseConfig()
        self.pool = ConnectionPool()
        self.pool.initialize(self.config)

    def _get_connection(self):
        """Get a database connection from the pool."""
        return self.pool.get_connection()

    def _return_connection(self, conn):
        """Return a connection to the pool."""
        self.pool.return_connection(conn)

    @contextmanager
    def _db_cursor(self):
        """
        Context manager for database operations with automatic connection management.

        Handles connection acquisition, cursor creation, commit/rollback,
        and resource cleanup automatically.

        Yields:
            Database cursor for executing queries.

        Example:
            with self._db_cursor() as cursor:
                cursor.execute("SELECT * FROM table")
                results = cursor.fetchall()
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

    def execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Any]:
        """
        Execute a parameterized query and return results.

        Args:
            query: SQL query with named parameters (%(param_name)s format).
            params: Dictionary of parameter values.

        Returns:
            List of results (if query has RETURNING clause).
            Empty list for INSERT/UPDATE/DELETE without RETURNING.

        Example:
            result = repo.execute_query(
                "INSERT INTO trades (id, price) VALUES (%(id)s, %(price)s) RETURNING id",
                {"id": 123, "price": 50000}
            )
        """
        with self._db_cursor() as cursor:
            cursor.execute(query, params or {})
            # Check if query has RETURNING clause
            if query.strip().upper().find("RETURNING") != -1:
                return cursor.fetchall()
            return []

    def save_snapshot(
        self,
        currency: str,
        data: List[Dict[str, Any]],
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save raw snapshot data to the snapshots table.

        Args:
            currency: Currency symbol (ETH, BTC).
            data: List of book summary items.
            captured_at: Timestamp of capture. Uses current time if not provided.

        Returns:
            Number of rows inserted.
        """
        if not data:
            return 0

        captured_at = captured_at or datetime.now()

        try:
            with self._db_cursor() as cursor:
                insert_sql = """
                    INSERT INTO snapshots (
                        captured_at, currency, instrument_name, expiration,
                        strike, option_type, open_interest, volume, volume_usd,
                        underlying_price, mark_price, bid_price, ask_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """

                rows = []
                for item in data:
                    instrument_name = item.get("instrument_name", "")
                    parts = instrument_name.split("-")

                    if len(parts) < 4:
                        continue

                    expiration = parts[1]
                    try:
                        strike = float(parts[2])
                    except ValueError:
                        continue
                    option_type = parts[3][0].upper()

                    rows.append((
                        captured_at,
                        currency,
                        instrument_name,
                        expiration,
                        strike,
                        option_type,
                        item.get("open_interest"),
                        item.get("volume"),
                        item.get("volume_usd"),
                        item.get("underlying_price"),
                        item.get("mark_price"),
                        item.get("bid_price"),
                        item.get("ask_price"),
                    ))

                cursor.executemany(insert_sql, rows)

                logger.info(f"Saved {len(rows)} snapshot records for {currency}")
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}")
            raise

    def get_unaggregated_hours(
        self,
        currency: str,
        lookback_hours: int = 168
    ) -> List[datetime]:
        """
        Find hours that have trades but no hourly snapshots.

        This is used by HourlyAggregationService to discover gaps.

        The scan is bounded to the last `lookback_hours` so the query stays
        O(recent window) instead of rescanning the full, ever-growing trades
        table every collection cycle (unbounded, this took minutes on the VPS
        and stalled the pipeline). Gaps older than the window are filled
        manually via scripts/aggregate_hourly_snapshots.py.

        Args:
            currency: Currency symbol (BTC, ETH).
            lookback_hours: How far back to scan for gaps (default 7 days).

        Returns:
            List of datetime objects representing hour buckets that need aggregation.
        """
        lookback_ms = int(
            (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp() * 1000
        )
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT
                    date_trunc('hour', to_timestamp(t.trade_timestamp / 1000.0)) as hour_bucket
                FROM historical_trades t
                WHERE t.currency = %s
                  AND t.trade_timestamp >= %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM hourly_snapshots h
                      WHERE h.currency = %s
                        AND h.snapshot_hour =
                            date_trunc('hour', to_timestamp(t.trade_timestamp / 1000.0))
                  )
                ORDER BY hour_bucket
            """, (currency, lookback_ms, currency))

            return [row[0] for row in cursor.fetchall()]

    def get_trades_for_hour(
        self,
        currency: str,
        hour_start: datetime,
        hour_end: datetime
    ) -> List[tuple]:
        """
        Fetch all trades for a specific hour bucket.

        Returns trades in format needed by HourlyAggregationService:
        (instrument_name, price, amount, direction, iv, index_price, mark_price)

        Args:
            currency: Currency symbol.
            hour_start: Start of hour bucket.
            hour_end: End of hour bucket.

        Returns:
            List of trade tuples.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT
                    instrument_name,
                    price,
                    amount,
                    direction,
                    iv,
                    index_price,
                    mark_price
                FROM historical_trades
                WHERE currency = %s
                  AND to_timestamp(trade_timestamp / 1000.0) >= %s
                  AND to_timestamp(trade_timestamp / 1000.0) < %s
                ORDER BY trade_timestamp
            """, (currency, hour_start, hour_end))

            return cursor.fetchall()

    def get_trades_for_hour_and_expiration(
        self,
        currency: str,
        hour_start: datetime,
        hour_end: datetime,
        expiration: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch trades for a specific hour bucket and expiration (for VWAP IV reconstruction).

        Mirrors the {iv, amount} shape OnChainAnalysisService._calculate_vwap_iv
        (on_chain_analysis_service.py:446) consumes for its VWAP leg.

        Args:
            currency: Currency symbol.
            hour_start: Start of hour bucket.
            hour_end: End of hour bucket.
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            List of dicts with {"iv": float, "amount": float}.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT iv, amount
                FROM historical_trades
                WHERE currency = %s
                  AND expiration = %s
                  AND to_timestamp(trade_timestamp / 1000.0) >= %s
                  AND to_timestamp(trade_timestamp / 1000.0) < %s
            """, (currency, expiration, hour_start, hour_end))

            return [
                {
                    "iv": float(row[0]) if row[0] is not None else None,
                    "amount": float(row[1]) if row[1] is not None else None,
                }
                for row in cursor.fetchall()
            ]

    def get_trades_for_flow_analysis(
        self,
        currency: str,
        expiration: str,
        start_ts: int,
        end_ts: int,
        trade_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        """
        Fetch trades for buy/sell flow analysis (BuySellFlowAnalyzer).

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "27MAR26").
            start_ts: Window start, epoch milliseconds.
            end_ts: Window end, epoch milliseconds.
            trade_filter: "all" (no filter), "block" (notional >= $100k), or
                "non_block" (notional < $100k).

        Returns:
            List of trade dicts with keys: trade_id, trade_timestamp,
            instrument_name, strike, option_type, price, amount, direction,
            index_price.
        """
        filter_clause = {
            "block":     "AND (amount * index_price) >= 100000",
            "non_block": "AND (amount * index_price) < 100000",
        }.get(trade_filter, "")

        query = f"""
            SELECT
                trade_id, trade_timestamp, instrument_name, strike,
                option_type, price, amount, direction, index_price
            FROM historical_trades
            WHERE currency = %s
                AND expiration = %s
                AND trade_timestamp >= %s
                AND trade_timestamp <= %s
                AND strike IS NOT NULL
                AND direction IS NOT NULL
                {filter_clause}
            ORDER BY trade_timestamp ASC
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, expiration, start_ts, end_ts))

            columns = [
                "trade_id", "trade_timestamp", "instrument_name", "strike",
                "option_type", "price", "amount", "direction", "index_price"
            ]

            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_hourly_flow_volumes(
        self,
        currency: str,
        start_ts: int,
        end_ts: int,
        expiration: Optional[str] = None,
        trade_filter: str = "all",
    ) -> List[tuple]:
        """
        Fetch hourly-bucketed traded volume split by option type and direction.

        Used by chart_generator.generate_flow_trend_chart.

        Args:
            currency: Currency symbol (BTC or ETH).
            start_ts: Window start, epoch milliseconds.
            end_ts: Window end, epoch milliseconds.
            expiration: Optional expiration filter (e.g., "27MAR26").
                None aggregates across all expirations.
            trade_filter: "all" (no filter), "block" (notional >= $100k), or
                "non_block" (notional < $100k).

        Returns:
            List of (hour, option_type, direction, total_volume) tuples
            ordered by hour ascending.
        """
        filter_clause = {
            "block":     "AND (amount * index_price) >= 100000",
            "non_block": "AND (amount * index_price) < 100000",
        }.get(trade_filter, "")

        expiration_clause = "AND expiration = %s" if expiration else ""
        params = (
            (currency, expiration, start_ts, end_ts)
            if expiration
            else (currency, start_ts, end_ts)
        )

        query = f"""
            SELECT
                DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000)) AS hour,
                option_type,
                direction,
                SUM(amount) AS total_volume
            FROM historical_trades
            WHERE currency = %s
                {expiration_clause}
                AND trade_timestamp >= %s
                AND trade_timestamp <= %s
                AND direction IS NOT NULL
                {filter_clause}
            GROUP BY hour, option_type, direction
            ORDER BY hour ASC
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def get_onchain_snapshot_history(
        self,
        currency: str,
        expiration: str,
        limit: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Get recent on-chain analysis snapshots for an expiration.

        Replaces the legacy max_pain/open_interest/volume history readers —
        those tables are frozen (writers removed 2026-07-13); the daemon
        writes the same metrics hourly into onchain_analysis_snapshots.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string (e.g., "27MAR26").
            limit: Number of most-recent snapshots to return.

        Returns:
            List of snapshot dicts in chronological order (oldest first) with
            keys: snapshot_hour, max_pain_strike, total_call_oi, total_put_oi,
            put_call_ratio_oi, total_volume, put_call_ratio_volume,
            underlying_price.
        """
        columns = [
            "snapshot_hour", "max_pain_strike", "total_call_oi",
            "total_put_oi", "put_call_ratio_oi", "total_volume",
            "put_call_ratio_volume", "underlying_price"
        ]
        with self._db_cursor() as cursor:
            cursor.execute(f"""
                SELECT {', '.join(columns)}
                FROM onchain_analysis_snapshots
                WHERE currency = %s AND expiration = %s
                ORDER BY snapshot_hour DESC
                LIMIT %s
            """, (currency, expiration, limit))

            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))  # Chronological order

    def get_latest_snapshot_oi(
        self,
        currency: str,
        around_time: datetime
    ) -> Dict[str, float]:
        """
        Get latest open interest values from snapshots table.

        Used to enrich hourly snapshots with OI data.

        Args:
            currency: Currency symbol.
            around_time: Timestamp to search around (finds closest snapshots).

        Returns:
            Dictionary mapping instrument_name -> open_interest.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT ON (instrument_name)
                    instrument_name,
                    open_interest
                FROM snapshots
                WHERE currency = %s
                  AND captured_at <= %s
                  AND open_interest IS NOT NULL
                ORDER BY instrument_name, captured_at DESC
            """, (currency, around_time))

            return {row[0]: row[1] for row in cursor.fetchall()}

    def save_hourly_snapshots(self, snapshots: List[Dict]) -> int:
        """
        Save aggregated hourly snapshots to database.

        Uses ON CONFLICT to handle duplicates (same instrument + hour).

        Args:
            snapshots: List of snapshot dictionaries from HourlyAggregationService.

        Returns:
            Number of snapshots inserted.
        """
        if not snapshots:
            return 0

        with self._db_cursor() as cursor:
            insert_sql = """
                INSERT INTO hourly_snapshots (
                    snapshot_hour, captured_at, instrument_name, currency,
                    strike, expiration, option_type,
                    trade_count, total_volume, vwap,
                    bid_price, ask_price, mark_price, mark_iv,
                    open_interest, index_price, futures_price, basis,
                    avg_delta, avg_gamma, avg_theta, avg_vega
                ) VALUES (
                    %(snapshot_hour)s, %(captured_at)s, %(instrument_name)s, %(currency)s,
                    %(strike)s, %(expiration)s, %(option_type)s,
                    %(trade_count)s, %(total_volume)s, %(vwap)s,
                    %(bid_price)s, %(ask_price)s, %(mark_price)s, %(mark_iv)s,
                    %(open_interest)s, %(index_price)s, %(futures_price)s, %(basis)s,
                    %(avg_delta)s, %(avg_gamma)s, %(avg_theta)s, %(avg_vega)s
                )
                ON CONFLICT (instrument_name, snapshot_hour)
                DO UPDATE SET
                    captured_at = EXCLUDED.captured_at,
                    trade_count = EXCLUDED.trade_count,
                    total_volume = EXCLUDED.total_volume,
                    vwap = EXCLUDED.vwap,
                    bid_price = EXCLUDED.bid_price,
                    ask_price = EXCLUDED.ask_price,
                    mark_price = EXCLUDED.mark_price,
                    mark_iv = EXCLUDED.mark_iv,
                    open_interest = EXCLUDED.open_interest,
                    index_price = EXCLUDED.index_price,
                    avg_delta = EXCLUDED.avg_delta,
                    avg_gamma = EXCLUDED.avg_gamma,
                    avg_theta = EXCLUDED.avg_theta,
                    avg_vega = EXCLUDED.avg_vega
            """

            rows_inserted = 0
            for snapshot in snapshots:
                cursor.execute(insert_sql, snapshot)
                rows_inserted += cursor.rowcount

            return rows_inserted

    def save_flow_metrics(
        self,
        currency: str,
        expiration: str,
        flow_data: Dict[float, Dict[str, Dict[str, float]]],
        underlying_price: float,
        window_hours: int = 24,
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save aggregated flow metrics to database.

        Inserts per-strike buy/sell aggregates from BuySellFlowAnalyzer.

        Args:
            currency: Currency symbol (BTC, ETH).
            expiration: Expiration date string.
            flow_data: Flow data structure {strike: {option_type: {metrics}}}.
            underlying_price: Current underlying price.
            window_hours: Lookback window in hours.
            captured_at: Timestamp of capture.

        Returns:
            Number of rows inserted.
        """
        if not flow_data:
            return 0

        captured_at = captured_at or datetime.now()

        try:
            with self._db_cursor() as cursor:
                insert_sql = """
                    INSERT INTO buy_sell_flow_metrics (
                        captured_at, window_hours, currency, expiration,
                        strike, option_type, buy_count, buy_volume, buy_notional,
                        sell_count, sell_volume, sell_notional, net_flow,
                        buy_sell_ratio, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (captured_at, currency, expiration, strike, option_type) DO NOTHING
                """

                rows = []
                for strike, option_types in flow_data.items():
                    for option_type, metrics in option_types.items():
                        buy_volume = metrics.get("buy_volume", 0)
                        sell_volume = metrics.get("sell_volume", 0)
                        net_flow = buy_volume - sell_volume
                        buy_sell_ratio = (buy_volume / sell_volume) if sell_volume > 0 else None

                        rows.append((
                            captured_at,
                            window_hours,
                            currency,
                            expiration,
                            strike,
                            option_type[0].upper(),  # 'C' or 'P'
                            metrics.get("buy_count", 0),
                            buy_volume,
                            metrics.get("buy_notional", 0),
                            metrics.get("sell_count", 0),
                            sell_volume,
                            metrics.get("sell_notional", 0),
                            net_flow,
                            buy_sell_ratio,
                            underlying_price
                        ))

                cursor.executemany(insert_sql, rows)

                logger.info(f"Saved {len(rows)} flow metrics for {currency} {expiration}")
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to save flow metrics: {e}")
            raise

    def get_flow_metrics(
        self,
        currency: str,
        expiration: str,
        limit: int = 1
    ) -> Dict[str, Any]:
        """
        Get latest flow metrics for an expiration.

        Returns flow_data structure matching BuySellFlowAnalyzer output.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            limit: Number of most recent captures to retrieve (default 1).

        Returns:
            Dict with flow_data structure and metadata.
        """
        with self._db_cursor() as cursor:
            # Get latest captured_at for this expiration
            cursor.execute("""
                SELECT DISTINCT captured_at
                FROM buy_sell_flow_metrics
                WHERE currency = %s AND expiration = %s
                ORDER BY captured_at DESC
                LIMIT %s
            """, (currency, expiration, limit))

            timestamps = [row[0] for row in cursor.fetchall()]
            if not timestamps:
                return {"flow_data": {}, "spot_price": 0.0}

            latest_timestamp = timestamps[0]

            # Get all metrics for latest timestamp
            cursor.execute("""
                SELECT strike, option_type, buy_count, buy_volume, buy_notional,
                       sell_count, sell_volume, sell_notional, net_flow,
                       buy_sell_ratio, underlying_price
                FROM buy_sell_flow_metrics
                WHERE currency = %s AND expiration = %s AND captured_at = %s
                ORDER BY strike, option_type
            """, (currency, expiration, latest_timestamp))

            # Reconstruct flow_data structure (convert all Decimals to float)
            flow_data = {}
            underlying_price = 0.0

            for row in cursor.fetchall():
                strike, opt_type, buy_count, buy_vol, buy_not, sell_count, sell_vol, sell_not, net_flow, bs_ratio, price = row

                # Convert strike to float for dict key
                strike_float = float(strike)

                if strike_float not in flow_data:
                    flow_data[strike_float] = {}

                # Use "C" and "P" as keys (matches BuySellFlowAnalyzer format)
                flow_data[strike_float][opt_type] = {
                    "buy_count": int(buy_count),
                    "buy_volume": float(buy_vol),
                    "buy_notional": float(buy_not),
                    "sell_count": int(sell_count),
                    "sell_volume": float(sell_vol),
                    "sell_notional": float(sell_not),
                    "net_flow": float(net_flow),
                    "buy_sell_ratio": float(bs_ratio) if bs_ratio else None
                }

                underlying_price = float(price)

            return {
                "flow_data": flow_data,
                "spot_price": underlying_price
            }

    def get_aggregated_flow_metrics(self, currency: str) -> Dict[str, Any]:
        """
        Get flow metrics aggregated across all expirations for a currency.

        Uses the latest snapshot per (expiration, strike, option_type) then
        sums all flow columns by (strike, option_type) across expirations.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            Dict with flow_data structure (same format as get_flow_metrics) and median spot_price.
        """
        query = """
            WITH latest_per_expiry AS (
                SELECT DISTINCT ON (expiration, strike, option_type)
                    strike,
                    option_type,
                    buy_count,
                    buy_volume,
                    buy_notional,
                    sell_count,
                    sell_volume,
                    sell_notional,
                    net_flow,
                    underlying_price
                FROM buy_sell_flow_metrics
                WHERE currency = %s
                ORDER BY expiration, strike, option_type, captured_at DESC
            )
            SELECT
                strike,
                option_type,
                SUM(buy_count)        AS buy_count,
                SUM(buy_volume)       AS buy_volume,
                SUM(buy_notional)     AS buy_notional,
                SUM(sell_count)       AS sell_count,
                SUM(sell_volume)      AS sell_volume,
                SUM(sell_notional)    AS sell_notional,
                SUM(net_flow)         AS net_flow,
                AVG(underlying_price) AS underlying_price
            FROM latest_per_expiry
            GROUP BY strike, option_type
            ORDER BY strike, option_type
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency,))
            rows = cursor.fetchall()

        if not rows:
            return {"flow_data": {}, "spot_price": 0.0}

        flow_data: Dict[float, Dict[str, Any]] = {}
        prices = []

        for row in rows:
            strike, opt_type, buy_count, buy_vol, buy_not, sell_count, sell_vol, sell_not, net_flow, price = row

            strike_f = float(strike)
            if strike_f not in flow_data:
                flow_data[strike_f] = {}

            sell_vol_f = float(sell_vol)
            flow_data[strike_f][opt_type] = {
                "buy_count":     int(buy_count),
                "buy_volume":    float(buy_vol),
                "buy_notional":  float(buy_not),
                "sell_count":    int(sell_count),
                "sell_volume":   sell_vol_f,
                "sell_notional": float(sell_not),
                "net_flow":      float(net_flow),
                "buy_sell_ratio": float(buy_vol) / sell_vol_f if sell_vol_f > 0 else None,
            }
            prices.append(float(price))

        spot_price = sorted(prices)[len(prices) // 2] if prices else 0.0  # median

        return {"flow_data": flow_data, "spot_price": spot_price}

    def get_active_expirations_with_flow(
        self,
        currency: str
    ) -> List[Dict[str, Any]]:
        """
        Get active expirations with flow data.

        Filters:
        - Expiration date >= today (not expired)
        - Has flow metrics in database
        - Joins with open_interest table to get total OI

        Returns:
            List sorted by total_oi DESC (highest OI first).
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT
                    f.expiration,
                    COALESCE(oi.total_oi, 0) as total_oi
                FROM buy_sell_flow_metrics f
                LEFT JOIN LATERAL (
                    SELECT total_oi
                    FROM open_interest
                    WHERE currency = f.currency
                      AND expiration = f.expiration
                    ORDER BY captured_at DESC
                    LIMIT 1
                ) oi ON true
                WHERE f.currency = %s
                  AND TO_DATE(f.expiration, 'DDMONYY') >= CURRENT_DATE
                ORDER BY total_oi DESC, f.expiration
            """, (currency,))

            columns = ["expiration", "total_oi"]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            logger.info(f"Found {len(results)} active expirations with flow data for {currency}")
            return results

    def save_daily_oi_snapshot(
        self,
        currency: str,
        expiration: str,
        instruments: List[Dict[str, Any]],
        underlying_price: float,
        snapshot_date: Optional[datetime] = None
    ) -> int:
        """
        Save daily OI snapshot for all instruments in an expiration.

        Uses UPSERT to avoid duplicates within the same day.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            instruments: List of enriched instrument dicts with strike, option_type,
                        open_interest, mark_iv.
            underlying_price: Current underlying price.
            snapshot_date: Date for snapshot. Uses today if not provided.

        Returns:
            Number of rows upserted.
        """
        if not instruments:
            return 0

        from datetime import date as date_type
        snap_date = snapshot_date or datetime.now().date()
        if isinstance(snap_date, datetime):
            snap_date = snap_date.date()

        try:
            with self._db_cursor() as cursor:
                insert_sql = """
                    INSERT INTO daily_oi_snapshots (
                        snapshot_date, currency, expiration, strike,
                        option_type, open_interest, mark_iv, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_date, currency, expiration, strike, option_type)
                    DO UPDATE SET
                        open_interest = EXCLUDED.open_interest,
                        mark_iv = EXCLUDED.mark_iv,
                        underlying_price = EXCLUDED.underlying_price
                """

                rows = []
                for inst in instruments:
                    rows.append((
                        snap_date,
                        currency,
                        expiration,
                        inst["strike"],
                        inst["option_type"],
                        inst.get("open_interest", 0),
                        inst.get("mark_iv"),
                        underlying_price,
                    ))

                cursor.executemany(insert_sql, rows)

                logger.info(
                    f"Saved {len(rows)} daily OI snapshots for "
                    f"{currency} {expiration} ({snap_date})"
                )
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to save daily OI snapshot: {e}")
            raise

    def get_previous_oi_snapshot(
        self,
        currency: str,
        expiration: str,
        before_date: Optional[datetime] = None
    ) -> Dict[str, float]:
        """
        Get the most recent OI snapshot before a given date.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            before_date: Get snapshot before this date. Uses yesterday if not provided.

        Returns:
            Dict mapping (strike, option_type) -> open_interest.
        """
        from datetime import date as date_type, timedelta
        if before_date is None:
            target_date = datetime.now().date() - timedelta(days=1)
        elif isinstance(before_date, datetime):
            target_date = before_date.date()
        else:
            target_date = before_date

        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT strike, option_type, open_interest
                FROM daily_oi_snapshots
                WHERE currency = %s
                  AND expiration = %s
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date)
                      FROM daily_oi_snapshots
                      WHERE currency = %s
                        AND expiration = %s
                        AND snapshot_date <= %s
                  )
            """, (currency, expiration, currency, expiration, target_date))

            result = {}
            for row in cursor.fetchall():
                strike, opt_type, oi = row
                result[(float(strike), opt_type)] = float(oi)

            return result

    def save_funding_rate(
        self,
        currency: str,
        instrument_name: str,
        timestamp: int,
        date,
        funding_rate: float
    ) -> None:
        """
        Save funding rate from a perpetual contract to the database.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            instrument_name: Perpetual instrument name (e.g., "BTC-PERPETUAL").
            timestamp: Unix timestamp in milliseconds.
            date: Datetime object for this entry.
            funding_rate: Funding rate as a decimal (e.g., 0.0001 for 0.01%).
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO funding_rate_history (
                    currency, instrument_name, timestamp, date, funding_rate
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (instrument_name, timestamp) DO NOTHING
            """, (currency, instrument_name, timestamp, date, funding_rate))
            logger.info(f"Saved funding rate for {instrument_name}: {funding_rate:.8f}")

    def save_dvol(
        self,
        currency: str,
        index_name: str,
        timestamp: int,
        date,
        dvol: float
    ) -> None:
        """
        Save DVOL (Deribit Volatility Index) value to the database.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            index_name: Index name (e.g., "BTCDVOL").
            timestamp: Unix timestamp in milliseconds.
            date: Datetime object for this entry.
            dvol: DVOL value.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO volatility_index_history (
                    currency, index_name, timestamp, date, dvol
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (index_name, timestamp) DO NOTHING
            """, (currency, index_name, timestamp, date, dvol))
            logger.info(f"Saved DVOL for {index_name}: {dvol:.2f}")

    def save_ohlcv(
        self,
        currency: str,
        instrument_name: str,
        timestamp: int,
        date,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float
    ) -> None:
        """
        Save one OHLCV daily candle to ohlcv_history.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            instrument_name: Perpetual instrument (e.g., "BTC-PERPETUAL").
            timestamp: Unix timestamp in milliseconds.
            date: Datetime object for this candle.
            open_price: Opening price.
            high: High price.
            low: Low price.
            close: Closing price.
            volume: Trading volume.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO ohlcv_history (
                    currency, instrument_name, timestamp, date,
                    open, high, low, close, volume
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (instrument_name, timestamp) DO NOTHING
            """, (currency, instrument_name, timestamp, date,
                  open_price, high, low, close, volume))
            logger.debug(f"Saved OHLCV candle for {instrument_name} at {date}: close={close:.2f}")

    def save_external_metrics(
        self,
        date,
        fear_greed_value,
        fear_greed_classification,
        btc_dominance,
        eth_dominance
    ) -> None:
        """
        Save external sentiment metrics to the database.

        Args:
            date: Datetime for this snapshot.
            fear_greed_value: Fear & Greed index value (0-100).
            fear_greed_classification: Classification string (e.g., "Extreme Fear").
            btc_dominance: BTC market dominance percentage.
            eth_dominance: ETH market dominance percentage.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO external_metrics (
                    date, fear_greed_value, fear_greed_classification,
                    btc_dominance, eth_dominance
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (date) DO UPDATE SET
                    fear_greed_value = EXCLUDED.fear_greed_value,
                    fear_greed_classification = EXCLUDED.fear_greed_classification,
                    btc_dominance = EXCLUDED.btc_dominance,
                    eth_dominance = EXCLUDED.eth_dominance
            """, (date, fear_greed_value, fear_greed_classification, btc_dominance, eth_dominance))
            logger.info(f"Saved external metrics: Fear&Greed={fear_greed_value}, BTC dom={btc_dominance}")

    def save_onchain_snapshot(
        self,
        snapshot_hour,
        currency: str,
        expiration: str,
        analysis_data: Dict[str, Any],
        gex_dex_data: Dict[str, Any],
        underlying_price: float
    ) -> None:
        """
        Save on-chain analysis snapshot to the database.

        Args:
            snapshot_hour: Timestamp of the snapshot hour.
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "27DEC24").
            analysis_data: Output of OnChainAnalyzer.analyze_expiration().
            gex_dex_data: Output of GexDexCalculator.calculate().
            underlying_price: Current underlying asset price.
        """
        max_pain = analysis_data.get("max_pain", {})
        put_call = analysis_data.get("put_call_ratio", {})
        volume_stats = analysis_data.get("volume_stats", {})
        moneyness = analysis_data.get("moneyness", {})
        support_resistance = analysis_data.get("support_resistance", {})
        key_levels = gex_dex_data.get("key_levels", {})

        max_pain_strike = max_pain.get("max_pain_strike")
        max_pain_distance_pct = (
            (max_pain_strike - underlying_price) / underlying_price * 100
            if max_pain_strike and underlying_price
            else None
        )

        resistance_levels = support_resistance.get("resistance_levels", [])
        support_levels = support_resistance.get("support_levels", [])
        resistance_1 = resistance_levels[0] if resistance_levels else {}
        support_1 = support_levels[0] if support_levels else {}

        # key_levels call_resistance/put_support are dicts ({"strike", "net_gex"})
        # when greeks are non-zero; the table stores only the strike scalar
        call_resistance = key_levels.get("call_resistance") or {}
        put_support = key_levels.get("put_support") or {}

        volume_stats_call = volume_stats.get("total_call_volume", 0)
        volume_stats_put = volume_stats.get("total_put_volume", 0)
        put_call_ratio_volume = (
            volume_stats_put / volume_stats_call if volume_stats_call > 0 else None
        )

        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO onchain_analysis_snapshots (
                    snapshot_hour, currency, expiration,
                    max_pain_strike, max_pain_distance_pct,
                    put_call_ratio_oi, put_call_ratio_volume,
                    total_call_oi, total_put_oi,
                    total_net_gex, total_net_dex,
                    call_resistance_strike, put_support_strike, hvl_level,
                    resistance_1_strike, resistance_1_call_oi,
                    support_1_strike, support_1_put_oi,
                    total_volume,
                    itm_call_oi_pct, otm_call_oi_pct,
                    itm_put_oi_pct, otm_put_oi_pct,
                    underlying_price
                ) VALUES (
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s,
                    %s, %s,
                    %s, %s,
                    %s
                )
                ON CONFLICT (snapshot_hour, currency, expiration) DO UPDATE SET
                    max_pain_strike = EXCLUDED.max_pain_strike,
                    max_pain_distance_pct = EXCLUDED.max_pain_distance_pct,
                    put_call_ratio_oi = EXCLUDED.put_call_ratio_oi,
                    put_call_ratio_volume = EXCLUDED.put_call_ratio_volume,
                    total_call_oi = EXCLUDED.total_call_oi,
                    total_put_oi = EXCLUDED.total_put_oi,
                    total_net_gex = EXCLUDED.total_net_gex,
                    total_net_dex = EXCLUDED.total_net_dex,
                    call_resistance_strike = EXCLUDED.call_resistance_strike,
                    put_support_strike = EXCLUDED.put_support_strike,
                    hvl_level = EXCLUDED.hvl_level,
                    resistance_1_strike = EXCLUDED.resistance_1_strike,
                    resistance_1_call_oi = EXCLUDED.resistance_1_call_oi,
                    support_1_strike = EXCLUDED.support_1_strike,
                    support_1_put_oi = EXCLUDED.support_1_put_oi,
                    total_volume = EXCLUDED.total_volume,
                    itm_call_oi_pct = EXCLUDED.itm_call_oi_pct,
                    otm_call_oi_pct = EXCLUDED.otm_call_oi_pct,
                    itm_put_oi_pct = EXCLUDED.itm_put_oi_pct,
                    otm_put_oi_pct = EXCLUDED.otm_put_oi_pct,
                    underlying_price = EXCLUDED.underlying_price
            """, (
                snapshot_hour, currency, expiration,
                max_pain_strike, max_pain_distance_pct,
                put_call.get("ratio"), put_call_ratio_volume,
                put_call.get("total_call_oi"), put_call.get("total_put_oi"),
                gex_dex_data.get("total_net_gex"), gex_dex_data.get("total_net_dex"),
                call_resistance.get("strike"), put_support.get("strike"), key_levels.get("hvl"),
                resistance_1.get("strike"), resistance_1.get("call_oi"),
                support_1.get("strike"), support_1.get("put_oi"),
                volume_stats.get("total_volume"),
                moneyness.get("calls", {}).get("itm_pct"),
                moneyness.get("calls", {}).get("otm_pct"),
                moneyness.get("puts", {}).get("itm_pct"),
                moneyness.get("puts", {}).get("otm_pct"),
                underlying_price,
            ))
            logger.info(f"Saved on-chain snapshot for {currency} {expiration} at {snapshot_hour}")

    def get_ohlcv_by_date_range(
        self,
        currency: str,
        start: datetime,
        end: datetime
    ) -> List[Dict[str, Any]]:
        """
        Retrieve OHLCV candles for a currency's perpetual instrument within a date range.

        Queries ohlcv_history filtered by instrument_name = '{currency}-PERPETUAL'
        and date BETWEEN start AND end. Both start and end are timezone-naive UTC datetimes.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            start: Start of date range (timezone-naive UTC).
            end: End of date range (timezone-naive UTC).

        Returns:
            List of dicts with {"date": datetime, "close": float}, ordered by date ASC.
        """
        instrument_name = f"{currency}-PERPETUAL"
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT date, close
                FROM ohlcv_history
                WHERE instrument_name = %s
                  AND date BETWEEN %s AND %s
                ORDER BY date ASC
            """, (instrument_name, start, end))
            return [{"date": row[0], "close": float(row[1])} for row in cursor.fetchall()]

    def get_atm_iv_history(
        self,
        currency: str,
        expiration: str,
        strike: float,
        option_type: str = "C",
        limit: int = 90
    ) -> List[Dict[str, Any]]:
        """
        Get historical ATM IV values for IV percentile calculation.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            strike: ATM strike price.
            option_type: Option type to query (default "C").
            limit: Maximum days of history.

        Returns:
            List of dicts with snapshot_date and mark_iv.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT snapshot_date, mark_iv
                FROM daily_oi_snapshots
                WHERE currency = %s
                  AND expiration = %s
                  AND strike = %s
                  AND option_type = %s
                  AND mark_iv IS NOT NULL
                ORDER BY snapshot_date DESC
                LIMIT %s
            """, (currency, expiration, strike, option_type, limit))

            columns = ["snapshot_date", "mark_iv"]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))

    # ── Volatility Reconstruction (backfill) ─────────────────────────────────

    def get_distinct_snapshot_hours_with_expirations(
        self,
        currency: str,
        start: datetime,
        end: datetime
    ) -> List[tuple]:
        """
        Find (snapshot_hour, expiration) pairs that have option data in hourly_snapshots.

        Drives the iteration loop for the volatility-metric backfill: each pair
        identifies one (hour, expiration) slice to reconstruct.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            start: Start of date range (inclusive, timezone-naive UTC).
            end: End of date range (inclusive, timezone-naive UTC).

        Returns:
            List of (snapshot_hour, expiration) tuples ordered by hour then expiration.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT snapshot_hour, expiration
                FROM hourly_snapshots
                WHERE currency = %s
                  AND option_type IN ('C', 'P')
                  AND snapshot_hour >= %s
                  AND snapshot_hour <= %s
                ORDER BY snapshot_hour, expiration
            """, (currency, start, end))

            return [(row[0], row[1]) for row in cursor.fetchall()]

    def get_hourly_snapshots_for_hour(
        self,
        currency: str,
        hour: datetime,
        expiration: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch option instruments from hourly_snapshots for one (hour, expiration) slice,
        mapped into the instrument-dict shape VolatilitySurfaceCalculator expects.

        Field renames: avg_delta->delta, avg_gamma->gamma, avg_theta->theta,
        avg_vega->vega (matching the live on-chain analysis input contract).

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            hour: Snapshot hour (timezone-naive UTC).
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            List of instrument dicts with strike, option_type, mark_iv, delta,
            gamma, theta, vega, open_interest, index_price.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT
                    instrument_name, strike, option_type, mark_iv,
                    avg_delta, avg_gamma, avg_theta, avg_vega,
                    open_interest, index_price
                FROM hourly_snapshots
                WHERE currency = %s
                  AND snapshot_hour = %s
                  AND expiration = %s
                  AND option_type IN ('C', 'P')
                ORDER BY strike, option_type
            """, (currency, hour, expiration))

            columns = [
                "instrument_name", "strike", "option_type", "mark_iv",
                "delta", "gamma", "theta", "vega",
                "open_interest", "index_price",
            ]
            numeric_fields = {
                "strike", "mark_iv", "delta", "gamma", "theta", "vega",
                "open_interest", "index_price",
            }
            instruments = []
            for row in cursor.fetchall():
                inst = dict(zip(columns, row))
                for field in numeric_fields:
                    if inst[field] is not None:
                        inst[field] = float(inst[field])
                # Matches the live normalization at on_chain_analyzer.py:145
                # (`item.get("open_interest", 0) or 0`) — NULL OI must become 0,
                # not None, or VolatilitySurfaceCalculator._calculate_pc_by_moneyness
                # crashes on `buckets[bucket]["call_oi"] += oi`.
                inst["open_interest"] = inst["open_interest"] or 0
                instruments.append(inst)
            return instruments

    def save_volatility_snapshot(
        self,
        snapshot_hour,
        currency: str,
        expiration: str,
        metrics: Dict[str, Any],
        underlying_price: float
    ) -> None:
        """
        Save a reconstructed on-chain volatility snapshot to the database.

        Args:
            snapshot_hour: Timestamp of the snapshot hour.
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "27DEC24").
            metrics: Dict with the ~22 reconstructed metric fields (keys match
                onchain_volatility_snapshots columns; missing keys default to None).
            underlying_price: Underlying asset price for this snapshot.
        """
        fields = [
            "atm_iv", "skew_25d", "put_25d_iv", "call_25d_iv",
            "net_vanna", "net_charm",
            "vwap_iv", "mark_iv_avg",
            "vrp_absolute", "vrp_percentage", "realized_vol",
            "iv_percentile_expiry", "iv_percentile_365d", "iv_rank_365d",
            "expected_daily_move", "expected_weekly_move", "expected_monthly_move",
            "pc_atm_ratio", "pc_near_otm_ratio", "pc_far_otm_ratio",
        ]
        values = {field: metrics.get(field) for field in fields}

        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO onchain_volatility_snapshots (
                    snapshot_hour, currency, expiration,
                    atm_iv, skew_25d, put_25d_iv, call_25d_iv,
                    net_vanna, net_charm,
                    vwap_iv, mark_iv_avg,
                    vrp_absolute, vrp_percentage, realized_vol,
                    iv_percentile_expiry, iv_percentile_365d, iv_rank_365d,
                    expected_daily_move, expected_weekly_move, expected_monthly_move,
                    pc_atm_ratio, pc_near_otm_ratio, pc_far_otm_ratio,
                    underlying_price
                ) VALUES (
                    %(snapshot_hour)s, %(currency)s, %(expiration)s,
                    %(atm_iv)s, %(skew_25d)s, %(put_25d_iv)s, %(call_25d_iv)s,
                    %(net_vanna)s, %(net_charm)s,
                    %(vwap_iv)s, %(mark_iv_avg)s,
                    %(vrp_absolute)s, %(vrp_percentage)s, %(realized_vol)s,
                    %(iv_percentile_expiry)s, %(iv_percentile_365d)s, %(iv_rank_365d)s,
                    %(expected_daily_move)s, %(expected_weekly_move)s, %(expected_monthly_move)s,
                    %(pc_atm_ratio)s, %(pc_near_otm_ratio)s, %(pc_far_otm_ratio)s,
                    %(underlying_price)s
                )
                ON CONFLICT (snapshot_hour, currency, expiration) DO UPDATE SET
                    atm_iv = EXCLUDED.atm_iv,
                    skew_25d = EXCLUDED.skew_25d,
                    put_25d_iv = EXCLUDED.put_25d_iv,
                    call_25d_iv = EXCLUDED.call_25d_iv,
                    net_vanna = EXCLUDED.net_vanna,
                    net_charm = EXCLUDED.net_charm,
                    vwap_iv = EXCLUDED.vwap_iv,
                    mark_iv_avg = EXCLUDED.mark_iv_avg,
                    vrp_absolute = EXCLUDED.vrp_absolute,
                    vrp_percentage = EXCLUDED.vrp_percentage,
                    realized_vol = EXCLUDED.realized_vol,
                    iv_percentile_expiry = EXCLUDED.iv_percentile_expiry,
                    iv_percentile_365d = EXCLUDED.iv_percentile_365d,
                    iv_rank_365d = EXCLUDED.iv_rank_365d,
                    expected_daily_move = EXCLUDED.expected_daily_move,
                    expected_weekly_move = EXCLUDED.expected_weekly_move,
                    expected_monthly_move = EXCLUDED.expected_monthly_move,
                    pc_atm_ratio = EXCLUDED.pc_atm_ratio,
                    pc_near_otm_ratio = EXCLUDED.pc_near_otm_ratio,
                    pc_far_otm_ratio = EXCLUDED.pc_far_otm_ratio,
                    underlying_price = EXCLUDED.underlying_price
            """, {
                "snapshot_hour": snapshot_hour,
                "currency": currency,
                "expiration": expiration,
                "underlying_price": underlying_price,
                **values,
            })
            logger.info(f"Saved volatility snapshot for {currency} {expiration} at {snapshot_hour}")

    def get_volatility_snapshots_for_percentile_backfill(
        self,
        currency: str,
        start: datetime,
        end: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch the (snapshot_hour, expiration, atm_iv) series for the per-expiry
        IV-percentile backfill pass (pass 2 of volatility reconstruction — needs
        the ATM-IV series from pass 1 to exist before percentiles can be computed).

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            start: Start of date range (inclusive, timezone-naive UTC).
            end: End of date range (inclusive, timezone-naive UTC).

        Returns:
            List of dicts with {"snapshot_hour", "expiration", "atm_iv"}.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT snapshot_hour, expiration, atm_iv
                FROM onchain_volatility_snapshots
                WHERE currency = %s
                  AND snapshot_hour >= %s
                  AND snapshot_hour <= %s
                ORDER BY expiration, snapshot_hour
            """, (currency, start, end))

            columns = ["snapshot_hour", "expiration", "atm_iv"]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def update_iv_percentile_expiry(
        self,
        snapshot_hour,
        currency: str,
        expiration: str,
        iv_percentile_expiry: float
    ) -> None:
        """
        Update the per-expiry IV percentile for an existing volatility snapshot row.

        Pass 2 of volatility reconstruction — called once the trailing-window
        percentile has been computed against the row's own (currency, expiration)
        ATM-IV series.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                UPDATE onchain_volatility_snapshots
                SET iv_percentile_expiry = %s
                WHERE snapshot_hour = %s AND currency = %s AND expiration = %s
            """, (iv_percentile_expiry, snapshot_hour, currency, expiration))

    def get_latest_iv_percentile_expiry(
        self,
        currency: str,
        expiration: str
    ) -> Optional[float]:
        """
        Latest per-expiry IV percentile for (currency, expiration) — the
        entry-time ranking signal for the straddle scanner
        (coding/service/scanner/straddle_scan_service.py). Chosen because
        iv_percentile_expiry was the deal-quality metric that best predicted
        long-straddle P&L in scripts/backtest_straddle_metrics.py.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            Most recent iv_percentile_expiry value (float), or None if no
            volatility snapshot with a non-NULL percentile exists yet for
            this (currency, expiration) pair.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT iv_percentile_expiry
                FROM onchain_volatility_snapshots
                WHERE currency = %s AND expiration = %s
                  AND iv_percentile_expiry IS NOT NULL
                ORDER BY snapshot_hour DESC
                LIMIT 1
            """, (currency, expiration))
            row = cursor.fetchone()
            return float(row[0]) if row else None

    def get_iv_percentile_with_window(
        self,
        currency: str,
        expiration: str
    ) -> Dict[str, Any]:
        """
        Freshly computed per-expiry IV percentile that excludes invalid
        atm_iv (NULL or <= 0) observations from the ranking population, and
        reports how much history backs the number.

        Unlike get_latest_iv_percentile_expiry (which reads the pre-computed
        iv_percentile_expiry column written by VolatilityReconstructionService
        against an UNFILTERED population), this method recomputes the
        percentile at query time against a population that has zero/NULL
        atm_iv rows stripped out first -- those are missing-data artifacts,
        not real "cheap IV" observations, and must never rank as cheap.

        Same percentile convention as the stored column (count of population
        values <= the latest valid observation, divided by population size,
        x100) -- the two will differ ONLY when the stored column's unfiltered
        population included a zero/NULL atm_iv row that this method excludes,
        or (for long-lived expiries) because the stored column also applies
        a 365-day trailing window while this method uses the expiry's full
        available history (that's what window_days below exposes).

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            Dict: {percentile, n_obs, window_days, latest_atm_iv}.
              percentile: float in [0, 100], or None if no valid
                observations exist yet for this (currency, expiration).
              n_obs: count of valid (non-NULL, non-zero) atm_iv
                observations backing the percentile, including the latest.
              window_days: days spanned by the valid population (latest
                snapshot_hour - earliest), 0.0 if fewer than 2 observations.
              latest_atm_iv: the most recent valid atm_iv value, or None.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT snapshot_hour, atm_iv
                FROM onchain_volatility_snapshots
                WHERE currency = %s AND expiration = %s
                  AND atm_iv IS NOT NULL AND atm_iv > 0
                ORDER BY snapshot_hour ASC
            """, (currency, expiration))
            rows = cursor.fetchall()

        if not rows:
            return {"percentile": None, "n_obs": 0, "window_days": 0, "latest_atm_iv": None}

        valid = [(row[0], float(row[1])) for row in rows]
        n_obs = len(valid)
        latest_atm_iv = valid[-1][1]
        window_days = (
            (valid[-1][0] - valid[0][0]).total_seconds() / 86400.0 if n_obs > 1 else 0.0
        )

        below_or_equal = sum(1 for _, iv in valid if iv <= latest_atm_iv)
        percentile = (below_or_equal / n_obs) * 100.0

        return {
            "percentile": percentile,
            "n_obs": n_obs,
            "window_days": window_days,
            "latest_atm_iv": latest_atm_iv,
        }

    # ------------------------------------------------------------------
    # Straddle scanner forward-testing (migration 014)
    # ------------------------------------------------------------------

    def save_straddle_scan(self, row: Dict[str, Any]) -> bool:
        """
        Insert one straddle_scan_history row (one per included expiry per
        scan cycle). Deduped on (currency, expiration, scan_time) — a
        repeat call for the same cycle is a silent no-op.

        Args:
            row: Dict with keys matching straddle_scan_history columns
                (see migrations/014_add_straddle_scan_history.sql).
                Required: scan_time, currency, expiration, dte,
                future_price, index_price, strike, cost_usd,
                breakeven_down, breakeven_up. Everything else optional.

        Returns:
            True if a new row was inserted, False if a row for this
            (currency, expiration, scan_time) already existed (dedup skip).
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO straddle_scan_history (
                    scan_time, currency, expiration,
                    dte, future_price, index_price,
                    strike, call_ask_usd, put_ask_usd, cost_usd,
                    breakeven_down, breakeven_up,
                    atm_iv, iv_percentile, iv_percentile_n_obs, iv_percentile_window_days,
                    rv, rv_iv_ratio, vrp, min_pnl_score, deribit_url
                ) VALUES (
                    %(scan_time)s, %(currency)s, %(expiration)s,
                    %(dte)s, %(future_price)s, %(index_price)s,
                    %(strike)s, %(call_ask_usd)s, %(put_ask_usd)s, %(cost_usd)s,
                    %(breakeven_down)s, %(breakeven_up)s,
                    %(atm_iv)s, %(iv_percentile)s, %(iv_percentile_n_obs)s, %(iv_percentile_window_days)s,
                    %(rv)s, %(rv_iv_ratio)s, %(vrp)s, %(min_pnl_score)s, %(deribit_url)s
                )
                ON CONFLICT (currency, expiration, scan_time) DO NOTHING
                RETURNING id
            """, {
                "scan_time": row["scan_time"],
                "currency": row["currency"],
                "expiration": row["expiration"],
                "dte": row["dte"],
                "future_price": row["future_price"],
                "index_price": row["index_price"],
                "strike": row["strike"],
                "call_ask_usd": row.get("call_ask_usd"),
                "put_ask_usd": row.get("put_ask_usd"),
                "cost_usd": row["cost_usd"],
                "breakeven_down": row["breakeven_down"],
                "breakeven_up": row["breakeven_up"],
                "atm_iv": row.get("atm_iv"),
                "iv_percentile": row.get("iv_percentile"),
                "iv_percentile_n_obs": row.get("iv_percentile_n_obs"),
                "iv_percentile_window_days": row.get("iv_percentile_window_days"),
                "rv": row.get("rv"),
                "rv_iv_ratio": row.get("rv_iv_ratio"),
                "vrp": row.get("vrp"),
                "min_pnl_score": row.get("min_pnl_score"),
                "deribit_url": row.get("deribit_url"),
            })
            inserted = cursor.fetchone()
            return inserted is not None

    def get_unresolved_straddle_scans(self, currency: str) -> List[Dict[str, Any]]:
        """
        Return straddle_scan_history rows for `currency` awaiting settlement
        resolution (resolved_at IS NULL), oldest first.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").

        Returns:
            List of dicts: {id, expiration, scan_time, strike, cost_usd}.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT id, expiration, scan_time, strike, cost_usd
                FROM straddle_scan_history
                WHERE currency = %s AND resolved_at IS NULL
                ORDER BY scan_time ASC
            """, (currency,))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0], "expiration": r[1], "scan_time": r[2],
                    "strike": float(r[3]), "cost_usd": float(r[4]),
                }
                for r in rows
            ]

    def resolve_straddle_scan(
        self,
        scan_id: int,
        settlement_index_price: float,
        settlement_pnl_usd: float,
        settlement_return_pct: float,
        resolved_at,
    ) -> None:
        """
        Fill in the settlement resolution fields for a straddle_scan_history row.

        Args:
            scan_id: Primary key of the row.
            settlement_index_price: Underlying settlement price used to
                compute the P&L.
            settlement_pnl_usd: |settlement_index_price - strike| - cost_usd.
            settlement_return_pct: settlement_pnl_usd / cost_usd.
            resolved_at: Timestamp of resolution.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                UPDATE straddle_scan_history
                SET settlement_index_price = %s,
                    settlement_pnl_usd = %s,
                    settlement_return_pct = %s,
                    resolved_at = %s
                WHERE id = %s
            """, (
                settlement_index_price, settlement_pnl_usd,
                settlement_return_pct, resolved_at, scan_id,
            ))

    def get_last_alert_for_expiry(
        self,
        currency: str,
        expiration: str
    ) -> Optional[Dict[str, Any]]:
        """
        Return the most recently alerted straddle_scan_history row for
        (currency, expiration) — used by the alert rate-limit rule.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "25SEP26").

        Returns:
            Dict {iv_percentile, alert_sent_at}, or None if never alerted.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT iv_percentile, alert_sent_at
                FROM straddle_scan_history
                WHERE currency = %s AND expiration = %s AND alert_sent = TRUE
                ORDER BY alert_sent_at DESC
                LIMIT 1
            """, (currency, expiration))
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "iv_percentile": float(row[0]) if row[0] is not None else None,
                "alert_sent_at": row[1],
            }

    def mark_straddle_scan_alert_sent(
        self,
        currency: str,
        expiration: str,
        scan_time,
    ) -> None:
        """
        Mark the straddle_scan_history row for this (currency, expiration,
        scan_time) as alerted — called right after a successful Telegram send.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "25SEP26").
            scan_time: The scan cycle's timestamp (same value passed to
                save_straddle_scan for this row).
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                UPDATE straddle_scan_history
                SET alert_sent = TRUE, alert_sent_at = NOW()
                WHERE currency = %s AND expiration = %s AND scan_time = %s
            """, (currency, expiration, scan_time))

    # ── OTM Contract Finder ───────────────────────────────────────────────────

    def get_dvol_history_before(
        self,
        asset: str,
        before_time: datetime,
        days: int = 365
    ) -> List[float]:
        """
        Return DVOL close values for the trailing window ending at a historical time.

        Used for reconstructing 365d IV percentile against a past snapshot_hour
        (the live system anchors this window on "now" — see
        OnChainAnalysisService._fetch_market_metrics, on_chain_analysis_service.py:896).
        Returns oldest-first, plain floats (dvol_value), empty list on error.

        Note: dvol_history only stores daily close-equivalent dvol_value, not
        daily high/low — so this can reconstruct iv_percentile_365d (close-based)
        but NOT iv_rank_365d (true-range based, needs high/low that was never persisted).

        Args:
            asset: Currency symbol (e.g., "BTC", "ETH").
            before_time: Anchor — only rows with timestamp <= this are included.
            days: Trailing window size in days (default 365, matches live lookback).
        """
        try:
            window_start = before_time - timedelta(days=days)
            with self._db_cursor() as cursor:
                cursor.execute(
                    "SELECT dvol_value FROM dvol_history "
                    "WHERE asset = %s AND timestamp >= %s AND timestamp <= %s "
                    "ORDER BY timestamp ASC",
                    (asset, window_start, before_time),
                )
                return [float(r[0]) for r in cursor.fetchall()]
        except Exception as exc:
            logger.warning("get_dvol_history failed for %s: %s", asset, exc)
            return []

    # ------------------------------------------------------------------
    # Phase 3 — Forward-test predictions
    # ------------------------------------------------------------------

    def save_forward_prediction(self, prediction: Dict[str, Any]) -> None:
        """
        Insert a forward-test prediction row.

        Args:
            prediction: Dict with keys matching forward_test_predictions columns.
                Required: currency, snapshot_hour, spot_price_at_prediction,
                          signal_direction, signal_score, signal_confidence.
                Optional metric/z-score fields default to None.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO forward_test_predictions (
                    currency, snapshot_hour,
                    itm_put_oi_pct, otm_put_oi_pct,
                    itm_call_oi_pct, otm_call_oi_pct,
                    max_pain_distance_pct, pc_far_otm_ratio,
                    spot_price_at_prediction,
                    signal_direction, signal_score, signal_confidence,
                    z_itm_put_oi_pct, z_otm_put_oi_pct,
                    z_itm_call_oi_pct, z_otm_call_oi_pct,
                    z_max_pain_distance_pct, z_pc_far_otm_ratio
                ) VALUES (
                    %(currency)s, %(snapshot_hour)s,
                    %(itm_put_oi_pct)s, %(otm_put_oi_pct)s,
                    %(itm_call_oi_pct)s, %(otm_call_oi_pct)s,
                    %(max_pain_distance_pct)s, %(pc_far_otm_ratio)s,
                    %(spot_price_at_prediction)s,
                    %(signal_direction)s, %(signal_score)s, %(signal_confidence)s,
                    %(z_itm_put_oi_pct)s, %(z_otm_put_oi_pct)s,
                    %(z_itm_call_oi_pct)s, %(z_otm_call_oi_pct)s,
                    %(z_max_pain_distance_pct)s, %(z_pc_far_otm_ratio)s
                )
                ON CONFLICT (currency, snapshot_hour) DO NOTHING
            """, {
                "currency": prediction["currency"],
                "snapshot_hour": prediction["snapshot_hour"],
                "itm_put_oi_pct": prediction.get("itm_put_oi_pct"),
                "otm_put_oi_pct": prediction.get("otm_put_oi_pct"),
                "itm_call_oi_pct": prediction.get("itm_call_oi_pct"),
                "otm_call_oi_pct": prediction.get("otm_call_oi_pct"),
                "max_pain_distance_pct": prediction.get("max_pain_distance_pct"),
                "pc_far_otm_ratio": prediction.get("pc_far_otm_ratio"),
                "spot_price_at_prediction": prediction["spot_price_at_prediction"],
                "signal_direction": prediction["signal_direction"],
                "signal_score": prediction["signal_score"],
                "signal_confidence": prediction["signal_confidence"],
                "z_itm_put_oi_pct": prediction.get("z_itm_put_oi_pct"),
                "z_otm_put_oi_pct": prediction.get("z_otm_put_oi_pct"),
                "z_itm_call_oi_pct": prediction.get("z_itm_call_oi_pct"),
                "z_otm_call_oi_pct": prediction.get("z_otm_call_oi_pct"),
                "z_max_pain_distance_pct": prediction.get("z_max_pain_distance_pct"),
                "z_pc_far_otm_ratio": prediction.get("z_pc_far_otm_ratio"),
            })

    def get_unresolved_predictions(
        self,
        currency: str,
        older_than_hours: float = 1.0
    ) -> List[Dict[str, Any]]:
        """
        Return predictions that have no resolution and are at least `older_than_hours` old.

        Args:
            currency: Currency filter.
            older_than_hours: How many hours must have passed since snapshot_hour.

        Returns:
            List of dicts with id, snapshot_hour, spot_price_at_prediction,
            signal_direction.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT id, snapshot_hour, spot_price_at_prediction, signal_direction
                FROM forward_test_predictions
                WHERE currency = %s
                  AND resolved_at IS NULL
                  AND snapshot_hour <= NOW() - INTERVAL '1 hour' * %s
                ORDER BY snapshot_hour ASC
            """, (currency, older_than_hours))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "snapshot_hour": r[1],
                    "spot_price_at_prediction": float(r[2]),
                    "signal_direction": r[3],
                }
                for r in rows
            ]

    def resolve_prediction(
        self,
        prediction_id: int,
        spot_price_at_resolution: float,
        resolved_at,
    ) -> None:
        """
        Fill in the resolution fields for a prediction row.

        Args:
            prediction_id: Primary key of the prediction.
            spot_price_at_resolution: Spot price ~1h after prediction.
            resolved_at: Timestamp of resolution.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                UPDATE forward_test_predictions
                SET spot_price_at_resolution = %s,
                    resolved_at = %s,
                    actual_1h_return_pct = ((%s - spot_price_at_prediction)
                                            / spot_price_at_prediction * 100),
                    signal_correct = CASE
                        WHEN signal_direction = 'neutral' THEN NULL
                        WHEN signal_direction = 'bullish'
                             AND (%s - spot_price_at_prediction) > 0 THEN TRUE
                        WHEN signal_direction = 'bearish'
                             AND (%s - spot_price_at_prediction) < 0 THEN TRUE
                        ELSE FALSE
                    END
                WHERE id = %s
            """, (
                spot_price_at_resolution,
                resolved_at,
                spot_price_at_resolution,
                spot_price_at_resolution,
                spot_price_at_resolution,
                prediction_id,
            ))

    def get_latest_spot_price(self, currency: str) -> Optional[float]:
        """
        Return the most recent underlying_price for `currency` from
        onchain_analysis_snapshots.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").

        Returns:
            Latest spot price, or None if no data.
        """
        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    SELECT underlying_price
                    FROM onchain_analysis_snapshots
                    WHERE currency = %s
                    ORDER BY snapshot_hour DESC
                    LIMIT 1
                """, (currency,))
                row = cursor.fetchone()
                return float(row[0]) if row else None
        except Exception as exc:
            logger.warning("get_latest_spot_price failed for %s: %s", currency, exc)
            return None

    def get_forward_test_stats(self, currency: str) -> Dict[str, Any]:
        """
        Compute track-record statistics for resolved forward-test predictions.

        Returns a dict with: n_total, n_resolved, n_signals, hit_rate,
        mean_return_on_signal, std_return_on_signal, information_ratio.
        Neutral predictions are excluded from hit_rate and IR calculations.

        Args:
            currency: Currency to query.

        Returns:
            Statistics dict. Returns zeroed dict if no resolved data.
        """
        empty = {
            "n_total": 0, "n_resolved": 0, "n_signals": 0,
            "hit_rate": None, "mean_return_on_signal": None,
            "std_return_on_signal": None, "information_ratio": None,
        }
        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    SELECT
                        COUNT(*) AS n_total,
                        COUNT(resolved_at) AS n_resolved,
                        COUNT(CASE WHEN signal_direction != 'neutral' AND resolved_at IS NOT NULL
                                   THEN 1 END) AS n_signals,
                        AVG(CASE WHEN signal_direction != 'neutral' AND signal_correct IS NOT NULL
                                 THEN signal_correct::int END) AS hit_rate,
                        AVG(CASE WHEN signal_direction != 'neutral' AND resolved_at IS NOT NULL
                                 THEN actual_1h_return_pct END) AS mean_return,
                        STDDEV(CASE WHEN signal_direction != 'neutral' AND resolved_at IS NOT NULL
                                    THEN actual_1h_return_pct END) AS std_return
                    FROM forward_test_predictions
                    WHERE currency = %s
                """, (currency,))
                row = cursor.fetchone()
                if not row or row[2] == 0:
                    return empty
                n_total, n_resolved, n_signals, hit_rate, mean_ret, std_ret = row
                ir = (float(mean_ret) / float(std_ret)) if std_ret and float(std_ret) > 0 else None
                return {
                    "n_total": int(n_total),
                    "n_resolved": int(n_resolved),
                    "n_signals": int(n_signals),
                    "hit_rate": float(hit_rate) if hit_rate is not None else None,
                    "mean_return_on_signal": float(mean_ret) if mean_ret is not None else None,
                    "std_return_on_signal": float(std_ret) if std_ret is not None else None,
                    "information_ratio": ir,
                }
        except Exception as exc:
            logger.error("get_forward_test_stats failed for %s: %s", currency, exc)
            return empty

    def get_recent_onchain_history(
        self,
        currency: str,
        metric_columns: List[str],
        lookback_hours: int = 720,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent front-month (nearest expiry) on-chain metric values
        for z-score normalization in the forward-test harness.

        Args:
            currency: Currency symbol.
            metric_columns: List of column names to fetch from
                onchain_analysis_snapshots.
            lookback_hours: How many hours of history to include (default 30d).

        Returns:
            List of dicts keyed by metric name, ordered oldest-first.
        """
        safe_cols = [
            c for c in metric_columns
            if c in {
                "itm_put_oi_pct", "otm_put_oi_pct",
                "itm_call_oi_pct", "otm_call_oi_pct",
                "max_pain_distance_pct",
            }
        ]
        vol_cols = [
            c for c in metric_columns if c == "pc_far_otm_ratio"
        ]

        results = {}

        if safe_cols:
            col_list = ", ".join(safe_cols)
            try:
                with self._db_cursor() as cursor:
                    cursor.execute(f"""
                        SELECT snapshot_hour, {col_list}
                        FROM onchain_analysis_snapshots
                        WHERE currency = %s
                          AND snapshot_hour >= NOW() - INTERVAL '1 hour' * %s
                          AND expiration = (
                              SELECT expiration
                              FROM onchain_analysis_snapshots AS sub
                              WHERE sub.currency = %s
                                AND sub.snapshot_hour = onchain_analysis_snapshots.snapshot_hour
                              ORDER BY sub.expiration ASC
                              LIMIT 1
                          )
                        ORDER BY snapshot_hour ASC
                    """, (currency, lookback_hours, currency))
                    rows = cursor.fetchall()
                    colnames = ["snapshot_hour"] + safe_cols
                    for row in rows:
                        d = dict(zip(colnames, row))
                        h = d["snapshot_hour"]
                        if h not in results:
                            results[h] = {"snapshot_hour": h}
                        results[h].update({k: float(v) if v is not None else None
                                           for k, v in d.items() if k != "snapshot_hour"})
            except Exception as exc:
                logger.warning("get_recent_onchain_history (analysis) failed: %s", exc)

        if vol_cols:
            try:
                with self._db_cursor() as cursor:
                    cursor.execute("""
                        SELECT snapshot_hour, pc_far_otm_ratio
                        FROM onchain_volatility_snapshots
                        WHERE currency = %s
                          AND snapshot_hour >= NOW() - INTERVAL '1 hour' * %s
                          AND expiration = (
                              SELECT expiration
                              FROM onchain_volatility_snapshots AS sub
                              WHERE sub.currency = %s
                                AND sub.snapshot_hour = onchain_volatility_snapshots.snapshot_hour
                              ORDER BY sub.expiration ASC
                              LIMIT 1
                          )
                        ORDER BY snapshot_hour ASC
                    """, (currency, lookback_hours, currency))
                    rows = cursor.fetchall()
                    for row in rows:
                        h = row[0]
                        val = float(row[1]) if row[1] is not None else None
                        if h not in results:
                            results[h] = {"snapshot_hour": h}
                        results[h]["pc_far_otm_ratio"] = val
            except Exception as exc:
                logger.warning("get_recent_onchain_history (vol) failed: %s", exc)

        return sorted(results.values(), key=lambda x: x["snapshot_hour"])
