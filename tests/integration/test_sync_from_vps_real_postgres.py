"""
Integration test: sync_table against real PostgreSQL servers.

Two throw-away databases on the local server (port 5433) stand in for the VPS
and the local copy, so the real psycopg2 behaviour is exercised (paging queries,
per-batch commits, ON CONFLICT, RETURNING). They are created per test and dropped
afterwards; nothing else is touched. Skipped when the local server is not
reachable or the user cannot create databases.

What this proves, on a table with NO conflict target (so duplicates would show):
  * the copy equals the source, including watermark groups larger than a page;
  * after a failure part-way through, what was committed is a COMPLETE prefix
    of watermark groups, and the next run finishes with no gap and no duplicate;
  * a further run is "up to date", and new source rows are picked up.
"""

import uuid
from collections import Counter
from unittest.mock import patch

import psycopg2
import pytest

from scripts import sync_from_vps
from scripts.sync_from_vps import LOCAL_CONN, sync_table

TABLE = {
    "name": "probe",
    "watermark_col": "captured_at",
    "watermark_type": "bigint",
    "conflict_target": None,
}
# Group sizes by watermark value: ties, singletons, and one group (30) bigger
# than the page size used below (10).
GROUP_SIZES = [3, 1, 4, 30, 2, 1, 5, 7, 1, 2]


def _admin_connection():
    if LOCAL_CONN["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    try:
        connection = psycopg2.connect(connect_timeout=3, **dict(LOCAL_CONN, database="postgres"))
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    connection.autocommit = True
    return connection


def _rows(group_sizes, start_watermark=1):
    rows = []
    for offset, size in enumerate(group_sizes):
        watermark = start_watermark + offset
        rows += [(watermark, f"w{watermark}-{i}") for i in range(size)]
    return rows


@pytest.fixture
def source_and_copy():
    suffix = uuid.uuid4().hex[:8]
    source_name, copy_name = f"sync_probe_src_{suffix}", f"sync_probe_dst_{suffix}"
    admin = _admin_connection()
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE {source_name}")
            cursor.execute(f"CREATE DATABASE {copy_name}")
    except psycopg2.Error as error:
        admin.close()
        pytest.skip(f"cannot create scratch databases: {error}")

    source = psycopg2.connect(**dict(LOCAL_CONN, database=source_name))
    source.autocommit = True  # the real VPS connection is autocommit too
    copy = psycopg2.connect(**dict(LOCAL_CONN, database=copy_name))
    for connection in (source, copy):
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE probe (id serial PRIMARY KEY, captured_at bigint, value text)")
            cursor.execute(
                "CREATE TABLE scans (id serial PRIMARY KEY, currency text, scan_time bigint, "
                "resolved_at bigint, pnl numeric, UNIQUE (currency, scan_time))"
            )
            cursor.execute(
                "CREATE TABLE candles (id serial PRIMARY KEY, asset text, bucket timestamptz, "
                "close numeric, UNIQUE (asset, bucket))"
            )
        connection.commit()
    try:
        yield source, copy
    finally:
        source.close()
        copy.close()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {source_name}")
            cursor.execute(f"DROP DATABASE IF EXISTS {copy_name}")
        admin.close()


def _insert_source(source, rows):
    with source.cursor() as cursor:
        cursor.executemany("INSERT INTO probe (captured_at, value) VALUES (%s, %s)", rows)


def _copy_rows(copy):
    with copy.cursor() as cursor:
        cursor.execute("SELECT captured_at, value FROM probe")
        return cursor.fetchall()


def test_copy_equals_source_including_groups_larger_than_a_page(source_and_copy, monkeypatch):
    source, copy = source_and_copy
    expected = _rows(GROUP_SIZES)
    _insert_source(source, expected)
    monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

    count, message = sync_table(source, copy, TABLE)

    assert (count, message) == (len(expected), f"+{len(expected)} rows")
    assert Counter(_copy_rows(copy)) == Counter(expected)


def test_interrupted_run_leaves_a_complete_prefix_and_the_next_run_finishes_cleanly(
    source_and_copy, monkeypatch
):
    source, copy = source_and_copy
    expected = _rows(GROUP_SIZES)
    _insert_source(source, expected)
    monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

    real_insert_rows = sync_from_vps.insert_rows
    calls = {"count": 0}

    def fail_on_third_batch(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("simulated crash mid-sync")
        return real_insert_rows(*args, **kwargs)

    with patch.object(sync_from_vps, "insert_rows", side_effect=fail_on_third_batch):
        count, message = sync_table(source, copy, TABLE)

    assert count == -1 and "simulated crash" in message and "already committed" in message
    partial = _copy_rows(copy)
    assert 0 < len(partial) < len(expected), "expected some, but not all, rows to be committed"
    # Complete prefix: every watermark value present in the copy has ALL its rows.
    partial_counts = Counter(row[0] for row in partial)
    expected_counts = Counter(row[0] for row in expected)
    assert all(partial_counts[w] == expected_counts[w] for w in partial_counts)
    assert max(partial_counts) == len(partial_counts), "committed groups must be a gap-free prefix"

    count, message = sync_table(source, copy, TABLE)  # resume

    assert count == len(expected) - len(partial)
    assert Counter(_copy_rows(copy)) == Counter(expected), "gap or duplicate after resume"


def test_second_run_is_up_to_date_and_new_source_rows_are_picked_up(source_and_copy, monkeypatch):
    source, copy = source_and_copy
    first = _rows(GROUP_SIZES)
    _insert_source(source, first)
    monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)
    sync_table(source, copy, TABLE)

    assert sync_table(source, copy, TABLE) == (0, "up to date")

    later = _rows([6, 2], start_watermark=len(GROUP_SIZES) + 1)
    _insert_source(source, later)
    count, _ = sync_table(source, copy, TABLE)

    assert count == len(later)
    assert Counter(_copy_rows(copy)) == Counter(first + later)


MUTABLE_TABLE = {
    "name": "scans",
    "watermark_col": "scan_time",
    "watermark_type": "bigint",
    "conflict_target": None,
    "upsert_key": ["currency", "scan_time"],
}


def _scans(copy):
    with copy.cursor() as cursor:
        cursor.execute("SELECT currency, scan_time, resolved_at, pnl FROM scans ORDER BY currency, scan_time")
        return cursor.fetchall()


def test_rows_updated_on_the_source_after_the_first_sync_reach_the_copy(source_and_copy, monkeypatch):
    """Scan-history rows get resolved_at and pnl filled in LATER on the VPS; a
    copy-once sync would leave them unresolved locally forever."""
    source, copy = source_and_copy
    monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)
    with source.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO scans (currency, scan_time) VALUES (%s, %s)",
            [(currency, scan_time) for scan_time in range(1, 26) for currency in ("BTC", "ETH")],
        )

    count, _ = sync_table(source, copy, MUTABLE_TABLE)
    assert count == 50
    assert all(row[2] is None for row in _scans(copy))

    with source.cursor() as cursor:  # the VPS resolves some rows and adds new ones
        cursor.execute("UPDATE scans SET resolved_at = 100, pnl = 12.5 WHERE scan_time <= 5")
        cursor.executemany("INSERT INTO scans (currency, scan_time) VALUES (%s, %s)", [("BTC", 26), ("ETH", 26)])

    count, message = sync_table(source, copy, MUTABLE_TABLE)

    assert count == 12, "10 updated rows + 2 new rows, and nothing else rewritten"
    assert "new or updated (52 rows checked)" in message
    copied = _scans(copy)
    assert len(copied) == 52
    assert all(row[2] == 100 and row[3] == 12.5 for row in copied if row[1] <= 5)
    assert all(row[2] is None for row in copied if row[1] > 5)

    assert sync_table(source, copy, MUTABLE_TABLE) == (0, "up to date (52 rows checked)")


