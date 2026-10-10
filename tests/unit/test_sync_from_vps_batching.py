"""
Tests for the batched (streaming) read in scripts/sync_from_vps.py.

The first full sync of a 16M-row table loaded every row into memory with
fetchall() (~19 GB as Python objects). iter_new_row_batches now reads through a
server-side cursor in fixed-size batches, and sync_table writes the whole table
in a single local transaction (commit once, or roll back on error).

No database or SSH tunnel is used: connections are small fakes.
"""

from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from scripts import sync_from_vps
from scripts.sync_from_vps import iter_new_row_batches, sync_table

TABLE = {
    "name": "snapshots",
    "watermark_col": "captured_at",
    "watermark_type": "timestamp",
    "conflict_target": None,
}
COLUMNS = ["captured_at", "value"]


class FakeNamedCursor:
    """Server-side cursor stand-in: serves rows via fetchmany, records the query."""

    def __init__(self, rows, fail_after_batches=None, close_error=None):
        self._rows = list(rows)
        self._close_error = close_error
        self._served = 0
        self._batches_served = 0
        self._fail_after_batches = fail_after_batches
        self.executed = []
        self.batch_sizes_requested = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchmany(self, size):
        self.batch_sizes_requested.append(size)
        if self._fail_after_batches is not None and self._batches_served >= self._fail_after_batches:
            raise RuntimeError("connection lost")
        chunk = self._rows[self._served:self._served + size]
        self._served += len(chunk)
        self._batches_served += 1
        return chunk

    def close(self):
        if self._close_error is not None:
            raise self._close_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeVpsConn:
    def __init__(self, named_cursor, rollback_error=None):
        self.autocommit = True
        self.rollback_error = rollback_error
        self.named_cursor = named_cursor
        self.named_cursor_names = []
        self.autocommit_while_streaming = []
        self.rollbacks = 0

    def cursor(self, name=None):
        if name is not None:
            self.named_cursor_names.append(name)
            self.autocommit_while_streaming.append(self.autocommit)
            return self.named_cursor
        plain = MagicMock()
        plain.fetchall.return_value = [("captured_at",), ("value",)]
        return plain

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error


def _rows(n):
    return [(i, f"v{i}") for i in range(n)]


class TestIterNewRowBatches:
    def test_yields_all_rows_in_order_in_bounded_batches(self):
        cursor = FakeNamedCursor(_rows(25))
        conn = FakeVpsConn(cursor)

        batches = list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

        assert [len(b) for b in batches] == [10, 10, 5]
        assert [row for batch in batches for row in batch] == _rows(25)
        assert set(cursor.batch_sizes_requested) == {10}

    def test_uses_a_named_server_side_cursor_outside_autocommit(self):
        conn = FakeVpsConn(FakeNamedCursor(_rows(3)))

        list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

        assert conn.named_cursor_names == ["sync_snapshots"]
        assert conn.autocommit_while_streaming == [False]

    def test_restores_autocommit_and_rolls_back_the_read_transaction(self):
        conn = FakeVpsConn(FakeNamedCursor(_rows(3)))

        list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

        assert conn.autocommit is True
        assert conn.rollbacks == 1

    def test_restores_autocommit_when_the_read_fails(self):
        conn = FakeVpsConn(FakeNamedCursor(_rows(30), fail_after_batches=1))

        with pytest.raises(RuntimeError):
            list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

        assert conn.autocommit is True

    def test_broken_connection_does_not_mask_the_original_error(self):
        """A dropped tunnel makes rollback() raise too; the caller must still
        see the original read error, not the cleanup error."""
        conn = FakeVpsConn(
            FakeNamedCursor(_rows(30), fail_after_batches=1),
            rollback_error=psycopg2.OperationalError("connection already closed"),
        )

        with pytest.raises(RuntimeError, match="connection lost"):
            list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

    def test_cursor_close_failure_does_not_mask_the_original_error(self):
        conn = FakeVpsConn(FakeNamedCursor(
            _rows(30), fail_after_batches=1,
            close_error=psycopg2.InterfaceError("connection already closed"),
        ))

        with pytest.raises(RuntimeError, match="connection lost"):
            list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

    def test_autocommit_is_restored_even_when_rollback_fails(self):
        conn = FakeVpsConn(
            FakeNamedCursor(_rows(3)),
            rollback_error=psycopg2.OperationalError("rollback failed"),
        )

        list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

        assert conn.autocommit is True

    def test_full_read_has_no_watermark_filter(self):
        cursor = FakeNamedCursor([])
        conn = FakeVpsConn(cursor)

        list(iter_new_row_batches(conn, TABLE, COLUMNS, None, batch_size=10))

        query, params = cursor.executed[0]
        assert "WHERE" not in query
        assert "ORDER BY captured_at" in query
        assert params is None

    def test_incremental_read_filters_on_the_local_watermark(self):
        cursor = FakeNamedCursor([])
        conn = FakeVpsConn(cursor)

        list(iter_new_row_batches(conn, TABLE, COLUMNS, "2026-01-01", batch_size=10))

        query, params = cursor.executed[0]
        assert "WHERE captured_at > %s" in query
        assert params == ("2026-01-01",)


