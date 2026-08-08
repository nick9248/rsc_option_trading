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
