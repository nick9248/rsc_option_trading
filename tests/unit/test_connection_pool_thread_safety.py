"""
Thread-safety of the process-wide ConnectionPool (coding/core/database/config.py).

The collector's APScheduler runs several jobs in worker threads that all share
the one pool, so two threads must never hold the same connection at once. No
database is touched: psycopg2.connect is replaced by a fake connection factory.
"""

import sys
import threading
import time

import psycopg2
import psycopg2.extensions
import psycopg2.pool
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig


class FakeConnectionInfo:
    """The `info` attribute psycopg2's pool reads when a connection is returned."""

    transaction_status = psycopg2.extensions.TRANSACTION_STATUS_IDLE


class FakeConnection:
    """A stand-in for a psycopg2 connection (only what the pool touches)."""

    def __init__(self):
        self.closed = 0
        self.info = FakeConnectionInfo()

    def close(self):
        """Mark the fake connection closed."""
        self.closed = 1


@pytest.fixture
def fake_connection_pool(monkeypatch):
    """A fresh singleton pool built on fake connections; reset afterwards."""
    monkeypatch.setattr(psycopg2, "connect", lambda *arguments, **keyword_arguments: FakeConnection())
    ConnectionPool().close_all()
    connection_pool = ConnectionPool()
    connection_pool.initialize(DatabaseConfig(), min_conn=1, max_conn=10)
    yield connection_pool
    connection_pool.close_all()


def test_pool_is_the_threaded_psycopg2_pool(fake_connection_pool):
    """The shared pool is psycopg2's lock-protected ThreadedConnectionPool."""
    assert isinstance(fake_connection_pool._pool, psycopg2.pool.ThreadedConnectionPool)


def test_threads_never_share_a_connection(fake_connection_pool):
    """
    8 threads x 200 take/return cycles (8 <= max_conn 10, so the pool is never
    exhausted): no connection is ever handed to two threads at the same time,
    and every connection comes back (nothing is left marked as used).
    """
    thread_count = 8
    cycles_per_thread = 200
    connections_in_use = set()
    in_use_lock = threading.Lock()
    failures = []

    def take_and_return_connections():
        for _ in range(cycles_per_thread):
            connection = fake_connection_pool.get_connection()
            with in_use_lock:
                if id(connection) in connections_in_use:
                    failures.append("connection handed to two threads at once")
                connections_in_use.add(id(connection))
            time.sleep(0)
            with in_use_lock:
                connections_in_use.discard(id(connection))
            fake_connection_pool.return_connection(connection)

    previous_switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads very often to provoke races
    try:
        threads = [threading.Thread(target=take_and_return_connections) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(previous_switch_interval)

    assert failures == []
    assert fake_connection_pool._pool._used == {}
