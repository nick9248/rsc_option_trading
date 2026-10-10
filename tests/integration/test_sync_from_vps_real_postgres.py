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
    calls = {"n": 0}

    def fail_on_third_batch(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
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
