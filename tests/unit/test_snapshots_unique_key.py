"""Migration 035: one row per instrument per capture in ``snapshots``, and the writers that respect it."""

import contextlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool
from coding.core.database.repository import DatabaseRepository

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "035_add_snapshots_unique_key.sql"
CAPTURED_AT = datetime(2026, 11, 14, 12, 0)


def test_save_snapshot_skips_rows_that_are_already_stored():
    cursor = MagicMock()
    repository = object.__new__(DatabaseRepository)
    repository._db_cursor = contextlib.contextmanager(lambda: (yield cursor))
    saved = repository.save_snapshot("BTC", [{"instrument_name": "BTC-27NOV26-70000-C", "open_interest": 1.0}], CAPTURED_AT)
    sql = cursor.executemany.call_args.args[0]
    assert "ON CONFLICT DO NOTHING" in sql and "ON CONFLICT (" not in sql          # no target: works before and after the index
    assert saved == 1


def test_vps_sync_names_the_snapshot_key():
    from scripts.sync_from_vps import SYNC_TABLES
    entry = next(table for table in SYNC_TABLES if table["name"] == "snapshots")
    assert entry["conflict_target"] == "(captured_at, instrument_name) DO NOTHING"


@pytest.fixture()
def scratch_connection():
    """A throw-away database (never ``option_trading``) holding a minimal ``snapshots`` table."""
    from dotenv import load_dotenv
    import os
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
    base: Dict[str, Any] = {"host": os.getenv("DB_HOST", "localhost"), "port": int(os.getenv("DB_PORT", "5433")),
                            "user": os.getenv("DB_USER", "postgres"), "password": os.getenv("DB_PASSWORD")}
    if base["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    name = f"test_snapshots_key_{uuid.uuid4().hex[:8]}"
    assert name != "option_trading"
    try:
        admin = psycopg2.connect(connect_timeout=3, database="postgres", **base)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {name}")
    connection = psycopg2.connect(database=name, **base)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE snapshots (id serial PRIMARY KEY, captured_at timestamp NOT NULL, "
                       "instrument_name varchar(100) NOT NULL, open_interest numeric)")
    try:
        yield connection
    finally:
        connection.close()
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


def test_migration_rejects_a_repeated_instrument_in_one_capture_and_is_idempotent(scratch_connection):
    insert = "INSERT INTO snapshots (captured_at, instrument_name, open_interest) VALUES (%s, %s, %s)"
    with scratch_connection.cursor() as cursor:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))                         # a second run changes nothing
        cursor.execute(insert, (CAPTURED_AT, "BTC-27NOV26-70000-C", 1))
        cursor.execute(insert + " ON CONFLICT DO NOTHING", (CAPTURED_AT, "BTC-27NOV26-70000-C", 2))     # skipped
        cursor.execute(insert, (CAPTURED_AT, "BTC-27NOV26-70000-P", 3))                                # another instrument
        cursor.execute(insert, (datetime(2026, 11, 14, 13, 0), "BTC-27NOV26-70000-C", 4))              # another capture
        cursor.execute("SELECT count(*), sum(open_interest) FROM snapshots")
        assert cursor.fetchone() == (3, 8)                                         # the first value (1) was kept, 2 was dropped
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cursor.execute(insert, (CAPTURED_AT, "BTC-27NOV26-70000-C", 5))        # a plain insert now fails loudly
