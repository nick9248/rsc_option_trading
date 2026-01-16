"""
Database repository for on-chain analysis data storage.

Provides methods to save and retrieve data from PostgreSQL tables.
"""

import logging
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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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
            conn.commit()

            logger.info(f"Saved {len(rows)} snapshot records for {currency}")
            return len(rows)

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save snapshot: {e}")
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

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

        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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
            conn.commit()

            logger.info(f"Saved max pain for {currency} {expiration}: {max_pain_strike}")
            return row_id

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save max pain: {e}")
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

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

        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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
            conn.commit()

            logger.info(f"Saved OI for {currency} {expiration}: {total_oi}")
            return row_id

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save open interest: {e}")
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

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

        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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
            conn.commit()

            logger.info(f"Saved volume for {currency} {expiration}: {total_volume}")
            return row_id

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save volume: {e}")
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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
            conn.commit()

            logger.info(f"Saved {len(rows)} levels for {currency} {expiration}")
            return len(rows)

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save levels: {e}")
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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

        finally:
            cursor.close()
            self._return_connection(conn)

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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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

        finally:
            cursor.close()
            self._return_connection(conn)

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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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

        finally:
            cursor.close()
            self._return_connection(conn)

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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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

        finally:
            cursor.close()
            self._return_connection(conn)

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

        conn = self._get_connection()

        try:
            cursor = conn.cursor()

            cursor.execute(f"""
                SELECT DISTINCT expiration
                FROM {table}
                WHERE currency = %s
                ORDER BY expiration
            """, (currency,))

            return [row[0] for row in cursor.fetchall()]

        finally:
            cursor.close()
            self._return_connection(conn)

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
            hvl_strike: High Volume Level (zero gamma) strike.
            underlying_price: Current underlying price.
            captured_at: Timestamp of capture.

        Returns:
            ID of inserted row.
        """
        captured_at = captured_at or datetime.now()
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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
            conn.commit()

            logger.info(f"Saved GEX/DEX for {currency} {expiration}")
            return row_id

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save GEX/DEX: {e}")
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

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
        conn = self._get_connection()

        try:
            cursor = conn.cursor()

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

        finally:
            cursor.close()
            self._return_connection(conn)
