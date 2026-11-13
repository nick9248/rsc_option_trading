"""
Reads hourly taker volume of Binance USD-M perpetuals from the public klines
endpoint (no API key, read-only).

Each 1-hour kline carries the total quote volume (index 7) and the quote volume
bought by takers (index 10); taker sell volume is the difference. Only CLOSED
hours are returned: the hour in progress changes and must not be stored.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any, List, Optional

import requests

from coding.core.analytics.futures_flow.taker_flow import TakerFlowHour, start_of_utc_hour

logger = logging.getLogger(__name__)

KLINES_PATH = "/fapi/v1/klines"
MAXIMUM_KLINES_PER_REQUEST = 1500
REQUEST_TIMEOUT_SECONDS = 20
KLINE_OPEN_TIME, KLINE_QUOTE_VOLUME, KLINE_TRADE_COUNT, KLINE_TAKER_BUY_QUOTE = 0, 7, 8, 10
KLINE_FIELD_COUNT = 11


class TakerFlowDataError(RuntimeError):
    """Binance (or the relay) answered, but not with usable klines."""


class BinanceTakerFlowClient:
    """Fetches closed hourly taker volumes of one symbol."""

    def __init__(self, base_url: str, session: Optional[requests.Session] = None):
        """
        Args:
            base_url: ``http(s)://host[:port]`` of Binance futures or the relay.
            session: HTTP session (a new one by default; tests pass a fake).
        """
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def fetch_closed_hours(self, symbol: str, start_hour_utc: datetime, now: datetime) -> List[TakerFlowHour]:
        """
        The closed UTC hours of ``symbol`` from ``start_hour_utc`` on (at most 1500).

        Args:
            symbol: e.g. ``BTCUSDT``.
            start_hour_utc: First hour wanted (a full UTC hour).
            now: Current time (UTC-aware); the hour containing it is not returned.

        Raises:
            TakerFlowDataError: when the answer is not a list of well-formed klines
                (including a block message such as HTTP 451).
            requests.RequestException: when the request itself fails.
        """
        response = self.session.get(
            self.base_url + KLINES_PATH,
            params={"symbol": symbol, "interval": "1h", "limit": MAXIMUM_KLINES_PER_REQUEST,
                    "startTime": int(start_of_utc_hour(start_hour_utc).timestamp() * 1000)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise TakerFlowDataError(f"{symbol} klines: HTTP {response.status_code} from {self.base_url}")
        try:
            rows = response.json()
        except ValueError:
            raise TakerFlowDataError(f"{symbol} klines: the answer is not JSON") from None
        if not isinstance(rows, list):
            raise TakerFlowDataError(f"{symbol} klines: unexpected answer type {type(rows).__name__}")

        current_hour = start_of_utc_hour(now)
        hours = []
        for row in rows:
            hour = self._parse_row(symbol, row)
            if hour is not None and hour.hour_utc < current_hour:
                hours.append(hour)
        return hours

    @staticmethod
    def _parse_row(symbol: str, row: Any) -> Optional[TakerFlowHour]:
        """One kline as an hour, or None (logged) when the row is not plausible."""
        try:
            if len(row) < KLINE_FIELD_COUNT:
                raise ValueError(f"{len(row)} fields")
            hour_utc = datetime.fromtimestamp(int(row[KLINE_OPEN_TIME]) / 1000, tz=timezone.utc)
            total = float(row[KLINE_QUOTE_VOLUME])
            buy = float(row[KLINE_TAKER_BUY_QUOTE])
            trades = int(row[KLINE_TRADE_COUNT])
        except (TypeError, ValueError, IndexError, KeyError) as error:
            logger.warning("Taker flow %s: skipped a malformed kline (%s)", symbol, error)
            return None
        if (hour_utc != start_of_utc_hour(hour_utc) or not math.isfinite(total) or not math.isfinite(buy)
                or total < 0 or buy < 0 or buy > total):
            logger.warning("Taker flow %s: skipped an implausible kline at %s (total %s, taker buy %s)",
                           symbol, hour_utc.isoformat(), total, buy)
            return None
        return TakerFlowHour(symbol=symbol, hour_utc=hour_utc, taker_buy_usd=buy,
                             taker_sell_usd=total - buy, trade_count=trades)
