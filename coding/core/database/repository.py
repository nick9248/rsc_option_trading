"""
Database repository for on-chain analysis data storage.

Provides methods to save and retrieve data from PostgreSQL tables.
"""

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.gex_dex_results import GexDexResult
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

        Persists `mark_iv` from each book-summary item (institutional_metrics_spec.md
        Migration M1 / Decision D11) -- the full-chain hourly capture historically
        dropped it even though get_book_summary already returns it. NULL when the
        item lacks the key (defensive; the live API always includes it for options).

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
                        underlying_price, mark_price, bid_price, ask_price, mark_iv
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        item.get("mark_iv"),
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

        Mirrors the {iv, amount} shape VolatilityReconstructionService._calculate_vwap_iv
        consumes for its VWAP leg. Note: unlike
        OnChainAnalysisService._calculate_vwap_iv (on_chain_analysis_service.py:461,
        fixed per bugfix_spec.md Item 3 to use a volume-weighted "matched"
        mark-IV baseline), this query does not select instrument_name, so
        the reconstruction path cannot attribute a trade to a specific
        instrument's mark_iv and still uses the older, unweighted chain
        average as its second leg — see
        VolatilityReconstructionService._calculate_vwap_iv's docstring.

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

    def get_signed_taker_flow_by_strike(
        self,
        currency: str,
        expiration: str,
        since_ts: int,
    ) -> List[Dict[str, Any]]:
        """
        Signed cumulative taker flow per strike/option_type since ``since_ts``
        (institutional_metrics_spec.md section 2 -- Glassnode taker-flow
        method, task C3). Feeds ``DealerInventoryCalculator`` after negation
        (``dealer_net = -taker_net``); this method returns the raw taker-side
        signed sum, never flips it (``direction`` is the taker's side by
        Deribit convention -- spec §2(c) edge cases).

        ``AND direction IS NOT NULL`` is defensive, not dictated by the
        spec's own SQL: current data is verified clean (0 nulls across
        2.28M+ rows), but without this filter a future null direction would
        silently fall into the CASE's ELSE branch and be counted as a sell,
        corrupting the signed sum. Matches the filter already established at
        this same table for the same purpose in
        ``get_trades_for_flow_analysis``.

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "27MAR26").
            since_ts: Window start (T0), epoch milliseconds.

        Returns:
            List of dicts with keys: strike, option_type, taker_net,
            gross_volume, trade_count, first_ts.
        """
        query = """
            SELECT strike, option_type,
                   SUM(CASE WHEN direction='buy' THEN amount ELSE -amount END) AS taker_net,
                   SUM(amount) AS gross_volume,
                   COUNT(*) AS trade_count,
                   MIN(trade_timestamp) AS first_ts
            FROM historical_trades
            WHERE currency = %s
                AND expiration = %s
                AND trade_timestamp >= %s
                AND strike IS NOT NULL
                AND direction IS NOT NULL
            GROUP BY strike, option_type
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, expiration, since_ts))

            # strike/taker_net/gross_volume come back as Decimal (NUMERIC
            # columns) -- cast to float at the repository boundary (matches
            # the established get_previous_oi_snapshot convention). Left
            # uncast, DealerInventoryCalculator's arithmetic against
            # float-valued Greeks (dealer_net * gamma) would raise
            # `TypeError: unsupported operand type(s) for *: 'decimal.
            # Decimal' and 'float'` -- Decimal/float compare and hash equal
            # (dict-key lookups by (strike, option_type) are unaffected),
            # but Decimal/float arithmetic is not allowed.
            return [
                {
                    "strike": float(row[0]),
                    "option_type": row[1],
                    "taker_net": float(row[2]),
                    "gross_volume": float(row[3]),
                    "trade_count": int(row[4]),
                    "first_ts": int(row[5]) if row[5] is not None else None,
                }
                for row in cursor.fetchall()
            ]

    def get_trades_for_delta_flow(
        self,
        currency: str,
        start_ts: int,
        end_ts: int,
    ) -> List[Dict[str, Any]]:
        """
        Fetch trades for signed delta-weighted flow computation
        (institutional_metrics_spec.md section 6 / task C7 --
        ``DeltaFlowCalculator.enrich_trade``/``compute_hourly_buckets``).

        ``historical_trades`` has no delta column; the returned rows carry
        every column ``DeltaFlowCalculator`` needs to recompute BS delta
        itself (``iv``, ``strike``, ``index_price``, ``instrument_name`` for
        its expiry) plus ``direction``/``amount`` for the signed-
        contribution math and ``expiration`` as the per-expiry grouping
        key. Raw types returned uncast (matches ``get_trades_for_flow_
        analysis``'s convention at this same table) -- ``DeltaFlowCalculator.
        enrich_trade`` does its own float casting.

        ``AND direction IS NOT NULL AND strike IS NOT NULL`` mirrors the
        established filter at this same table
        (``get_signed_taker_flow_by_strike`` / ``get_trades_for_flow_
        analysis``) -- defensive; current data is verified clean (0 nulls
        over the last 7 days) but a future null direction must never
        silently reach ``DeltaFlowCalculator``'s own direction check in a
        way that's hard to attribute back to a query gap.

        Args:
            currency: Currency symbol (BTC or ETH).
            start_ts: Window start (hour bucket start), epoch milliseconds,
                inclusive.
            end_ts: Window end (hour bucket end), epoch milliseconds,
                exclusive.

        Returns:
            List of dicts with keys: trade_id, trade_timestamp,
            instrument_name, expiration, strike, option_type, direction,
            amount, price, index_price, iv.
        """
        query = """
            SELECT
                trade_id, trade_timestamp, instrument_name, expiration,
                strike, option_type, direction, amount, price, index_price, iv
            FROM historical_trades
            WHERE currency = %s
                AND trade_timestamp >= %s
                AND trade_timestamp < %s
                AND direction IS NOT NULL
                AND strike IS NOT NULL
            ORDER BY trade_timestamp ASC
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, start_ts, end_ts))

            columns = [
                "trade_id", "trade_timestamp", "instrument_name", "expiration",
                "strike", "option_type", "direction", "amount", "price",
                "index_price", "iv",
            ]

            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def save_delta_flow_hourly(
        self,
        currency: str,
        snapshot_hour: datetime,
        bucket: FlowBucket,
    ) -> None:
        """
        Upsert one ``flow_delta_hourly`` row (institutional_metrics_spec.md
        section 6 / infra_spec.md section 2 -- task C7). One row per
        ``(snapshot_hour, currency, expiration)`` -- ``expiration == "ALL"``
        is the currency-level rollup.

        ``ON CONFLICT ... DO UPDATE`` (not ``DO NOTHING``) mirrors the
        ``onchain_analysis_snapshots``/``hourly_snapshots`` convention at
        this same ``(snapshot_hour, currency, expiration)`` unique key: a
        daemon re-run for an hour that already has a row (crash/restart, or
        trades that arrived late) must REFRESH the aggregate from the
        latest trade data, not freeze at whatever partial data existed on
        the first attempt.

        Args:
            currency: Currency symbol (BTC or ETH).
            snapshot_hour: UTC hour bucket, already hour-aligned by the
                caller -- matches the convention established for
                ``hourly_snapshots``/``onchain_analysis_snapshots``.
            bucket: ``FlowBucket`` to persist.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO flow_delta_hourly (
                    snapshot_hour, currency, expiration,
                    hiro_usd, premium_usd, gross_delta_usd,
                    net_contracts, gross_contracts,
                    trade_count, buy_count, sell_count, skipped_count
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (snapshot_hour, currency, expiration) DO UPDATE SET
                    hiro_usd = EXCLUDED.hiro_usd,
                    premium_usd = EXCLUDED.premium_usd,
                    gross_delta_usd = EXCLUDED.gross_delta_usd,
                    net_contracts = EXCLUDED.net_contracts,
                    gross_contracts = EXCLUDED.gross_contracts,
                    trade_count = EXCLUDED.trade_count,
                    buy_count = EXCLUDED.buy_count,
                    sell_count = EXCLUDED.sell_count,
                    skipped_count = EXCLUDED.skipped_count
            """, (
                snapshot_hour, currency, bucket.expiration,
                bucket.hiro_usd, bucket.premium_usd, bucket.gross_delta_usd,
                bucket.net_contracts, bucket.gross_contracts,
                bucket.trade_count, bucket.buy_count, bucket.sell_count, bucket.skipped_count,
            ))

    def get_delta_flow_summary(
        self,
        currency: str,
        since: datetime,
    ) -> List[Dict[str, Any]]:
        """
        Sum ``flow_delta_hourly`` rows since ``since``, grouped by
        expiration (institutional_metrics_spec.md section 6 / task C7 --
        the report's "DELTA-ADJUSTED FLOW (24h)" section reads this instead
        of recomputing from raw trades at report time).

        Returns ``[]`` when no rows exist in the window (e.g. the feature
        just shipped, or the daemon hasn't run yet) -- never a fabricated
        zero-valued summary row. A currency/hour that genuinely had zero
        trades still has a real, persisted "ALL" row with trade_count == 0
        (``ProspectiveCollector._persist_delta_flow`` writes it
        explicitly) -- that is a legitimate SUM input, not degenerate.

        Args:
            currency: Currency symbol (BTC or ETH).
            since: Window start (inclusive) -- report callers pass
                ``now - timedelta(hours=24)``.

        Returns:
            List of dicts with keys: expiration, hiro_usd, premium_usd,
            gross_delta_usd, net_contracts, gross_contracts, trade_count,
            buy_count, sell_count, skipped_count.
        """
        query = """
            SELECT
                expiration,
                SUM(hiro_usd), SUM(premium_usd), SUM(gross_delta_usd),
                SUM(net_contracts), SUM(gross_contracts),
                SUM(trade_count), SUM(buy_count), SUM(sell_count), SUM(skipped_count)
            FROM flow_delta_hourly
            WHERE currency = %s
                AND snapshot_hour >= %s
            GROUP BY expiration
        """

        _INT_COLUMNS = ("trade_count", "buy_count", "sell_count", "skipped_count")

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, since))

            columns = [
                "expiration", "hiro_usd", "premium_usd", "gross_delta_usd",
                "net_contracts", "gross_contracts", "trade_count", "buy_count",
                "sell_count", "skipped_count",
            ]

            results = []
            for row in cursor.fetchall():
                row_dict: Dict[str, Any] = {"expiration": row[0]}
                for col, val in zip(columns[1:], row[1:]):
                    row_dict[col] = int(val) if col in _INT_COLUMNS else float(val)
                results.append(row_dict)
            return results

    def get_delta_flow_coverage(
        self,
        currency: str,
        since: datetime,
    ) -> Dict[str, Any]:
        """
        Coverage/recency signal for ``flow_delta_hourly`` (institutional_
        metrics_spec.md section 6 / task C7 review fix, Important #4 --
        task-C7-brief.md explicitly named "a currency with a stale/lagging
        daemon" as a case to handle, and the original implementation
        didn't). ``get_delta_flow_summary``'s SUMs alone cannot disclose a
        gap: a daemon down for 12h still produces a confident-looking
        total over whatever rows DID land, with the report header still
        claiming the full lookback window.

        Counts ONLY ``expiration == 'ALL'`` rows -- ``ProspectiveCollector.
        _persist_delta_flow``'s always-write-ALL invariant guarantees
        exactly one such row per hour the daemon actually ran, so this is
        a clean "how many of the expected hours are present" signal.
        Counting per-expiration rows too would overstate presence
        (multiple rows can exist for the same hour, one per traded
        expiration).

        Args:
            currency: Currency symbol (BTC or ETH).
            since: Window start (inclusive) -- callers pass the SAME value
                given to ``get_delta_flow_summary``, so both describe the
                same window.

        Returns:
            Dict with ``hours_present`` (int, 0 if none) and
            ``max_snapshot_hour`` (the most recently persisted hour, or
            ``None`` if no rows at all since ``since``).
        """
        query = """
            SELECT COUNT(*), MAX(snapshot_hour)
            FROM flow_delta_hourly
            WHERE currency = %s AND expiration = 'ALL' AND snapshot_hour >= %s
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, since))
            row = cursor.fetchone()

        hours_present = int(row[0]) if row and row[0] is not None else 0
        max_snapshot_hour = row[1] if row else None
        return {"hours_present": hours_present, "max_snapshot_hour": max_snapshot_hour}

    def get_trade_hour_coverage(
        self,
        currency: str,
        since_ts: int,
    ) -> Tuple[int, int]:
        """
        Table-wide (currency-wide) trade-history hour coverage since
        ``since_ts`` (institutional_metrics_spec.md section 2 / task C3 --
        the trade-history half of decision D9's gate; the other half is
        ``DealerInventoryCalculator.coverage_report``'s violation rate).

        Deliberately NOT filtered by expiration (fix round, Important #2,
        orchestrator ruling -- a deviation from the spec's own listed
        3-argument signature, `get_trade_hour_coverage(currency, expiration,
        since_ts)`, which this task's first pass implemented literally and
        which turned out to measure the wrong thing). Section 2(a)'s own
        empirical validation measured collector-wide completeness ("Trailing
        90 days: 2,138 hours in range, 2,137 present") across ALL trades for
        a currency, not one contract's own trading activity. Filtering by
        expiration here measures that specific strike/expiry's LIQUIDITY (did
        anyone trade THIS contract every hour) rather than whether the
        collector was capturing trades AT ALL during that hour -- those are
        different questions, and D9's gate needs the second one. The
        per-expiry version inverted which expiries the spec's own violation-
        rate study validated as trustworthy: short-dated, fully-covered
        expiries (0% violations in that study) scored low on per-contract
        hour presence and got permanently gated out, while only thin-volume,
        high-open-interest long-dated contracts could ever pass.

        ``AND direction IS NOT NULL AND strike IS NOT NULL`` mirrors
        ``get_signed_taker_flow_by_strike``'s own filters (fix round, Minor
        #1) -- without them this method could count an hour as "present"
        from a row the flow query would exclude, overstating coverage in
        exactly the direction the gate exists to guard against.

        Args:
            currency: Currency symbol (BTC or ETH).
            since_ts: Window start (T0), epoch milliseconds.

        Returns:
            (present_hours, expected_hours) -- present_hours is the count of
            distinct UTC hour buckets with at least one trade (for this
            currency, any expiration) since since_ts; expected_hours is the
            wall-clock hours between since_ts and now (0 if since_ts is in
            the future / equal to now, never negative).
        """
        query = """
            SELECT COUNT(DISTINCT DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000)))
            FROM historical_trades
            WHERE currency = %s
                AND trade_timestamp >= %s
                AND direction IS NOT NULL
                AND strike IS NOT NULL
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, since_ts))
            row = cursor.fetchone()
            present_hours = int(row[0]) if row and row[0] is not None else 0

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        expected_hours = max(0, (now_ms - since_ts) // (3600 * 1000))

        return present_hours, expected_hours

    def get_first_trade_timestamp(self, currency: str, expiration: str) -> Optional[int]:
        """
        Earliest trade ever recorded for this expiry (no ``since`` filter --
        institutional_metrics_spec.md section 2 / task C3's T0 decision:
        ``T0 = max(first_listing_seen, coverage_start)``). ``None`` when no
        trades exist for this expiry at all (e.g. a brand-new listing, or an
        expiry the collector never covered).

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string.

        Returns:
            Earliest trade_timestamp (epoch milliseconds), or None.
        """
        query = """
            SELECT MIN(trade_timestamp)
            FROM historical_trades
            WHERE currency = %s AND expiration = %s
        """

        with self._db_cursor() as cursor:
            cursor.execute(query, (currency, expiration))
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else None

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
                    futures_price = EXCLUDED.futures_price,
                    basis = EXCLUDED.basis,
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
        snapshot_date: Optional[datetime] = None,
        snapshot_hour_utc: int = 8,
    ) -> int:
        """
        Save daily OI snapshot for all instruments in an expiration.

        Uses UPSERT to avoid duplicates within the same (date, hour).

        institutional_metrics_spec.md section 7(c) Migration M8 (Task E4):
        ``snapshot_hour_utc`` (migration 023) is now part of the conflict
        key so ``ProspectiveCollector``'s daemon write at 08:00 UTC can
        never be silently overwritten by a later GUI run
        (``on_chain_analysis_service.py``, still calling this method with
        no explicit hour, per its "harmless, upserts the same day" design)
        at a DIFFERENT hour of the same day. The literal default of ``8``
        here matches the column's own DB default and Deribit's settlement
        hour (``MarketWideCalculator.DERIBIT_SETTLEMENT_HOUR_UTC`` /
        ``GexDexCalculator._DERIBIT_SETTLEMENT_HOUR_UTC`` / this class's
        own ``_FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC``) -- a caller that omits
        it (the GUI) still lands on the same anchor hour the daemon uses.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            instruments: List of enriched instrument dicts with strike, option_type,
                        open_interest, mark_iv.
            underlying_price: Current underlying price.
            snapshot_date: Date for snapshot. Uses today (UTC) if not provided.
            snapshot_hour_utc: UTC hour this row anchors to. Defaults to 8
                (Deribit settlement), matching the column's DB default.

        Returns:
            Number of rows upserted.
        """
        if not instruments:
            return 0

        # Independent review (Task C8 fix round, Important #2): this
        # method is called with no explicit snapshot_date from
        # OnChainAnalysisService._calculate_oi_changes_and_iv_percentile,
        # in the SAME analysis run that now also calls
        # _calculate_fixed_strike_vol_matrix -- Task C8's new exact-date
        # lookup (get_chain_iv_at) depends on this write's date label
        # being correct. The old `datetime.now().date()` default is this
        # (non-UTC) machine's LOCAL date -- on this UTC+2 machine, any run
        # between 00:00-02:00 local time would label a row with the WRONG
        # calendar day (a ~35-hour-old snapshot could be mislabelled as
        # "yesterday"), exactly the failure mode spec section 7(c)'s edge
        # cases forbid. UTC-explicit, matching every other day-boundary
        # fix in this campaign (Tasks C4/C5/C7/C8).
        snap_date = snapshot_date or datetime.now(timezone.utc).date()
        if isinstance(snap_date, datetime):
            snap_date = snap_date.date()

        try:
            with self._db_cursor() as cursor:
                # institutional_metrics_spec.md section 7(c) Migration M8
                # (Task E4): snapshot_hour_utc is now part of both the
                # inserted columns AND the ON CONFLICT target (migration
                # 023 widened the unique constraint to match) -- this is
                # what actually prevents a later run at a different hour
                # of the same day from overwriting the daemon's 08:00 UTC
                # anchor row.
                insert_sql = """
                    INSERT INTO daily_oi_snapshots (
                        snapshot_date, snapshot_hour_utc, currency, expiration, strike,
                        option_type, open_interest, mark_iv, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (snapshot_date, snapshot_hour_utc, currency, expiration, strike, option_type)
                    DO UPDATE SET
                        open_interest = EXCLUDED.open_interest,
                        mark_iv = EXCLUDED.mark_iv,
                        underlying_price = EXCLUDED.underlying_price
                """

                rows = []
                for inst in instruments:
                    rows.append((
                        snap_date,
                        snapshot_hour_utc,
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
                    f"{currency} {expiration} ({snap_date} {snapshot_hour_utc:02d}:00 UTC)"
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
        # Fix round 2 (Important, "paired sibling" pattern this campaign
        # has now hit in Tasks C3/C5/C7/C8): this method is this table's
        # READER, called ~8 lines away from save_daily_oi_snapshot's
        # WRITE in the same on_chain_analysis_service.py loop iteration.
        # save_daily_oi_snapshot's default was fixed to
        # datetime.now(timezone.utc).date() in the round-1 fix; leaving
        # THIS default on naive-local datetime.now() desynced the pair --
        # on this UTC+2 machine, a run during the UTC 22:00-24:00 window
        # (local 00:00-02:00) would write a row dated "today UTC" via the
        # sibling's now-correct default, then this method's still-local
        # "yesterday" would resolve to that SAME UTC date, comparing a
        # snapshot against itself and reporting ~zero OI change for every
        # strike. UTC-explicit, matching the sibling.
        if before_date is None:
            target_date = datetime.now(timezone.utc).date() - timedelta(days=1)
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

    # institutional_metrics_spec.md section 7(b): the snapshots fallback
    # anchors to this UTC hour, matching Deribit's daily settlement
    # convention -- the same anchor migration M8 would eventually pin
    # daily_oi_snapshots to (not yet implemented; see get_chain_iv_at's
    # docstring).
    _FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC = 8

    # Independent review (Task C8 fix round, Minor #2): the nearest tick
    # to 08:00 UTC on the requested date must be a genuine local read of
    # that anchor, not "whatever tick happened to exist that day, however
    # far away" -- a tick at 23:30 silently used as "the 08:00 anchor"
    # with no warning would be exactly the kind of misleading-precision
    # bug this campaign's exhaustive-gate standard exists to catch.
    _FIXED_STRIKE_VOL_ANCHOR_TOLERANCE_HOURS = 3

    def get_chain_iv_at(
        self,
        currency: str,
        expiration: str,
        snapshot_date,
    ) -> Dict[str, Any]:
        """
        Fetch the full per-strike ``mark_iv`` chain for one (currency,
        expiration) on EXACTLY ``snapshot_date`` (institutional_metrics_
        spec.md section 7 / Task C8's fixed-strike vol change matrix).

        Never substitutes the nearest available date -- that decision
        belongs to the caller (``FixedStrikeVolCalculator``'s stale-prior
        guard, T7.3), which needs to know the exact requested date came up
        empty, not silently receive a plausible-looking older snapshot
        mislabelled as "yesterday".

        Reads ``daily_oi_snapshots`` first (real per-strike ``mark_iv``
        history, though GUI-triggered and therefore sparse/irregular --
        [verified 2026-08-01] 90 distinct dates all-time, non-consecutive,
        most recent two entries 7 days apart). Falls back to ``snapshots``
        (the daemon's full ~900-instrument hourly chain, ``mark_iv``
        populated by migration 017 / Decision D11) only when
        ``daily_oi_snapshots`` has no rows for this exact date -- picks the
        hour closest to ``_FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC`` (08:00 UTC).

        [Verified 2026-08-01] As of this task, ``snapshots.mark_iv`` is
        100% NULL across all 6.2M+ rows in the live database -- the write
        path (``save_snapshot`` persisting ``item.get("mark_iv")``) exists
        in code (this branch) but has not yet reached the deployed VPS
        daemon, so this fallback is currently unreachable with real data.
        Implemented anyway per institutional_metrics_spec.md section 11
        judgment call #4: "ship the calculator + the stale-prior guard now
        and let it light up as the daemon fills in."

        ``snapshots.captured_at`` is written via naive ``datetime.now()``
        by the VPS collector; the VPS OS/DB clock is confirmed UTC
        elsewhere in this campaign (task-C7-report.md's Important #2), so
        it is treated as a UTC-labeled timestamp here, consistent with that
        established finding -- never re-derived from this (non-UTC) local
        dev machine's clock.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string, e.g. "26MAR27".
            snapshot_date: The exact calendar date (UTC) to fetch --
                a ``datetime.date``.

        Returns:
            Dict with:
            - "rows": list of ``{"strike": float, "option_type": str,
              "mark_iv": float}``, empty if nothing found on either table
              for this exact date.
            - "underlying_price": float or None -- averaged across
              whichever rows had a non-null value (neither table
              guarantees exactly one distinct underlying_price per
              snapshot); None if every row's underlying_price was null.
            - "source": "daily_oi_snapshots" | "snapshots" | None.
        """
        rows, underlying_price = self._get_daily_oi_chain_iv(currency, expiration, snapshot_date)
        if rows:
            return {"rows": rows, "underlying_price": underlying_price, "source": "daily_oi_snapshots"}

        rows, underlying_price = self._get_hourly_snapshot_chain_iv(currency, expiration, snapshot_date)
        if rows:
            return {"rows": rows, "underlying_price": underlying_price, "source": "snapshots"}

        return {"rows": [], "underlying_price": None, "source": None}

    def _get_daily_oi_chain_iv(
        self, currency: str, expiration: str, snapshot_date,
    ) -> tuple:
        """Primary source for ``get_chain_iv_at`` -- see its docstring."""
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT strike, option_type, mark_iv, underlying_price
                FROM daily_oi_snapshots
                WHERE currency = %s
                  AND expiration = %s
                  AND snapshot_date = %s
                  AND mark_iv IS NOT NULL
                ORDER BY strike, option_type
            """, (currency, expiration, snapshot_date))
            return self._rows_and_avg_underlying_price(cursor.fetchall())

    def _get_hourly_snapshot_chain_iv(
        self, currency: str, expiration: str, snapshot_date,
    ) -> tuple:
        """
        Fallback source for ``get_chain_iv_at`` -- see its docstring.

        Independent review (Task C8 fix round, Minor #1/#2):
        - The inner subquery (picking the nearest tick to the 08:00 UTC
          anchor) now filters ``mark_iv IS NOT NULL`` and matches
          ``expiration`` -- without these, a pre-deploy tick near 08:00
          with a NULL ``mark_iv`` (or a tick that never even wrote this
          expiration) could win the "nearest" pick and make the whole
          lookup report "no data" even though a later same-day tick has
          real IV for this expiration.
        - The window is bounded to +/- ``_FIXED_STRIKE_VOL_ANCHOR_
          TOLERANCE_HOURS`` around the anchor, not the whole calendar
          day -- a tick that happens to be the day's ONLY one but sits
          hours away from 08:00 (e.g. 23:30) is no longer silently
          accepted as "the 08:00 anchor"; beyond tolerance, this returns
          empty, same as no data at all.
        """
        anchor_ts = datetime(
            snapshot_date.year, snapshot_date.month, snapshot_date.day,
            self._FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC, 0, 0,
        )
        tolerance = timedelta(hours=self._FIXED_STRIKE_VOL_ANCHOR_TOLERANCE_HOURS)
        window_start = anchor_ts - tolerance
        window_end = anchor_ts + tolerance

        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT strike, option_type, mark_iv, underlying_price
                FROM snapshots
                WHERE currency = %s
                  AND expiration = %s
                  AND mark_iv IS NOT NULL
                  AND captured_at = (
                      SELECT captured_at
                      FROM snapshots
                      WHERE currency = %s
                        AND expiration = %s
                        AND mark_iv IS NOT NULL
                        AND captured_at >= %s
                        AND captured_at <= %s
                      ORDER BY ABS(EXTRACT(EPOCH FROM (captured_at - %s)))
                      LIMIT 1
                  )
                ORDER BY strike, option_type
            """, (
                currency, expiration, currency, expiration,
                window_start, window_end, anchor_ts,
            ))
            return self._rows_and_avg_underlying_price(cursor.fetchall())

    def get_latest_chain_iv_date(
        self, currency: str, expiration: str, before_date,
    ) -> Optional[Any]:
        """
        Find the most recent date <= ``before_date`` that has ANY chain
        IV data for (currency, expiration), across both
        ``daily_oi_snapshots`` and ``snapshots`` (independent review, Task
        C8 fix round, Important #3).

        Diagnostic-only -- used SOLELY to power the "insufficient history"
        message's actual date (institutional_metrics_spec.md section 7(c):
        "no comparable prior snapshot (last: 2026-07-20)"). Never used to
        fetch data to compare against; ``get_chain_iv_at``'s exact-date-
        only contract (never substitutes a different date) is unaffected
        and unchanged by this method's existence.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            before_date: Upper bound (inclusive) on the date searched --
                typically the requested "prior" date that came up empty.

        Returns:
            The latest matching ``date``, or ``None`` if neither table has
            ANY row for this (currency, expiration) on or before
            ``before_date``.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT MAX(snapshot_date)
                FROM daily_oi_snapshots
                WHERE currency = %s
                  AND expiration = %s
                  AND snapshot_date <= %s
                  AND mark_iv IS NOT NULL
            """, (currency, expiration, before_date))
            daily_oi_date = cursor.fetchone()[0]

        # Fix round 2 (Low #2): apply the SAME +/- anchor tolerance
        # get_chain_iv_at's snapshots fallback enforces for the actual
        # comparison -- without it, this diagnostic method could report
        # a date as "having data" (e.g. a 14:00 UTC tick, hours from the
        # 08:00 anchor) that get_chain_iv_at would then refuse to use,
        # letting the two methods disagree about whether a given date
        # "has data". Hour-of-day filtering is sufficient here (no
        # per-day timestamp arithmetic needed) since the tolerance window
        # (05:00-11:00 UTC) never wraps past a calendar-day boundary.
        anchor_hour_low = (
            self._FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC
            - self._FIXED_STRIKE_VOL_ANCHOR_TOLERANCE_HOURS
        )
        anchor_hour_high = (
            self._FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC
            + self._FIXED_STRIKE_VOL_ANCHOR_TOLERANCE_HOURS
        )
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT MAX(DATE(captured_at))
                FROM snapshots
                WHERE currency = %s
                  AND expiration = %s
                  AND DATE(captured_at) <= %s
                  AND mark_iv IS NOT NULL
                  AND EXTRACT(HOUR FROM captured_at) BETWEEN %s AND %s
            """, (currency, expiration, before_date, anchor_hour_low, anchor_hour_high))
            snapshots_date = cursor.fetchone()[0]

        candidates = [d for d in (daily_oi_date, snapshots_date) if d is not None]
        return max(candidates) if candidates else None

    @staticmethod
    def _rows_and_avg_underlying_price(fetched_rows) -> tuple:
        """Shared shaping for both ``get_chain_iv_at`` sources: strike/
        option_type/mark_iv rows plus the underlying_price averaged across
        whichever rows had a non-null value."""
        rows = []
        underlying_prices = []
        for strike, option_type, mark_iv, underlying_price in fetched_rows:
            rows.append({
                "strike": float(strike),
                "option_type": option_type,
                "mark_iv": float(mark_iv),
            })
            if underlying_price is not None:
                underlying_prices.append(float(underlying_price))

        avg_underlying = (
            sum(underlying_prices) / len(underlying_prices) if underlying_prices else None
        )
        return rows, avg_underlying

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

    def save_dvol_history_row(
        self,
        currency: str,
        timestamp: datetime,
        dvol_value: float
    ) -> int:
        """
        Persist one row to dvol_history (infra_spec.md section 1 / Task E3).

        dvol_history is a SEPARATE table from volatility_index_history
        (written by save_dvol above) -- it feeds iv_percentile_365d /
        expected-move calculations that need >24h of history
        (on_chain_analysis_service.py:244-250). Prior to this method it was
        only ever written by the one-time scripts/backfill_dvol_history.py.

        Architectural note: DVOLFetcher.save_to_db() (coding/service/deribit/
        dvol_fetcher.py) already has an idempotent insert with this exact
        ON CONFLICT clause, but it takes a raw psycopg2 connection and lives
        in the Service layer. This repository (Core layer) must not import
        a Service-layer class -- that would invert the project's Core ->
        Service dependency direction. The insert SQL is therefore
        re-declared here rather than reused; both call sites share the same
        (asset, timestamp) idempotency key by convention, not shared code.

        Args:
            currency: Asset symbol (BTC or ETH) -- stored as `asset`.
            timestamp: UTC-aware timestamp for this DVOL reading.
            dvol_value: The DVOL index value.

        Returns:
            1 if a new row was inserted, 0 if (asset, timestamp) already
            existed (ON CONFLICT DO NOTHING).
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO dvol_history (asset, timestamp, dvol_value)
                VALUES (%s, %s, %s)
                ON CONFLICT (asset, timestamp) DO NOTHING
            """, (currency, timestamp, dvol_value))
            inserted = cursor.rowcount
        logger.info(f"Saved dvol_history row for {currency}: {dvol_value} (inserted={inserted})")
        return inserted

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

    def save_onchain_snapshot(
        self,
        snapshot_hour,
        currency: str,
        expiration: str,
        analysis_data: ExpirationAnalysisResult,
        gex_dex_data: GexDexResult,
        underlying_price: float,
        forward_price: Optional[float] = None,
    ) -> None:
        """
        Save on-chain analysis snapshot to the database.

        refactor_design_spec.md section T10 (compatibility-map row #9):
        ``analysis_data``/``gex_dex_data`` are the typed results now
        (attribute access), not the legacy dicts (``.get()``) --
        ``OnChainMetricsCalculator.analyze_expiration()`` and
        ``GexDexCalculator.calculate()`` both return typed results
        directly as of this same task. ``ProspectiveCollector`` (the only
        production caller) updated in the same commit.

        bugfix_spec.md Item 7 / F7.3.3: ``underlying_price`` is DEPRECATED as
        a name -- new rows expect the INDEX price here (not the old
        arbitrary highest-volume future), and it is now ALSO written into
        the new ``index_price`` column for clarity. ``forward_price`` (this
        expiration's own future price) is new -- used for
        ``max_pain_distance_pct`` (a strike-vs-settlement, settlement-space
        distance) and stored in its own new column. Falls back to
        ``underlying_price`` when omitted so an un-migrated caller still
        gets a value, just a less precise one.

        Args:
            snapshot_hour: Timestamp of the snapshot hour.
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "27DEC24").
            analysis_data: Output of OnChainMetricsCalculator.analyze_expiration().
            gex_dex_data: Output of GexDexCalculator.calculate().
            underlying_price: Current spot index price.
            forward_price: This expiration's own future price. Defaults to
                ``underlying_price`` when not given.
        """
        if forward_price is None:
            forward_price = underlying_price

        max_pain = analysis_data.max_pain
        put_call = analysis_data.put_call_ratio
        volume_stats = analysis_data.volume_stats
        moneyness = analysis_data.moneyness
        support_resistance = analysis_data.support_resistance
        key_levels = gex_dex_data.key_levels

        max_pain_strike = max_pain.max_pain_strike
        max_pain_distance_pct = (
            (max_pain_strike - forward_price) / forward_price * 100
            if max_pain_strike and forward_price
            else None
        )

        resistance_levels = support_resistance.resistance_levels
        support_levels = support_resistance.support_levels
        resistance_1 = resistance_levels[0] if resistance_levels else None
        support_1 = support_levels[0] if support_levels else None

        # key_levels.call_resistance/put_support are GexDexLevel instances
        # ({"strike", "net_gex"}) when greeks are non-zero, else None -- the
        # table stores only the strike scalar.
        call_resistance = key_levels.call_resistance
        put_support = key_levels.put_support

        volume_stats_call = volume_stats.total_call_volume
        volume_stats_put = volume_stats.total_put_volume
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
                    underlying_price,
                    index_price, forward_price
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
                    %s,
                    %s, %s
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
                    underlying_price = EXCLUDED.underlying_price,
                    index_price = EXCLUDED.index_price,
                    forward_price = EXCLUDED.forward_price
            """, (
                snapshot_hour, currency, expiration,
                max_pain_strike, max_pain_distance_pct,
                put_call.ratio, put_call_ratio_volume,
                put_call.total_call_oi, put_call.total_put_oi,
                gex_dex_data.total_net_gex, gex_dex_data.total_net_dex,
                call_resistance.strike if call_resistance else None,
                put_support.strike if put_support else None,
                key_levels.hvl,
                resistance_1.strike if resistance_1 else None,
                resistance_1.open_interest if resistance_1 else None,
                support_1.strike if support_1 else None,
                support_1.open_interest if support_1 else None,
                volume_stats.total_volume,
                moneyness.calls.itm_pct,
                moneyness.calls.otm_pct,
                moneyness.puts.itm_pct,
                moneyness.puts.otm_pct,
                underlying_price,
                underlying_price,
                forward_price,
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
            # institutional_metrics_spec.md section 4 / Task C5 (Migration
            # 019's 6 new columns) -- per-expiry VEX/CEX aggregates. Legacy
            # net_vanna/net_charm above are untouched (frozen, migration
            # 019's own header comment) -- these are additive, not a
            # replacement.
            "vex_holder", "cex_holder",
            "vex_assumed_dealer", "cex_assumed_dealer",
            "vex_peak_strike", "cex_peak_strike",
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
                    vex_holder, cex_holder,
                    vex_assumed_dealer, cex_assumed_dealer,
                    vex_peak_strike, cex_peak_strike,
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
                    %(vex_holder)s, %(cex_holder)s,
                    %(vex_assumed_dealer)s, %(cex_assumed_dealer)s,
                    %(vex_peak_strike)s, %(cex_peak_strike)s,
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
                    vex_holder = EXCLUDED.vex_holder,
                    cex_holder = EXCLUDED.cex_holder,
                    vex_assumed_dealer = EXCLUDED.vex_assumed_dealer,
                    cex_assumed_dealer = EXCLUDED.cex_assumed_dealer,
                    vex_peak_strike = EXCLUDED.vex_peak_strike,
                    cex_peak_strike = EXCLUDED.cex_peak_strike,
                    underlying_price = EXCLUDED.underlying_price
            """, {
                "snapshot_hour": snapshot_hour,
                "currency": currency,
                "expiration": expiration,
                "underlying_price": underlying_price,
                **values,
            })
            logger.info(f"Saved volatility snapshot for {currency} {expiration} at {snapshot_hour}")

    def save_volatility_skew(
        self,
        snapshot_hour,
        currency: str,
        expiration: str,
        dte_years: Optional[float],
        skew: Dict[str, Any],
    ) -> None:
        """
        Persist one delta-interpolated RR25/BF25 term-structure row to
        ``volatility_skew_history`` (migration 018 /
        institutional_metrics_spec.md Migration M3, section 3, Task C4).

        Decision D10 (BINDING, migration 018's header): this table is a
        fresh series, unrelated to and NOT backfilled from the degenerate
        ``onchain_volatility_snapshots.skew_25d``/``call_25d_iv``/
        ``put_25d_iv`` history -- that history is untouched.

        Args:
            snapshot_hour: Timestamp of the snapshot hour.
            currency: Currency symbol (e.g., "BTC", "ETH").
            expiration: Expiration date string (e.g., "25JUL26").
            dte_years: Days-to-expiry in years, or None if it could not be
                computed (never blocks the write -- the row is still
                persisted with a NULL dte_years).
            skew: The dict returned by ``VolatilitySurfaceCalculator.
                calculate_risk_reversal_butterfly()`` -- rr_25d, bf_25d,
                call_25d_iv, put_25d_iv, call_25d_strike, put_25d_strike,
                atm_iv_interp, n_quotes_used, method. Any of the
                interpolated values may be None (T3.2/T3.3: chain does not
                bracket the target delta) -- written as SQL NULL, never
                coerced to 0. ``call_bracket``/``put_bracket`` (also in the
                dict) are not persisted -- the schema has no column for
                them; they exist for report/debugging use only.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO volatility_skew_history (
                    snapshot_hour, currency, expiration, dte_years,
                    atm_iv_interp, call_25d_iv, put_25d_iv,
                    call_25d_strike, put_25d_strike,
                    rr_25d, bf_25d, n_quotes_used, interp_method
                ) VALUES (
                    %(snapshot_hour)s, %(currency)s, %(expiration)s, %(dte_years)s,
                    %(atm_iv_interp)s, %(call_25d_iv)s, %(put_25d_iv)s,
                    %(call_25d_strike)s, %(put_25d_strike)s,
                    %(rr_25d)s, %(bf_25d)s, %(n_quotes_used)s, %(interp_method)s
                )
                ON CONFLICT (snapshot_hour, currency, expiration) DO UPDATE SET
                    dte_years = EXCLUDED.dte_years,
                    atm_iv_interp = EXCLUDED.atm_iv_interp,
                    call_25d_iv = EXCLUDED.call_25d_iv,
                    put_25d_iv = EXCLUDED.put_25d_iv,
                    call_25d_strike = EXCLUDED.call_25d_strike,
                    put_25d_strike = EXCLUDED.put_25d_strike,
                    rr_25d = EXCLUDED.rr_25d,
                    bf_25d = EXCLUDED.bf_25d,
                    n_quotes_used = EXCLUDED.n_quotes_used,
                    interp_method = EXCLUDED.interp_method
            """, {
                "snapshot_hour": snapshot_hour,
                "currency": currency,
                "expiration": expiration,
                "dte_years": dte_years,
                "atm_iv_interp": skew.get("atm_iv_interp"),
                "call_25d_iv": skew.get("call_25d_iv"),
                "put_25d_iv": skew.get("put_25d_iv"),
                "call_25d_strike": skew.get("call_25d_strike"),
                "put_25d_strike": skew.get("put_25d_strike"),
                "rr_25d": skew.get("rr_25d"),
                "bf_25d": skew.get("bf_25d"),
                "n_quotes_used": skew.get("n_quotes_used"),
                "interp_method": skew.get("method", "linear_delta"),
            })
            logger.info(
                f"Saved volatility skew (RR25/BF25) for {currency} {expiration} at {snapshot_hour}"
            )

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

    # ── Defined-Risk Scanner (Iron Condor / Butterfly) ──────────────────────────

    def save_defined_risk_scan(self, row: Dict[str, Any]) -> bool:
        """
        Insert one defined_risk_scan_history row. Deduped on (currency,
        expiration, structure_type, scan_time).

        Args:
            row: Dict with keys matching defined_risk_scan_history columns
                (see migrations/015_add_defined_risk_scan_history.sql).

        Returns:
            True if a new row was inserted, False on dedup skip.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO defined_risk_scan_history (
                    scan_time, currency, expiration, structure_type,
                    dte, future_price, index_price,
                    short_call, long_call, short_put, long_put,
                    k1, k2, k3,
                    cost_or_credit, max_loss, max_profit,
                    breakeven_lo, breakeven_hi, prob_profit, ev,
                    net_gex, rv_10d, rv_30d, rv_ratio, gate_pass,
                    deribit_url
                ) VALUES (
                    %(scan_time)s, %(currency)s, %(expiration)s, %(structure_type)s,
                    %(dte)s, %(future_price)s, %(index_price)s,
                    %(short_call)s, %(long_call)s, %(short_put)s, %(long_put)s,
                    %(k1)s, %(k2)s, %(k3)s,
                    %(cost_or_credit)s, %(max_loss)s, %(max_profit)s,
                    %(breakeven_lo)s, %(breakeven_hi)s, %(prob_profit)s, %(ev)s,
                    %(net_gex)s, %(rv_10d)s, %(rv_30d)s, %(rv_ratio)s, %(gate_pass)s,
                    %(deribit_url)s
                )
                ON CONFLICT (currency, expiration, structure_type, scan_time) DO NOTHING
                RETURNING id
            """, row)
            return cursor.fetchone() is not None

    def get_unresolved_defined_risk_scans(self, currency: str, structure_type: str) -> List[Dict[str, Any]]:
        """
        Return defined_risk_scan_history rows for (currency, structure_type)
        awaiting settlement resolution, oldest first.

        Returns:
            List of dicts: {id, expiration, scan_time, short_call, long_call,
            short_put, long_put, k1, k2, k3, cost_or_credit, max_loss, max_profit}.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT id, expiration, scan_time,
                       short_call, long_call, short_put, long_put,
                       k1, k2, k3, cost_or_credit, max_loss, max_profit
                FROM defined_risk_scan_history
                WHERE currency = %s AND structure_type = %s AND resolved_at IS NULL
                ORDER BY scan_time ASC
            """, (currency, structure_type))
            cols = ["id", "expiration", "scan_time", "short_call", "long_call",
                    "short_put", "long_put", "k1", "k2", "k3",
                    "cost_or_credit", "max_loss", "max_profit"]
            return [dict(zip(cols, r)) for r in cursor.fetchall()]

    def resolve_defined_risk_scan(
        self,
        scan_id: int,
        settlement_index_price: float,
        settlement_pnl_usd: float,
        settlement_return_pct: Optional[float],
        resolved_at,
    ) -> None:
        """Fill in the settlement resolution fields for a defined_risk_scan_history row."""
        with self._db_cursor() as cursor:
            cursor.execute("""
                UPDATE defined_risk_scan_history
                SET settlement_index_price = %s,
                    settlement_pnl_usd = %s,
                    settlement_return_pct = %s,
                    resolved_at = %s
                WHERE id = %s
            """, (settlement_index_price, settlement_pnl_usd, settlement_return_pct, resolved_at, scan_id))

    def get_last_alert_for_defined_risk(
        self, currency: str, expiration: str, structure_type: str
    ) -> Optional[Dict[str, Any]]:
        """Return the most recently alerted row for (currency, expiration, structure_type), or None."""
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT ev, alert_sent_at
                FROM defined_risk_scan_history
                WHERE currency = %s AND expiration = %s AND structure_type = %s AND alert_sent = TRUE
                ORDER BY alert_sent_at DESC
                LIMIT 1
            """, (currency, expiration, structure_type))
            row = cursor.fetchone()
            if row is None:
                return None
            return {"ev": float(row[0]) if row[0] is not None else None, "alert_sent_at": row[1]}

    def mark_defined_risk_scan_alert_sent(
        self, currency: str, expiration: str, structure_type: str, scan_time
    ) -> None:
        """Mark the row for this (currency, expiration, structure_type, scan_time) as alerted."""
        with self._db_cursor() as cursor:
            cursor.execute("""
                UPDATE defined_risk_scan_history
                SET alert_sent = TRUE, alert_sent_at = NOW()
                WHERE currency = %s AND expiration = %s AND structure_type = %s AND scan_time = %s
            """, (currency, expiration, structure_type, scan_time))

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

    # institutional_metrics_spec.md section 1(c): whitelist of (table,
    # column) pairs get_metric_history is allowed to read, plus the
    # time_column each table uses for its trailing-window filter. Extends
    # get_recent_onchain_history's existing whitelist pattern instead of
    # inventing a second one -- one generic reader replaces ad-hoc
    # per-metric SQL for every AVAILABLE metric in section 1(a): net GEX,
    # PCR-OI (+ PCR-volume for bugfix_spec.md Item 10), total call/put OI,
    # DVOL, VRP, funding.
    _METRIC_HISTORY_WHITELIST = {
        ("onchain_analysis_snapshots", "total_net_gex"): "snapshot_hour",
        ("onchain_analysis_snapshots", "put_call_ratio_oi"): "snapshot_hour",
        ("onchain_analysis_snapshots", "put_call_ratio_volume"): "snapshot_hour",
        ("onchain_analysis_snapshots", "total_call_oi"): "snapshot_hour",
        ("onchain_analysis_snapshots", "total_put_oi"): "snapshot_hour",
        ("onchain_volatility_snapshots", "vrp_absolute"): "snapshot_hour",
        ("volatility_index_history", "dvol"): "date",
        ("funding_rate_history", "funding_rate"): "date",
        # institutional_metrics_spec.md section 3(c) (Task C4): RR25/BF25
        # term-structure percentile history.
        ("volatility_skew_history", "rr_25d"): "snapshot_hour",
        ("volatility_skew_history", "bf_25d"): "snapshot_hour",
    }

    # institutional_metrics_spec.md section 3(c): "so section 1 can filter
    # thin rows out of the percentile window (WHERE n_quotes_used >= 8)".
    # A per-table extra WHERE clause, not a second whitelist mechanism --
    # get_metric_history appends this for the one table that needs it.
    _METRIC_HISTORY_EXTRA_FILTER = {
        "volatility_skew_history": "n_quotes_used >= 8",
    }

    def get_metric_history(
        self,
        table: str,
        column: str,
        currency: str,
        lookback_hours: int,
        expiration: Optional[str] = None,
        time_column: Optional[str] = None,
    ) -> List[float]:
        """
        Generic trailing-history reader for HistoricalNormalizer
        (institutional_metrics_spec.md section 1(c)).

        Args:
            table: Source table. Must be one of the whitelisted tables.
            column: Source column. Must be whitelisted for ``table``.
            currency: Currency symbol.
            lookback_hours: Trailing window size in hours (30d ~= 720,
                90d ~= 2160).
            expiration: When set, filters to one (currency, expiration)
                series (onchain_analysis_snapshots / onchain_volatility_
                snapshots). Market-wide tables (volatility_index_history,
                funding_rate_history) have no expiration column -- omit it.
            time_column: Overrides the whitelist's default time column.
                Callers normally rely on the whitelist default; this exists
                so tests/callers can be explicit without a second lookup.

        Returns:
            Plain floats, oldest-first, NULLs dropped, Decimal cast to
            float at this boundary (never inside HistoricalNormalizer,
            which must not know about psycopg2's Decimal type).

        Raises:
            ValueError: (table, column) is not in the whitelist.
        """
        key = (table, column)
        if key not in self._METRIC_HISTORY_WHITELIST:
            raise ValueError(
                f"get_metric_history: ({table!r}, {column!r}) is not whitelisted. "
                f"Allowed pairs: {sorted(self._METRIC_HISTORY_WHITELIST)}"
            )
        col = self._METRIC_HISTORY_WHITELIST[key] if time_column is None else time_column
        extra_filter = self._METRIC_HISTORY_EXTRA_FILTER.get(table)
        extra_clause = f" AND {extra_filter}" if extra_filter else ""

        if expiration is not None:
            sql = (
                f"SELECT {column} FROM {table} "
                f"WHERE currency = %s AND expiration = %s "
                f"AND {col} >= NOW() - INTERVAL '1 hour' * %s"
                f"{extra_clause} "
                f"ORDER BY {col} ASC"
            )
            params = (currency, expiration, lookback_hours)
        else:
            sql = (
                f"SELECT {column} FROM {table} "
                f"WHERE currency = %s "
                f"AND {col} >= NOW() - INTERVAL '1 hour' * %s"
                f"{extra_clause} "
                f"ORDER BY {col} ASC"
            )
            params = (currency, lookback_hours)

        try:
            with self._db_cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
        except Exception as exc:
            logger.warning("get_metric_history(%s, %s) failed: %s", table, column, exc)
            return []

        return [float(row[0]) for row in rows if row[0] is not None]

    # institutional_metrics_spec.md section 1(c) / C1 review Important #4:
    # "STALE: history ends {ts}" requires knowing how fresh the queried
    # history actually is. Derived from the same table set
    # _METRIC_HISTORY_WHITELIST already covers, keeping one time-column
    # mapping instead of a second one that could drift out of sync (kept
    # as an explicit dict, not a class-body comprehension over
    # _METRIC_HISTORY_WHITELIST, to sidestep Python's class-body
    # comprehension scoping rule).
    _TABLE_TIME_COLUMNS = {
        "onchain_analysis_snapshots": "snapshot_hour",
        "onchain_volatility_snapshots": "snapshot_hour",
        "volatility_index_history": "date",
        "funding_rate_history": "date",
        # Task C4 review Minor #2: was added to _METRIC_HISTORY_WHITELIST
        # (get_metric_history) but not here, which meant the "STALE:
        # history ends {ts}" mechanism could never cover this table.
        "volatility_skew_history": "snapshot_hour",
    }

    def get_metric_freshness(
        self,
        table: str,
        currency: str,
        expiration: Optional[str] = None,
        time_column: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        Most recent timestamp available for ``table`` (institutional_metrics
        _spec.md section 1(c)'s staleness gate: "if max(snapshot_hour) <
        now() - 3h, prefix the whole normalization block with STALE:
        history ends {ts}").

        Args:
            table: Must be one of ``_TABLE_TIME_COLUMNS``'s keys (the same
                tables ``get_metric_history`` is whitelisted against).
            currency: Currency symbol.
            expiration: When set, filters to one (currency, expiration)
                series. Market-wide tables have no expiration column.
            time_column: Overrides the table's default time column.

        Returns:
            The max timestamp, or ``None`` if the table has no rows for
            this currency/expiration, the table is not whitelisted, or the
            query failed (logged, not raised -- freshness is a display
            nicety, not something that should crash analysis).
        """
        if table not in self._TABLE_TIME_COLUMNS:
            logger.warning("get_metric_freshness: table %r is not whitelisted", table)
            return None
        col = self._TABLE_TIME_COLUMNS[table] if time_column is None else time_column

        if expiration is not None:
            sql = f"SELECT MAX({col}) FROM {table} WHERE currency = %s AND expiration = %s"
            params = (currency, expiration)
        else:
            sql = f"SELECT MAX({col}) FROM {table} WHERE currency = %s"
            params = (currency,)

        try:
            with self._db_cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
        except Exception as exc:
            logger.warning("get_metric_freshness(%s) failed: %s", table, exc)
            return None

        return row[0] if row and row[0] is not None else None
