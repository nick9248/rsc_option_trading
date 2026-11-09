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
    proven instant). Computed once the date is over (the hourly run fills
    any missing record of the last 7 ended days, and retries a
    not-computed one when a newer snapshot has arrived); not
    computed from an input more than 60 min older than its instant
    (``stale_input_reasons`` and ``DAILY_CLOSE_MAXIMUM_INPUT_AGE`` in
    core/analytics/menthorq_monitor/input_age.py, shared with the backtest).

Reuses ``MenthorqInputLoader`` and ``replicate_menthorq`` (no chart import).
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from coding.core.analytics.menthorq_monitor.input_age import (
    DAILY_CLOSE_MAXIMUM_INPUT_AGE,
    stale_input_reasons,
)
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
    DAILY_CLOSE_DATA_TIME_UTC,
    replicate_menthorq,
    start_of_utc_day,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings

logger = logging.getLogger(__name__)

# The hourly run fills missing daily-close records of this many ended UTC days.
DAILY_CLOSE_BACKFILL_DAYS = 7
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
        # Daily-close date -> newest snapshot capture (at or before its 23:59 UTC)
        # when this process last computed it; None when there was none.
        self._daily_close_attempt_captures: Dict[date, Optional[datetime]] = {}

    def compute_record(
        self, mode: str, as_of: datetime, open_interest_as_of: Optional[datetime] = None,
        maximum_input_age: Optional[timedelta] = None,
    ) -> MenthorqLevelRecord:
        """
        Compute one record without storing it. Inputs that cannot be loaded
        give a record with every number None and the reason (never a guess).
        With ``maximum_input_age``, inputs older than that before their own
        instant (``stale_input_reasons``) give such a record too.
        """
        settings = self.settings
        try:
            inputs = self.input_loader.load_inputs(settings.currency, as_of, open_interest_as_of)
        except LookupError as error:
            logger.warning("MenthorQ %s record at %s not computed: %s", mode, as_of.isoformat(), error)
            return build_not_computed_record(
                settings.currency, mode, as_of, self.clock(), settings.expiry_universe,
                settings.gamma_underlying, f"inputs not available: {error}")
        if maximum_input_age is not None:
            reasons = stale_input_reasons(inputs, as_of, open_interest_as_of or as_of, maximum_input_age)
            if reasons:
                reason = "; ".join(reasons)
                logger.warning("MenthorQ %s record at %s not computed: %s", mode, as_of.isoformat(), reason)
                # The snapshots used are known: keep their capture times, so a retry after a
                # restart sees that no newer snapshot exists (_daily_close_retry_is_due).
                return build_not_computed_record(
                    settings.currency, mode, as_of, self.clock(), settings.expiry_universe,
                    settings.gamma_underlying, reason,
                    snapshot_captured_at_utc=inputs.get("snapshot_captured_at_utc"),
                    open_interest_captured_at_utc=inputs.get("open_interest_captured_at_utc"))
        replication = replicate_menthorq(
            inputs["chain_rows"], inputs["index_price"], as_of,
            settings.expiry_universe, settings.gamma_underlying, currency=settings.currency,
        )
        return build_level_record(replication, inputs, settings.currency, mode, as_of, self.clock())

    def compute_live_record(self, now: datetime) -> MenthorqLevelRecord:
        """Live record at ``now`` (newest snapshot, its own open interest)."""
        return self.compute_record(MODE_LIVE, live_as_of(now))

    def compute_daily_close_record(self, utc_date: date) -> MenthorqLevelRecord:
        """
        MenthorQ-style daily record of ``utc_date`` (23:59 UTC, OI from 00:00
        UTC). Like the backtest, it is NOT computed from an input older than
        ``DAILY_CLOSE_MAXIMUM_INPUT_AGE`` before its instant (the day is
        incomplete in the database).
        """
        as_of = daily_close_as_of(utc_date)
        return self.compute_record(MODE_DAILY_CLOSE, as_of, start_of_utc_day(as_of),
                                   maximum_input_age=DAILY_CLOSE_MAXIMUM_INPUT_AGE)

    def store(self, record: MenthorqLevelRecord) -> MenthorqLevelRecord:
        """
        Save ``record``; sets ``record_id`` (None when that run was already
        stored and kept, see ``save_menthorq_level_snapshot``).
        """
        record.record_id = self.repository.save_menthorq_level_snapshot(record)
        # A computed record's reasons are only caveats (e.g. some rows without IV).
        reasons_label = "caveats" if record.has_levels else "not computed"
        logger.info(
            "MenthorQ %s record %s as of %s: CR %s PS %s HVL %s net GEX %s regime %s (id %s)%s",
            record.mode, record.currency, record.as_of_utc.isoformat(), record.call_resistance,
            record.put_support, record.hvl, record.total_net_gex, record.gamma_regime,
            record.record_id,
            f"; {reasons_label}: {record.not_computed_reasons}" if record.not_computed_reasons else "",
        )
        return record

    def run_live(self, now: datetime) -> MenthorqLevelRecord:
        """Compute and store the live record of ``now``."""
        return self.store(self.compute_live_record(now))

    def _daily_close_retry_is_due(
        self, utc_date: date, stored_row: MenthorqLevelRecord, newest_capture: Optional[datetime]
    ) -> bool:
        """
        Whether a stored NOT-computed daily close of ``utc_date`` is worth
        recomputing: only when a snapshot newer than the one it was last
        tried with exists at or before its 23:59 UTC (``newest_capture``).
        "Last tried with" is the newer of the row's
        ``snapshot_captured_at_utc`` and the newest capture seen at this
        process's last attempt. A row without a snapshot time that this
        process has not tried yet is retried once.
        """
        attempted = utc_date in self._daily_close_attempt_captures
        if stored_row.snapshot_captured_at_utc is None and not attempted:
            return True
        known = [instant for instant in (stored_row.snapshot_captured_at_utc,
                                         self._daily_close_attempt_captures.get(utc_date))
                 if instant is not None]
        last_tried_with = max(known) if known else None
        return newest_capture is not None and (last_tried_with is None or newest_capture > last_tried_with)

    def run_daily_close_if_missing(self, now: datetime) -> List[MenthorqLevelRecord]:
        """
        Compute and store every daily-close record of the last
        ``DAILY_CLOSE_BACKFILL_DAYS`` ended UTC days (yesterday and the six
        days before it) that is not stored as COMPUTED yet, oldest first, so
        a collector that was down for a few days fills the gap. A stored
        not-computed row (no levels, e.g. the snapshots of that day were
        synced late) is retried only when newer input data has arrived
        (``_daily_close_retry_is_due``: one MAX query, no chain load), so a
        day that cannot be computed is not reloaded every hour; a computed
        result replaces it (``save_menthorq_level_snapshot``). Today is
        never computed (it has not ended). Each record uses only data at or
        before its own 23:59 UTC (no lookahead). A day that fails is logged
        and the other days are still computed.

        Returns:
            The records computed by this call, oldest first (empty when none
            was missing or due); a not-computed retry that left the stored
            row unchanged has ``record_id`` None.

        Raises:
            Exception: the error of the latest ended day (yesterday) when its
                step raised, after every other day was tried, so the caller
                can report it; a failure of an older day is only logged.
        """
        today = now.astimezone(timezone.utc).date()
        settings = self.settings
        stored = []
        failed_days = []
        latest_day_error: Optional[Exception] = None
        for days_back in range(DAILY_CLOSE_BACKFILL_DAYS, 0, -1):
            utc_date = today - timedelta(days=days_back)
            as_of = daily_close_as_of(utc_date)
            try:
                stored_row = self.repository.get_menthorq_level_snapshot(
                    settings.currency, MODE_DAILY_CLOSE, settings.expiry_universe,
                    settings.gamma_underlying, as_of)
                if stored_row is not None and stored_row.has_levels:
                    continue
                newest_capture = self.repository.get_latest_option_chain_capture_at_or_before(
                    settings.currency, as_of)
                if stored_row is not None and not self._daily_close_retry_is_due(
                        utc_date, stored_row, newest_capture):
                    logger.info("MenthorQ daily close of %s stays not computed: no snapshot newer than "
                                "the last attempt (newest at or before 23:59 UTC: %s)",
                                utc_date.isoformat(),
                                newest_capture.isoformat() if newest_capture else "none")
                    continue
                self._daily_close_attempt_captures[utc_date] = newest_capture
                stored.append(self.store(self.compute_daily_close_record(utc_date)))
            except Exception as error:
                logger.exception("MenthorQ daily-close record of %s failed", utc_date.isoformat())
                failed_days.append(utc_date.isoformat())
                if days_back == 1:
                    latest_day_error = error
        if stored or failed_days:
            logger.info("MenthorQ daily-close backfill: %d computed (%s), %d failed%s",
                        len(stored), ", ".join(record.as_of_utc.date().isoformat() for record in stored),
                        len(failed_days), f" ({', '.join(failed_days)})" if failed_days else "")
        if latest_day_error is not None:
            raise latest_day_error
        return stored

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

    def event_baseline_records(self, latest: MenthorqLevelRecord) -> List[MenthorqLevelRecord]:
        """
        The stored computed records the 24 h event baseline needs
        (``event_baseline``): same currency, mode and settings, strictly
        before ``latest``, back to the oldest instant the 24 h comparison
        accepts (24 h plus its tolerance), oldest first.
        """
        earliest = (latest.as_of_utc - COMPARISON_OFFSET[COMPARISON_ONE_DAY]
                    - COMPARISON_TOLERANCE[COMPARISON_ONE_DAY])
        return self.repository.get_menthorq_level_snapshots_between(
            latest.currency, latest.mode, latest.expiry_universe, latest.gamma_underlying,
            earliest, latest.as_of_utc)

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
                latest, reference if reference.has_levels else None, label))
        return comparisons

    def latest_record(self, mode: str, now: datetime) -> Optional[MenthorqLevelRecord]:
        """Newest computed stored record of ``mode`` at or before ``now``."""
        settings = self.settings
        return self.repository.get_menthorq_level_snapshot_at_or_before(
            settings.currency, mode, settings.expiry_universe, settings.gamma_underlying, now)
