"""
Open interest per expiry of the newest stored option-chain snapshot (read-only).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

from coding.core.analytics.open_interest.expiry_open_interest import ExpiryOpenInterest, open_interest_by_expiry
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader

logger = logging.getLogger(__name__)


@dataclass
class ExpiryOpenInterestReport:
    """Open interest per expiry of one snapshot: currency, when it was captured, the index price and one row per expiry."""

    currency: str
    captured_at: datetime
    index_price: float
    rows: List[ExpiryOpenInterest]


class ExpiryOpenInterestService:
    """Loads the newest chain snapshot at or before the clock's instant and sums its open interest per expiry."""

    def __init__(self, repository: Optional[DatabaseRepository] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """
        Args:
            repository: Database access (the configured database by default); only read methods are used.
            clock: Wall clock (UTC-aware); the snapshot is the newest one at or before it.
        """
        self.repository = repository or DatabaseRepository()
        self.clock = clock

    def open_interest(self, currency: str) -> ExpiryOpenInterestReport:
        """
        Open interest per unexpired expiry, largest first.

        Raises:
            LookupError: when no chain snapshot or index price is stored (as ``MenthorqInputLoader`` does).
        """
        as_of = self.clock()
        inputs = MenthorqInputLoader(self.repository).load_inputs(currency, as_of)
        rows = open_interest_by_expiry(inputs["chain_rows"], inputs["index_price"], as_of)
        logger.info("Open interest of %s: %d expiries in the snapshot of %s", currency, len(rows),
                    inputs["snapshot_captured_at_utc"].isoformat())
        return ExpiryOpenInterestReport(currency, inputs["snapshot_captured_at_utc"], inputs["index_price"], rows)
