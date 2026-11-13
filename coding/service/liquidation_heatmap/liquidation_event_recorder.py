"""
Records real liquidation events: every run reads pages of OKX liquidation orders, newest first, until a page reaches
events that are already stored, so a burst of liquidations does not leave a gap, and then stores everything it read in ONE
transaction, oldest first. If a page request fails nothing of that run is stored, so the next run starts again from the
same newest stored event and no gap can appear between runs (and a sync never sees half a run). When the page limit is
reached before the stored events, a gap is possible and is logged as a WARNING (nothing is filled in).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from coding.core.analytics.liquidation_heatmap.level_snapshot import RecordedLiquidation
from coding.core.database.repository import DatabaseRepository
from coding.service.liquidation_heatmap.okx_liquidation_client import VENUE, OkxLiquidationClient

logger = logging.getLogger(__name__)

INSTRUMENT = "BTC-USDT-SWAP"
MAXIMUM_PAGES = 30           # 3000 orders per run; a quiet hour has a few hundred
PAUSE_SECONDS = 0.2          # between two pages, to stay far below OKX's public rate limit


class LiquidationEventRecorder:
    """Stores new liquidation events of one instrument."""

    def __init__(
        self,
        client: OkxLiquidationClient,
        repository: DatabaseRepository,
        instrument: str = INSTRUMENT,
        maximum_pages: int = MAXIMUM_PAGES,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        pause_seconds: float = PAUSE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """
        Args:
            client: Source of the events.
            repository: Where they are stored.
            instrument: The instrument whose events are kept (others in the answer are ignored).
            maximum_pages: Page limit of one run.
            clock: Wall clock (UTC-aware).
            pause_seconds: Pause between two pages of one run.
            sleep: Sleep function (tests pass a no-op).
        """
        self.pause_seconds = pause_seconds
        self.sleep = sleep
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
        collected: List[RecordedLiquidation] = []
        cursor: Optional[int] = None
        reached_stored = False
        for page_number in range(self.maximum_pages):
            raw_page = self.client.fetch_page(cursor)
            if not raw_page:
                reached_stored = True              # the source has nothing older
                break
            collected.extend(event for event in raw_page if event.instrument == self.instrument)
            oldest = min(event.event_time for event in raw_page)     # the cursor follows the RAW page, not the filtered one
            if newest_stored is not None and oldest <= newest_stored:
                reached_stored = True
                break
            oldest_ms = int(oldest.timestamp() * 1000)
            # The cursor is inclusive (+1 ms): orders of the same millisecond that a full page cut off are read again
            # (the table ignores duplicates). If that makes no progress (a page that is one single millisecond),
            # step strictly past it.
            next_cursor = oldest_ms + 1
            cursor = oldest_ms if cursor is not None and next_cursor >= cursor else next_cursor
            if page_number + 1 < self.maximum_pages:
                self.sleep(self.pause_seconds)
        stored = self.repository.save_liquidation_events(sorted(collected, key=lambda event: event.event_time), now)
        if not reached_stored and newest_stored is not None:
            logger.warning("OKX liquidations: %d pages did not reach the newest stored event (%s); events between "
                           "may be missing", self.maximum_pages, newest_stored.isoformat())
        if stored:
            logger.info("Liquidations %s: stored %d event(s)", self.instrument, stored)
        return stored
