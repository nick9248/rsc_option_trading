"""
Migration 032 (menthorq_monitor_state) and the repository methods of the
bot commands against the real migrations on a THROW-AWAY database (never
option_trading): mute persistence across a new repository (a restart),
unmute, held-back alerts, newest run per mode, last delivered alert of a
key set. Skipped when the local PostgreSQL server is not reachable.
"""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

MIGRATIONS = Path(__file__).parents[2] / "migrations"
MIGRATION_031 = MIGRATIONS / "031_add_menthorq_monitor_tables.sql"
MIGRATION_032 = MIGRATIONS / "032_add_menthorq_monitor_state.sql"


def _repository(connection: Dict[str, Any], database: str) -> DatabaseRepository:
    ConnectionPool().close_all()
    return DatabaseRepository(config=DatabaseConfig(
        host=connection["host"], port=int(connection["port"]), database=database,
        user=connection["user"], password=connection["password"],
    ))


@pytest.fixture
def scratch():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_menthorq_state_{uuid.uuid4().hex[:8]}"
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
            cursor.execute(MIGRATION_031.read_text(encoding="utf-8"))
            cursor.execute(MIGRATION_032.read_text(encoding="utf-8"))
            cursor.execute(MIGRATION_032.read_text(encoding="utf-8"))   # idempotent
    try:
        yield scratch_connection, scratch_name
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def test_mute_survives_a_new_repository_and_can_be_cleared(scratch):
    connection, name = scratch
    repository = _repository(connection, name)
    assert repository.get_menthorq_monitor_state("BTC") is None
    muted_until = BASE_TIME + timedelta(hours=24)
    repository.save_menthorq_alert_mute("BTC", muted_until, BASE_TIME)

    restarted = _repository(connection, name)
    state = restarted.get_menthorq_monitor_state("BTC")
    assert state is not None
    assert state.alerts_muted_until is not None and state.alerts_muted_until.tzinfo is not None
    assert state.alerts_muted_until == muted_until
    assert state.mute_updated_at == BASE_TIME
    assert state.last_suppressed_at is None and state.last_suppressed_alert_keys == []

    restarted.save_menthorq_alert_mute("BTC", None, BASE_TIME + timedelta(hours=1))
    cleared = restarted.get_menthorq_monitor_state("BTC")
    assert cleared is not None and cleared.alerts_muted_until is None
    assert cleared.mute_updated_at == BASE_TIME + timedelta(hours=1)


def test_held_back_alerts_keep_the_mute(scratch):
    connection, name = scratch
    repository = _repository(connection, name)
    repository.save_menthorq_suppressed_alerts("BTC", ["hvl_move_24h"], BASE_TIME)   # row created
    repository.save_menthorq_alert_mute("BTC", BASE_TIME + timedelta(days=3), BASE_TIME)
    repository.save_menthorq_suppressed_alerts("BTC", ["hvl_move_24h", "put_support_move_24h"],
                                               BASE_TIME + timedelta(hours=1))
    state = repository.get_menthorq_monitor_state("BTC")
    assert state is not None
    assert state.alerts_muted_until == BASE_TIME + timedelta(days=3)       # not overwritten
    assert state.last_suppressed_alert_keys == ["hvl_move_24h", "put_support_move_24h"]
    assert state.last_suppressed_at == BASE_TIME + timedelta(hours=1)


def test_latest_run_includes_not_computed_runs(scratch):
    connection, name = scratch
    repository = _repository(connection, name)
    assert repository.get_latest_menthorq_level_run("BTC", "live", "monthly", "index") is None
    repository.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=1)))
    repository.save_menthorq_level_snapshot(make_record(spot=None, hvl=None, total_net_gex=None,
                                                        call_resistance=None, put_support=None))
    latest = repository.get_latest_menthorq_level_run("BTC", "live", "monthly", "index")
    assert latest is not None and latest.as_of_utc == BASE_TIME and latest.spot is None
    assert repository.get_latest_menthorq_level_run("BTC", "daily_close", "monthly", "index") is None


def test_last_delivery_of_a_key_set(scratch):
    connection, name = scratch
    repository = _repository(connection, name)
    keys = ["hvl_move_24h", "net_gex_turned_negative"]
    assert repository.get_last_menthorq_alert_delivery_of_keys("BTC", keys) is None
    repository.save_menthorq_alert_delivery("BTC", "hvl_move_24h", BASE_TIME, "m1")
    repository.save_menthorq_alert_delivery("BTC", "weekly_brief", BASE_TIME + timedelta(hours=2), "b")
    repository.save_menthorq_alert_delivery("BTC", "net_gex_turned_negative",
                                            BASE_TIME + timedelta(hours=1), "m2")
    assert repository.get_last_menthorq_alert_delivery_of_keys("BTC", keys) == \
        ("net_gex_turned_negative", BASE_TIME + timedelta(hours=1))
    assert repository.get_last_menthorq_alert_delivery_of_keys("ETH", keys) is None
