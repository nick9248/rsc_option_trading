"""
Database repository for on-chain analysis data storage.

Provides methods to save and retrieve data from PostgreSQL tables.
"""

import json
import logging
from contextlib import contextmanager
from datetime import datetime
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

    def save_max_pain(
        self,
        currency: str,
        expiration: str,
        max_pain_strike: float,
        underlying_price: float,
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save max pain data to the max_pain table.

        Args:
            currency: Currency symbol (ETH, BTC).
            expiration: Expiration date string.
            max_pain_strike: Max pain strike price.
            underlying_price: Current underlying price.
            captured_at: Timestamp of capture.

        Returns:
            ID of inserted row.
        """
        captured_at = captured_at or datetime.now()
        distance = underlying_price - max_pain_strike
        distance_pct = (distance / max_pain_strike * 100) if max_pain_strike else 0

        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO max_pain (
                        captured_at, currency, expiration, max_pain_strike,
                        underlying_price, distance_from_price, distance_percent
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    captured_at, currency, expiration, max_pain_strike,
                    underlying_price, distance, distance_pct
                ))

                row_id = cursor.fetchone()[0]

                logger.info(f"Saved max pain for {currency} {expiration}: {max_pain_strike}")
                return row_id

        except Exception as e:
            logger.error(f"Failed to save max pain: {e}")
            raise

    def save_open_interest(
        self,
        currency: str,
        expiration: str,
        total_call_oi: float,
        total_put_oi: float,
        underlying_price: float,
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save open interest data to the open_interest table.

        Args:
            currency: Currency symbol (ETH, BTC).
            expiration: Expiration date string.
            total_call_oi: Total call open interest.
            total_put_oi: Total put open interest.
            underlying_price: Current underlying price.
            captured_at: Timestamp of capture.

        Returns:
            ID of inserted row.
        """
        captured_at = captured_at or datetime.now()
        total_oi = total_call_oi + total_put_oi
        pc_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO open_interest (
                        captured_at, currency, expiration, total_call_oi,
                        total_put_oi, total_oi, put_call_ratio, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    captured_at, currency, expiration, total_call_oi,
                    total_put_oi, total_oi, pc_ratio, underlying_price
                ))

                row_id = cursor.fetchone()[0]

                logger.info(f"Saved OI for {currency} {expiration}: {total_oi}")
                return row_id

        except Exception as e:
            logger.error(f"Failed to save open interest: {e}")
            raise

    def save_volume(
        self,
        currency: str,
        expiration: str,
        total_call_volume: float,
        total_put_volume: float,
        underlying_price: float,
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save volume data to the volume table.

        Args:
            currency: Currency symbol (ETH, BTC).
            expiration: Expiration date string.
            total_call_volume: Total call volume.
            total_put_volume: Total put volume.
            underlying_price: Current underlying price.
            captured_at: Timestamp of capture.

        Returns:
            ID of inserted row.
        """
        captured_at = captured_at or datetime.now()
        total_volume = total_call_volume + total_put_volume
        pc_ratio = (total_put_volume / total_call_volume) if total_call_volume > 0 else None

        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO volume (
                        captured_at, currency, expiration, total_call_volume,
                        total_put_volume, total_volume, volume_put_call_ratio, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    captured_at, currency, expiration, total_call_volume,
                    total_put_volume, total_volume, pc_ratio, underlying_price
                ))

                row_id = cursor.fetchone()[0]

                logger.info(f"Saved volume for {currency} {expiration}: {total_volume}")
                return row_id

        except Exception as e:
            logger.error(f"Failed to save volume: {e}")
            raise

    def save_levels(
        self,
        currency: str,
        expiration: str,
        levels: List[Dict[str, Any]],
        underlying_price: float,
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save support/resistance and GEX/DEX levels to the levels table.

        Args:
            currency: Currency symbol (ETH, BTC).
            expiration: Expiration date string.
            levels: List of level dicts with keys: level_type, strike, value.
            underlying_price: Current underlying price.
            captured_at: Timestamp of capture.

        Returns:
            Number of rows inserted.
        """
        if not levels:
            return 0

        captured_at = captured_at or datetime.now()

        try:
            with self._db_cursor() as cursor:
                insert_sql = """
                    INSERT INTO levels (
                        captured_at, currency, expiration, level_type,
                        strike, oi_or_gex_value, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """

                rows = [
                    (
                        captured_at,
                        currency,
                        expiration,
                        level["level_type"],
                        level["strike"],
                        level.get("value"),
                        underlying_price,
                    )
                    for level in levels
                ]

                cursor.executemany(insert_sql, rows)

                logger.info(f"Saved {len(rows)} levels for {currency} {expiration}")
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to save levels: {e}")
            raise

    def get_max_pain_history(
        self,
        currency: str,
        expiration: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get max pain history for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            limit: Maximum number of records to return.

        Returns:
            List of max pain records ordered by captured_at.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT captured_at, max_pain_strike, underlying_price,
                       distance_from_price, distance_percent
                FROM max_pain
                WHERE currency = %s AND expiration = %s
                ORDER BY captured_at DESC
                LIMIT %s
            """, (currency, expiration, limit))

            columns = ["captured_at", "max_pain_strike", "underlying_price",
                       "distance_from_price", "distance_percent"]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))  # Return in chronological order

    def get_open_interest_history(
        self,
        currency: str,
        expiration: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get open interest history for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            limit: Maximum number of records to return.

        Returns:
            List of OI records ordered by captured_at.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT captured_at, total_call_oi, total_put_oi,
                       total_oi, put_call_ratio, underlying_price
                FROM open_interest
                WHERE currency = %s AND expiration = %s
                ORDER BY captured_at DESC
                LIMIT %s
            """, (currency, expiration, limit))

            columns = ["captured_at", "total_call_oi", "total_put_oi",
                       "total_oi", "put_call_ratio", "underlying_price"]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))

    def get_volume_history(
        self,
        currency: str,
        expiration: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get volume history for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            limit: Maximum number of records to return.

        Returns:
            List of volume records ordered by captured_at.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT captured_at, total_call_volume, total_put_volume,
                       total_volume, volume_put_call_ratio, underlying_price
                FROM volume
                WHERE currency = %s AND expiration = %s
                ORDER BY captured_at DESC
                LIMIT %s
            """, (currency, expiration, limit))

            columns = ["captured_at", "total_call_volume", "total_put_volume",
                       "total_volume", "volume_put_call_ratio", "underlying_price"]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))

    def get_levels_history(
        self,
        currency: str,
        expiration: str,
        level_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get levels history for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            level_type: Optional filter by level type.
            limit: Maximum number of records to return.

        Returns:
            List of level records ordered by captured_at.
        """
        with self._db_cursor() as cursor:
            if level_type:
                cursor.execute("""
                    SELECT captured_at, level_type, strike, oi_or_gex_value, underlying_price
                    FROM levels
                    WHERE currency = %s AND expiration = %s AND level_type = %s
                    ORDER BY captured_at DESC
                    LIMIT %s
                """, (currency, expiration, level_type, limit))
            else:
                cursor.execute("""
                    SELECT captured_at, level_type, strike, oi_or_gex_value, underlying_price
                    FROM levels
                    WHERE currency = %s AND expiration = %s
                    ORDER BY captured_at DESC
                    LIMIT %s
                """, (currency, expiration, limit))

            columns = ["captured_at", "level_type", "strike", "oi_or_gex_value", "underlying_price"]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))

    def get_available_expirations(self, currency: str, table: str = "max_pain") -> List[str]:
        """
        Get list of expirations with data in a table.

        Args:
            currency: Currency symbol.
            table: Table name to query.

        Returns:
            List of expiration strings.
        """
        valid_tables = ["max_pain", "open_interest", "volume", "levels", "snapshots", "gex_dex"]
        if table not in valid_tables:
            raise ValueError(f"Invalid table: {table}")

        with self._db_cursor() as cursor:
            cursor.execute(f"""
                SELECT DISTINCT expiration
                FROM {table}
                WHERE currency = %s
                ORDER BY expiration
            """, (currency,))

            return [row[0] for row in cursor.fetchall()]

    def get_last_captured_times(self, currency: str) -> Dict[str, Optional[datetime]]:
        """
        Get the most recent captured_at timestamp per capture type for a currency.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            Dict keyed by capture type. Value is datetime if data exists, None if never captured.
            Keys: "snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"
        """
        table_map = {
            "snapshot": "snapshots",
            "max_pain": "max_pain",
            "open_interest": "open_interest",
            "volume": "volume",
            "levels": "levels",
            "gex_dex": "gex_dex",
        }
        result: Dict[str, Optional[datetime]] = {}

        with self._db_cursor() as cursor:
            for capture_type, table in table_map.items():
                cursor.execute(
                    f"SELECT MAX(captured_at) FROM {table} WHERE currency = %s",
                    (currency,)
                )
                row = cursor.fetchone()
                result[capture_type] = row[0] if row and row[0] is not None else None

        return result

    def save_gex_dex(
        self,
        currency: str,
        expiration: str,
        total_net_gex: float,
        total_net_dex: float,
        call_resistance_strike: Optional[float],
        call_resistance_gex: Optional[float],
        put_support_strike: Optional[float],
        put_support_gex: Optional[float],
        hvl_strike: Optional[float],
        underlying_price: float,
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save GEX/DEX data to the gex_dex table.

        Args:
            currency: Currency symbol (ETH, BTC).
            expiration: Expiration date string.
            total_net_gex: Total net gamma exposure.
            total_net_dex: Total net delta exposure.
            call_resistance_strike: Call resistance strike price.
            call_resistance_gex: GEX value at call resistance.
            put_support_strike: Put support strike price.
            put_support_gex: GEX value at put support.
            hvl_strike: Zero Gamma Level (cumulative GEX zero crossing) strike. Not MenthorQ HVL.
            underlying_price: Current underlying price.
            captured_at: Timestamp of capture.

        Returns:
            ID of inserted row.
        """
        captured_at = captured_at or datetime.now()

        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO gex_dex (
                        captured_at, currency, expiration, total_net_gex,
                        total_net_dex, call_resistance_strike, call_resistance_gex,
                        put_support_strike, put_support_gex, hvl_strike, underlying_price
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    captured_at, currency, expiration, total_net_gex,
                    total_net_dex, call_resistance_strike, call_resistance_gex,
                    put_support_strike, put_support_gex, hvl_strike, underlying_price
                ))

                row_id = cursor.fetchone()[0]

                logger.info(f"Saved GEX/DEX for {currency} {expiration}")
                return row_id

        except Exception as e:
            logger.error(f"Failed to save GEX/DEX: {e}")
            raise

    def get_gex_dex_history(
        self,
        currency: str,
        expiration: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get GEX/DEX history for an expiration.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            limit: Maximum number of records to return.

        Returns:
            List of GEX/DEX records ordered by captured_at.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT captured_at, total_net_gex, total_net_dex,
                       call_resistance_strike, call_resistance_gex,
                       put_support_strike, put_support_gex,
                       hvl_strike, underlying_price
                FROM gex_dex
                WHERE currency = %s AND expiration = %s
                ORDER BY captured_at DESC
                LIMIT %s
            """, (currency, expiration, limit))

            columns = [
                "captured_at", "total_net_gex", "total_net_dex",
                "call_resistance_strike", "call_resistance_gex",
                "put_support_strike", "put_support_gex",
                "hvl_strike", "underlying_price"
            ]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]

            return list(reversed(results))

    def save_strategy_signal(
        self,
        signal: Dict[str, Any],
        captured_at: Optional[datetime] = None
    ) -> int:
        """
        Save strategy signal to the strategy_signals table.

        Args:
            signal: Strategy signal dictionary with all fields
            captured_at: Timestamp of signal generation. Uses current time if not provided.

        Returns:
            ID of inserted row.
        """
        captured_at = captured_at or signal.get("generated_at") or datetime.now()

        try:
            with self._db_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO strategy_signals (
                        generated_at, strategy_name, currency, expiration,
                        intrinsic_score, on_chain_score, composite_score, rank,
                        legs, intrinsic_breakdown, on_chain_breakdown,
                        underlying_price, implied_volatility, max_pain_strike,
                        max_risk, max_profit, total_cost, breakeven_points,
                        max_loss_percentage, take_profit_percentage, market_regime,
                        net_delta, net_gamma, net_theta, net_vega
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING id
                """, (
                    captured_at,
                    signal.get("strategy_name"),
                    signal.get("currency"),
                    signal.get("expiration"),
                    signal.get("intrinsic_score"),
                    signal.get("on_chain_score"),
                    signal.get("composite_score"),
                    signal.get("rank"),
                    json.dumps(signal.get("legs", [])),
                    json.dumps(signal.get("intrinsic_breakdown", {})),
                    json.dumps(signal.get("on_chain_breakdown", {})),
                    signal.get("underlying_price"),
                    signal.get("implied_volatility"),
                    signal.get("max_pain_strike"),
                    signal.get("max_risk"),
                    signal.get("max_profit"),
                    signal.get("total_cost"),
                    signal.get("breakeven_points", []),
                    signal.get("max_loss_percentage"),
                    signal.get("take_profit_percentage"),
                    signal.get("market_regime"),
                    signal.get("net_delta"),
                    signal.get("net_gamma"),
                    signal.get("net_theta"),
                    signal.get("net_vega")
                ))

                row_id = cursor.fetchone()[0]

                logger.info(
                    f"Saved strategy signal: {signal.get('strategy_name')} "
                    f"for {signal.get('currency')}-{signal.get('expiration')}, "
                    f"composite_score={signal.get('composite_score'):.2f}"
                )
                return row_id

        except Exception as e:
            logger.error(f"Failed to save strategy signal: {e}")
            raise

    def get_strategy_signals(
        self,
        currency: Optional[str] = None,
        expiration: Optional[str] = None,
        strategy_name: Optional[str] = None,
        min_composite_score: float = 0.0,
        market_regime: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query strategy signals with filters.

        Args:
            currency: Filter by currency (optional)
            expiration: Filter by expiration (optional)
            strategy_name: Filter by strategy name (optional)
            min_composite_score: Minimum composite score filter
            market_regime: Filter by market regime (optional)
            limit: Maximum number of results

        Returns:
            List of strategy signal records ordered by composite_score DESC
        """
        with self._db_cursor() as cursor:
            # Build WHERE clauses
            where_clauses = ["composite_score >= %s"]
            params = [min_composite_score]

            if currency:
                where_clauses.append("currency = %s")
                params.append(currency)

            if expiration:
                where_clauses.append("expiration = %s")
                params.append(expiration)

            if strategy_name:
                where_clauses.append("strategy_name = %s")
                params.append(strategy_name)

            if market_regime:
                where_clauses.append("market_regime = %s")
                params.append(market_regime)

            where_sql = " AND ".join(where_clauses)
            params.append(limit)

            cursor.execute(f"""
                SELECT
                    id, generated_at, strategy_name, currency, expiration,
                    intrinsic_score, on_chain_score, composite_score, rank,
                    legs, intrinsic_breakdown, on_chain_breakdown,
                    underlying_price, implied_volatility, max_pain_strike,
                    max_risk, max_profit, total_cost, breakeven_points,
                    max_loss_percentage, take_profit_percentage, market_regime,
                    net_delta, net_gamma, net_theta, net_vega
                FROM strategy_signals
                WHERE {where_sql}
                ORDER BY composite_score DESC, generated_at DESC
                LIMIT %s
            """, params)

            columns = [
                "id", "generated_at", "strategy_name", "currency", "expiration",
                "intrinsic_score", "on_chain_score", "composite_score", "rank",
                "legs", "intrinsic_breakdown", "on_chain_breakdown",
                "underlying_price", "implied_volatility", "max_pain_strike",
                "max_risk", "max_profit", "total_cost", "breakeven_points",
                "max_loss_percentage", "take_profit_percentage", "market_regime",
                "net_delta", "net_gamma", "net_theta", "net_vega"
            ]

            results = []
            for row in cursor.fetchall():
                signal_dict = dict(zip(columns, row))

                # Parse JSON fields
                signal_dict["legs"] = json.loads(signal_dict["legs"]) if signal_dict["legs"] else []
                signal_dict["intrinsic_breakdown"] = json.loads(signal_dict["intrinsic_breakdown"]) if signal_dict["intrinsic_breakdown"] else {}
                signal_dict["on_chain_breakdown"] = json.loads(signal_dict["on_chain_breakdown"]) if signal_dict["on_chain_breakdown"] else {}

                results.append(signal_dict)

            logger.info(f"Retrieved {len(results)} strategy signals")
            return results

    def save_regime_detection(
        self,
        currency: str,
        detected_at,
        regime_data: dict
    ) -> int:
        """
        Save regime detection result to database.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH")
            detected_at: Detection timestamp
            regime_data: Complete regime detection result dictionary

        Returns:
            Row ID of inserted record
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO regime_detections (
                    currency, detected_at, regime, confidence_score,
                    trend_score, volatility_score, momentum_score,
                    onchain_score, sentiment_score,
                    current_price, sma_50, sma_200,
                    adx, atr_percentile, rsi,
                    funding_rate, put_call_ratio, fear_greed,
                    reasoning
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
                ON CONFLICT (currency, detected_at) DO UPDATE SET
                    regime = EXCLUDED.regime,
                    confidence_score = EXCLUDED.confidence_score,
                    trend_score = EXCLUDED.trend_score,
                    volatility_score = EXCLUDED.volatility_score,
                    momentum_score = EXCLUDED.momentum_score,
                    onchain_score = EXCLUDED.onchain_score,
                    sentiment_score = EXCLUDED.sentiment_score,
                    current_price = EXCLUDED.current_price,
                    sma_50 = EXCLUDED.sma_50,
                    sma_200 = EXCLUDED.sma_200,
                    adx = EXCLUDED.adx,
                    atr_percentile = EXCLUDED.atr_percentile,
                    rsi = EXCLUDED.rsi,
                    funding_rate = EXCLUDED.funding_rate,
                    put_call_ratio = EXCLUDED.put_call_ratio,
                    fear_greed = EXCLUDED.fear_greed,
                    reasoning = EXCLUDED.reasoning
                RETURNING id
            """, (
                currency,
                detected_at,
                regime_data.get("regime"),
                regime_data.get("confidence"),
                regime_data.get("signals", {}).get("trend"),
                regime_data.get("signals", {}).get("volatility"),
                regime_data.get("signals", {}).get("momentum"),
                regime_data.get("signals", {}).get("onchain"),
                regime_data.get("signals", {}).get("sentiment"),
                regime_data.get("current_price"),
                regime_data.get("indicators", {}).get("sma_50"),
                regime_data.get("indicators", {}).get("sma_200"),
                regime_data.get("indicators", {}).get("adx"),
                regime_data.get("indicators", {}).get("atr_percentile"),
                regime_data.get("indicators", {}).get("rsi"),
                regime_data.get("indicators", {}).get("funding_rate"),
                regime_data.get("indicators", {}).get("put_call_ratio"),
                regime_data.get("indicators", {}).get("fear_greed"),
                regime_data.get("reasoning")
            ))

            row_id = cursor.fetchone()[0]
            logger.info(f"Saved regime detection: {currency} - {regime_data.get('regime')} (ID: {row_id})")
            return row_id

    def get_unaggregated_hours(self, currency: str) -> List[datetime]:
        """
        Find hours that have trades but no hourly snapshots.

        This is used by HourlyAggregationService to discover gaps.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            List of datetime objects representing hour buckets that need aggregation.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT
                    date_trunc('hour', to_timestamp(trade_timestamp / 1000.0)) as hour_bucket
                FROM historical_trades
                WHERE currency = %s
                  AND date_trunc('hour', to_timestamp(trade_timestamp / 1000.0)) NOT IN (
                      SELECT DISTINCT snapshot_hour
                      FROM hourly_snapshots
                      WHERE currency = %s
                  )
                ORDER BY hour_bucket
            """, (currency, currency))

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

    def save_technical_indicators(
        self,
        currency: str,
        date,
        indicators: Dict[str, Any]
    ) -> None:
        """
        Save calculated technical indicators to the database.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            date: Timestamp for these indicators.
            indicators: Dict of indicator values (sma_50, sma_200, ema_50, ema_200,
                        adx, plus_di, minus_di, atr, atr_percentile, rsi,
                        macd, macd_signal, macd_histogram).
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                INSERT INTO technical_indicators (
                    currency, date,
                    sma_50, sma_200, ema_50, ema_200,
                    adx, plus_di, minus_di,
                    atr, atr_percentile, rsi,
                    macd, macd_signal, macd_histogram
                ) VALUES (
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (currency, date) DO UPDATE SET
                    sma_50 = EXCLUDED.sma_50,
                    sma_200 = EXCLUDED.sma_200,
                    ema_50 = EXCLUDED.ema_50,
                    ema_200 = EXCLUDED.ema_200,
                    adx = EXCLUDED.adx,
                    plus_di = EXCLUDED.plus_di,
                    minus_di = EXCLUDED.minus_di,
                    atr = EXCLUDED.atr,
                    atr_percentile = EXCLUDED.atr_percentile,
                    rsi = EXCLUDED.rsi,
                    macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal,
                    macd_histogram = EXCLUDED.macd_histogram
            """, (
                currency, date,
                indicators.get("sma_50"), indicators.get("sma_200"),
                indicators.get("ema_50"), indicators.get("ema_200"),
                indicators.get("adx"), indicators.get("plus_di"), indicators.get("minus_di"),
                indicators.get("atr"), indicators.get("atr_percentile"), indicators.get("rsi"),
                indicators.get("macd"), indicators.get("macd_signal"), indicators.get("macd_histogram"),
            ))
            logger.info(f"Saved technical indicators for {currency} at {date}")

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
                key_levels.get("call_resistance"), key_levels.get("put_support"), key_levels.get("hvl"),
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

    def get_regime_detections(
        self,
        currency: str,
        start_time,
        end_time
    ) -> List[Dict[str, Any]]:
        """
        Retrieve regime detection results from the database.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
            start_time: Start of the time range.
            end_time: End of the time range.

        Returns:
            List of dicts with regime detection records.
        """
        with self._db_cursor() as cursor:
            cursor.execute("""
                SELECT
                    id, currency, detected_at, regime, confidence_score,
                    trend_score, volatility_score, momentum_score,
                    onchain_score, sentiment_score,
                    current_price, sma_50, sma_200,
                    adx, atr_percentile, rsi,
                    funding_rate, put_call_ratio, fear_greed,
                    reasoning, created_at
                FROM regime_detections
                WHERE currency = %s
                  AND detected_at >= %s
                  AND detected_at <= %s
                ORDER BY detected_at DESC
            """, (currency, start_time, end_time))

            columns = [
                "id", "currency", "detected_at", "regime", "confidence_score",
                "trend_score", "volatility_score", "momentum_score",
                "onchain_score", "sentiment_score",
                "current_price", "sma_50", "sma_200",
                "adx", "atr_percentile", "rsi",
                "funding_rate", "put_call_ratio", "fear_greed",
                "reasoning", "created_at",
            ]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

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

    # ── OTM Contract Finder ───────────────────────────────────────────────────

    def get_connection(self):
        """Return a raw database connection from the pool (caller must return it)."""
        return self._get_connection()

    def get_dvol_history(self, asset: str, limit: int = 400) -> List[float]:
        """
        Return recent DVOL values for the given asset, oldest-first.

        Queries the dvol_history table written by DVOLFetcher.save_to_db.
        Returns a plain list of floats (dvol_value), empty list on error.
        """
        try:
            with self._db_cursor() as cursor:
                cursor.execute(
                    "SELECT dvol_value FROM dvol_history "
                    "WHERE asset = %s ORDER BY timestamp DESC LIMIT %s",
                    (asset, limit),
                )
                rows = cursor.fetchall()
                return [float(r[0]) for r in reversed(rows)]
        except Exception as exc:
            logger.warning("get_dvol_history failed for %s: %s", asset, exc)
            return []

    def get_funding_rate_history(self, asset: str, limit: int = 1000) -> List[float]:
        """
        Return recent perpetual funding rates for the given asset, oldest-first.

        Queries funding_rate_history for the PERPETUAL instrument.
        Returns plain list of floats, empty list on error.
        """
        try:
            instrument = f"{asset}-PERPETUAL"
            with self._db_cursor() as cursor:
                cursor.execute(
                    "SELECT funding_rate FROM funding_rate_history "
                    "WHERE instrument_name = %s ORDER BY date DESC LIMIT %s",
                    (instrument, limit),
                )
                rows = cursor.fetchall()
                return [float(r[0]) for r in reversed(rows)]
        except Exception as exc:
            logger.warning("get_funding_rate_history failed for %s: %s", asset, exc)
            return []

    def get_pc_ratio_history(self, asset: str, limit: int = 200) -> List[float]:
        """
        Return recent put/call ratio history.

        No dedicated table exists yet — returns empty list until data is collected.
        """
        return []

    def get_rr25_history(self, asset: str, limit: int = 30) -> List[float]:
        """
        Return recent 25-delta risk-reversal history.

        No dedicated table exists yet — returns empty list until data is collected.
        """
        return []

    def get_ohlcv_daily(self, asset: str, limit: int = 60) -> List[Dict[str, Any]]:
        """
        Return recent daily OHLCV candles for the given asset's perpetual, oldest-first.

        Queries ohlcv_history for {asset}-PERPETUAL.
        Returns list of dicts with at least a 'close' key.
        """
        try:
            instrument = f"{asset}-PERPETUAL"
            with self._db_cursor() as cursor:
                cursor.execute(
                    "SELECT date, open, high, low, close, volume "
                    "FROM ohlcv_history "
                    "WHERE instrument_name = %s ORDER BY date DESC LIMIT %s",
                    (instrument, limit),
                )
                rows = cursor.fetchall()
                return [
                    {"date": r[0], "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]),
                     "volume": float(r[5]) if r[5] is not None else 0.0}
                    for r in reversed(rows)
                ]
        except Exception as exc:
            logger.warning("get_ohlcv_daily failed for %s: %s", asset, exc)
            return []

    def save_otm_signals(self, signals: list) -> int:
        """
        Upsert OTMSignal objects into the otm_signals table.

        Args:
            signals: List of OTMSignal Pydantic model instances.

        Returns:
            Number of rows inserted/updated.
        """
        import json as _json
        saved = 0
        try:
            with self._db_cursor() as cursor:
                for s in signals:
                    breakdown = {
                        "d1_d7": s.d1_d7_score, "d2": s.d2_score,
                        "d3": s.d3_score, "d4": s.d4_score,
                        "d6_d9": s.d6_d9_score, "d8": s.d8_score,
                        "d10": s.d10_score, "ris": s.ris_score,
                    }
                    exit_params = {
                        "stop_loss_pct": s.stop_loss_pct,
                        "time_stop_dte": s.time_stop_dte,
                        "take_profit_multiple": s.take_profit_multiple,
                    }
                    cursor.execute(
                        """
                        INSERT INTO otm_signals (
                            signal_id, generated_at, asset, instrument_name,
                            direction, strike, expiry, dte, delta, mark_iv,
                            entry_premium, underlying_price,
                            gate2_score, gate3_call_score, gate3_put_score,
                            conviction_score, position_usd, take_profit_multiple,
                            expiry_category, regime_flag,
                            signal_breakdown, exit_params
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s
                        )
                        ON CONFLICT (signal_id) DO UPDATE SET
                            conviction_score = EXCLUDED.conviction_score,
                            position_usd = EXCLUDED.position_usd
                        """,
                        (
                            s.signal_id, s.generated_at, s.asset, s.instrument_name,
                            s.direction, s.strike, s.expiry, s.dte, s.delta, s.mark_iv,
                            s.entry_premium, s.underlying_price,
                            s.gate2_score, s.gate3_call_score, s.gate3_put_score,
                            s.conviction_score, s.position_usd, s.take_profit_multiple,
                            s.expiry_category, s.regime_flag,
                            _json.dumps(breakdown), _json.dumps(exit_params),
                        ),
                    )
                    saved += 1
        except Exception as exc:
            logger.error("save_otm_signals failed: %s", exc)
        return saved
