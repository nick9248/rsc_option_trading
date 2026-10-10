"""
Integration test: iter_new_row_batches against a real PostgreSQL server.

The unit tests use fakes, which cannot prove psycopg2's real server-side
(named) cursor behaviour: it only works inside a transaction, and it keeps
serving batches across fetchmany() calls. This test uses a TEMP table in the
local development database (port 5433), so nothing persistent is touched.
It is skipped when that database is not reachable.
"""

import psycopg2
import pytest

from scripts.sync_from_vps import LOCAL_CONN, iter_new_row_batches

TABLE = {
    "name": "sync_batch_probe",
    "watermark_col": "captured_at",
    "watermark_type": "timestamp",
    "conflict_target": None,
}
COLUMNS = ["captured_at", "value"]


@pytest.fixture
def connection_with_probe_table():
    try:
        connection = psycopg2.connect(connect_timeout=3, **LOCAL_CONN)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE sync_batch_probe (captured_at int, value text)")
        cursor.execute(
            "INSERT INTO sync_batch_probe SELECT g, 'v' || g FROM generate_series(1, 125) g"
        )
    yield connection
    connection.close()


def test_real_named_cursor_streams_all_rows_in_batches(connection_with_probe_table):
    batches = list(iter_new_row_batches(connection_with_probe_table, TABLE, COLUMNS, None, batch_size=50))

    assert [len(batch) for batch in batches] == [50, 50, 25]
    assert [row[0] for batch in batches for row in batch] == list(range(1, 126))
    assert connection_with_probe_table.autocommit is True


def test_real_named_cursor_applies_the_watermark_filter(connection_with_probe_table):
    batches = list(iter_new_row_batches(connection_with_probe_table, TABLE, COLUMNS, 100, batch_size=50))

    assert [row[0] for batch in batches for row in batch] == list(range(101, 126))
