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

The hourly run also stores missing LIVE records of the last 7 days
(``run_live_backfill``: point-in-time replay of the live schedule, marked
``BACKFILLED_LIVE_CAVEAT``, never alerted on), so the comparisons have a
reference after a monitor start or an outage.

Reuses ``MenthorqInputLoader`` and ``replicate_menthorq`` (no chart import).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from coding.core.analytics.menthorq_monitor.input_age import (
    DAILY_CLOSE_MAXIMUM_INPUT_AGE,
    stale_input_reasons,
)
from coding.core.analytics.menthorq_monitor.level_record import (
    BACKFILLED_LIVE_CAVEAT,
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
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader, MissingIndexPriceError
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings

logger = logging.getLogger(__name__)

# The hourly run fills missing daily-close records of this many ended UTC days.
DAILY_CLOSE_BACKFILL_DAYS = 7
# The hourly run fills missing live records of this many days (run_live_backfill),
# computing at most this many per run so the run stays short.
LIVE_BACKFILL_DAYS = 7
LIVE_BACKFILL_MAXIMUM_RECORDS_PER_RUN = 48   # instants whose inputs were loaded
BACKFILL_FAILED_INSTANTS_LOGGED = 5


class BackfillSkipReason(Enum):
    """Why a live-backfill instant was not stored."""

    NO_SNAPSHOT = "no snapshot"
    STALE_INPUT = "stale input"
    NO_INDEX_PRICE = "no index price"
    NO_LEVELS = "no levels"
    FAILED = "failed"


# Not retried in this process: the data before the instant will not change.
PERMANENT_SKIP_REASONS = frozenset({BackfillSkipReason.STALE_INPUT, BackfillSkipReason.NO_LEVELS})


@dataclass(frozen=True)
class BackfillOutcome:
    """
    Result of ``compute_backfill_record``: the record to store, or the skip
    reason. ``attempted`` is True when the inputs were loaded (the costly
    step that the per-run cap counts).
    """

    record: Optional[MenthorqLevelRecord] = None
    skip_reason: Optional[BackfillSkipReason] = None
    attempted: bool = True


@dataclass(frozen=True)
class _InputComputation:
    """
    Result of ``MenthorqMonitorService._compute_from_inputs``: exactly one of
    ``missing_input_error`` (an input has no row at or before its instant),
    ``stale_reasons`` (an input is too old) or ``record`` (computed, with or
    without levels) is set. ``inputs`` are the loaded inputs, when loaded.
    """

    record: Optional[MenthorqLevelRecord] = None
    missing_input_error: Optional[LookupError] = None
    stale_reasons: Tuple[str, ...] = ()
    inputs: Optional[Dict[str, Any]] = None


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
        # Live-backfill instants judged unfillable for a permanent reason in this process.
        self._backfill_unfillable_instants: Set[datetime] = set()

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
        computation = self._compute_from_inputs(mode, as_of, open_interest_as_of, maximum_input_age)
        if computation.missing_input_error is not None:
            error = computation.missing_input_error
            logger.warning("MenthorQ %s record at %s not computed: %s", mode, as_of.isoformat(), error)
            return build_not_computed_record(
                settings.currency, mode, as_of, self.clock(), settings.expiry_universe,
                settings.gamma_underlying, f"inputs not available: {error}")
        if computation.stale_reasons:
            reason = "; ".join(computation.stale_reasons)
            logger.warning("MenthorQ %s record at %s not computed: %s", mode, as_of.isoformat(), reason)
            inputs = computation.inputs or {}
            # The snapshots used are known: keep their capture times, so a retry after a
            # restart sees that no newer snapshot exists (_daily_close_retry_is_due).
            return build_not_computed_record(
                settings.currency, mode, as_of, self.clock(), settings.expiry_universe,
                settings.gamma_underlying, reason,
                snapshot_captured_at_utc=inputs.get("snapshot_captured_at_utc"),
                open_interest_captured_at_utc=inputs.get("open_interest_captured_at_utc"))
        if computation.record is None:
            raise ValueError("an input computation without an error must carry its record")
        return computation.record

    def _compute_from_inputs(
        self, mode: str, as_of: datetime, open_interest_as_of: Optional[datetime],
        maximum_input_age: Optional[timedelta],
    ) -> "_InputComputation":
        """
        The one shared path of ``compute_record`` and
        ``compute_backfill_record``: load the point-in-time inputs, check
        their age (only with ``maximum_input_age``), replicate the levels and
        build the record (not stored). Logs nothing: each caller decides what
        a missing or stale input means for it.
        """
        settings = self.settings
        try:
            inputs = self.input_loader.load_inputs(settings.currency, as_of, open_interest_as_of)
        except LookupError as error:
            return _InputComputation(missing_input_error=error)
        if maximum_input_age is not None:
            reasons = stale_input_reasons(inputs, as_of, open_interest_as_of or as_of, maximum_input_age)
            if reasons:
                return _InputComputation(stale_reasons=tuple(reasons), inputs=inputs)
        replication = replicate_menthorq(
            inputs["chain_rows"], inputs["index_price"], as_of,
            settings.expiry_universe, settings.gamma_underlying, currency=settings.currency,
        )
        record = build_level_record(replication, inputs, settings.currency, mode, as_of, self.clock())
        return _InputComputation(record=record, inputs=inputs)

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
        """
        Compute and store the live record of ``now``. When the save kept an
        existing row of the same run (``record_id`` None, e.g. a computed row
        and a not-computed recompute after a restart), the STORED row is
        returned, not the fresh copy, so the alerts judge what is stored
        (a not-computed copy would raise a false data_stale alert).
        """
        record = self.store(self.compute_live_record(now))
        if record.record_id is not None:
            return record
        stored = self.repository.get_menthorq_level_snapshot(
            record.currency, record.mode, record.expiry_universe, record.gamma_underlying,
            record.as_of_utc)
        if stored is None:
            logger.warning("MenthorQ live record as of %s was not saved and no stored row was found; "
                           "the fresh record is used", record.as_of_utc.isoformat())
            return record
        return stored

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
                stored.append(self.store(self.compute_daily_close_record(utc_date)))
                # Only after compute and store succeeded: a failed attempt must be
                # retried next run, not skipped as "already tried with this snapshot".
                self._daily_close_attempt_captures[utc_date] = newest_capture
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

    def live_backfill_instants(self, now: datetime) -> List[datetime]:
        """
        The live-schedule instants (minute ``settings.live_run_minute`` of
        every UTC hour) of the last ``LIVE_BACKFILL_DAYS`` days, strictly
        before the current live run's instant (``live_as_of(now)``), oldest
        first.
        """
        current = live_as_of(now)
        newest = current.replace(minute=self.settings.live_run_minute)
        if newest >= current:
            newest -= timedelta(hours=1)
        earliest = current - timedelta(days=LIVE_BACKFILL_DAYS)
        instants = []
        instant = newest
        while instant >= earliest:
            instants.append(instant)
            instant -= timedelta(hours=1)
        return instants[::-1]

    def compute_backfill_record(self, as_of: datetime) -> BackfillOutcome:
        """
        The live record the hourly run would have stored at ``as_of``, or
        why none can be stored. Steps, cheapest first:

        1. One MAX query for the newest snapshot capture at or before
           ``as_of``: none -> ``NO_SNAPSHOT``; older than
           ``settings.stale_snapshot_limit`` -> ``STALE_INPUT`` (no chain
           load; ``attempted`` False).
        2. The live loader path (snapshot, its open interest and the index
           price at or before ``as_of``, nothing after it): no index price
           -> ``NO_INDEX_PRICE``, another missing input -> ``NO_SNAPSHOT``,
           an input older than the limit -> ``STALE_INPUT``.
        3. The levels: none computed -> ``NO_LEVELS`` (a replay never
           stores a row without levels). Otherwise the record, with
           ``BACKFILLED_LIVE_CAVEAT`` appended after its own caveats.

        Raises:
            Exception: any other error (the caller counts it as ``FAILED``).
        """
        settings = self.settings
        newest_capture = self.repository.get_latest_option_chain_capture_at_or_before(
            settings.currency, as_of)
        if newest_capture is None:
            return BackfillOutcome(skip_reason=BackfillSkipReason.NO_SNAPSHOT, attempted=False)
        if as_of - newest_capture > settings.stale_snapshot_limit:
            return BackfillOutcome(skip_reason=BackfillSkipReason.STALE_INPUT, attempted=False)
        computation = self._compute_from_inputs(MODE_LIVE, as_of, None, settings.stale_snapshot_limit)
        if isinstance(computation.missing_input_error, MissingIndexPriceError):
            return BackfillOutcome(skip_reason=BackfillSkipReason.NO_INDEX_PRICE)
        if computation.missing_input_error is not None:
            return BackfillOutcome(skip_reason=BackfillSkipReason.NO_SNAPSHOT)
        if computation.stale_reasons:
            return BackfillOutcome(skip_reason=BackfillSkipReason.STALE_INPUT)
        record = computation.record
        if record is None or not record.has_levels:
            return BackfillOutcome(skip_reason=BackfillSkipReason.NO_LEVELS)
        record.not_computed_reasons.append(BACKFILLED_LIVE_CAVEAT)
        return BackfillOutcome(record=record)

    def run_live_backfill(self, now: datetime) -> List[MenthorqLevelRecord]:
        """
        Store the MISSING live records of the last ``LIVE_BACKFILL_DAYS``
        days (``live_backfill_instants``), oldest first, so the "7 days ago"
        and "24 h ago" comparisons have a reference after a monitor start or
        an outage. An hour counts as stored when a COMPUTED live row (with
        levels) of the same key has its ``as_of_utc`` in that UTC hour (one
        query, ``get_computed_menthorq_level_run_instants_between``); the hour
        of a not-computed row is replayed, and the save rule replaces that
        row when the replay computes levels. Every instant
        before the earliest snapshot capture (one MIN query) is skipped
        without further work; each other one goes through
        ``compute_backfill_record``. Only instants whose inputs were actually
        loaded (``attempted``) count toward
        ``LIVE_BACKFILL_MAXIMUM_RECORDS_PER_RUN``, so cheap skips never block
        newer fillable hours; a long gap fills over a few hourly runs.
        Instants unfillable for a permanent reason (``PERMANENT_SKIP_REASONS``,
        or no snapshot found by the cheap check) are remembered in this
        process and not queried or counted again; ``NO_INDEX_PRICE``, a late
        missing snapshot and ``FAILED`` are retried next run. Stored with
        mode "live" and ``BACKFILLED_LIVE_CAVEAT``; the save rule never
        replaces a computed row. No alert is evaluated and nothing is sent. One INFO
        line per run gives the count of each outcome; failed instants are
        listed at WARNING (at most ``BACKFILL_FAILED_INSTANTS_LOGGED``).

        Returns:
            The records stored by this call, oldest first.
        """
        settings = self.settings
        instants = self.live_backfill_instants(now)
        if not instants:
            return []
        stored_instants = self.repository.get_computed_menthorq_level_run_instants_between(
            settings.currency, MODE_LIVE, settings.expiry_universe, settings.gamma_underlying,
            instants[0].replace(minute=0), instants[-1].replace(minute=0) + timedelta(hours=1))
        stored_hours = {instant.replace(minute=0, second=0, microsecond=0) for instant in stored_instants}
        missing = [instant for instant in instants
                   if instant.replace(minute=0) not in stored_hours
                   and instant not in self._backfill_unfillable_instants]
        if not missing:
            return []
        earliest_capture = self.repository.get_earliest_option_chain_capture(settings.currency)
        stored: List[MenthorqLevelRecord] = []
        counts: Dict[BackfillSkipReason, int] = {reason: 0 for reason in BackfillSkipReason}
        failed: List[datetime] = []
        errors: List[Exception] = []

        def count_failure(instant: datetime, error: Exception) -> None:
            logger.debug("MenthorQ live backfill at %s failed", instant.isoformat(), exc_info=error)
            counts[BackfillSkipReason.FAILED] += 1
            failed.append(instant)
            errors.append(error)

        attempted = 0
        for instant in missing:
            if earliest_capture is None or instant < earliest_capture:
                counts[BackfillSkipReason.NO_SNAPSHOT] += 1
                self._backfill_unfillable_instants.add(instant)
                continue
            if attempted >= LIVE_BACKFILL_MAXIMUM_RECORDS_PER_RUN:
                break
            try:
                outcome = self.compute_backfill_record(instant)
            except Exception as error:
                attempted += 1
                count_failure(instant, error)
                continue
            if outcome.attempted:
                attempted += 1
            if outcome.record is not None:
                try:
                    stored.append(self.store(outcome.record))
                except Exception as error:
                    count_failure(instant, error)
                continue
            reason = outcome.skip_reason
            if reason is None:
                raise ValueError("a backfill outcome without a record must name its skip reason")
            counts[reason] += 1
            if reason in PERMANENT_SKIP_REASONS or (reason is BackfillSkipReason.NO_SNAPSHOT
                                                    and not outcome.attempted):
                self._backfill_unfillable_instants.add(instant)
        if stored or any(counts.values()):
            logger.info("MenthorQ live backfill: %d missing hours: %d stored, %d no snapshot, %d stale input "
                        "(limit %d min), %d no index price, %d no levels, %d failed",
                        len(missing), len(stored), counts[BackfillSkipReason.NO_SNAPSHOT],
                        counts[BackfillSkipReason.STALE_INPUT], settings.stale_snapshot_minutes,
                        counts[BackfillSkipReason.NO_INDEX_PRICE], counts[BackfillSkipReason.NO_LEVELS],
                        counts[BackfillSkipReason.FAILED])
        if failed:
            listed = ", ".join(instant.isoformat() for instant in failed[:BACKFILL_FAILED_INSTANTS_LOGGED])
            more = len(failed) - BACKFILL_FAILED_INSTANTS_LOGGED
            logger.warning("MenthorQ live backfill: %d instant(s) failed (%s%s); first error: %s: %s",
                           len(failed), listed, f" and {more} more" if more > 0 else "",
                           type(errors[0]).__name__, errors[0])
        return stored

    def is_usable_reference(self, record: MenthorqLevelRecord) -> bool:
        """
        Whether a stored computed record may serve as a comparison reference
        or event-baseline record. A record whose own snapshot was older than
        ``settings.stale_snapshot_limit`` before its ``as_of_utc`` (a live
        run during a collector outage) never described a real hourly state,
        so it is not used (it is still stored: the data-stale alert and
        /status need it). Exactly the limit is still usable; a record
        without a snapshot time is kept, as before.
        """
        if record.snapshot_captured_at_utc is None:
            return True
        return record.as_of_utc - record.snapshot_captured_at_utc <= self.settings.stale_snapshot_limit

    def find_reference(
        self, latest: MenthorqLevelRecord, label: str
    ) -> Optional[MenthorqLevelRecord]:
        """
        The stored record ``label`` ("previous", "24h", "7d") refers to:
        the newest computed record of the same currency, mode and settings
        at or before ``latest.as_of_utc`` minus the offset (strictly before
        for "previous"), within ``COMPARISON_TOLERANCE``, skipping records
        built from an outdated snapshot (``is_usable_reference``).
        """
        target = latest.as_of_utc - COMPARISON_OFFSET[label]
        earliest = target - COMPARISON_TOLERANCE[label]
        instant, strictly_before = target, label == COMPARISON_PREVIOUS
        while True:
            candidate = self.repository.get_menthorq_level_snapshot_at_or_before(
                latest.currency, latest.mode, latest.expiry_universe, latest.gamma_underlying,
                instant, earliest=earliest, strictly_before=strictly_before,
            )
            if candidate is None or self.is_usable_reference(candidate):
                return candidate
            logger.info("MenthorQ %s reference at %s skipped: built from a snapshot captured %s",
                        label, candidate.as_of_utc.isoformat(),
                        candidate.snapshot_captured_at_utc.isoformat()
                        if candidate.snapshot_captured_at_utc else "never")
            # as_of_utc falls strictly with each step and stops at ``earliest``.
            instant, strictly_before = candidate.as_of_utc, True

    def event_baseline_records(self, latest: MenthorqLevelRecord) -> List[MenthorqLevelRecord]:
        """
        The stored computed records the 24 h event baseline needs
        (``event_baseline``): same currency, mode and settings, strictly
        before ``latest``, back to the oldest instant the 24 h comparison
        accepts (24 h plus its tolerance), oldest first, without records
        built from an outdated snapshot (``is_usable_reference``).
        """
        earliest = (latest.as_of_utc - COMPARISON_OFFSET[COMPARISON_ONE_DAY]
                    - COMPARISON_TOLERANCE[COMPARISON_ONE_DAY])
        records = self.repository.get_menthorq_level_snapshots_between(
            latest.currency, latest.mode, latest.expiry_universe, latest.gamma_underlying,
            earliest, latest.as_of_utc)
        return [record for record in records if self.is_usable_reference(record)]

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
        monitor table. A replayed record built from an outdated snapshot
        fails ``is_usable_reference`` (the filter ``find_reference``
        applies) and gives no reference, as the hourly job would not use it.
        """
        comparisons = []
        for label, offset in ((COMPARISON_PREVIOUS, timedelta(hours=1)),
                              (COMPARISON_ONE_DAY, COMPARISON_OFFSET[COMPARISON_ONE_DAY]),
                              (COMPARISON_SEVEN_DAYS, COMPARISON_OFFSET[COMPARISON_SEVEN_DAYS])):
            replayed = self.compute_record(latest.mode, latest.as_of_utc - offset)
            reference: Optional[MenthorqLevelRecord] = replayed if replayed.has_levels else None
            if reference is not None and not self.is_usable_reference(reference):
                logger.info("MenthorQ replayed %s reference at %s skipped: built from a snapshot captured %s",
                            label, reference.as_of_utc.isoformat(),
                            reference.snapshot_captured_at_utc.isoformat()
                            if reference.snapshot_captured_at_utc else "never")
                reference = None
            comparisons.append(compare_records(latest, reference, label))
        return comparisons

    def latest_record(self, mode: str, now: datetime) -> Optional[MenthorqLevelRecord]:
        """Newest computed stored record of ``mode`` at or before ``now``."""
        settings = self.settings
        return self.repository.get_menthorq_level_snapshot_at_or_before(
            settings.currency, mode, settings.expiry_universe, settings.gamma_underlying, now)
