"""
Tests for CollectionDaemon's startup gap-check/backfill logic (Task
Wave-J-F Fix 3).

Two bugs, reproduced and fixed here:

1. ``_get_last_collection_time`` queries ``TO_TIMESTAMP(trade_timestamp /
   1000.0)``, which Postgres types as ``timestamptz`` -- psycopg2 returns
   those as TIMEZONE-AWARE datetimes. The old ``_handle_startup`` compared
   this against naive ``datetime.now()``, which raises ``TypeError: can't
   subtract offset-naive and offset-aware datetimes``, silently swallowed
   by the method's own broad ``except Exception`` -- so the gap-check/
   backfill-on-startup logic never actually ran, on any host, ever.

2. Even with the types aligned, ``ProspectiveCollector.collect_hour``'s
   ``hour`` convention is naive-UTC-VALUED (see prospective_collector.py's
   own default-hour fix) -- the old code fed it naive-LOCAL
   ``datetime.now()`` values, which would mislabel every backfilled hour by
   the host's UTC offset on any non-UTC host.

Uses ``CollectionDaemon.__new__(CollectionDaemon)`` (matching
test_collection_daemon_shutdown.py's pattern) to construct an instance
without running ``__init__`` (which would construct a real
``ProspectiveCollector``/DB connection).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.service.data_collection.collection_daemon import CollectionDaemon


def _make_daemon():
    daemon = CollectionDaemon.__new__(CollectionDaemon)
    daemon.currencies = ["BTC", "ETH"]
    daemon.collector = MagicMock()
    return daemon


class TestHandleStartupDoesNotRaiseOnAwareLastCollection:
    def test_small_gap_triggers_backfill_without_typeerror(self):
        """
        Reproduces the exact live-verified bug: _get_last_collection_time
        returns a timezone-aware datetime (e.g. +02:00 offset, as observed
        against the real local DB). The old naive datetime.now() comparison
        raised TypeError, caught silently -- backfill never ran.
        """
        daemon = _make_daemon()
        # Use a genuinely non-UTC-offset aware datetime, matching what was
        # observed live against psycopg2 (e.g. +02:00 local session tz).
        last_collection = (datetime.now(timezone.utc) - timedelta(minutes=30)).astimezone(
            timezone(timedelta(hours=2))
        )

        daemon._get_last_collection_time = MagicMock(return_value=last_collection)
        daemon._backfill_gap = MagicMock()
        daemon._log_gap = MagicMock()

        # Must not raise, and must actually reach the backfill branch (not
        # the except-Exception fallback the old TypeError bug always hit).
        daemon._handle_startup()

        daemon._backfill_gap.assert_called_once()
        daemon._log_gap.assert_not_called()

    def test_large_gap_calls_log_gap_without_typeerror(self):
        daemon = _make_daemon()
        last_collection = (datetime.now(timezone.utc) - timedelta(hours=5)).astimezone(
            timezone(timedelta(hours=2))
        )

        daemon._get_last_collection_time = MagicMock(return_value=last_collection)
        daemon._backfill_gap = MagicMock()
        daemon._log_gap = MagicMock()

        daemon._handle_startup()

        daemon._log_gap.assert_called_once()
        daemon._backfill_gap.assert_not_called()
        args, _ = daemon._log_gap.call_args
        assert abs(args[2] - 5.0) < 0.01  # gap_hours

    def test_no_previous_collection_does_not_call_backfill_or_log(self):
        daemon = _make_daemon()
        daemon._get_last_collection_time = MagicMock(return_value=None)
        daemon._backfill_gap = MagicMock()
        daemon._log_gap = MagicMock()

        daemon._handle_startup()

        daemon._backfill_gap.assert_not_called()
        daemon._log_gap.assert_not_called()


class TestBackfillGapUsesUtcValuedNaiveHours:
    def test_collect_hour_receives_naive_utc_valued_hours_not_local(self):
        """
        start_time/end_time are timezone-aware, with a non-UTC offset (as
        observed live). collect_hour must be called with naive datetimes
        whose wall-clock value is the UTC-converted hour, not the raw
        (offset-still-applied) local wall-clock hour.
        """
        daemon = _make_daemon()
        tz_plus2 = timezone(timedelta(hours=2))
        # 2026-08-22 23:17 +02:00 == 2026-08-22 21:17 UTC.
        start_time = datetime(2026, 8, 22, 23, 17, 0, tzinfo=tz_plus2)
        end_time = datetime(2026, 8, 22, 23, 47, 0, tzinfo=tz_plus2)

        daemon.collector.collect_hour.return_value = {
            "trades_collected": 0, "instruments_collected": 0,
        }

        daemon._backfill_gap(start_time, end_time)

        assert daemon.collector.collect_hour.call_count == 1
        _, kwargs = daemon.collector.collect_hour.call_args
        called_hour = kwargs["hour"]

        assert called_hour.tzinfo is None
        assert called_hour == datetime(2026, 8, 22, 21, 0, 0)
        assert kwargs["currencies"] == ["BTC", "ETH"]

    def test_gap_spanning_an_hour_boundary_backfills_both_hours(self):
        daemon = _make_daemon()
        tz_utc = timezone.utc
        start_time = datetime(2026, 8, 22, 20, 50, 0, tzinfo=tz_utc)
        end_time = datetime(2026, 8, 22, 21, 10, 0, tzinfo=tz_utc)

        daemon.collector.collect_hour.return_value = {
            "trades_collected": 0, "instruments_collected": 0,
        }

        daemon._backfill_gap(start_time, end_time)

        called_hours = [
            call.kwargs["hour"] for call in daemon.collector.collect_hour.call_args_list
        ]
        assert called_hours == [
            datetime(2026, 8, 22, 20, 0, 0),
            datetime(2026, 8, 22, 21, 0, 0),
        ]

    def test_backfill_error_is_caught_not_raised(self):
        daemon = _make_daemon()
        daemon.collector.collect_hour.side_effect = RuntimeError("api down")

        start_time = datetime.now(timezone.utc)
        end_time = datetime.now(timezone.utc)

        # Must not raise -- _backfill_gap has its own broad except.
        daemon._backfill_gap(start_time, end_time)
