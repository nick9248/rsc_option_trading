"""Point-in-time tests for the two read methods the MenthorQ replay uses:
DatabaseRepository.get_index_price_at_or_before and
DatabaseRepository.get_option_chain_snapshot_at_or_before.

Both must return the newest row at or before ``as_of`` and ignore any row
after it (no lookahead). These tests write rows, so they run on a throw-away
database (never option_trading). They are skipped when the local PostgreSQL
server is not reachable.
"""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

# The columns the two queries read, with the types of the real tables
# (migrations/000_base_schema.sql and later).
CREATE_TABLES = """
CREATE TABLE snapshots (
    id               SERIAL PRIMARY KEY,
    captured_at      TIMESTAMP      NOT NULL,
    currency         VARCHAR(10)    NOT NULL,
    instrument_name  VARCHAR(100)   NOT NULL,
    expiration       VARCHAR(20),
    strike           DECIMAL(12,2),
    option_type      CHAR(1),
    open_interest    DECIMAL(18,8),
    underlying_price DECIMAL(18,8),
    mark_price       DECIMAL(18,8),
    mark_iv          DECIMAL(10,4)
);
CREATE TABLE historical_trades (
    id               SERIAL PRIMARY KEY,
    trade_id         VARCHAR(50)    NOT NULL,
    trade_timestamp  BIGINT         NOT NULL,
    currency         VARCHAR(10)    NOT NULL,
    index_price      DECIMAL(18,8)
);
"""

AS_OF = datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_menthorq_pit_{uuid.uuid4().hex[:8]}"

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
            cursor.execute(CREATE_TABLES)

    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=scratch_connection["port"],
        database=scratch_name, user=scratch_connection["user"],
        password=scratch_connection["password"],
    ))
    try:
        yield repository, scratch_connection
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def _execute(scratch_connection, sql, rows):
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(sql, rows)


def _insert_trades(scratch_connection, rows):
    """rows: (trade_id, aware UTC trade time, currency, index_price)."""
    _execute(
        scratch_connection,
        "INSERT INTO historical_trades (trade_id, trade_timestamp, currency, index_price) "
        "VALUES (%s, %s, %s, %s)",
        [(trade_id, int(time.timestamp() * 1000), currency, price)
         for trade_id, time, currency, price in rows],
    )


def _insert_snapshot_rows(scratch_connection, rows):
    """rows: (aware UTC capture time, instrument, open_interest, mark_iv, underlying_price)."""
    _execute(
        scratch_connection,
        "INSERT INTO snapshots (captured_at, currency, instrument_name, expiration, strike, "
        "option_type, open_interest, mark_iv, underlying_price) "
        "VALUES (%s, 'BTC', %s, '27NOV26', 80000, 'C', %s, %s, %s)",
        [(captured.astimezone(timezone.utc).replace(tzinfo=None), instrument, open_interest,
          mark_iv, underlying)
         for captured, instrument, open_interest, mark_iv, underlying in rows],
    )


def test_index_price_ignores_a_trade_after_as_of(repo):
    repository, scratch_connection = repo
    _insert_trades(scratch_connection, [
        ("older", AS_OF - timedelta(minutes=5), "BTC", 75000.0),
        ("newest_before", AS_OF - timedelta(seconds=7), "BTC", 75822.72),
        ("after", AS_OF + timedelta(seconds=1), "BTC", 99999.0),
        ("other_currency", AS_OF - timedelta(seconds=1), "ETH", 2500.0),
    ])

    price, trade_time = repository.get_index_price_at_or_before("BTC", AS_OF)

    assert price == 75822.72
    assert trade_time == AS_OF - timedelta(seconds=7)
    assert trade_time.tzinfo is not None


def test_index_price_includes_a_trade_exactly_at_as_of(repo):
    repository, scratch_connection = repo
    _insert_trades(scratch_connection, [("at", AS_OF, "BTC", 76000.0)])

    assert repository.get_index_price_at_or_before("BTC", AS_OF) == (76000.0, AS_OF)


def test_index_price_is_none_when_only_later_trades_exist(repo):
    repository, scratch_connection = repo
    _insert_trades(scratch_connection, [("after", AS_OF + timedelta(minutes=1), "BTC", 99999.0)])

    assert repository.get_index_price_at_or_before("BTC", AS_OF) is None


def test_earliest_capture_is_the_oldest_of_the_currency_or_none(repo):
    repository, scratch_connection = repo
    assert repository.get_earliest_option_chain_capture("BTC") is None
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=10), "BTC-27NOV26-80000-C", 200.0, 46.5, 76100.0),
        (AS_OF - timedelta(minutes=40), "BTC-27NOV26-80000-C", 100.0, 45.0, 76000.0),
    ])
    earliest = repository.get_earliest_option_chain_capture("BTC")
    assert earliest == AS_OF - timedelta(minutes=40) and earliest is not None and earliest.tzinfo is not None
    assert repository.get_earliest_option_chain_capture("ETH") is None


def test_snapshot_ignores_a_capture_after_as_of(repo):
    repository, scratch_connection = repo
    before = AS_OF - timedelta(minutes=10)
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=40), "BTC-27NOV26-80000-C", 100.0, 45.0, 76000.0),
        (before, "BTC-27NOV26-80000-C", 200.0, 46.5, 76100.0),
        (AS_OF + timedelta(minutes=20), "BTC-27NOV26-80000-C", 999.0, 99.0, 99999.0),
    ])

    captured_at, rows = repository.get_option_chain_snapshot_at_or_before("BTC", AS_OF)

    assert captured_at == before
    assert captured_at.tzinfo is not None
    assert len(rows) == 1
    assert rows[0]["open_interest"] == 200.0
    assert rows[0]["mark_iv"] == 46.5
    assert rows[0]["underlying_price"] == 76100.0


def test_snapshot_keeps_missing_numbers_as_none(repo):
    repository, scratch_connection = repo
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=1), "BTC-27NOV26-80000-C", None, None, None),
    ])

    _, rows = repository.get_option_chain_snapshot_at_or_before("BTC", AS_OF)

    assert rows[0]["open_interest"] is None
    assert rows[0]["mark_iv"] is None
    assert rows[0]["underlying_price"] is None
    assert rows[0]["mark_price"] is None


def test_snapshot_is_none_when_only_later_captures_exist(repo):
    repository, scratch_connection = repo
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF + timedelta(seconds=1), "BTC-27NOV26-80000-C", 999.0, 99.0, 99999.0),
    ])

    assert repository.get_option_chain_snapshot_at_or_before("BTC", AS_OF) is None


def test_latest_capture_is_the_newest_at_or_before_as_of_without_loading_rows(repo):
    repository, scratch_connection = repo
    assert repository.get_latest_option_chain_capture_at_or_before("BTC", AS_OF) is None
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=40), "BTC-27NOV26-80000-C", 100.0, 45.0, 76000.0),
        (AS_OF, "BTC-27NOV26-80000-C", 200.0, 46.5, 76100.0),                # exactly at as_of: counts
        (AS_OF + timedelta(minutes=20), "BTC-27NOV26-80000-C", 999.0, 99.0, 99999.0),
    ])

    latest = repository.get_latest_option_chain_capture_at_or_before("BTC", AS_OF)

    assert latest == AS_OF and latest.tzinfo is not None
    assert repository.get_latest_option_chain_capture_at_or_before("BTC", AS_OF - timedelta(minutes=1)) == \
        AS_OF - timedelta(minutes=40)
    assert repository.get_latest_option_chain_capture_at_or_before("ETH", AS_OF) is None
