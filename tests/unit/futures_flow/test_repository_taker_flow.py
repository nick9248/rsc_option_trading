"""
DatabaseRepository taker flow methods against the real migration
(migrations/033_add_taker_flow_hourly.sql) on a THROW-AWAY database (never
option_trading). Skipped when the local PostgreSQL server is not reachable.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2
import pytest

from coding.core.analytics.futures_flow.taker_flow import TakerFlowHour
from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATION = Path(__file__).parents[3] / "migrations" / "033_add_taker_flow_hourly.sql"
FIRST_HOUR = datetime(2026, 11, 10, tzinfo=timezone.utc)
COLLECTED_AT = datetime(2026, 11, 13, 14, 5, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_taker_flow_{uuid.uuid4().hex[:8]}"
    assert scratch_name != "option_trading"
    admin_connection: Dict[str, Any] = {**base_connection, "database": "postgres"}
    try:
        admin = psycopg2.connect(connect_timeout=3, **admin_connection)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {scratch_name}")

    scratch_connection: Dict[str, Any] = {**base_connection, "database": scratch_name}
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(MIGRATION.read_text(encoding="utf-8"))
            cursor.execute(MIGRATION.read_text(encoding="utf-8"))   # idempotent

    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=int(scratch_connection["port"]), database=scratch_name,
        user=scratch_connection["user"], password=scratch_connection["password"],
    ))
    try:
        yield repository
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def make_hours(symbol: str, count: int, buy: float = 40.5, sell: float = 59.25):
    return [TakerFlowHour(symbol, FIRST_HOUR + timedelta(hours=offset), buy, sell, 1000 + offset)
            for offset in range(count)]


def test_round_trip_keeps_every_field_and_orders_by_hour(repo):
    assert repo.save_taker_flow_hours(list(reversed(make_hours("BTCUSDT", 5))), COLLECTED_AT) == 5
    stored = repo.get_taker_flow_hours("BTCUSDT", FIRST_HOUR, FIRST_HOUR + timedelta(hours=4))
    assert stored == make_hours("BTCUSDT", 5)
    assert all(hour.hour_utc.tzinfo is not None for hour in stored)


def test_an_existing_hour_is_kept_and_not_counted_again(repo):
    repo.save_taker_flow_hours(make_hours("BTCUSDT", 3), COLLECTED_AT)
    changed = [TakerFlowHour("BTCUSDT", FIRST_HOUR, 1.0, 1.0, 1)] + make_hours("BTCUSDT", 4)[3:]
    assert repo.save_taker_flow_hours(changed, COLLECTED_AT) == 1          # only hour 3 is new
    assert repo.get_taker_flow_hours("BTCUSDT", FIRST_HOUR, FIRST_HOUR)[0].taker_buy_usd == 40.5


def test_symbols_are_separate_and_range_ends_are_inclusive(repo):
    repo.save_taker_flow_hours(make_hours("BTCUSDT", 4) + make_hours("ETHUSDT", 4, buy=7.0, sell=3.0), COLLECTED_AT)
    window = repo.get_taker_flow_hours("ETHUSDT", FIRST_HOUR + timedelta(hours=1), FIRST_HOUR + timedelta(hours=2))
    assert [hour.hour_utc for hour in window] == [FIRST_HOUR + timedelta(hours=1), FIRST_HOUR + timedelta(hours=2)]
    assert all(hour.taker_buy_usd == 7.0 for hour in window)


def test_saving_nothing_stores_nothing(repo):
    assert repo.save_taker_flow_hours([], COLLECTED_AT) == 0


def test_the_table_refuses_negative_volume(repo):
    with pytest.raises(psycopg2.errors.CheckViolation):
        repo.save_taker_flow_hours([TakerFlowHour("BTCUSDT", FIRST_HOUR, -1.0, 1.0)], COLLECTED_AT)
