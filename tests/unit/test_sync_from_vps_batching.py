"""
Tests for the batched read and per-batch commit in scripts/sync_from_vps.py.

A first full sync used to load every row of a table with fetchall() (~19 GB for
the 16.9M-row snapshots table). The VPS is now read in short ORDER BY watermark
LIMIT n pages, a batch never splits a group of rows sharing one watermark value,
and each batch is committed locally as soon as it is inserted.

No database or SSH tunnel is used: the VPS is an in-memory fake that understands
the three query shapes iter_new_row_batches issues.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from scripts import sync_from_vps
from scripts.sync_from_vps import insert_rows, iter_new_row_batches, sync_table, upsert_rows

TABLE = {
    "name": "snapshots",
    "watermark_col": "captured_at",
    "watermark_type": "bigint",
    "conflict_target": None,
}
COLUMNS = ["captured_at", "value"]


class FakeCursor:
    def __init__(self, connection):
        self._connection = connection
        self._result = []
        self.closed = False

    def execute(self, query, params=None):
        self._connection.queries.append((query, params))
        rows = self._connection.rows
        if "information_schema" in query:
            self._result = [(name,) for name in COLUMNS]
        elif "captured_at = %s" in query:
            self._result = [r for r in rows if r[0] == params[0]]
        elif "captured_at > %s" in query:
            after, limit = params
            self._result = [r for r in rows if r[0] > after][:limit]
        else:
            self._result = rows[: params[0]]

    def fetchall(self):
        return list(self._result)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exception_info):
        return False


class FakeVpsConnection:
    """In-memory source table, kept sorted by the watermark column."""

    def __init__(self, rows, fail_on_query_number=None):
        self.rows = sorted(rows, key=lambda r: r[0])
        self.queries = []
        self.commits = 0
        self.rollbacks = 0
        self.autocommit = True
        self._fail_on = fail_on_query_number

    def cursor(self, name=None):
        assert name is None, "the sync must not use server-side cursors any more"
        if self._fail_on is not None and len(self.queries) + 1 == self._fail_on:
            raise psycopg2.OperationalError("connection lost")
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _rows_with_ties(group_sizes):
    """One row per (watermark, index): watermark g appears group_sizes[g-1] times."""
    rows = []
    for watermark, size in enumerate(group_sizes, start=1):
        rows += [(watermark, f"w{watermark}-{i}") for i in range(size)]
    return rows


def _batches(connection, local_max=None, batch_size=10):
    return list(iter_new_row_batches(connection, TABLE, COLUMNS, local_max, batch_size))


class TestIterNewRowBatches:
    def test_all_rows_come_back_in_watermark_order(self):
        rows = _rows_with_ties([3, 1, 4, 1, 5, 9, 2, 6, 5, 3])
        batches = _batches(FakeVpsConnection(rows))

        flattened = [row for batch in batches for row in batch]
        assert sorted(flattened) == sorted(rows)
        assert [row[0] for row in flattened] == sorted(row[0] for row in rows)

    def test_a_watermark_group_is_never_split_across_batches(self):
        batches = _batches(FakeVpsConnection(_rows_with_ties([3, 1, 4, 1, 5, 9, 2, 6, 5, 3])))

        seen = [{row[0] for row in batch} for batch in batches]
        for index, watermarks in enumerate(seen):
            for later in seen[index + 1:]:
                assert not watermarks & later, "a watermark value appears in two batches"

    def test_a_group_larger_than_the_page_is_returned_whole(self):
        batches = _batches(FakeVpsConnection(_rows_with_ties([2, 25, 2])), batch_size=10)

        sizes_by_watermark = {}
        for batch in batches:
            for row in batch:
                sizes_by_watermark[row[0]] = sizes_by_watermark.get(row[0], 0) + 1
        assert sizes_by_watermark == {1: 2, 2: 25, 3: 2}
        assert any(len(batch) == 25 for batch in batches)

    def test_a_group_above_the_safety_cap_stops_the_sync_with_an_error(self, monkeypatch):
        monkeypatch.setattr(sync_from_vps, "MAX_WATERMARK_GROUP_ROWS", 20)
        connection = FakeVpsConnection(_rows_with_ties([2, 25, 2]))

        with pytest.raises(RuntimeError, match="refusing to load"):
            _batches(connection, batch_size=10)

    def test_a_failed_read_rolls_back_the_vps_connection(self):
        connection = FakeVpsConnection(_rows_with_ties([5, 5, 5, 5]), fail_on_query_number=2)

        with pytest.raises(psycopg2.OperationalError):
            _batches(connection, batch_size=5)

        assert connection.rollbacks == 1

    def test_an_exactly_full_last_page_loses_nothing(self):
        rows = _rows_with_ties([5, 5])  # two full pages of 5, then an empty page
        flattened = [row for batch in _batches(FakeVpsConnection(rows), batch_size=5) for row in batch]

        assert sorted(flattened) == sorted(rows)

    def test_incremental_read_returns_only_rows_after_the_local_watermark(self):
        rows = _rows_with_ties([3, 3, 3, 3])
        flattened = [row for batch in _batches(FakeVpsConnection(rows), local_max=2) for row in batch]

        assert {row[0] for row in flattened} == {3, 4}

    def test_empty_source_yields_nothing(self):
        assert _batches(FakeVpsConnection([])) == []

    def test_every_page_is_a_bounded_query_and_ends_its_read_transaction(self):
        connection = FakeVpsConnection(_rows_with_ties([4, 4, 4, 4, 4, 4]))
        _batches(connection, batch_size=5)

        for query, _ in connection.queries:
            assert "LIMIT %s" in query or "captured_at = %s" in query
        assert connection.commits == len(connection.queries)

    def test_first_read_skips_null_watermarks(self):
        connection = FakeVpsConnection([])
        _batches(connection)

        assert "captured_at IS NOT NULL" in connection.queries[0][0]

    def test_a_read_failure_propagates(self):
        connection = FakeVpsConnection(_rows_with_ties([5, 5, 5, 5]), fail_on_query_number=2)

        with pytest.raises(psycopg2.OperationalError, match="connection lost"):
            _batches(connection, batch_size=5)


class TestInsertRows:
    def test_returns_the_number_of_rows_actually_inserted(self):
        # RETURNING 1 yields one row per inserted row, none for ON CONFLICT skips
        with patch.object(sync_from_vps, "execute_values", return_value=[(1,), (1,)]) as mock_execute_values:
            inserted = insert_rows(MagicMock(), TABLE, COLUMNS, [(1, "a"), (2, "b"), (3, "c")])

        assert inserted == 2
        assert mock_execute_values.call_args.kwargs == {"fetch": True}
        assert mock_execute_values.call_args.args[1].endswith("RETURNING 1")

    def test_empty_input_inserts_nothing(self):
        assert insert_rows(MagicMock(), TABLE, COLUMNS, []) == 0


MUTABLE_TABLE = {
    "name": "straddle_scan_history",
    "watermark_col": "captured_at",
    "watermark_type": "bigint",
    "conflict_target": None,
    "upsert_key": ["captured_at"],
}


class TestUpsertRows:
    def test_updates_only_rows_that_differ_and_counts_only_those(self):
        with patch.object(sync_from_vps, "execute_values", return_value=[(1,)]) as mock_execute_values:
            changed = upsert_rows(MagicMock(), MUTABLE_TABLE, COLUMNS, [(1, "a"), (2, "b")])

        sql = mock_execute_values.call_args.args[1]
        assert changed == 1
        assert "ON CONFLICT (captured_at) DO UPDATE SET value = EXCLUDED.value" in sql
        assert "WHERE (t.value) IS DISTINCT FROM (EXCLUDED.value)" in sql
        assert sql.endswith("RETURNING 1")
        assert "INSERT INTO straddle_scan_history AS t" in sql
        assert mock_execute_values.call_args.kwargs == {"fetch": True}

    def test_empty_input_writes_nothing(self):
        assert upsert_rows(MagicMock(), MUTABLE_TABLE, COLUMNS, []) == 0

    def test_a_key_that_covers_every_column_is_rejected_instead_of_generating_invalid_sql(self):
        table = dict(MUTABLE_TABLE, upsert_key=["captured_at", "value"])

        with pytest.raises(ValueError, match="nothing to update"):
            upsert_rows(MagicMock(), table, COLUMNS, [(1, "a")])


class TestSyncTable:
    def _local_conn(self):
        local = MagicMock()
        local.cursor.return_value.fetchone.return_value = (None,)  # empty local table
        return local

    def _run(self, vps, local, monkeypatch, batch_size=10):
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", batch_size)
        # Pretend every row is inserted: count = len(rows) per batch
        def fake_execute_values(_cursor, _sql, rows, **_kwargs):
            return [(1,)] * len(rows)

        with patch.object(sync_from_vps, "execute_values", side_effect=fake_execute_values):
            return sync_table(vps, local, TABLE)

    def test_commits_after_every_batch(self, monkeypatch):
        vps = FakeVpsConnection(_rows_with_ties([4, 4, 4, 4, 4, 4]))
        local = self._local_conn()

        count, message = self._run(vps, local, monkeypatch)

        assert count == 24
        assert message == "+24 rows"
        assert local.commit.call_count >= 2  # several batches, one commit each

    def test_rows_that_were_fetched_but_already_present_are_not_reported_as_up_to_date(self, monkeypatch):
        vps = FakeVpsConnection(_rows_with_ties([4, 4, 4]))
        local = self._local_conn()
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

        # ON CONFLICT DO NOTHING skipped everything: RETURNING gives back no rows
        with patch.object(sync_from_vps, "execute_values", return_value=[]):
            count, message = sync_table(vps, local, TABLE)

        assert count == 0
        assert message == "+0 rows (12 already present)"

    def test_mutable_table_is_re_read_in_full_even_when_the_local_table_has_rows(self, monkeypatch):
        """Rows change on the VPS after insertion, so the local watermark must not
        limit the read: the page queries have no "watermark > value" filter."""
        vps = FakeVpsConnection(_rows_with_ties([4, 4, 4]))
        local = self._local_conn()
        local.cursor.return_value.fetchone.return_value = (999,)  # local table already has data
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

        with patch.object(sync_from_vps, "execute_values", return_value=[(1,)] * 3):
            count, message = sync_table(vps, local, MUTABLE_TABLE)

        page_queries = [(query, params) for query, params in vps.queries if "information_schema" not in query]
        first_query, _ = page_queries[0]
        assert "captured_at > %s" not in first_query, "the first page must start at the beginning of the table"
        assert all(999 not in (params or ()) for _, params in page_queries), "the local watermark leaked into a query"
        assert count > 0
        assert "new or updated (12 rows checked)" in message

    def test_lookback_table_re_reads_only_the_window_before_the_newest_local_row(self, monkeypatch):
        newest_local = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
        table = dict(MUTABLE_TABLE, lookback_hours=72)
        vps = FakeVpsConnection(_rows_with_ties([2, 2]))
        local = self._local_conn()
        local.cursor.return_value.fetchone.return_value = (newest_local,)
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

        with patch.object(sync_from_vps, "execute_values", return_value=[]):
            sync_table(vps, local, table)

        page_queries = [(query, params) for query, params in vps.queries if "information_schema" not in query]
        first_query, first_params = page_queries[0]
        assert "captured_at > %s" in first_query
        assert first_params[0] == newest_local - timedelta(hours=72)

    def test_lookback_table_with_an_empty_local_table_reads_everything(self, monkeypatch):
        table = dict(MUTABLE_TABLE, lookback_hours=72)
        vps = FakeVpsConnection(_rows_with_ties([2, 2]))
        local = self._local_conn()  # MAX() returns None: nothing copied yet
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

        with patch.object(sync_from_vps, "execute_values", return_value=[(1,)] * 4):
            count, _ = sync_table(vps, local, table)

        first_query, _ = [(q, p) for q, p in vps.queries if "information_schema" not in q][0]
        assert "captured_at > %s" not in first_query
        assert count > 0

    def test_mutable_table_with_no_changes_reports_up_to_date_with_the_rows_checked(self, monkeypatch):
        vps = FakeVpsConnection(_rows_with_ties([4, 4, 4]))
        local = self._local_conn()
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)

        with patch.object(sync_from_vps, "execute_values", return_value=[]):
            count, message = sync_table(vps, local, MUTABLE_TABLE)

        assert (count, message) == (0, "up to date (12 rows checked)")

    def test_empty_source_reports_up_to_date_and_ends_the_local_read_transaction(self, monkeypatch):
        local = self._local_conn()

        count, message = self._run(FakeVpsConnection([]), local, monkeypatch)

        assert (count, message) == (0, "up to date")
        local.commit.assert_not_called()
        local.rollback.assert_called_once()

    def test_failure_keeps_committed_batches_and_reports_the_resume_point(self, monkeypatch):
        # query 1 = column list, 2 = first page (commits), 3 = second page (fails)
        vps = FakeVpsConnection(_rows_with_ties([4, 4, 4, 4, 4, 4]), fail_on_query_number=3)
        local = self._local_conn()

        count, message = self._run(vps, local, monkeypatch)

        assert count == -1
        assert message.startswith("ERROR")
        assert "already committed" in message
        assert local.commit.call_count >= 1  # earlier batches stay committed
        local.rollback.assert_called()

    def test_a_dead_local_connection_still_returns_an_error_tuple(self, monkeypatch):
        """rollback() on an already-closed local connection must not escape
        sync_table; the caller's per-table loop relies on the (-1, msg) return."""
        vps = FakeVpsConnection(_rows_with_ties([4, 4, 4]), fail_on_query_number=2)
        local = self._local_conn()
        local.rollback.side_effect = psycopg2.InterfaceError("connection already closed")

        count, message = self._run(vps, local, monkeypatch, batch_size=5)

        assert count == -1
        assert message.startswith("ERROR")

    def test_cursors_are_closed_on_success_and_on_error(self, monkeypatch):
        for vps in (
            FakeVpsConnection(_rows_with_ties([4, 4])),
            FakeVpsConnection(_rows_with_ties([4, 4, 4]), fail_on_query_number=3),
        ):
            local = self._local_conn()
            self._run(vps, local, monkeypatch, batch_size=5)
            local.cursor.return_value.close.assert_called_once()
