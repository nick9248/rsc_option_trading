"""
Records real liquidation events: every run reads pages of OKX liquidation orders, newest first, and stores them
until a page reaches events that are already stored, so a burst of liquidations does not leave a gap. When the page
limit is reached before that, a gap is possible and is logged as a WARNING (nothing is filled in).
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from coding.core.database.repository import DatabaseRepository
from coding.service.liquidation_heatmap.okx_liquidation_client import VENUE, OkxLiquidationClient

logger = logging.getLogger(__name__)

INSTRUMENT = "BTC-USDT-SWAP"
MAXIMUM_PAGES = 30           # 3000 orders per run; a quiet hour has a few hundred


class LiquidationEventRecorder:
    """Stores new liquidation events of one instrument."""

    def __init__(
        self,
        client: OkxLiquidationClient,
        repository: DatabaseRepository,
        instrument: str = INSTRUMENT,
        maximum_pages: int = MAXIMUM_PAGES,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            client: Source of the events.
            repository: Where they are stored.
            instrument: The instrument whose events are kept (others in the answer are ignored).
            maximum_pages: Page limit of one run.
            clock: Wall clock (UTC-aware).
        """
        self.client = client
        self.repository = repository
        self.instrument = instrument
        self.maximum_pages = maximum_pages
        self.clock = clock

    def record(self) -> int:
        """
        Read and store the events newer than the newest stored one (all that the source has on the first run).

        Returns:
            The number of rows stored.
        """
        newest_stored: Optional[datetime] = self.repository.get_latest_liquidation_event_time(VENUE, self.instrument)
        now = self.clock()
        stored = 0
        cursor: Optional[int] = None
        reached_stored = False
        for _ in range(self.maximum_pages):
            page = [event for event in self.client.fetch_page(cursor) if event.instrument == self.instrument]
            raw_page_is_empty = not page
            if raw_page_is_empty:
                reached_stored = True              # the source has nothing older
                break
            stored += self.repository.save_liquidation_events(page, now)
            oldest = min(event.event_time for event in page)
            if newest_stored is not None and oldest <= newest_stored:
                reached_stored = True
                break
            cursor = int(oldest.timestamp() * 1000)
        if not reached_stored and newest_stored is not None:
            logger.warning("OKX liquidations: %d pages did not reach the newest stored event (%s); events between "
                           "may be missing", self.maximum_pages, newest_stored.isoformat())
        if stored:
            logger.info("Liquidations %s: stored %d event(s)", self.instrument, stored)
        return stored
