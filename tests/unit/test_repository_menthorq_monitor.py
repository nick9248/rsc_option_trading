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
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record, make_record_without_levels

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


def _has_computed_run(repo, mode):
    """A computed run (with levels) of ``mode`` is stored at BASE_TIME (BTC, monthly, index)."""
    stored = repo.get_menthorq_level_snapshot("BTC", mode, "monthly", "index", BASE_TIME)
    return stored is not None and stored.has_levels


def test_duplicate_run_is_kept_once(repo):
    assert repo.save_menthorq_level_snapshot(make_record()) is not None
    assert repo.save_menthorq_level_snapshot(make_record(spot=1.0)) is None
    assert _has_computed_run(repo, "live")
    assert not _has_computed_run(repo, "daily_close")


def _row_count_and_spot(repo):
    with repo._db_cursor() as cursor:
        cursor.execute("SELECT count(*), max(spot), max(computed_at) FROM menthorq_level_snapshots")
        return cursor.fetchone()


def test_a_not_computed_row_is_replaced_by_a_computed_one_and_never_the_reverse(repo):
    not_computed = make_record_without_levels(spot=None, not_computed_reasons=["no snapshot"])
    first_id = repo.save_menthorq_level_snapshot(not_computed)
    assert isinstance(first_id, int)
    assert not _has_computed_run(repo, "live")

    # a second not-computed attempt does not rewrite the not-computed row
    assert repo.save_menthorq_level_snapshot(make_record_without_levels(
        spot=None, not_computed_reasons=["other"], computed_at_utc=BASE_TIME + timedelta(hours=1))) is None

    later = BASE_TIME + timedelta(hours=2)
    assert repo.save_menthorq_level_snapshot(make_record(computed_at_utc=later)) == first_id   # replaced in place
    assert _has_computed_run(repo, "live")
    loaded = repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "monthly", "index", BASE_TIME)
    assert (loaded.spot, loaded.hvl, loaded.not_computed_reasons) == (100000.0, 95000.0, [])
    assert loaded.computed_at_utc == later     # the sync watermark moves with the replacement

    # a computed row is never overwritten, neither by a computed nor by a not-computed record
    assert repo.save_menthorq_level_snapshot(make_record(spot=1.0)) is None
    assert repo.save_menthorq_level_snapshot(not_computed) is None
    count, spot, computed_at = _row_count_and_spot(repo)
    assert (count, float(spot), computed_at.astimezone(timezone.utc)) == (1, 100000.0, later)


def test_lookup_is_point_in_time_and_skips_not_computed_rows(repo):
    for hours_back, spot in ((0, 100000.0), (1, 99000.0), (26, 97000.0)):
        repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=hours_back), spot=spot))
    # -2 h: inputs loaded (spot set) but no level computed -> not computed, skipped
    repo.save_menthorq_level_snapshot(make_record_without_levels(as_of=BASE_TIME - timedelta(hours=2), spot=98000.0))
    repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME + timedelta(hours=1), spot=1.0))  # later row

    at = repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "monthly", "index", BASE_TIME)
    assert at.spot == 100000.0                           # the later row is never returned
    before = repo.get_menthorq_level_snapshot_at_or_before(
        "BTC", "live", "monthly", "index", BASE_TIME, strictly_before=True)
    assert before.spot == 99000.0
    skipped = repo.get_menthorq_level_snapshot_at_or_before(
        "BTC", "live", "monthly", "index", BASE_TIME - timedelta(hours=2))
    assert skipped.spot == 97000.0                       # the row without levels at -2 h is skipped
    windowed = repo.get_menthorq_level_snapshot_at_or_before(
        "BTC", "live", "monthly", "index", BASE_TIME - timedelta(hours=24),
        earliest=BASE_TIME - timedelta(hours=25))
    assert windowed is None                              # -26 h is outside the window
    assert repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "all", "index", BASE_TIME) is None