class TestSyncTable:
    def _local_conn(self):
        local = MagicMock()
        local.cursor.return_value.fetchone.return_value = (None,)  # empty local table
        return local

    def test_inserts_every_batch_and_commits_once_at_the_end(self, monkeypatch):
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)
        vps = FakeVpsConn(FakeNamedCursor(_rows(25)))
        local = self._local_conn()

        with patch.object(sync_from_vps, "execute_values") as mock_execute_values:
            count, message = sync_table(vps, local, TABLE)

        assert count == 25
        assert message == "+25 rows"
        assert [len(call.args[2]) for call in mock_execute_values.call_args_list] == [10, 10, 5]
        local.commit.assert_called_once()
        local.rollback.assert_not_called()

    def test_empty_source_reports_up_to_date_without_committing(self):
        vps = FakeVpsConn(FakeNamedCursor([]))
        local = self._local_conn()

        with patch.object(sync_from_vps, "execute_values"):
            count, message = sync_table(vps, local, TABLE)

        assert (count, message) == (0, "up to date")
        local.commit.assert_not_called()

    def test_up_to_date_ends_the_local_read_transaction(self):
        """The watermark MAX() query opens a transaction; an early return must
        not leave it idle-in-transaction for the rest of the run."""
        vps = FakeVpsConn(FakeNamedCursor([]))
        local = self._local_conn()

        with patch.object(sync_from_vps, "execute_values"):
            sync_table(vps, local, TABLE)

        local.rollback.assert_called_once()

    def test_a_dead_local_connection_still_returns_an_error_tuple(self, monkeypatch):
        """rollback() on an already-closed local connection must not escape
        sync_table; the caller's per-table loop relies on the (-1, msg) return."""
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)
        vps = FakeVpsConn(FakeNamedCursor(_rows(30), fail_after_batches=1))
        local = self._local_conn()
        local.rollback.side_effect = psycopg2.InterfaceError("connection already closed")

        with patch.object(sync_from_vps, "execute_values"):
            count, message = sync_table(vps, local, TABLE)

        assert count == -1
        assert message.startswith("ERROR")

    def test_cursors_are_closed_on_success_and_on_error(self, monkeypatch):
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)
        for vps in (
            FakeVpsConn(FakeNamedCursor(_rows(15))),
            FakeVpsConn(FakeNamedCursor(_rows(30), fail_after_batches=1)),
        ):
            local = self._local_conn()
            with patch.object(sync_from_vps, "execute_values"):
                sync_table(vps, local, TABLE)
            local.cursor.return_value.close.assert_called_once()

    def test_read_failure_rolls_back_the_local_transaction_and_commits_nothing(self, monkeypatch):
        monkeypatch.setattr(sync_from_vps, "FETCH_BATCH_SIZE", 10)
        vps = FakeVpsConn(FakeNamedCursor(_rows(30), fail_after_batches=2))
        local = self._local_conn()

        with patch.object(sync_from_vps, "execute_values"):
            count, message = sync_table(vps, local, TABLE)

        assert count == -1
        assert message.startswith("ERROR")
        local.commit.assert_not_called()
        local.rollback.assert_called_once()
        assert vps.autocommit is True
