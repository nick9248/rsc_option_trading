"""
Collects hourly taker volume of the Binance USD-M perpetuals into
``taker_flow_hourly``: every run stores the closed hours of the last
``BACKFILL_DAYS`` days that are not stored yet, so the first run fills 30
days and a missed run is repaired by the next one.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Sequence

from coding.core.analytics.futures_flow.taker_flow import missing_hours, start_of_utc_hour
from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.binance_taker_flow_client import BinanceTakerFlowClient
from coding.service.futures_flow.taker_flow_settings import BACKFILL_DAYS, SYMBOLS

logger = logging.getLogger(__name__)


class TakerFlowCollectionService:
    """Stores the missing closed hours of each symbol."""

    def __init__(
        self,
        client: BinanceTakerFlowClient,
        repository: DatabaseRepository,
        symbols: Sequence[str] = SYMBOLS,
        backfill_days: int = BACKFILL_DAYS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            client: Source of the hourly volumes.
            repository: Where the hours are stored.
            symbols: Binance symbols to collect.
            backfill_days: How far back a missing hour is looked for.
            clock: Wall clock (UTC-aware).
        """
        self.client = client
        self.repository = repository
        self.symbols = tuple(symbols)
        self.backfill_days = backfill_days
        self.clock = clock

    def collect(self) -> Dict[str, Optional[int]]:
        """
        Store the missing closed hours of every symbol. One symbol failing is
        logged (ERROR) and does not stop the next one.

        Returns:
            Symbol -> number of rows stored, or None when that symbol failed.
        """
        now = self.clock()
        stored: Dict[str, Optional[int]] = {}
        for symbol in self.symbols:
            try:
                stored[symbol] = self._collect_symbol(symbol, now)
            except Exception:
                logger.exception("Taker flow %s: collection failed", symbol)
                stored[symbol] = None
        return stored

    def _collect_symbol(self, symbol: str, now: datetime) -> int:
        last_closed_hour = start_of_utc_hour(now) - timedelta(hours=1)
        first_hour = last_closed_hour - timedelta(days=self.backfill_days) + timedelta(hours=1)
        present = [hour.hour_utc for hour in self.repository.get_taker_flow_hours(symbol, first_hour, last_closed_hour)]
        missing = missing_hours(present, first_hour, last_closed_hour)
        if not missing:
            return 0

        fetched = self.client.fetch_closed_hours(symbol, missing[0], now)
        wanted = set(missing)
        to_store = [hour for hour in fetched if hour.hour_utc in wanted]
        inserted = self.repository.save_taker_flow_hours(to_store, now)

        still_missing = wanted - {hour.hour_utc for hour in to_store}
        if still_missing:
            logger.warning("Taker flow %s: %d hour(s) not available from the source, oldest %s",
                           symbol, len(still_missing), min(still_missing).isoformat())
        logger.info("Taker flow %s: stored %d hour(s)", symbol, inserted)
        return inserted
