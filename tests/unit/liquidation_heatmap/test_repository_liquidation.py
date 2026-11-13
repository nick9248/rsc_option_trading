"""
DatabaseRepository methods of the liquidation recorder against the real migration
(migrations/034_add_liquidation_recorder_tables.sql) on a THROW-AWAY database (never option_trading).
Skipped when the local PostgreSQL server is not reachable.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2
import pytest

from coding.core.analytics.liquidation_heatmap.level_snapshot import RecordedLiquidation
from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATION = Path(__file__).parents[3] / "migrations" / "034_add_liquidation_recorder_tables.sql"
T0 = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_liquidation_{uuid.uuid4().hex[:8]}"
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


def event(seconds, side="long", price=68_000.5, raw=250.0, instrument="BTC-USDT-SWAP"):
    return RecordedLiquidation("okx", instrument, T0 + timedelta(seconds=seconds), side, price, raw * 0.01, raw)


def test_events_round_trip_and_an_existing_event_is_not_stored_twice(repo):
    first = [event(0), event(5, side="short", price=69_000.0, raw=10.0)]
    assert repo.save_liquidation_events(first, T0) == 2
    assert repo.save_liquidation_events(first + [event(9)], T0) == 1                  # only the new one is inserted
    stored = repo.get_liquidation_events_between("okx", "BTC-USDT-SWAP", T0, T0 + timedelta(minutes=1))
    assert stored == [event(0), event(5, side="short", price=69_000.0, raw=10.0), event(9)]
    assert all(item.event_time.tzinfo is not None for item in stored)
    assert repo.get_liquidation_events_between("okx", "BTC-USDT-SWAP", T0 + timedelta(seconds=5), T0 + timedelta(seconds=9))[0].raw_size == 10.0


def test_latest_event_time_is_per_instrument_and_none_when_empty(repo):
    assert repo.get_latest_liquidation_event_time("okx", "BTC-USDT-SWAP") is None
    repo.save_liquidation_events([event(0), event(30), event(60, instrument="ETH-USDT-SWAP")], T0)
    assert repo.get_latest_liquidation_event_time("okx", "BTC-USDT-SWAP") == T0 + timedelta(seconds=30)


def test_saving_nothing_stores_nothing(repo):
    assert repo.save_liquidation_events([], T0) == 0


def test_the_events_table_refuses_a_bad_side_and_non_positive_sizes(repo):
    bad_side = RecordedLiquidation("okx", "BTC-USDT-SWAP", T0, "net", 68_000.0, 1.0, 100.0)
    with pytest.raises(psycopg2.errors.CheckViolation):
        repo.save_liquidation_events([bad_side], T0)
    with pytest.raises(psycopg2.errors.CheckViolation):
        repo.save_liquidation_events([RecordedLiquidation("okx", "BTC-USDT-SWAP", T0, "long", 68_000.0, 0.0, 100.0)], T0)


def test_snapshots_round_trip_and_a_snapshot_is_never_rewritten(repo):
    levels = [[9_800.0, 50.5, 0.0], [10_300.0, 0.0, 70.25]]
    assert repo.save_liquidation_heatmap_snapshot("BTCUSDT", T0, T0 + timedelta(minutes=12), 10_000.0, 100.0,
                                                  {"creation_source": "volume"}, levels) is True
    assert repo.save_liquidation_heatmap_snapshot("BTCUSDT", T0, T0 + timedelta(minutes=30), 11_000.0, 100.0,
                                                  {}, [[1.0, 1.0, 1.0]]) is False           # same as_of: kept unchanged
    snapshots = repo.get_liquidation_heatmap_snapshots_between("BTCUSDT", T0, T0 + timedelta(hours=1))
    assert len(snapshots) == 1
    assert snapshots[0].levels == levels and snapshots[0].reference_price == 10_000.0
    assert snapshots[0].computed_at == T0 + timedelta(minutes=12) and snapshots[0].bin_size_usd == 100.0
    assert repo.get_liquidation_heatmap_snapshots_between("BTCUSDT", T0 + timedelta(hours=2), T0 + timedelta(hours=3)) == []
