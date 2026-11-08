"""
DatabaseRepository methods of the MenthorQ-style monitor against the real
migration (migrations/031_add_menthorq_monitor_tables.sql) on a THROW-AWAY
database (never option_trading). Skipped when the local PostgreSQL server is
not reachable.
"""

import uuid
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

MIGRATION = Path(__file__).parents[2] / "migrations" / "031_add_menthorq_monitor_tables.sql"


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_menthorq_monitor_{uuid.uuid4().hex[:8]}"
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


def test_round_trip_keeps_every_field(repo):
    record = make_record(panel_levels={"first_expiration": {"expiration": "27NOV26", "hvl": 95000.0}},
                         not_computed_reasons=["panel next_expiration: no expiry with computed GEX"])
    record_id = repo.save_menthorq_level_snapshot(record)
    assert isinstance(record_id, int)

    loaded = repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "monthly", "index", BASE_TIME)
    assert loaded.record_id == record_id
    assert loaded.as_of_utc == BASE_TIME and loaded.as_of_utc.tzinfo == timezone.utc
    assert loaded.snapshot_captured_at_utc == record.snapshot_captured_at_utc
    assert (loaded.spot, loaded.call_resistance, loaded.put_support, loaded.hvl, loaded.total_net_gex) == \
        (100000.0, 110000.0, 90000.0, 95000.0, 5e6)
    assert loaded.gamma_regime == "positive"
    assert loaded.expirations == record.expirations
    assert loaded.panel_levels == record.panel_levels
    assert loaded.iv_source_counts == record.iv_source_counts
    assert loaded.not_computed_reasons == record.not_computed_reasons


def test_duplicate_run_is_kept_once(repo):
    assert repo.save_menthorq_level_snapshot(make_record()) is not None
    assert repo.save_menthorq_level_snapshot(make_record(spot=1.0)) is None
    assert repo.has_menthorq_level_snapshot("BTC", "live", "monthly", "index", BASE_TIME)
    assert not repo.has_menthorq_level_snapshot("BTC", "daily_close", "monthly", "index", BASE_TIME)


def test_lookup_is_point_in_time_and_skips_not_computed_rows(repo):
    for hours_back, spot in ((0, 100000.0), (1, 99000.0), (2, None), (26, 97000.0)):
        repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=hours_back), spot=spot))
    repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME + timedelta(hours=1), spot=1.0))  # later row

    at = repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "monthly", "index", BASE_TIME)
    assert at.spot == 100000.0                           # the later row is never returned
    before = repo.get_menthorq_level_snapshot_at_or_before(
        "BTC", "live", "monthly", "index", BASE_TIME, strictly_before=True)
    assert before.spot == 99000.0
    skipped = repo.get_menthorq_level_snapshot_at_or_before(
        "BTC", "live", "monthly", "index", BASE_TIME - timedelta(hours=2))
    assert skipped.spot == 97000.0                       # the NULL-spot row at -2 h is skipped
    windowed = repo.get_menthorq_level_snapshot_at_or_before(
        "BTC", "live", "monthly", "index", BASE_TIME - timedelta(hours=24),
        earliest=BASE_TIME - timedelta(hours=25))
    assert windowed is None                              # -26 h is outside the window
    assert repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "all", "index", BASE_TIME) is None


def test_invalid_mode_is_rejected_by_the_table(repo):
    with pytest.raises(psycopg2.errors.CheckViolation):
        repo.save_menthorq_level_snapshot(make_record(mode="hourly"))


def test_alert_deliveries_last_time(repo):
    assert repo.get_last_menthorq_alert_delivery("BTC", "hvl_move_24h") is None
    repo.save_menthorq_alert_delivery("BTC", "hvl_move_24h", BASE_TIME - timedelta(hours=3), "a", 7)
    repo.save_menthorq_alert_delivery("BTC", "hvl_move_24h", BASE_TIME, "b")
    repo.save_menthorq_alert_delivery("ETH", "hvl_move_24h", BASE_TIME + timedelta(hours=1), "c")
    last = repo.get_last_menthorq_alert_delivery("BTC", "hvl_move_24h")
    assert last == BASE_TIME and last.tzinfo == timezone.utc


def _delivery_keys(repo):
    with repo._db_cursor() as cursor:
        cursor.execute("SELECT alert_key FROM menthorq_alert_deliveries ORDER BY id")
        return [row[0] for row in cursor.fetchall()]


def test_several_delivery_keys_are_saved_in_one_transaction(repo):
    repo.save_menthorq_alert_deliveries("BTC", ["hvl_move_24h", "net_gex_turned_negative"], BASE_TIME, "m", 3)
    assert _delivery_keys(repo) == ["hvl_move_24h", "net_gex_turned_negative"]
    assert repo.get_last_menthorq_alert_delivery("BTC", "net_gex_turned_negative") == BASE_TIME


def test_a_failing_key_rolls_back_every_key_of_the_message(repo):
    too_long = "x" * 81                       # alert_key is varchar(80): the second INSERT fails
    with pytest.raises(psycopg2.errors.StringDataRightTruncation):
        repo.save_menthorq_alert_deliveries("BTC", ["hvl_move_24h", too_long], BASE_TIME, "m")
    assert _delivery_keys(repo) == []         # the first key was rolled back too
