"""
Computes and stores the MenthorQ-style numbers (table
``menthorq_level_snapshots``) and loads the earlier records the trend
comparison needs.

Two modes (column ``mode``):
  - "live": spot, IV and open interest from the newest data at or before
    the run instant (hourly). Not what MenthorQ publishes: MenthorQ's open
    interest is about one day older.
  - "daily_close": the MenthorQ-style daily record of a UTC date: spot and
    IV at 23:59 UTC, open interest from the snapshot at or before 00:00 UTC
    of the same date (a consistent choice on three checked dates, not a
    proven instant). Computed once the date is over (the hourly run checks
    whether yesterday's record exists).

Reuses ``MenthorqInputLoader`` and ``replicate_menthorq`` (no chart import).
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, List, Optional

from coding.core.analytics.menthorq_monitor.level_record import (
    MODE_DAILY_CLOSE,
    MODE_LIVE,
    MenthorqLevelRecord,
    build_level_record,
    build_not_computed_record,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    COMPARISON_ONE_DAY,
    COMPARISON_PREVIOUS,
    COMPARISON_SEVEN_DAYS,
    ONE_DAY_COMPARISON_OFFSET,
    RecordComparison,
    compare_records,
)
from coding.core.analytics.menthorq_replication_calculator import (
    replicate_menthorq,
    start_of_utc_day,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings

logger = logging.getLogger(__name__)

DAILY_CLOSE_DATA_TIME_UTC = time(23, 59)
# How far a comparison record may be from its target instant (e.g. "24 h
# ago" accepts a record between 30 h and 24 h ago). Older -> "no record".
COMPARISON_TOLERANCE = {
    COMPARISON_PREVIOUS: timedelta(hours=3),
    COMPARISON_ONE_DAY: timedelta(hours=6),
    COMPARISON_SEVEN_DAYS: timedelta(hours=24),
}
COMPARISON_OFFSET = {
    COMPARISON_PREVIOUS: timedelta(0),
    COMPARISON_ONE_DAY: ONE_DAY_COMPARISON_OFFSET,
    COMPARISON_SEVEN_DAYS: timedelta(days=7),
}


def daily_close_as_of(utc_date: date) -> datetime:
    """23:59 UTC of ``utc_date``: the MenthorQ-style data instant."""
    return datetime.combine(utc_date, DAILY_CLOSE_DATA_TIME_UTC, tzinfo=timezone.utc)


def live_as_of(now: datetime) -> datetime:
    """The live run instant: ``now`` in UTC, truncated to the minute."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc).replace(second=0, microsecond=0)


