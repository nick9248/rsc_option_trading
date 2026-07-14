"""
Tests for CollectionDaemon shutdown behavior (watchdog hard-exit fix).

Background: concurrent.futures.ThreadPoolExecutor (used internally by
APScheduler's default executor) registers a process-wide, unconditional,
no-timeout thread-join at interpreter exit (threading._register_atexit in
Lib/concurrent/futures/thread.py). This blocks process termination on any
in-flight job regardless of scheduler.shutdown(wait=False) and regardless of
the worker thread's daemon flag -- confirmed empirically to hang a process
past sys.exit(0) for as long as the in-flight job takes. _shutdown() must
therefore arm a bounded watchdog that force-exits via os._exit() if the
graceful path doesn't complete in time.
"""
import os
from unittest.mock import MagicMock, patch

from coding.service.data_collection.collection_daemon import CollectionDaemon


def _make_daemon():
    daemon = CollectionDaemon.__new__(CollectionDaemon)
    daemon.scheduler = MagicMock()
    daemon.scheduler.running = True
    daemon.stats = {
        "daemon_started": None,
        "collections_run": 0,
        "collections_success": 0,
        "collections_failed": 0,
        "last_collection": None,
        "next_collection": None,
    }
    daemon.is_running = True
    return daemon


def test_shutdown_stops_scheduler_without_waiting():
    daemon = _make_daemon()

    with patch("coding.service.data_collection.collection_daemon.threading.Timer"), \
         patch("coding.service.data_collection.collection_daemon.sys.exit") as mock_exit:
        daemon._shutdown()

    daemon.scheduler.shutdown.assert_called_once_with(wait=False)
    mock_exit.assert_called_once_with(0)
    assert daemon.is_running is False


def test_shutdown_arms_daemon_watchdog_targeting_os_exit():
    """The watchdog must be a real threading.Timer targeting os._exit(0),
    scheduled for SHUTDOWN_GRACE_SECONDS, and marked daemon so it can fire
    even while the interpreter is stuck in the atexit ThreadPoolExecutor join."""
    daemon = _make_daemon()

    with patch("coding.service.data_collection.collection_daemon.threading.Timer") as mock_timer_cls, \
         patch("coding.service.data_collection.collection_daemon.sys.exit"):
        mock_timer_instance = MagicMock()
        mock_timer_cls.return_value = mock_timer_instance

        daemon._shutdown()

        mock_timer_cls.assert_called_once_with(
            CollectionDaemon.SHUTDOWN_GRACE_SECONDS, os._exit, args=(0,)
        )
        assert mock_timer_instance.daemon is True
        mock_timer_instance.start.assert_called_once()


def test_shutdown_does_not_stop_scheduler_when_already_stopped():
    daemon = _make_daemon()
    daemon.scheduler.running = False

    with patch("coding.service.data_collection.collection_daemon.threading.Timer"), \
         patch("coding.service.data_collection.collection_daemon.sys.exit"):
        daemon._shutdown()

    daemon.scheduler.shutdown.assert_not_called()
