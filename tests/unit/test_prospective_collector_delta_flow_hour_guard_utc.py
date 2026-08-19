"""
Regression test for ProspectiveCollector._resolve_delta_flow_target_hour's
guard clock basis (Wave H fresh-audit finding, Task Wave-H-C).

The guard (``if hour >= current_hour: return hour - 1h``) exists to make
sure a TRUE hourly aggregate is never persisted for an hour that hasn't
fully elapsed. ``hour`` itself is UTC-valued naive (``collect_hour``'s
default, fixed in an earlier Wave G task -- see
test_prospective_collector_default_hour_utc.py). This guard's own
``current_hour`` used to be computed from naive-local ``datetime.now()``,
which shared a basis with ``hour`` only by accident on hosts whose OS
clock happens to already be UTC (the VPS). On any other host, the two
sides compare apples to oranges.

Reproduced live on this dev machine (W. Europe Standard/Daylight Time,
UTC+2 in August): naive-local ``datetime.now()`` reads ~2 hours ahead of
UTC ``datetime.now(timezone.utc)`` right now. Concretely, with the
pre-fix guard, a still in-progress UTC hour (e.g. 14:00 UTC, real UTC
time 14:23) compared against local-now current_hour (16:00, i.e. local
16:23 floored) evaluates ``14:00 >= 16:00`` as False -- the guard fails
to fire, and the in-progress hour (14:00) is returned UNCHANGED instead
of resolving to the just-closed hour (13:00). That still-accumulating
hour then gets persisted to ``flow_delta_hourly`` as if it were a
complete aggregate, silently under-reporting delta flow for the rest of
that hour's trades.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from coding.service.data_collection.prospective_collector import ProspectiveCollector


def test_guard_resolves_in_progress_utc_hour_to_just_closed_on_non_utc_host():
    """
    Freeze the module's clock to a fixed UTC instant on a simulated
    non-UTC host (Europe/Berlin summer, UTC+2) and assert the guard still
    correctly identifies the UTC in-progress hour as not-yet-elapsed --
    proving the fix by construction, not by trusting the file diff.
    """
    fixed_utc = datetime(2026, 8, 8, 14, 23, 0, tzinfo=timezone.utc)
    local_offset = timedelta(hours=2)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_utc.astimezone(tz)
            # Naive datetime.now() (no tz) -- simulates a host whose local
            # wall clock reads 2 hours ahead of UTC.
            return fixed_utc.replace(tzinfo=None) + local_offset

        @classmethod
        def utcnow(cls):
            return fixed_utc.replace(tzinfo=None)

    # The exact value collect_hour's default computes: UTC-valued naive,
    # floored to the hour -- 14:00, still in progress (real UTC time is
    # 14:23).
    in_progress_utc_hour = datetime(2026, 8, 8, 14, 0, 0)

    with patch(
        "coding.service.data_collection.prospective_collector.datetime", _FrozenDateTime
    ):
        resolved = ProspectiveCollector._resolve_delta_flow_target_hour(in_progress_utc_hour)

    assert resolved == datetime(2026, 8, 8, 13, 0, 0), (
        f"expected the just-closed hour 13:00 UTC, got {resolved} -- the "
        "guard is comparing hour (UTC-valued) against a naive-local "
        "current_hour again, so it failed to recognize 14:00 UTC as still "
        "in progress on this simulated UTC+2 host"
    )
    # The bug's exact failure mode: the in-progress hour must never be
    # returned unchanged (that's what "guard failed to fire" looks like).
    assert resolved != in_progress_utc_hour


def test_guard_still_resolves_correctly_when_local_clock_matches_utc():
    """Sanity check: on a UTC host (local offset zero, e.g. the
    production VPS), the guard's behavior is unchanged by this fix."""
    fixed_utc = datetime(2026, 8, 8, 14, 23, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_utc.astimezone(tz)
            return fixed_utc.replace(tzinfo=None)

    in_progress_utc_hour = datetime(2026, 8, 8, 14, 0, 0)

    with patch(
        "coding.service.data_collection.prospective_collector.datetime", _FrozenDateTime
    ):
        resolved = ProspectiveCollector._resolve_delta_flow_target_hour(in_progress_utc_hour)

    assert resolved == datetime(2026, 8, 8, 13, 0, 0)
