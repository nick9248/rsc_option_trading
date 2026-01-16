"""
Database configuration for PostgreSQL connection.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import psycopg2
from psycopg2 import pool

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

    host: str = "localhost"
    port: int = 5433
    database: str = "option_trading"
    user: str = "postgres"
    password: str = "DB_PASSWORD_REDACTED"

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
    Singleton connection pool for database connections.
    """

    _instance: Optional["ConnectionPool"] = None
    _pool: Optional[pool.SimpleConnectionPool] = None

    def __new__(cls):
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
            self._pool = pool.SimpleConnectionPool(
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
