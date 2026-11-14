"""
DatabaseRepository.get_block_trade_legs_between against the real historical_trades migrations (006 and 022) on a
THROW-AWAY database (never option_trading). Skipped when the local PostgreSQL server is not reachable.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATIONS = Path(__file__).parents[3] / "migrations"
BASE_MS = int(datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_block_legs_{uuid.uuid4().hex[:8]}"
    assert scratch_name != "option_trading"
    admin_connection: Dict[str, Any] = {**base_connection, "database": "postgres"}
    try:
        admin = psycopg2.connect(connect_timeout=3, **admin_connection)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {scratch_name}")

    try:          # from here on the scratch database exists: drop it whatever fails below
        scratch_connection: Dict[str, Any] = {**base_connection, "database": scratch_name}
        with psycopg2.connect(**scratch_connection) as connection:
            with connection.cursor() as cursor:
                for name in ("006_add_prospective_collection_tables.sql", "022_add_block_trade_columns.sql"):
                    cursor.execute((MIGRATIONS / name).read_text(encoding="utf-8"))

        ConnectionPool().close_all()
        repository = DatabaseRepository(config=DatabaseConfig(
            host=scratch_connection["host"], port=int(scratch_connection["port"]), database=scratch_name,
            user=scratch_connection["user"], password=scratch_connection["password"],
        ))
        yield repository, scratch_connection
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def insert_trade(connection, trade_id, offset_ms, block_trade_id=None, leg_count=None, combo_id=None,
                 currency="BTC", expiration="25DEC26", strike=100000, option_type="C", direction="buy", amount=2.5):
    with psycopg2.connect(**connection) as raw:
        with raw.cursor() as cursor:
            cursor.execute(
                "INSERT INTO historical_trades (trade_id, trade_timestamp, instrument_name, currency, expiration, strike,"
                " option_type, price, amount, direction, block_trade_id, block_trade_leg_count, combo_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, 0.1, %s, %s, %s, %s, %s)",
                (trade_id, BASE_MS + offset_ms, f"{currency}-{expiration}-{strike}-{option_type}", currency, expiration,
                 strike, option_type, amount, direction, block_trade_id, leg_count, combo_id))


def test_returns_only_block_legs_of_the_currency_in_the_window_with_every_field(repo):
    repository, connection = repo
    insert_trade(connection, "t-late", 2000, "BLOCK-1", 15, "COMBO-1", direction="sell", option_type="P", strike=90000)
    insert_trade(connection, "t-early", 1000, "BLOCK-1", None)
    insert_trade(connection, "t-screen", 1500)                                    # not a block
    insert_trade(connection, "t-eth", 1500, "BLOCK-2", 2, currency="ETH")        # other currency
    insert_trade(connection, "t-before", -1, "BLOCK-3", 1)                       # before the window
    legs = repository.get_block_trade_legs_between("BTC", BASE_MS, BASE_MS + 3000)
    assert [leg.trade_id for leg in legs] == ["t-early", "t-late"]               # oldest first
    early, late = legs
    assert early.block_trade_leg_count is None and early.combo_id is None
    assert late.block_trade_leg_count == 15 and late.combo_id == "COMBO-1"
    assert (late.block_trade_id, late.expiration, late.strike, late.option_type, late.direction, late.amount) == (
        "BLOCK-1", "25DEC26", 90000.0, "P", "sell", 2.5)
    assert late.trade_timestamp == datetime.fromtimestamp((BASE_MS + 2000) / 1000, tz=timezone.utc)
    assert late.trade_timestamp.tzinfo is not None


def test_window_start_is_inclusive_and_end_exclusive(repo):
    repository, connection = repo
    insert_trade(connection, "t-start", 0, "BLOCK-1", 2)
    insert_trade(connection, "t-end", 1000, "BLOCK-1", 2)
    legs = repository.get_block_trade_legs_between("BTC", BASE_MS, BASE_MS + 1000)
    assert [leg.trade_id for leg in legs] == ["t-start"]
