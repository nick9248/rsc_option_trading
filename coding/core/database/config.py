"""
Database configuration for PostgreSQL connection.
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2 import pool

# Load .env file from project root
env_path = Path(__file__).parents[3] / ".env"
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    """
    PostgreSQL database configuration.

    Attributes:
        host: Database host address.
        port: Database port number.
        database: Database name.
        user: Database username.
        password: Database password.
    """

    host: str = os.getenv("DB_HOST", "localhost")
    port: int = int(os.getenv("DB_PORT", "5433"))
    database: str = os.getenv("DB_NAME", "option_trading")
    user: str = os.getenv("DB_USER", "postgres")
    password: str = os.getenv("DB_PASSWORD", "")

    def get_connection_dict(self) -> dict:
        """
        Get connection parameters as dictionary.

        Returns:
            Dictionary with connection parameters.
        """
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
        }


class ConnectionPool:
    """
    Singleton connection pool for database connections (one pool per process).

    Thread-safe: the collector's APScheduler runs its jobs in worker threads
    (prospective and trade collection, the MenthorQ hourly job, the weekly brief
    and the command poll: at most 5 at once, each holding one connection at a
    time), so the pool is a psycopg2 ThreadedConnectionPool. The default maximum
    of 10 connections covers those 5 jobs with room to spare.

    Creating the singleton and creating the pool are both guarded by one
    class-level lock (double-checked), so two threads constructing a
    DatabaseRepository at the same moment share one pool instead of each
    opening their own (the loser's connections would never be closed).
    """

    _instance: Optional["ConnectionPool"] = None
    _pool: Optional[pool.ThreadedConnectionPool] = None
    _creation_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._creation_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self, config: DatabaseConfig, min_conn: int = 1, max_conn: int = 10):
        """
        Initialize the connection pool.

        Args:
            config: Database configuration.
            min_conn: Minimum number of connections.
            max_conn: Maximum number of connections.
        """
        if self._pool is None:
            with self._creation_lock:
                if self._pool is None:
                    self._pool = pool.ThreadedConnectionPool(
                        min_conn,
                        max_conn,
                        **config.get_connection_dict()
                    )
                    logger.info("Database connection pool initialized")

    def get_connection(self):
        """
        Get a connection from the pool.

        Returns:
            Database connection.
        """
        if self._pool is None:
            raise RuntimeError("Connection pool not initialized")
        return self._pool.getconn()

    def return_connection(self, conn):
        """
        Return a connection to the pool.

        Args:
            conn: Database connection to return.
        """
        if self._pool is not None:
            self._pool.putconn(conn)

    def close_all(self):
        """Close all connections in the pool."""
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
            logger.info("Database connection pool closed")
