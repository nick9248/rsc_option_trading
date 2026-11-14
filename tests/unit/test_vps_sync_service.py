"""
Tests for coding.service.database.vps_sync_service.VpsSyncService
(Wave G task G2-F fix 3).

Extracted from coding/gui/tabs/database_tab.py's SyncWorker, which used to
import psycopg2 and manage the SSH tunnel + two raw DB connections directly
in the GUI file, with no try/finally -- a mid-loop failure leaked both
connections and the tunnel forever. These tests mock the underlying
scripts.sync_from_vps primitives (no real SSH/DB access) and prove:

  1. the happy path aggregates rows/errors and calls progress_callback
     per step, same behavior as the old inline GUI logic;
  2. the tunnel and both connections are ALWAYS closed via the service's
     try/finally, even when a step raises mid-run -- the resource-leak bug
     this fix targets.
"""
from unittest.mock import MagicMock, patch

import pytest

from coding.service.database.vps_sync_service import VpsSyncResult, VpsSyncService


def _table(name):
    return {"name": name, "watermark_col": "captured_at", "watermark_type": "timestamp", "conflict_target": None}


class TestVpsSyncServiceHappyPath:
    def test_sync_aggregates_rows_and_reports_progress(self):
        fake_tunnel = MagicMock()
        fake_vps_conn = MagicMock()
        fake_local_conn = MagicMock()
        tables = [_table("snapshots"), _table("hourly_snapshots")]

        with patch(
            "coding.service.database.vps_sync_service.open_ssh_tunnel", return_value=fake_tunnel
        ), patch(
            "coding.service.database.vps_sync_service.psycopg2.connect",
            side_effect=[fake_vps_conn, fake_local_conn],
        ), patch(
            "coding.service.database.vps_sync_service.SYNC_TABLES", tables
        ), patch(
            "coding.service.database.vps_sync_service.sync_table",
            side_effect=[(5, "+5 rows"), (0, "up to date")],
        ) as mock_sync_table, patch(
            "coding.service.database.vps_sync_service._pull_health_json"
        ) as mock_pull_health:
            progress_messages = []
            result = VpsSyncService().sync(progress_callback=progress_messages.append)

        assert isinstance(result, VpsSyncResult)
        assert result.success is True
        assert result.total_rows == 5
        assert result.errors == []
        assert mock_sync_table.call_count == 2
        mock_pull_health.assert_called_once()

        # Tunnel + both connections closed on the clean path too.
        fake_vps_conn.close.assert_called_once()
        fake_local_conn.close.assert_called_once()
        fake_tunnel.terminate.assert_called_once()

        # Progress reported for tunnel open/connect + each table.
        assert any("Opening SSH tunnel" in m for m in progress_messages)
        assert any("snapshots" in m for m in progress_messages)
        assert any("hourly_snapshots" in m for m in progress_messages)

    def test_sync_reports_per_table_errors_without_raising(self):
        """sync_table returning (-1, ...) is a per-table error, not an
        exception -- the service must surface it in result.errors, not raise."""
        tables = [_table("snapshots")]

        with patch(
            "coding.service.database.vps_sync_service.open_ssh_tunnel", return_value=MagicMock()
        ), patch(
            "coding.service.database.vps_sync_service.psycopg2.connect",
            side_effect=[MagicMock(), MagicMock()],
        ), patch(
            "coding.service.database.vps_sync_service.SYNC_TABLES", tables
        ), patch(
            "coding.service.database.vps_sync_service.sync_table",
            return_value=(-1, "ERROR: connection reset"),
        ), patch(
            "coding.service.database.vps_sync_service._pull_health_json"
        ):
            result = VpsSyncService().sync()

        assert result.success is False
        assert result.errors == ["snapshots"]
        assert "1 error(s)" in result.summary_message


