"""
Unit tests for the database tab's SyncWorker (Wave G task G2-F fix 3).

Mirrors tests/unit/gui/test_on_chain_analysis_tab.py's pattern (mock the
service class inside the tab module, assert the worker calls it exactly
once and translates the result into Qt signals -- no business logic, no
direct DB access, in the GUI file itself).

Review finding this fix addresses: SyncWorker used to `import psycopg2`
and manage the SSH tunnel + two raw connections directly inside this GUI
file, with no try/finally, so a mid-loop failure leaked both connections
and the tunnel. That logic moved to
coding.service.database.vps_sync_service.VpsSyncService; SyncWorker now
only calls VpsSyncService().sync(...) and emits progress/finished signals.
"""
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from coding.gui.tabs import database_tab as tab_module
from coding.gui.tabs.database_tab import SyncWorker
from coding.service.database.vps_sync_service import VpsSyncResult


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestSyncWorkerCallsServiceOnce:
    def test_run_calls_vps_sync_service_exactly_once_and_emits_result(self, qapp):
        fake_result = VpsSyncResult(success=True, total_rows=42, errors=[], duration_seconds=1.5)

        with patch.object(tab_module, "VpsSyncService") as mock_service_cls:
            mock_service_cls.return_value.sync.return_value = fake_result

            worker = SyncWorker()
            finished_messages = []
            worker.finished.connect(lambda success, msg: finished_messages.append((success, msg)))
            worker.run()

        mock_service_cls.assert_called_once_with()
        mock_service_cls.return_value.sync.assert_called_once()
        # progress_callback must be wired to the worker's own progress signal.
        _, kwargs = mock_service_cls.return_value.sync.call_args
        assert kwargs["progress_callback"] == worker.progress.emit

        assert finished_messages == [(True, fake_result.summary_message)]

    def test_run_emits_failure_signal_when_service_raises(self, qapp):
        with patch.object(tab_module, "VpsSyncService") as mock_service_cls:
            mock_service_cls.return_value.sync.side_effect = RuntimeError("tunnel died")

            worker = SyncWorker()
            finished_messages = []
            worker.finished.connect(lambda success, msg: finished_messages.append((success, msg)))
            worker.run()

        assert finished_messages == [(False, "Sync failed: tunnel died")]

    def test_module_does_not_import_psycopg2_directly(self):
        """
        Review fix: database_tab.py previously did `import psycopg2` inside
        SyncWorker.run() and called psycopg2.connect() twice itself. After
        this fix, the GUI module must not reference psycopg2 at all -- all
        DB access lives in VpsSyncService.
        """
        assert not hasattr(tab_module, "psycopg2")
