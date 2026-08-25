"""
Database tab for syncing collected data from the VPS collection daemon.

The VPS daemon (ProspectiveCollector) is the single source of truth for data
collection - it runs hourly and writes directly to the VPS database. This tab
only pulls that data down to the local database for analysis/GUI use, and
offers a shortcut to browse any historical chart artifacts on disk.

This is a thin GUI layer - all sync business logic (SSH tunnel, raw DB
connections, table-sync loop) lives in
coding.service.database.vps_sync_service.VpsSyncService, which itself
reuses scripts/sync_from_vps.py's mechanics rather than duplicating them.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSizePolicy,
)
from PySide6.QtCore import QThread, Signal

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.theme.colors import Colors
from coding.service.database.vps_sync_service import VpsSyncService

logger = logging.getLogger(__name__)


class SyncWorker(QThread):
    """
    Worker thread for syncing data from VPS.

    Wave G task G2-F fix 3: this used to import psycopg2 and manage raw
    connections/an SSH tunnel directly in this GUI file (with no
    try/finally, so a mid-loop failure leaked both connections and the
    tunnel). All of that now lives in VpsSyncService.sync() -- this class
    only calls it and translates the result into Qt signals.
    """

    progress = Signal(str)
    finished = Signal(bool, str)  # (success, summary_message)

    def run(self) -> None:
        """Run VPS sync via VpsSyncService, emitting Qt signals for progress/completion."""
        try:
            result = VpsSyncService().sync(progress_callback=self.progress.emit)
            self.finished.emit(result.success, result.summary_message)
        except Exception as e:
            self.finished.emit(False, f"Sync failed: {e}")


class DatabaseTab(QWidget):
    """
    Tab widget for syncing local DB state from the VPS collection daemon.

    This is a thin GUI layer - all sync logic is delegated to
    scripts/sync_from_vps.py.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """Initialize Database tab."""
        super().__init__(parent)
        self._sync_worker: Optional[SyncWorker] = None

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Header
        header = QLabel("Database Sync")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(header)

        subheader = QLabel(
            "Data collection runs on the VPS daemon (ProspectiveCollector), every hour. "
            "Use Sync to pull the latest rows down to the local database."
        )
        subheader.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 12px;")
        subheader.setWordWrap(True)
        main_layout.addWidget(subheader)

        # Controls row
        controls_frame = QFrame()
        controls_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        controls_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(12, 8, 12, 8)

        controls_layout.addStretch()

        # Open charts button (browse historical chart artifacts on disk)
        self.open_charts_btn = QPushButton("Open Charts Folder")
        self.open_charts_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                padding: 6px 12px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.open_charts_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.open_charts_btn)

        # Sync from VPS button
        self.sync_vps_btn = QPushButton("Sync from VPS")
        self.sync_vps_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.BACKGROUND_PRIMARY};
                border: none;
                padding: 6px 16px;
                border-radius: 6px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {Colors.ACCENT_HOVER};
            }}
            QPushButton:disabled {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_MUTED};
            }}
        """)
        self.sync_vps_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.sync_vps_btn)

        main_layout.addWidget(controls_frame)

        # Log viewer
        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        log_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(100)
        self.log_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(self.log_viewer, 1)

    def _setup_logging(self) -> None:
        """Set up logging to GUI."""
        self.gui_handler = GuiLogHandler(self.log_viewer)
        self.gui_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)-8s] %(message)s", datefmt="%H:%M:%S")
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(self.gui_handler)

    def _connect_signals(self) -> None:
        """Connect widget signals."""
        self.open_charts_btn.clicked.connect(self._on_open_charts_folder)
        self.sync_vps_btn.clicked.connect(self._on_sync_vps)

    def _on_open_charts_folder(self) -> None:
        """Open charts folder."""
        charts_dir = Path(__file__).parent.parent.parent.parent / "output" / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":
            os.startfile(str(charts_dir))
        else:
            subprocess.run(["xdg-open", str(charts_dir)])

        self.log_viewer.log_info(f"Opened charts folder: {charts_dir}")

    def _on_sync_vps(self) -> None:
        """Start VPS sync in background."""
        if self._sync_worker is not None and self._sync_worker.isRunning():
            return
        self.sync_vps_btn.setEnabled(False)
        self.sync_vps_btn.setText("Syncing...")
        self.log_viewer.log_info("Starting sync from VPS...")
        self._sync_worker = SyncWorker()
        self._sync_worker.progress.connect(self.log_viewer.log_info)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.start()

    def _on_sync_finished(self, success: bool, summary: str) -> None:
        """Handle sync completion."""
        self.sync_vps_btn.setEnabled(True)
        self.sync_vps_btn.setText("Sync from VPS")
        if success:
            self.log_viewer.log_info(f"Sync complete: {summary}")
        else:
            self.log_viewer.log_error(f"Sync failed: {summary}")
