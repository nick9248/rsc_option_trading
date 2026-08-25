"""
Tests for DatabaseRepository.save_daily_oi_snapshot / get_previous_oi_snapshot's
paired UTC-date-default invariant (Task C8 fix round 2, Important).

These two methods are called ~8 lines apart in the same
OnChainAnalysisService per-expiry loop iteration (_calculate_oi_changes_
and_iv_percentile) -- one writes "today's" OI/IV snapshot, the other
immediately reads "yesterday's" for the day-over-day OI-CHANGES comparison.

Fix round 1 (Task C8, Important #2) fixed save_daily_oi_snapshot's default
to datetime.now(timezone.utc).date() but missed that get_previous_oi_
snapshot's default was still naive-local datetime.now() -- desyncing the
pair for a ~2-hour daily window on this UTC+2 machine (any run during UTC
22:00-24:00 would write a row dated "today UTC" via the now-correct writer,
then the still-local reader's "yesterday" would resolve to that SAME UTC
date, comparing a snapshot against itself and reporting ~zero OI change for
every strike). This test pins BOTH defaults to the SAME mocked clock so a
future change to only one of these two paired methods is caught
immediately by this test, not by another review round.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _frozen_datetime(fixed_now, tz_calls):
    """A real ``datetime`` subclass whose ``now()`` returns ``fixed_now``
    and records the ``tz`` it was called with -- a bare ``MagicMock`` in
    place of the ``datetime`` class breaks ``isinstance(x, datetime)``
    checks elsewhere in repository.py.

    Recording ``tz`` per call (not just checking the final computed date)
    matters here specifically: since every call routes through this same
    frozen ``now()`` regardless of its ``tz`` argument, a test that only
    compared the two methods' RESULTING dates against each other could not
    tell a real ``datetime.now(timezone.utc)`` call apart from a
    regression back to a bare ``datetime.now()`` -- both would still
    return ``fixed_now`` and the two methods would still agree with each
    other (both wrong in the same way). Asserting on ``tz_calls`` directly
    is what actually re-verifies each call site is UTC-explicit.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            tz_calls.append(tz)
            return fixed_now

    return _Frozen


def _capture_save_snapshot_date(repo):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_daily_oi_snapshot(
            currency="BTC",
            expiration="31JUL26",
            instruments=[{
                "strike": 65000.0, "option_type": "C",
                "open_interest": 10.0, "mark_iv": 32.0,
            }],
            underlying_price=65000.0,
        )

    args, _ = mock_cursor.executemany.call_args
    _, rows = args
    return rows[0][0]  # snapshot_date is the first column


def _capture_get_previous_target_date(repo):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.get_previous_oi_snapshot(currency="BTC", expiration="31JUL26")

    args, _ = mock_cursor.execute.call_args
    _, params = args
    return params[-1]  # target_date is the last bound parameter


class TestWriteReadDateSync:
    def test_reader_yesterday_matches_writer_today_minus_one_day(self):
        """The invariant the round-2 bug violated: what the writer labels
        'today' must be exactly one day ahead of what the reader's default
        resolves as 'yesterday', when both derive from the SAME clock
        reading. Frozen at UTC 23:30 -- local time on this (UTC+2) machine
        would already be 01:30 the NEXT day, exactly the desync window the
        review identified."""
        fake_utc_now = datetime(2026, 7, 31, 23, 30, 0, tzinfo=timezone.utc)
        tz_calls = []

        with patch(
            "coding.core.database.repository.datetime",
            _frozen_datetime(fake_utc_now, tz_calls),
        ):
            write_date = _capture_save_snapshot_date(_make_repo())
            read_target_date = _capture_get_previous_target_date(_make_repo())

        # Both call sites must explicitly request UTC -- see
        # _frozen_datetime's docstring for why this, not just the
        # resulting dates, is what actually catches a one-sided
        # regression.
        assert tz_calls == [timezone.utc, timezone.utc]
        assert write_date == fake_utc_now.date()
        assert read_target_date == fake_utc_now.date() - timedelta(days=1)
        assert write_date == read_target_date + timedelta(days=1)

    def test_both_defaults_resolve_from_a_utc_call_not_local(self):
        """Regression guard for the exact class of bug this round fixed:
        if either method's default reverts to naive-local datetime.now(),
        this test (unlike one that only checks -1-day arithmetic given an
        already-resolved date) will catch it, because the frozen clock's
        OWN date is what both assertions are pinned to."""
        # UTC midnight -- picked so a naive-local read on ANY positive-UTC-
        # offset machine (this one is UTC+2) would resolve to the NEXT
        # calendar day, diverging from this assertion if the fix regressed.
        fake_utc_now = datetime(2026, 8, 1, 0, 5, 0, tzinfo=timezone.utc)
        tz_calls = []

        with patch(
            "coding.core.database.repository.datetime",
            _frozen_datetime(fake_utc_now, tz_calls),
        ):
            write_date = _capture_save_snapshot_date(_make_repo())
            read_target_date = _capture_get_previous_target_date(_make_repo())

        assert tz_calls == [timezone.utc, timezone.utc]
        assert write_date == datetime(2026, 8, 1).date()
        assert read_target_date == datetime(2026, 7, 31).date()
