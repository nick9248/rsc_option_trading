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


def test_default_maximum_is_20_connections_and_minimum_1(monkeypatch):
    """5 scheduler jobs, at most 10 executor threads, x 2 margin -> 20 (config.py docstring)."""
    monkeypatch.setattr(psycopg2, "connect", lambda *arguments, **keyword_arguments: FakeConnection())
    ConnectionPool().close_all()
    connection_pool = ConnectionPool()
    try:
        connection_pool.initialize(DatabaseConfig())
        psycopg2_pool = connection_pool._pool
        assert psycopg2_pool is not None
        assert psycopg2_pool.maxconn == 20
        assert psycopg2_pool.minconn == 1
    finally:
        connection_pool.close_all()


def test_an_exhausted_pool_raises_a_clear_pool_error(fake_connection_pool):
    """
    All 10 connections taken: the 11th request fails at once (no waiting)
    with the connection count in the message; returning one makes it work again.
    """
    taken = [fake_connection_pool.get_connection() for _ in range(10)]
    with pytest.raises(psycopg2.pool.PoolError, match=r"^database pool exhausted: 10 connections in use$"):
        fake_connection_pool.get_connection()
    fake_connection_pool.return_connection(taken.pop())
    taken.append(fake_connection_pool.get_connection())
    for connection in taken:
        fake_connection_pool.return_connection(connection)


def test_a_closed_pool_error_is_not_relabelled(fake_connection_pool):
    """Only exhaustion gets the new message; psycopg2's other errors pass through."""
    psycopg2_pool = fake_connection_pool._pool
    psycopg2_pool.closeall()
    try:
        with pytest.raises(psycopg2.pool.PoolError, match=r"^connection pool is closed$"):
            fake_connection_pool.get_connection()
    finally:
        # Closed already: replace it so the fixture's close_all() has a pool to close.
        fake_connection_pool._pool = psycopg2.pool.ThreadedConnectionPool(1, 10, **DatabaseConfig().get_connection_dict())


def test_concurrent_initialize_creates_exactly_one_pool(monkeypatch):
    """
    16 threads released together each construct ConnectionPool() and call
    initialize(): exactly ONE pool object is created. The fake pool sleeps in
    its constructor to widen the check-then-set window; without the lock every
    thread passes the `_pool is None` check and builds its own pool (16 pools).
    """
    thread_count = 16
    constructions = []
    construction_count_lock = threading.Lock()

    class SlowFakePool:
        """Counts constructions; slow enough that racing threads overlap."""

        def __init__(self, *arguments, **keyword_arguments):
            with construction_count_lock:
                constructions.append(self)
            time.sleep(0.05)

        def closeall(self):
            """Nothing to close: no real connections exist."""

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", SlowFakePool)
    ConnectionPool._instance = None
    ConnectionPool._pool = None
    start_barrier = threading.Barrier(thread_count)
    instances = []

    def construct_and_initialize():
        start_barrier.wait()
        connection_pool = ConnectionPool()
        connection_pool.initialize(DatabaseConfig(), min_conn=1, max_conn=10)
        with construction_count_lock:
            instances.append(connection_pool)

    try:
        threads = [threading.Thread(target=construct_and_initialize) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(constructions) == 1
        assert len({id(instance) for instance in instances}) == 1
        assert instances[0]._pool is constructions[0]
    finally:
        ConnectionPool().close_all()
        ConnectionPool._instance = None
