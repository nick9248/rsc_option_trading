"""
Unit tests for DatabaseRepository.save_daily_oi_snapshot's new
snapshot_hour_utc conflict-key column (institutional_metrics_spec.md
section 7(c) Migration M8, Task E4).

Prior to this task, daily_oi_snapshots was GUI-triggered only and its
ON CONFLICT (snapshot_date, currency, expiration, strike, option_type)
DO UPDATE meant the stored value for a given day was "whatever the last
GUI run of that day happened to capture" -- not a fixed, comparable daily
anchor for Task C8's FixedStrikeVolCalculator/get_chain_iv_at. Migration
023 adds snapshot_hour_utc (default 8, Deribit settlement convention) and
widens the unique key to include it, so the daemon's 08:00 UTC write can
never be silently overwritten by a later GUI run at a different hour of
the same day.

Mocked cursor only -- no live database (matches test_repository_save_
daily_oi_snapshot.py's established pattern). The live-DB confirmation that
the widened constraint actually persists two same-date/strike rows at
different hours without overwriting each other (and that migration 023
applies idempotently against the real schema) is covered by the manual
verification in task-E4-report.md, not by this file.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _capture_call(repo, **kwargs):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    defaults = dict(
        currency="BTC",
        expiration="31JUL26",
        instruments=[{"strike": 65000.0, "option_type": "C", "open_interest": 10.0, "mark_iv": 32.0}],
        underlying_price=65000.0,
    )
    defaults.update(kwargs)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_daily_oi_snapshot(**defaults)

    insert_args, _ = mock_cursor.executemany.call_args
    sql, rows = insert_args
    return sql, rows


class TestSnapshotHourUtcConflictKey:
    def test_insert_sql_conflict_key_includes_snapshot_hour_utc(self):
        """The ON CONFLICT target must be the new 6-column key -- this is
        what actually prevents a later run (GUI, different hour) from
        silently overwriting the daemon's 08:00 UTC anchor."""
        repo = _make_repo()
        sql, _ = _capture_call(repo)

        assert "ON CONFLICT (snapshot_date, snapshot_hour_utc, currency, expiration, strike, option_type)" in sql

    def test_insert_sql_inserts_snapshot_hour_utc_column(self):
        repo = _make_repo()
        sql, _ = _capture_call(repo)

        assert "snapshot_hour_utc" in sql.split("VALUES")[0]

    def test_default_snapshot_hour_utc_is_8(self):
        """No caller (GUI or daemon) passes snapshot_hour_utc=None; the
        default must resolve to Deribit's settlement hour so pre-existing
        GUI calls (which never pass this param) keep upserting the same
        anchor row the daemon writes."""
        repo = _make_repo()
        _, rows = _capture_call(repo)

        row = rows[0]
        assert 8 in row  # snapshot_hour_utc present somewhere in the tuple
        # Exact position: snapshot_date, snapshot_hour_utc, currency, ...
        assert row[1] == 8

    def test_explicit_snapshot_hour_utc_is_used(self):
        repo = _make_repo()
        _, rows = _capture_call(repo, snapshot_hour_utc=8)

        assert rows[0][1] == 8

    def test_snapshot_date_position_unchanged(self):
        """Regression guard: existing callers/tests (test_repository_save_
        daily_oi_snapshot.py, test_repository_oi_snapshot_date_sync.py)
        read rows[0][0] as snapshot_date -- snapshot_hour_utc must be
        inserted AFTER snapshot_date, not before it."""
        fixed_now = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
        repo = _make_repo()

        with patch(
            "coding.core.database.repository.datetime",
            _frozen_datetime(fixed_now),
        ):
            _, rows = _capture_call(repo)

        assert rows[0][0] == fixed_now.date()

    def test_two_calls_different_hours_produce_independent_row_tuples(self):
        """Two logically distinct anchor writes for the same date+strike at
        different hours must produce row tuples that differ only in the
        hour field -- the DB-level "both persist" guarantee comes from the
        widened UNIQUE constraint (migration 023), verified separately
        against the live schema; this test pins the Python-side contract
        that feeds it."""
        repo = _make_repo()

        _, rows_hour_8 = _capture_call(repo, snapshot_hour_utc=8)
        _, rows_hour_14 = _capture_call(repo, snapshot_hour_utc=14)

        row_8 = rows_hour_8[0]
        row_14 = rows_hour_14[0]

        assert row_8[1] == 8
        assert row_14[1] == 14
        # Every other field (date, currency, expiration, strike, option_type,
        # OI, mark_iv, underlying_price) is identical -- only the hour differs.
        assert row_8[0] == row_14[0]
        assert row_8[2:] == row_14[2:]


def _frozen_datetime(fixed_now):
    """Real ``datetime`` subclass whose ``now()`` returns ``fixed_now`` --
    matches test_repository_save_daily_oi_snapshot.py's established
    pattern (a bare MagicMock as ``datetime`` breaks the ``isinstance``
    check in ``save_daily_oi_snapshot``)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    return _Frozen
