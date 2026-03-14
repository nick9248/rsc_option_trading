"""
DVOLFetcher — fetches Deribit DVOL index history and latest value.

Deribit endpoint: /public/get_index_price_history
Index names: btc_dvol, eth_dvol
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.deribit.com/api/v2/public/get_index_price_history"
_ASSET_TO_INDEX = {"BTC": "btc_dvol", "ETH": "eth_dvol"}
_RESOLUTION = 1440  # daily (minutes)
_TIMEOUT_SEC = 15


class DVOLFetcher:
    """Fetches DVOL index values from Deribit for Gate 2 percentile calculation."""

    def _build_url(self, asset: str, start_ms: int, end_ms: int) -> str:
        if asset not in _ASSET_TO_INDEX:
            raise ValueError(f"Unsupported asset: {asset}. Must be BTC or ETH.")
        index_name = _ASSET_TO_INDEX[asset]
        return (f"{_BASE_URL}?index_name={index_name}"
                f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
                f"&resolution={_RESOLUTION}")

    def _parse_response(self, data: dict) -> List[Tuple[datetime, float]]:
        """Parse Deribit response into list of (datetime, dvol_value) tuples."""
        rows = data["result"]["data"]
        return [
            (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc), float(value))
            for ts_ms, value in rows
        ]

    def fetch_latest(self, asset: str) -> Optional[float]:
        """
        Fetch the most recent DVOL value for the given asset.

        Returns None on any error — callers should treat None as unavailable.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - 2 * 24 * 3600 * 1000  # 2 days back to ensure at least 1 row
        try:
            url = self._build_url(asset, start_ms, now_ms)
            resp = requests.get(url, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning("DVOL fetch failed for %s: HTTP %s", asset, resp.status_code)
                return None
            rows = self._parse_response(resp.json())
            if not rows:
                logger.warning("DVOL fetch returned empty data for %s", asset)
                return None
            return rows[-1][1]  # most recent
        except Exception as exc:
            logger.error("DVOLFetcher.fetch_latest error for %s: %s", asset, exc)
            return None

    def fetch_history(
        self, asset: str, months: int = 36
    ) -> List[Tuple[datetime, float]]:
        """
        Fetch up to `months` months of daily DVOL history.

        Returns list of (datetime, dvol_value) tuples, oldest first.
        Returns empty list on any error.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000
        )
        try:
            url = self._build_url(asset, start_ms, now_ms)
            resp = requests.get(url, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning("DVOL history fetch failed for %s: HTTP %s", asset, resp.status_code)
                return []
            rows = self._parse_response(resp.json())
            logger.info("DVOLFetcher: fetched %d rows for %s (%d months)", len(rows), asset, months)
            return sorted(rows, key=lambda x: x[0])
        except Exception as exc:
            logger.error("DVOLFetcher.fetch_history error for %s: %s", asset, exc)
            return []

    def save_to_db(self, rows: List[Tuple[datetime, float]], asset: str, conn) -> int:
        """
        Upsert DVOL rows into dvol_history table.

        Args:
            rows: list of (datetime, dvol_value) tuples.
            asset: "BTC" or "ETH".
            conn: open psycopg2 connection (caller manages lifecycle and MUST call conn.commit() after).

        Returns:
            Number of new rows inserted (existing rows skipped via ON CONFLICT DO NOTHING).
        """
        inserted = 0
        with conn.cursor() as cur:
            for ts, value in rows:
                cur.execute(
                    """
                    INSERT INTO dvol_history (asset, timestamp, dvol_value)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (asset, timestamp) DO NOTHING
                    """,
                    (asset, ts, value),
                )
                inserted += cur.rowcount
        logger.debug("DVOLFetcher.save_to_db: %d new rows for %s", inserted, asset)
        return inserted