class MenthorqMonitorService:
    """Compute, store and look up MenthorQ-style level records."""

    def __init__(
        self,
        repository: Optional[DatabaseRepository] = None,
        settings: Optional[MenthorqMonitorSettings] = None,
        input_loader: Optional[MenthorqInputLoader] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            repository: Database access (reads snapshots, writes
                ``menthorq_level_snapshots``).
            settings: Monitor settings (currency, universe, gamma underlying).
            input_loader: Loads the point-in-time inputs; built on
                ``repository`` when omitted.
            clock: Wall clock for ``computed_at`` (UTC-aware).
        """
        self.repository = repository or DatabaseRepository()
        self.settings = settings or MenthorqMonitorSettings()
        self.input_loader = input_loader or MenthorqInputLoader(self.repository)
        self.clock = clock

    def compute_record(
        self, mode: str, as_of: datetime, open_interest_as_of: Optional[datetime] = None
    ) -> MenthorqLevelRecord:
        """
        Compute one record without storing it. Inputs that cannot be loaded
        give a record with every number None and the reason (never a guess).
        """
        settings = self.settings
        try:
            inputs = self.input_loader.load_inputs(settings.currency, as_of, open_interest_as_of)
        except LookupError as error:
            logger.warning("MenthorQ %s record at %s not computed: %s", mode, as_of.isoformat(), error)
            return build_not_computed_record(
                settings.currency, mode, as_of, self.clock(), settings.expiry_universe,
                settings.gamma_underlying, f"inputs not available: {error}")
        replication = replicate_menthorq(
            inputs["chain_rows"], inputs["index_price"], as_of,
            settings.expiry_universe, settings.gamma_underlying,
        )
        return build_level_record(replication, inputs, settings.currency, mode, as_of, self.clock())

    def compute_live_record(self, now: datetime) -> MenthorqLevelRecord:
        """Live record at ``now`` (newest snapshot, its own open interest)."""
        return self.compute_record(MODE_LIVE, live_as_of(now))

    def compute_daily_close_record(self, utc_date: date) -> MenthorqLevelRecord:
        """MenthorQ-style daily record of ``utc_date`` (23:59 UTC, OI from 00:00 UTC)."""
        as_of = daily_close_as_of(utc_date)
        return self.compute_record(MODE_DAILY_CLOSE, as_of, start_of_utc_day(as_of))

    def store(self, record: MenthorqLevelRecord) -> MenthorqLevelRecord:
        """Save ``record``; sets ``record_id`` (None when that run was already stored)."""
        record.record_id = self.repository.save_menthorq_level_snapshot(record)
        logger.info(
            "MenthorQ %s record %s as of %s: CR %s PS %s HVL %s net GEX %s regime %s (id %s)%s",
            record.mode, record.currency, record.as_of_utc.isoformat(), record.call_resistance,
            record.put_support, record.hvl, record.total_net_gex, record.gamma_regime,
            record.record_id,
            f"; not computed: {record.not_computed_reasons}" if record.not_computed_reasons else "",
        )
        return record

    def run_live(self, now: datetime) -> MenthorqLevelRecord:
        """Compute and store the live record of ``now``."""
        return self.store(self.compute_live_record(now))

    def run_daily_close_if_missing(self, now: datetime) -> Optional[MenthorqLevelRecord]:
        """
        Compute and store yesterday's (UTC) daily-close record when it is not
        stored yet. Only data at or before yesterday 23:59 UTC is used, so
        running it any time after midnight adds no lookahead.

        Returns:
            The new record, or None when it was already stored.
        """
        yesterday = (now.astimezone(timezone.utc) - timedelta(days=1)).date()
        settings = self.settings
        if self.repository.has_menthorq_level_snapshot(
                settings.currency, MODE_DAILY_CLOSE, settings.expiry_universe,
                settings.gamma_underlying, daily_close_as_of(yesterday)):
            return None
        return self.store(self.compute_daily_close_record(yesterday))

    def find_reference(
        self, latest: MenthorqLevelRecord, label: str
    ) -> Optional[MenthorqLevelRecord]:
        """
        The stored record ``label`` ("previous", "24h", "7d") refers to:
        the newest computed record of the same currency, mode and settings
        at or before ``latest.as_of_utc`` minus the offset (strictly before
        for "previous"), within ``COMPARISON_TOLERANCE``.
        """
        target = latest.as_of_utc - COMPARISON_OFFSET[label]
        return self.repository.get_menthorq_level_snapshot_at_or_before(
            latest.currency, latest.mode, latest.expiry_universe, latest.gamma_underlying,
            target, earliest=target - COMPARISON_TOLERANCE[label],
            strictly_before=(label == COMPARISON_PREVIOUS),
        )

    def build_comparisons(self, latest: MenthorqLevelRecord) -> List[RecordComparison]:
        """Latest vs previous run, 24 h ago and 7 days ago (stored records)."""
        return [compare_records(latest, self.find_reference(latest, label), label)
                for label in (COMPARISON_PREVIOUS, COMPARISON_ONE_DAY, COMPARISON_SEVEN_DAYS)]

    def build_replayed_comparisons(self, latest: MenthorqLevelRecord) -> List[RecordComparison]:
        """
        Like ``build_comparisons`` but every earlier record is recomputed
        from the snapshots (point in time, nothing stored): what the hourly
        job would have stored one hour, 24 hours and 7 days before
        ``latest``. Used by the dry run, which must not need or write the
        monitor table.
        """
        comparisons = []
        for label, offset in ((COMPARISON_PREVIOUS, timedelta(hours=1)),
                              (COMPARISON_ONE_DAY, COMPARISON_OFFSET[COMPARISON_ONE_DAY]),
                              (COMPARISON_SEVEN_DAYS, COMPARISON_OFFSET[COMPARISON_SEVEN_DAYS])):
            reference = self.compute_record(latest.mode, latest.as_of_utc - offset)
            comparisons.append(compare_records(
                latest, reference if reference.spot is not None else None, label))
        return comparisons

    def latest_record(self, mode: str, now: datetime) -> Optional[MenthorqLevelRecord]:
        """Newest computed stored record of ``mode`` at or before ``now``."""
        settings = self.settings
        return self.repository.get_menthorq_level_snapshot_at_or_before(
            settings.currency, mode, settings.expiry_universe, settings.gamma_underlying, now)
