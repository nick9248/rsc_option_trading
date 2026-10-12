"""Point-in-time tests for DatabaseRepository.get_dvol_history_before.

dvol_history stores one row per (asset, hourly candle START in UTC) holding the FINAL
close. The close of the candle starting at T is only known at T + 1 hour, so
"DVOL as known at time X" may only return candles with start + 1 hour <= X.

These tests write rows, so they run on a throw-away database (never option_trading).
They are skipped when the local PostgreSQL server is not reachable.
"""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

# Same columns as the real table (migrations/000_base_schema.sql, 011_add_otm_tables.sql).
CREATE_TABLE = """
CREATE TABLE dvol_history (
    id          SERIAL PRIMARY KEY,
    asset       VARCHAR(10)   NOT NULL,
    timestamp   TIMESTAMPTZ   NOT NULL,
    dvol_value  DECIMAL(8,4)  NOT NULL,
    UNIQUE (asset, timestamp)
)
"""


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_dvol_pit_{uuid.uuid4().hex[:8]}"

    try:
        admin = psycopg2.connect(connect_timeout=3, **dict(base_connection, database="postgres"))
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {scratch_name}")

    scratch_connection = dict(base_connection, database=scratch_name)
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE)

    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=scratch_connection["port"],
        database=scratch_name, user=scratch_connection["user"],
        password=scratch_connection["password"],
    ))
    try:
        yield repository
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def _utc(hour: int) -> datetime:
    return datetime(2026, 10, 10, hour, 0, tzinfo=timezone.utc)


def _store(repo, candles):
    repo.save_dvol_candles("BTC", candles)


def test_candle_still_forming_at_the_anchor_is_excluded(repo):
    # Candle 11:00 closes at 12:00 (known at 12:00). Candle 12:00 closes at 13:00.
    _store(repo, [(_utc(10), 50.0), (_utc(11), 51.0), (_utc(12), 52.0), (_utc(13), 53.0)])

    assert repo.get_dvol_history_before("BTC", _utc(12)) == [50.0, 51.0]


def test_candle_is_included_exactly_when_it_closes(repo):
    _store(repo, [(_utc(11), 51.0), (_utc(12), 52.0)])

    assert repo.get_dvol_history_before("BTC", _utc(12) - timedelta(seconds=1)) == []
    assert repo.get_dvol_history_before("BTC", _utc(12)) == [51.0]
    assert repo.get_dvol_history_before("BTC", _utc(13)) == [51.0, 52.0]


def test_window_start_is_unchanged(repo):
    # Window: candles starting from (anchor - days) on, as before.
    _store(repo, [(_utc(12) - timedelta(days=1, hours=1), 40.0), (_utc(12) - timedelta(days=1), 41.0),
                  (_utc(11), 51.0)])

    assert repo.get_dvol_history_before("BTC", _utc(12), days=1) == [41.0, 51.0]
