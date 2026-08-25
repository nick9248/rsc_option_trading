"""
Unit tests for DatabaseRepository.save_daily_oi_snapshot's default
snapshot_date resolution (Task C8 fix round, Important #2).

OnChainAnalysisService._calculate_oi_changes_and_iv_percentile calls
save_daily_oi_snapshot with no explicit snapshot_date, in the SAME analysis
run that now also calls _calculate_fixed_strike_vol_matrix (Task C8) --
Task C8's new exact-date lookup (DatabaseRepository.get_chain_iv_at)
depends on this write's date label being correct. The old
``datetime.now().date()`` default was this (non-UTC) machine's LOCAL
date -- on a UTC+2 machine, any run between 00:00-02:00 local time would
mislabel a row by one calendar day (spec section 7(c)'s edge cases
explicitly forbid this failure mode). Verifies the fix: the default now
resolves from ``datetime.now(timezone.utc)``.

Mocked cursor only -- no live database (matches test_repository_save_
snapshot.py's established pattern).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _frozen_datetime(fixed_now, tz_calls):
    """A real ``datetime`` subclass whose ``now()`` returns ``fixed_now``
    and records the ``tz`` it was called with -- a MagicMock in place of
    the ``datetime`` class breaks ``isinstance(x, datetime)`` (repository.
    py's own ``isinstance(snap_date, datetime)`` check), so this uses a
    genuine subclass instead, matching the standard pattern for freezing
    ``datetime.now()`` without a third-party freezegun dependency."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            tz_calls.append(tz)
            return fixed_now

    return _Frozen


def _capture_executemany_rows(repo, snapshot_date=None):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_daily_oi_snapshot(
            currency="BTC",
            expiration="31JUL26",
            instruments=[{"strike": 65000.0, "option_type": "C", "open_interest": 10.0, "mark_iv": 32.0}],
            underlying_price=65000.0,
            snapshot_date=snapshot_date,
        )

    args, _ = mock_cursor.executemany.call_args
    _, rows = args
    return rows


class TestDefaultSnapshotDateIsUtc:
    def test_default_resolves_from_utc_clock_not_local(self):
        """Independent review: patches datetime.now itself so a
        regression back to naive-local datetime.now() would be caught --
        not just that a date is produced, but that it's the UTC one."""
        fake_utc_now = datetime(2026, 8, 1, 1, 30, 0, tzinfo=timezone.utc)
        tz_calls = []
        repo = _make_repo()

        with patch(
            "coding.core.database.repository.datetime",
            _frozen_datetime(fake_utc_now, tz_calls),
        ):
            rows = _capture_executemany_rows(repo, snapshot_date=None)

        assert tz_calls == [timezone.utc]
        snapshot_date_used = rows[0][0]
        assert snapshot_date_used == fake_utc_now.date()

    def test_utc_and_local_dates_diverge_across_the_day_boundary(self):
        """Concrete regression case: at 01:00 UTC, Europe/Berlin local
        time (UTC+2 in summer) is already 03:00 the SAME day -- pick a
        clock reading right after UTC midnight where the naive-local bug
        would have produced YESTERDAY's date this campaign has hit before.
        This machine's actual local offset doesn't matter for the
        assertion -- what matters is that the row is labelled with
        fake_utc_now's OWN date, proving the resolution path is UTC, not
        whatever this test machine's local zone says "today" is."""
        fake_utc_now = datetime(2026, 8, 1, 0, 30, 0, tzinfo=timezone.utc)
        tz_calls = []
        repo = _make_repo()

        with patch(
            "coding.core.database.repository.datetime",
            _frozen_datetime(fake_utc_now, tz_calls),
        ):
            rows = _capture_executemany_rows(repo, snapshot_date=None)

        assert rows[0][0] == fake_utc_now.date() == datetime(2026, 8, 1).date()

    def test_explicit_snapshot_date_is_not_overridden_by_the_clock(self):
        """An explicit snapshot_date (e.g. a backfill script) must win
        over the default -- the clock is only consulted when None."""
        repo = _make_repo()
        explicit_date = datetime(2026, 7, 20).date()

        rows = _capture_executemany_rows(repo, snapshot_date=explicit_date)

        assert rows[0][0] == explicit_date