def test_snapshots_between_are_computed_rows_in_the_half_open_window_oldest_first(repo):
    for hours_back, spot in ((0, 100000.0), (1, 99000.0), (26, 97000.0), (31, 96000.0)):
        repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=hours_back), spot=spot))
    repo.save_menthorq_level_snapshot(make_record_without_levels(as_of=BASE_TIME - timedelta(hours=2)))
    # [-30 h, 0 h): -26 h and -1 h; the latest (0 h) is excluded, -31 h too old, -2 h not computed
    records = repo.get_menthorq_level_snapshots_between(
        "BTC", "live", "monthly", "index", BASE_TIME - timedelta(hours=30), BASE_TIME)
    assert [record.spot for record in records] == [97000.0, 99000.0]
    assert records[0].as_of_utc.tzinfo == timezone.utc
    assert repo.get_menthorq_level_snapshots_between(
        "BTC", "live", "all", "index", BASE_TIME - timedelta(hours=30), BASE_TIME) == []


def test_computed_run_instants_between_leave_out_not_computed_rows_and_other_keys(repo):
    repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=4)))
    repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=1)))
    repo.save_menthorq_level_snapshot(make_record_without_levels(as_of=BASE_TIME - timedelta(hours=3)))
    repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME))                     # end: excluded
    repo.save_menthorq_level_snapshot(make_record(as_of=BASE_TIME - timedelta(hours=2),
                                                  expiry_universe="all"))               # other key
    instants = repo.get_computed_menthorq_level_run_instants_between(
        "BTC", "live", "monthly", "index", BASE_TIME - timedelta(hours=5), BASE_TIME)
    assert instants == [BASE_TIME - timedelta(hours=4), BASE_TIME - timedelta(hours=1)]
    assert instants[0].tzinfo == timezone.utc


def test_a_row_with_spot_but_no_levels_counts_as_not_computed(repo):
    # spot is set (inputs loaded) but CR, PS, HVL and total net GEX are all None
    without_levels = make_record_without_levels(spot=100000.0)
    first_id = repo.save_menthorq_level_snapshot(without_levels)
    assert isinstance(first_id, int)
    assert not _has_computed_run(repo, "live")
    assert repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "monthly", "index", BASE_TIME) is None
    latest_run = repo.get_latest_menthorq_level_run("BTC", "live", "monthly", "index")
    assert latest_run is not None and latest_run.spot == 100000.0 and not latest_run.has_levels

    # a computed run (here only total net GEX set) replaces it in place
    later = BASE_TIME + timedelta(hours=1)
    only_net_gex = make_record_without_levels(total_net_gex=-2e6, computed_at_utc=later,
                                              not_computed_reasons=[])
    assert repo.save_menthorq_level_snapshot(only_net_gex) == first_id
    assert _has_computed_run(repo, "live")
    # ... and a row with one level is never overwritten again
    assert repo.save_menthorq_level_snapshot(make_record(computed_at_utc=later + timedelta(hours=1))) is None
    loaded = repo.get_menthorq_level_snapshot_at_or_before("BTC", "live", "monthly", "index", BASE_TIME)
    assert (loaded.total_net_gex, loaded.hvl, loaded.computed_at_utc) == (-2e6, None, later)


def test_invalid_mode_is_rejected_by_the_table(repo):
    with pytest.raises(psycopg2.errors.CheckViolation):
        repo.save_menthorq_level_snapshot(make_record(mode="hourly"))


def test_alert_deliveries_last_time(repo):
    assert repo.get_last_menthorq_alert_delivery("BTC", "hvl_move_24h") is None
    repo.save_menthorq_alert_deliveries("BTC", ["hvl_move_24h"], BASE_TIME - timedelta(hours=3), "a", 7)
    repo.save_menthorq_alert_deliveries("BTC", ["hvl_move_24h"], BASE_TIME, "b")
    repo.save_menthorq_alert_deliveries("ETH", ["hvl_move_24h"], BASE_TIME + timedelta(hours=1), "c")
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


def test_get_level_snapshot_by_key_returns_computed_and_not_computed_rows(repo):
    assert repo.get_menthorq_level_snapshot("BTC", "live", "monthly", "index", BASE_TIME) is None
    repo.save_menthorq_level_snapshot(make_record_without_levels(spot=None, snapshot_captured_at_utc=None))
    stored = repo.get_menthorq_level_snapshot("BTC", "live", "monthly", "index", BASE_TIME)
    assert stored is not None and not stored.has_levels and stored.snapshot_captured_at_utc is None
    assert repo.get_menthorq_level_snapshot("BTC", "daily_close", "monthly", "index", BASE_TIME) is None
    assert repo.get_menthorq_level_snapshot(
        "BTC", "live", "monthly", "index", BASE_TIME + timedelta(minutes=1)) is None