class TestVpsSyncServiceCleanupOnFailure:
    """The resource-leak bug this fix targets: a mid-run exception must
    still close the tunnel and both connections."""

    def test_tunnel_and_connections_closed_when_sync_table_raises(self):
        fake_tunnel = MagicMock()
        fake_vps_conn = MagicMock()
        fake_local_conn = MagicMock()
        tables = [_table("snapshots")]

        with patch(
            "coding.service.database.vps_sync_service.open_ssh_tunnel", return_value=fake_tunnel
        ), patch(
            "coding.service.database.vps_sync_service.psycopg2.connect",
            side_effect=[fake_vps_conn, fake_local_conn],
        ), patch(
            "coding.service.database.vps_sync_service.SYNC_TABLES", tables
        ), patch(
            "coding.service.database.vps_sync_service.sync_table",
            side_effect=RuntimeError("boom"),
        ), pytest.raises(RuntimeError, match="boom"):
            VpsSyncService().sync()

        fake_vps_conn.close.assert_called_once()
        fake_local_conn.close.assert_called_once()
        fake_tunnel.terminate.assert_called_once()

    def test_tunnel_closed_when_local_connect_raises_after_vps_connect(self):
        """VPS connect succeeds, local connect fails -- the VPS connection
        and tunnel opened so far must still be cleaned up."""
        fake_tunnel = MagicMock()
        fake_vps_conn = MagicMock()

        with patch(
            "coding.service.database.vps_sync_service.open_ssh_tunnel", return_value=fake_tunnel
        ), patch(
            "coding.service.database.vps_sync_service.psycopg2.connect",
            side_effect=[fake_vps_conn, ConnectionError("local db down")],
        ), pytest.raises(ConnectionError, match="local db down"):
            VpsSyncService().sync()

        fake_vps_conn.close.assert_called_once()
        fake_tunnel.terminate.assert_called_once()

    def test_nothing_to_close_when_tunnel_itself_fails_to_open(self):
        """open_ssh_tunnel raising before anything is created must not
        error out of the finally block (no AttributeError on None)."""
        with patch(
            "coding.service.database.vps_sync_service.open_ssh_tunnel",
            side_effect=RuntimeError("tunnel failed to start"),
        ), pytest.raises(RuntimeError, match="tunnel failed to start"):
            VpsSyncService().sync()


# ---------------------------------------------------------------------------
# sync_if_behind / compare_watermarks
# ---------------------------------------------------------------------------
from datetime import datetime, timezone  # noqa: E402

from coding.service.database.vps_sync_service import (  # noqa: E402
    SyncCheckResult,
    WatermarkComparison,
    compare_watermarks,
)

MODULE = "coding.service.database.vps_sync_service"
EARLIER = datetime(2026, 11, 14, 9, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone.utc)


def _run_sync_if_behind(tables, vps_values, local_values, columns=None, sync_returns=None):
    """Run sync_if_behind with everything mocked; values map table name -> newest value or Exception."""
    fake_tunnel = MagicMock()
    fake_vps_conn = MagicMock()
    fake_local_conn = MagicMock()

    def fake_watermark(cursor, entry):
        values = vps_values if cursor is fake_vps_conn.cursor.return_value else local_values
        value = values[entry["name"]]
        if isinstance(value, Exception):
            raise value
        return value

    def fake_columns(cursor, name):
        return (columns or {}).get(name, ["captured_at"])

    names = [table["name"] for table in tables]
    with patch(f"{MODULE}.open_ssh_tunnel", return_value=fake_tunnel) as mock_tunnel, patch(
        f"{MODULE}.psycopg2.connect", side_effect=[fake_vps_conn, fake_local_conn]
    ), patch(f"{MODULE}.SYNC_TABLES", tables), patch(
        f"{MODULE}.get_columns", side_effect=fake_columns
    ), patch(f"{MODULE}.get_local_watermark", side_effect=fake_watermark), patch(
        f"{MODULE}.sync_table", side_effect=sync_returns or [(0, "up to date")] * len(tables)
    ) as mock_sync_table, patch(f"{MODULE}._pull_health_json"):
        messages = []
        result = VpsSyncService().sync_if_behind(names, progress_callback=messages.append)
    return result, messages, mock_tunnel, mock_sync_table, fake_tunnel, fake_vps_conn, fake_local_conn


