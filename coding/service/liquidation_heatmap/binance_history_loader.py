"""
Reads the history the liquidation heatmap research model needs from Binance (public, no key):
5-minute klines (REST) and 5-minute open interest (the public data archive for complete days,
REST for the days the archive does not have yet). Research use from the PC only: Binance
blocks the USA VPS (see documentation/futures_taker_flow.md).

Nothing is invented: a day that neither source has is reported in ``notes`` and left empty.
"""

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, PriceCandle

logger = logging.getLogger(__name__)

REST_BASE_URL = "https://fapi.binance.com"
ARCHIVE_URL = ("https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/"
               "{symbol}-metrics-{day}.zip")
KLINES_PER_REQUEST = 1500
OPEN_INTEREST_PER_REQUEST = 500
REQUEST_TIMEOUT_SECONDS = 30
REST_OPEN_INTEREST_DAYS = 30        # Binance serves about this much 5-minute OI history by REST


class BinanceHistoryError(RuntimeError):
    """Binance answered, but not with usable data."""


def _milliseconds(instant: datetime) -> int:
    return int(instant.timestamp() * 1000)


class BinanceHistoryLoader:
    """Loads 5-minute candles and open interest of a USD-M perpetual."""

    def __init__(self, cache_directory: Path, session: Optional[requests.Session] = None,
                 rest_base_url: str = REST_BASE_URL):
        """
        Args:
            cache_directory: Where downloaded archive days are kept (a complete day never changes).
            session: HTTP session (a new one by default; tests pass a fake).
            rest_base_url: Binance futures REST host.
        """
        self.cache_directory = cache_directory
        self.session = session or requests.Session()
        self.rest_base_url = rest_base_url.rstrip("/")

    def load_candles(self, symbol: str, start: datetime, end: datetime) -> List[PriceCandle]:
        """
        Closed 5-minute candles with open time in ``[start, end)``, oldest first.

        Raises:
            BinanceHistoryError: when Binance answers with an error or an unusable body.
        """
        candles: List[PriceCandle] = []
        cursor = start
        while cursor < end:
            response = self.session.get(
                self.rest_base_url + "/fapi/v1/klines",
                params={"symbol": symbol, "interval": "5m", "limit": KLINES_PER_REQUEST,
                        "startTime": _milliseconds(cursor), "endTime": _milliseconds(end) - 1},
                timeout=REQUEST_TIMEOUT_SECONDS)
            rows = self._rows(response, f"{symbol} klines")
            if not rows:
                break
            for row in rows:
                open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
                candles.append(PriceCandle(open_time, high=float(row[2]), low=float(row[3]), close=float(row[4])))
            next_cursor = candles[-1].open_time + STEP
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return [candle for candle in candles if start <= candle.open_time < end]

    def load_open_interest(self, symbol: str, start: datetime, end: datetime,
                           today: Optional[date] = None) -> Tuple[Dict[datetime, float], List[str]]:
        """
        Open interest in BTC by 5-minute sample time, for samples in ``[start, end]``.

        Complete UTC days come from the public archive (cached), the rest and any day the
        archive does not have from REST (about the last 30 days only).

        Returns:
            The samples, and notes naming each day that could not be loaded.
        """
        today = today or datetime.now(timezone.utc).date()
        samples: Dict[datetime, float] = {}
        notes: List[str] = []
        day = start.date()
        while day <= end.date():
            loaded = self._archive_day(symbol, day) if day < today else None
            if loaded is None:
                loaded = self._rest_day(symbol, day)
            if loaded is None:
                notes.append(f"open interest of {day.isoformat()} not available from the archive or REST")
            else:
                samples.update(loaded)
            day += timedelta(days=1)
        return {time: value for time, value in samples.items() if start <= time <= end}, notes

    def _rows(self, response, what: str):
        if response.status_code != 200:
            raise BinanceHistoryError(f"{what}: HTTP {response.status_code}")
        try:
            rows = response.json()
        except ValueError:
            raise BinanceHistoryError(f"{what}: the answer is not JSON") from None
        if not isinstance(rows, list):
            raise BinanceHistoryError(f"{what}: unexpected answer {str(rows)[:120]}")
        return rows

    def _archive_day(self, symbol: str, day: date) -> Optional[Dict[datetime, float]]:
        """One complete day from the archive (cached), or None when the archive does not have it."""
        cache_file = self.cache_directory / "open_interest" / f"{symbol}-metrics-{day.isoformat()}.zip"
        if cache_file.exists():
            content = cache_file.read_bytes()
        else:
            response = self.session.get(ARCHIVE_URL.format(symbol=symbol, day=day.isoformat()),
                                        timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise BinanceHistoryError(f"{symbol} archive {day}: HTTP {response.status_code}")
            content = response.content
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        samples: Dict[datetime, float] = {}
        for row in csv.DictReader(io.StringIO(text)):
            stamp = datetime.strptime(row["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            samples[stamp] = float(row["sum_open_interest"])
        return samples

    def _rest_day(self, symbol: str, day: date) -> Optional[Dict[datetime, float]]:
        """One UTC day of 5-minute OI by REST, or None when it is older than REST serves."""
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        if day_start < datetime.now(timezone.utc) - timedelta(days=REST_OPEN_INTEREST_DAYS - 1):
            return None
        samples: Dict[datetime, float] = {}
        cursor, day_end = day_start, day_start + timedelta(days=1)
        while cursor < day_end:
            response = self.session.get(
                self.rest_base_url + "/futures/data/openInterestHist",
                params={"symbol": symbol, "period": "5m", "limit": OPEN_INTEREST_PER_REQUEST,
                        "startTime": _milliseconds(cursor), "endTime": _milliseconds(day_end) - 1},
                timeout=REQUEST_TIMEOUT_SECONDS)
            rows = self._rows(response, f"{symbol} openInterestHist")
            if not rows:
                break
            for row in rows:
                samples[datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc)] = float(
                    row["sumOpenInterest"])
            next_cursor = max(samples) + STEP
            if next_cursor <= cursor:      # the source did not move forward: stop instead of looping
                break
            cursor = next_cursor
        return samples or None
