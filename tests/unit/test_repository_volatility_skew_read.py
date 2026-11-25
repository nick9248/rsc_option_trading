"""The point-in-time read of the full-chain implied volatility, on a throw-away database built from migration 018 (never option_trading)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository


@pytest.fixture
def repository():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    try:
        admin = psycopg2.connect(connect_timeout=3, **dict(base_connection, database="postgres"))
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    name = f"test_skew_{uuid.uuid4().hex[:8]}"
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {name}")
    scratch = dict(base_connection, database=name)
    try:
        connection = psycopg2.connect(**scratch)
        with connection, connection.cursor() as cursor:
            cursor.execute((Path(__file__).parents[2] / "migrations" / "018_add_volatility_skew_history.sql").read_text(encoding="utf-8"))
            for hour, written, atm in (("2026-11-25 20:00", "2026-11-25 20:19", 52.0), ("2026-11-25 21:00", "2026-11-25 21:19", 51.6),
                                       ("2026-11-25 22:00", "2026-11-26 01:30", 50.0)):                  # written late: after its hour ended
                cursor.execute("INSERT INTO volatility_skew_history (snapshot_hour, currency, expiration, atm_iv_interp, call_25d_iv, put_25d_iv, created_at) "
                               "VALUES (%s, 'BTC', '4DEC26', %s, 49.16, NULL, %s)", (hour, atm, written))
        connection.close()
        ConnectionPool().close_all()
        yield DatabaseRepository(config=DatabaseConfig(host=scratch["host"], port=scratch["port"], database=name, user=scratch["user"],
                                                       password=scratch["password"]))
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


def at(hour, minute):
    return datetime(2026, 11, 25, hour, minute, tzinfo=timezone.utc)


def test_a_row_counts_fifteen_minutes_after_its_hour_ended_and_a_missing_value_stays_none(repository):
    assert repository.get_volatility_skew_at_or_before("BTC", "4DEC26", at(21, 14)) is None            # the 20:00 hour ended 14 minutes ago
    earlier = repository.get_volatility_skew_at_or_before("BTC", "4DEC26", at(21, 15))
    assert earlier == {"snapshot_hour": at(20, 0), "atm_iv_interp": 52.0, "call_25d_iv": 49.16, "put_25d_iv": None}
    assert repository.get_volatility_skew_at_or_before("BTC", "4DEC26", at(22, 14))["atm_iv_interp"] == 52.0      # 21:00 may still be overwritten
    assert repository.get_volatility_skew_at_or_before("BTC", "4DEC26", at(22, 15))["atm_iv_interp"] == 51.6
    assert repository.get_volatility_skew_at_or_before("ETH", "4DEC26", at(22, 15)) is None


def test_a_row_that_did_not_exist_yet_is_not_read_even_when_its_hour_is_long_over(repository):
    assert repository.get_volatility_skew_at_or_before("BTC", "4DEC26", at(23, 59))["atm_iv_interp"] == 51.6      # the 22:00 row is written at 01:30
    next_day = datetime(2026, 11, 26, 1, 30, tzinfo=timezone.utc)
    assert repository.get_volatility_skew_at_or_before("BTC", "4DEC26", next_day)["atm_iv_interp"] == 50.0