class TestSyncIfBehind:
    def test_all_in_sync_does_not_sync_and_cleans_up(self):
        tables = [_table("snapshots"), _table("historical_trades")]
        values = {"snapshots": LATER, "historical_trades": LATER}
        result, _, _, mock_sync, tunnel, vps_conn, local_conn = _run_sync_if_behind(tables, values, values)
        assert result.sync_ran is False
        mock_sync.assert_not_called()
        assert "all up to date, no sync needed" in result.summary_message
        vps_conn.close.assert_called_once()
        local_conn.close.assert_called_once()
        tunnel.terminate.assert_called_once()

    def test_behind_table_triggers_full_sync_through_one_tunnel(self):
        tables = [_table("snapshots"), _table("historical_trades"), _table("other")]
        vps = {"snapshots": LATER, "historical_trades": LATER, "other": LATER}
        local = {"snapshots": EARLIER, "historical_trades": LATER, "other": LATER}
        result, messages, mock_tunnel, mock_sync, _, _, _ = _run_sync_if_behind(
            tables, vps, local, sync_returns=[(7, "+7"), (0, "ok"), (3, "+3")]
        )
        assert result.sync_ran is True
        assert result.tables_behind == ["snapshots"]
        assert mock_sync.call_count == len(tables)
        assert result.sync_result.total_rows == 10
        assert mock_tunnel.call_count == 1
        assert "snapshots behind" in result.summary_message
        assert "synced 10 rows" in result.summary_message
        assert any("(behind)" in m for m in messages)

    def test_local_empty_with_vps_rows_is_behind(self):
        tables = [_table("snapshots")]
        result, *_ = _run_sync_if_behind(tables, {"snapshots": LATER}, {"snapshots": None})
        assert result.tables_behind == ["snapshots"]
        assert result.sync_ran is True

    def test_both_empty_is_not_behind(self):
        tables = [_table("snapshots")]
        result, _, _, mock_sync, *_ = _run_sync_if_behind(tables, {"snapshots": None}, {"snapshots": None})
        assert result.tables_behind == []
        assert result.sync_ran is False
        mock_sync.assert_not_called()

    def test_comparison_error_marks_unknown_and_syncs(self):
        tables = [_table("snapshots"), _table("historical_trades")]
        vps = {"snapshots": RuntimeError("boom"), "historical_trades": LATER}
        local = {"snapshots": LATER, "historical_trades": LATER}
        result, _, _, mock_sync, _, _, local_conn = _run_sync_if_behind(tables, vps, local)
        assert result.tables_unknown == ["snapshots"]
        assert result.comparisons[1].error is None  # the other table was still compared
        assert result.sync_ran is True
        assert mock_sync.call_count == 2
        local_conn.cursor.return_value.connection.rollback.assert_called()
        assert "could not compare snapshots (RuntimeError: boom)" in result.summary_message

    def test_optional_table_missing_on_vps_does_not_trigger_sync(self):
        tables = [dict(_table("taker_flow_hourly"), optional=True)]
        result, _, _, mock_sync, *_ = _run_sync_if_behind(
            tables, {"taker_flow_hourly": None}, {"taker_flow_hourly": None}, columns={"taker_flow_hourly": []}
        )
        assert result.comparisons[0].missing_on_vps is True
        assert result.sync_ran is False
        mock_sync.assert_not_called()
        assert "taker_flow_hourly" in result.summary_message

    def test_tunnel_failure_propagates_and_nothing_is_opened(self):
        with patch(f"{MODULE}.open_ssh_tunnel", side_effect=RuntimeError("no tunnel")), patch(
            f"{MODULE}.psycopg2.connect"
        ) as mock_connect, pytest.raises(RuntimeError, match="no tunnel"):
            VpsSyncService().sync_if_behind()
        mock_connect.assert_not_called()

    def test_local_connect_failure_closes_vps_connection_and_tunnel(self):
        fake_tunnel, fake_vps_conn = MagicMock(), MagicMock()
        with patch(f"{MODULE}.open_ssh_tunnel", return_value=fake_tunnel), patch(
            f"{MODULE}.psycopg2.connect", side_effect=[fake_vps_conn, ConnectionError("down")]
        ), pytest.raises(ConnectionError):
            VpsSyncService().sync_if_behind()
        fake_vps_conn.close.assert_called_once()
        fake_tunnel.terminate.assert_called_once()


class TestCompareWatermarks:
    def test_unknown_table_name_raises_value_error(self):
        with patch(f"{MODULE}.SYNC_TABLES", [_table("snapshots")]), pytest.raises(ValueError):
            compare_watermarks(MagicMock(), MagicMock(), ["not_a_table"])

    def test_naive_versus_aware_datetime_sets_error_without_raising(self):
        naive = datetime(2026, 11, 14, 9, 0, 0)
        with patch(f"{MODULE}.SYNC_TABLES", [_table("snapshots")]), patch(
            f"{MODULE}.get_columns", return_value=["captured_at"]
        ), patch(f"{MODULE}.get_local_watermark", side_effect=[LATER, naive]):
            comparisons = compare_watermarks(MagicMock(), MagicMock(), ["snapshots"])
        assert comparisons[0].error.startswith("cannot compare")
        assert comparisons[0].unknown is True

    def test_missing_required_table_is_an_error(self):
        with patch(f"{MODULE}.SYNC_TABLES", [_table("snapshots")]), patch(
            f"{MODULE}.get_columns", return_value=[]
        ):
            comparisons = compare_watermarks(MagicMock(), MagicMock(), ["snapshots"])
        assert comparisons[0].error == "table not found on the VPS"
        assert comparisons[0].missing_on_vps is False

    def test_behind_property(self):
        assert WatermarkComparison("t", "c", LATER, EARLIER).behind is True
        assert WatermarkComparison("t", "c", EARLIER, LATER).behind is False
        assert WatermarkComparison("t", "c", LATER, None, error="x").behind is False
        assert SyncCheckResult([], False).tables_behind == []