LOOKBACK_TABLE = {
    "name": "candles",
    "watermark_col": "bucket",
    "watermark_type": "timestamp",
    "conflict_target": None,
    "upsert_key": ["asset", "bucket"],
    "lookback_hours": 6,
}


def test_lookback_window_refreshes_recent_rows_and_leaves_older_history_alone(source_and_copy):
    """Corrections only ever touch the last hours, so only that window is re-read: a
    recent correction reaches the copy, an old value is NOT overwritten (this is what
    protects a repaired local history from an unrepaired source)."""
    from datetime import datetime, timedelta, timezone

    source, copy = source_and_copy
    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    hours = [start + timedelta(hours=offset) for offset in range(48)]
    with source.cursor() as cursor:
        cursor.executemany("INSERT INTO candles (asset, bucket, close) VALUES ('BTC', %s, 30)", [(h,) for h in hours])
    sync_table(source, copy, LOOKBACK_TABLE)

    with source.cursor() as cursor:
        cursor.execute("UPDATE candles SET close = 99 WHERE bucket = %s", (hours[2],))   # old: outside the window
        cursor.execute("UPDATE candles SET close = 77 WHERE bucket = %s", (hours[-2],))  # recent: inside the window
    count, message = sync_table(source, copy, LOOKBACK_TABLE)

    with copy.cursor() as cursor:
        cursor.execute("SELECT bucket, close FROM candles")
        copied = dict(cursor.fetchall())
    assert copied[hours[-2]] == 77, "a recent correction must reach the copy"
    assert copied[hours[2]] == 30, "history older than the window must not be overwritten"
    assert count == 1
    assert "6 rows checked" in message  # the 6 hours after (newest - 6 h)


def test_migration_027_guard_ignores_the_hour_still_forming_but_blocks_real_gaps(source_and_copy):
    """Migration 027 drops volatility_index_history only if every COMPLETED hour of it
    is in dvol_history. The hour still forming is ignored."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    _, scratch = source_and_copy
    migration = (Path(__file__).parents[2] / "migrations" / "027_drop_volatility_index_history.sql").read_text(encoding="utf8")
    current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    done_hour = current_hour - timedelta(hours=3)
    scratch.autocommit = True
    with scratch.cursor() as cursor:
        cursor.execute("CREATE TABLE dvol_history (id serial primary key, asset text, timestamp timestamptz, dvol_value numeric)")
        cursor.execute("CREATE TABLE volatility_index_history (id serial primary key, currency text, date timestamp, dvol numeric)")
        cursor.execute("INSERT INTO volatility_index_history (currency, date, dvol) VALUES ('BTC', %s, 30), ('BTC', %s, 31)",
                       (done_hour.replace(tzinfo=None), current_hour.replace(tzinfo=None)))

        # a completed hour is missing from dvol_history -> abort, nothing dropped
        with pytest.raises(psycopg2.Error, match="Migration 027 aborted"):
            cursor.execute(migration)
        cursor.execute("SELECT to_regclass('public.volatility_index_history') IS NOT NULL")
        assert cursor.fetchone()[0] is True

        # the completed hour is now present; the forming hour is still absent -> allowed
        cursor.execute("INSERT INTO dvol_history (asset, timestamp, dvol_value) VALUES ('BTC', %s, 30)", (done_hour,))
        cursor.execute(migration)
        cursor.execute("SELECT to_regclass('public.volatility_index_history') IS NULL")
        assert cursor.fetchone()[0] is True
