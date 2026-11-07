"""
Point-in-time inputs of a MenthorQ-style computation: the option-chain
snapshot and index price at or before ``as_of`` (read-only queries).

Kept apart from ``MenthorqReplicationService`` so the collector's monitor job
can load inputs without importing the matplotlib chart renderer.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from coding.core.analytics.menthorq_replication_calculator import (
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
    fill_missing_implied_volatility,
)
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class MenthorqInputLoader:
    """Loads the newest chain snapshot and index price at or before an instant."""

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Args:
            repository: Database access; a default ``DatabaseRepository`` when
                omitted. Only its read methods are used.
        """
        self.repository = repository or DatabaseRepository()

    def load_inputs(
        self, currency: str, as_of: datetime, open_interest_as_of: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Newest chain snapshot and index price at or before ``as_of``.

        ``open_interest_as_of`` (optional, must be <= ``as_of``) replaces each
        instrument's open interest with its value in the newest snapshot at
        or before that earlier instant, keeping mark IV and forwards from
        ``as_of``. It exists to test whether a reference (MenthorQ) uses
        older open interest; an instrument absent from the older snapshot
        gets open interest None, not 0.

        A row without ``mark_iv`` gets an IV rebuilt from its ``mark_price``
        and forward (``fill_missing_implied_volatility``, priced at the
        snapshot's capture time). This fallback is logged as a warning, each
        row carries ``iv_source`` and the counts are returned as
        ``iv_source_counts``.

        Raises:
            LookupError: when an input has no row at or before its instant
                (the replay never falls back to a later row).
            ValueError: when ``open_interest_as_of`` is after ``as_of``.
        """
        snapshot = self.repository.get_option_chain_snapshot_at_or_before(currency, as_of)
        if snapshot is None:
            raise LookupError(f"No {currency} option-chain snapshot at or before {as_of.isoformat()}")
        index = self.repository.get_index_price_at_or_before(currency, as_of)
        if index is None:
            raise LookupError(f"No {currency} index price at or before {as_of.isoformat()}")
        captured_at, chain_rows = snapshot
        index_price, index_time = index
        open_interest_captured_at = captured_at
        if open_interest_as_of is not None:
            if open_interest_as_of > as_of:
                raise ValueError("open_interest_as_of must not be after as_of (lookahead)")
            older = self.repository.get_option_chain_snapshot_at_or_before(
                currency, open_interest_as_of
            )
            if older is None:
                raise LookupError(
                    f"No {currency} snapshot at or before {open_interest_as_of.isoformat()}"
                )
            open_interest_captured_at, older_rows = older
            older_open_interest = {
                row["instrument_name"]: row["open_interest"] for row in older_rows
            }
            chain_rows = [
                dict(row, open_interest=older_open_interest.get(row["instrument_name"]))
                for row in chain_rows
            ]
        chain_rows, iv_source_counts = fill_missing_implied_volatility(chain_rows, captured_at)
        if iv_source_counts[IV_SOURCE_REBUILT_FROM_MARK_PRICE]:
            logger.warning(
                "mark_iv missing on %d rows of the %s snapshot: IV rebuilt from mark_price "
                "and the forward (Black-76, r = 0), labelled iv_source=%r",
                iv_source_counts[IV_SOURCE_REBUILT_FROM_MARK_PRICE], captured_at.isoformat(),
                IV_SOURCE_REBUILT_FROM_MARK_PRICE,
            )
        if iv_source_counts[IV_SOURCE_MISSING]:
            logger.warning("%d rows have no IV (no mark_iv and none rebuilt); their gamma is not computed",
                           iv_source_counts[IV_SOURCE_MISSING])
        logger.info(
            "Inputs for %s: snapshot captured %s (%d rows), index %.2f from trade at %s",
            as_of.isoformat(), captured_at.isoformat(), len(chain_rows), index_price,
            index_time.isoformat(),
        )
        return {
            "snapshot_captured_at_utc": captured_at,
            "open_interest_captured_at_utc": open_interest_captured_at,
            "chain_rows": chain_rows,
            "iv_source_counts": iv_source_counts,
            "index_price": index_price,
            "index_price_time_utc": index_time,
        }
