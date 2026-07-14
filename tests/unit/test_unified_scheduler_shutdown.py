"""
Tests for UnifiedScheduler shutdown behavior (watchdog hard-exit fix).

Mirrors test_collection_daemon_shutdown.py -- see that file's module
docstring for the root-cause explanation (concurrent.futures.ThreadPoolExecutor's
unconditional, no-timeout atexit thread-join, confirmed empirically to hang
process exit past sys.exit(0) for as long as any in-flight job takes).
"""
import os
from unittest.mock import MagicMock, patch

from coding.service.data_collection.unified_scheduler import UnifiedScheduler


def _make_scheduler():
    scheduler = UnifiedScheduler.__new__(UnifiedScheduler)
    scheduler.scheduler = MagicMock()
    scheduler.scheduler.running = True
    scheduler.stats = {
        "scheduler_started": None,
        "prospective_collections": 0,
        "prospective_success": 0,
        "prospective_failed": 0,
        "trade_collections": 0,
        "trade_success": 0,
        "trade_failed": 0,
        "last_prospective": None,
        "last_trade": None,
        "next_prospective": None,
        "next_trade": None,
    }
    scheduler.is_running = True
    return scheduler


def test_shutdown_stops_scheduler_without_waiting():
    scheduler = _make_scheduler()

    with patch("coding.service.data_collection.unified_scheduler.threading.Timer"), \
         patch("coding.service.data_collection.unified_scheduler.sys.exit") as mock_exit:
        scheduler._shutdown()

    scheduler.scheduler.shutdown.assert_called_once_with(wait=False)
    mock_exit.assert_called_once_with(0)
    assert scheduler.is_running is False


def test_shutdown_arms_daemon_watchdog_targeting_os_exit():
    scheduler = _make_scheduler()

    with patch("coding.service.data_collection.unified_scheduler.threading.Timer") as mock_timer_cls, \
         patch("coding.service.data_collection.unified_scheduler.sys.exit"):
        mock_timer_instance = MagicMock()
        mock_timer_cls.return_value = mock_timer_instance

        scheduler._shutdown()

        mock_timer_cls.assert_called_once_with(
            UnifiedScheduler.SHUTDOWN_GRACE_SECONDS, os._exit, args=(0,)
        )
        assert mock_timer_instance.daemon is True
        mock_timer_instance.start.assert_called_once()


def test_shutdown_does_not_stop_scheduler_when_already_stopped():
    scheduler = _make_scheduler()
    scheduler.scheduler.running = False

    with patch("coding.service.data_collection.unified_scheduler.threading.Timer"), \
         patch("coding.service.data_collection.unified_scheduler.sys.exit"):
        scheduler._shutdown()

    scheduler.scheduler.shutdown.assert_not_called()
