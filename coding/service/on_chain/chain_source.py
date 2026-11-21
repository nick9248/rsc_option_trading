"""
Where the analysis gets its option chain from: the newest stored snapshot of the local database, or live from Deribit.

The VPS collector stores the whole chain (mark IV, open interest, volume, bid and ask) every 30 minutes and ``scripts/sync_from_vps``
copies it into the local database (the analysis run syncs first). Reading it from there makes a run repeatable and independent of the
exchange's rate limits. A stored snapshot can be old (the collector is late, nobody synced), so its age is checked: older than the limit
means the live chain is fetched instead, and the run says which source it used.

Only the option chain and the index price come from here. The other inputs of the analysis (volatility index, recent trades, price
history, futures) are still fetched live.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)

CHAIN_SOURCE_STORED = "stored"
CHAIN_SOURCE_LIVE = "live"
CHAIN_SOURCES = (CHAIN_SOURCE_STORED, CHAIN_SOURCE_LIVE)
DEFAULT_MAX_SNAPSHOT_AGE_MINUTES = 45.0
"""The collector captures every 30 minutes: a snapshot up to 45 minutes old is the newest one the collector can have made."""

_BOOK_SUMMARY_FIELDS = ("instrument_name", "open_interest", "volume", "volume_usd", "mark_price", "mark_iv", "underlying_price",
                        "bid_price", "ask_price")


@dataclass(frozen=True)
class ChainInput:
    """The option chain of one analysis run and where it came from."""

    instruments: List[Dict[str, Any]]
    """Rows shaped like Deribit's book summary."""
    index_price: Optional[float]
    """The index price at the snapshot's capture time; None for a live chain (the live index is fetched by the caller)."""
    source: str
    captured_at: Optional[datetime]
    """UTC capture time of a stored snapshot; None for a live chain."""
    age_minutes: Optional[float]
    description: str
    """One line for the log: the source, the age and, for a fallback, why the stored snapshot was not used."""


class ChainSourceService:
    """Loads the option chain from the stored snapshot (live when it is missing or too old) or live."""

    def __init__(self, api: Optional[DeribitApiService], repository: Optional[DatabaseRepository],
                 mode: str = CHAIN_SOURCE_STORED, max_age_minutes: float = DEFAULT_MAX_SNAPSHOT_AGE_MINUTES):
        """
        Args:
            api: Deribit access, used for the live chain and as the fallback.
            repository: Local database, read only.
            mode: ``CHAIN_SOURCE_STORED`` or ``CHAIN_SOURCE_LIVE``.
            max_age_minutes: Oldest stored snapshot that is still used.

        Raises:
            ValueError: for an unknown mode.
        """
        if mode not in CHAIN_SOURCES:
            raise ValueError(f"unknown chain source {mode!r}; choose from {CHAIN_SOURCES}")
        self.api = api
        self.repository = repository
        self.mode = mode
        self.max_age_minutes = max_age_minutes

    def load(self, currency: str, now_utc: Optional[datetime] = None) -> ChainInput:
        """
        The chain for one analysis run.

        Args:
            currency: "BTC" or "ETH".
            now_utc: The current instant, timezone-aware UTC (default: now).

        Raises:
            Whatever the live fetch raises when the live chain is needed and the API fails.
        """
        now = now_utc or datetime.now(timezone.utc)
        if self.mode == CHAIN_SOURCE_LIVE:
            return self._live(currency, "live chain from Deribit (chosen)")
        if self.repository is None:
            return self._live(currency, "live chain from Deribit (no local database)")
        stored = self._stored(currency, now)
        if isinstance(stored, ChainInput):
            return stored
        return self._live(currency, f"live chain from Deribit ({stored})")

    def _stored(self, currency: str, now: datetime):
        """A ``ChainInput`` from the stored snapshot, or the reason (text) why it cannot be used."""
        assert self.repository is not None
        snapshot = self.repository.get_option_chain_snapshot_at_or_before(currency, now)
        if snapshot is None:
            return "no stored snapshot"
        captured_at, rows = snapshot
        age_minutes = max((now - captured_at).total_seconds() / 60.0, 0.0)
        if age_minutes > self.max_age_minutes:
            return (f"stored snapshot of {captured_at:%Y-%m-%d %H:%M} UTC is {age_minutes:.0f} min old, limit "
                    f"{self.max_age_minutes:.0f} min")
        index = self.repository.get_index_price_at_or_before(currency, captured_at)
        if index is None:
            return f"no index price at or before the snapshot of {captured_at:%Y-%m-%d %H:%M} UTC"
        index_price, index_time = index
        instruments = [{field: row.get(field) for field in _BOOK_SUMMARY_FIELDS} for row in rows]
        description = (f"stored snapshot of {captured_at:%Y-%m-%d %H:%M} UTC, {age_minutes:.0f} min old, {len(instruments)} instruments, "
                       f"index {index_price:,.2f} from the trade at {index_time:%H:%M:%S} UTC")
        return ChainInput(instruments, index_price, CHAIN_SOURCE_STORED, captured_at, age_minutes, description)

    def _live(self, currency: str, description: str) -> ChainInput:
        assert self.api is not None, "a live chain needs the Deribit API"
        instruments = self.api.get_book_summary(currency=currency, kind="option")
        return ChainInput(instruments, None, CHAIN_SOURCE_LIVE, None, None, f"{description}, {len(instruments)} instruments")
