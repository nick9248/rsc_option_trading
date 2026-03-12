"""
Database tab for capturing and visualizing on-chain data.

Provides a grid of tiles for capturing different data types
(snapshots, max pain, OI, volume, levels, GEX/DEX) and displaying charts.

This is a thin GUI layer - all business logic is in the service layer.
"""

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
    QSizePolicy,
    QScrollArea,
)
from PySide6.QtCore import Qt, QThread, Signal

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.theme.colors import Colors
from coding.service.database import DatabaseCaptureService

logger = logging.getLogger(__name__)


class CaptureWorker(QThread):
    """
    Worker thread for capturing data.

    This is a thin wrapper that calls the service layer.
    """

    progress = Signal(str)
    finished = Signal(str, str, int, list)  # (capture_type, currency, count, chart_paths)
    error = Signal(str)

    def __init__(
        self,
        capture_type: str,
        currency: str,
        parent: Optional[QWidget] = None
    ):
        """
        Initialize capture worker.

        Args:
            capture_type: Type of capture to perform.
            currency: Currency symbol (ETH, BTC).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.capture_type = capture_type
        self.currency = currency

    def run(self) -> None:
        """Execute capture via service layer."""
        try:
            service = DatabaseCaptureService(
                progress_callback=self.progress.emit
            )

            result = service.capture(
                capture_type=self.capture_type,
                currency=self.currency,
                generate_charts=True
            )

            if result.success:
                self.finished.emit(
                    result.capture_type,
                    self.currency,
                    result.record_count,
                    result.chart_paths
                )
            else:
                self.error.emit(result.error or "Unknown error")

        except Exception as e:
            logger.exception(f"Error during {self.capture_type} capture")
            self.error.emit(str(e))


class CaptureTile(QFrame):
    """
    A tile widget for capturing a specific data type.
    """

    capture_clicked = Signal(str)

    def __init__(
        self,
        title: str,
        capture_type: str,
        description: str,
        parent: Optional[QWidget] = None
    ):
        """
        Initialize capture tile.

        Args:
            title: Display title.
            capture_type: Type identifier.
            description: Short description.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.capture_type = capture_type
        self._setup_ui(title, description)

    def _setup_ui(self, title: str, description: str) -> None:
        """Set up the tile UI."""
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
            QFrame:hover {{
                border-color: {Colors.ACCENT};
            }}
        """)
        self.setMinimumSize(150, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet(f"""
            font-size: 16px;
            font-weight: 600;
            color: {Colors.TEXT_PRIMARY};
        """)
        layout.addWidget(title_label)

        # Description
        desc_label = QLabel(description)
        desc_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 12px;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        # Last captured label
        self.last_captured_label = QLabel("Last: —")
        self.last_captured_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(self.last_captured_label)

        layout.addStretch()

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self.status_label)

        # Chart status label
        self.chart_label = QLabel("")
        self.chart_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
        self.chart_label.setWordWrap(True)
        layout.addWidget(self.chart_label)

        # Capture button
        self.capture_btn = QPushButton("Capture & Chart")
        self.capture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.capture_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.BACKGROUND_PRIMARY};
                border: none;
                padding: 8px 16px;
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
        self.capture_btn.clicked.connect(self._on_capture_click)
        layout.addWidget(self.capture_btn)

    def _on_capture_click(self) -> None:
        """Handle capture button click."""
        self.capture_clicked.emit(self.capture_type)

    def set_capturing(self, capturing: bool) -> None:
        """Set tile to capturing state."""
        self.capture_btn.setEnabled(not capturing)
        if capturing:
            self.status_label.setText("Capturing...")
            self.status_label.setStyleSheet(f"color: {Colors.WARNING}; font-size: 11px;")
            self.chart_label.setText("")
        else:
            self.status_label.setText("")

    def set_success(self, count: int, chart_count: int = 0) -> None:
        """Set success status."""
        self.status_label.setText(f"Captured {count} records")
        self.status_label.setStyleSheet(f"color: {Colors.SUCCESS}; font-size: 11px;")

        if chart_count > 0:
            self.chart_label.setText(f"Generated {chart_count} chart(s)")
            self.chart_label.setStyleSheet(f"color: {Colors.ACCENT}; font-size: 10px;")
        else:
            self.chart_label.setText("Need more data for charts")
            self.chart_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")

    def set_error(self, message: str) -> None:
        """Set error status."""
        truncated = "\n".join(message.splitlines()[:3])
        self.status_label.setText(f"Error: {truncated}")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR}; font-size: 11px;")
        self.chart_label.setText("")

    def set_last_captured(self, dt: Optional[datetime]) -> None:
        """Update the last captured timestamp label."""
        if dt is None:
            self.last_captured_label.setText("Last: Never")
        else:
            self.last_captured_label.setText(f"Last: {dt.strftime('%H:%M')}")


class DatabaseTab(QWidget):
    """
    Tab widget for database operations and visualization.

    This is a thin GUI layer - all business logic is delegated
    to the DatabaseCaptureService.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """Initialize Database tab."""
        super().__init__(parent)
        self.worker: Optional[CaptureWorker] = None
        self.tiles: Dict[str, CaptureTile] = {}
        self._capture_queue: List[tuple[str, str]] = []
        self._capture_all_in_progress: bool = False
        self._capture_all_total: int = 0
        self._capture_all_completed: int = 0
        self._active_capture_all_btn: Optional[QPushButton] = None
        self._timestamps_loaded: bool = False

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Scroll area for responsiveness
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        # Content widget
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(12)

        # Header
        header = QLabel("Database Capture & Charts")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(header)

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

        # Currency selector
        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        currency_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["BTC", "ETH"])
        self.currency_combo.setMinimumWidth(80)
        self.currency_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.currency_combo)

        controls_layout.addStretch()

        # Open charts button
        self.open_charts_btn = QPushButton("Open Charts")
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

        # Capture all button
        self.capture_all_btn = QPushButton("Capture All")
        self.capture_all_btn.setStyleSheet(f"""
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
            QPushButton:disabled {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_MUTED};
            }}
        """)
        self.capture_all_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.capture_all_btn)

        # Capture all (both currencies) button
        self.capture_all_both_btn = QPushButton("Capture All (BTC/ETH)")
        self.capture_all_both_btn.setStyleSheet(f"""
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
            QPushButton:disabled {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_MUTED};
            }}
        """)
        self.capture_all_both_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.capture_all_both_btn)

        # Cancel button (hidden by default)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ERROR};
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ERROR_MUTED};
            }}
        """)
        self.cancel_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.cancel_btn.hide()
        controls_layout.addWidget(self.cancel_btn)

        content_layout.addWidget(controls_frame)

        # Tiles grid
        tiles_frame = QFrame()
        tiles_frame.setStyleSheet("background: transparent; border: none;")
        tiles_layout = QGridLayout(tiles_frame)
        tiles_layout.setSpacing(8)
        tiles_layout.setContentsMargins(0, 0, 0, 0)

        # Create tiles
        tile_configs = [
            ("Snapshot", "snapshot", "Capture raw book summary data"),
            ("Max Pain", "max_pain", "Record max pain + trend chart"),
            ("Open Interest", "open_interest", "Track OI + P/C ratio charts"),
            ("Volume", "volume", "Track volume + trend charts"),
            ("Levels", "levels", "S/R levels + trend charts"),
            ("GEX/DEX", "gex_dex", "Gamma/Delta exposure trends"),
        ]

        for i, (title, capture_type, desc) in enumerate(tile_configs):
            tile = CaptureTile(title, capture_type, desc)
            tile.capture_clicked.connect(self._on_tile_capture)
            self.tiles[capture_type] = tile
            row, col = divmod(i, 3)
            tiles_layout.addWidget(tile, row, col)

        content_layout.addWidget(tiles_frame)

        # Log viewer
        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        log_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(100)
        self.log_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_layout.addWidget(self.log_viewer, 1)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

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
        self.capture_all_btn.clicked.connect(self._on_capture_all)
        self.capture_all_both_btn.clicked.connect(self._on_capture_all_both)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.open_charts_btn.clicked.connect(self._on_open_charts_folder)
        self.currency_combo.currentTextChanged.connect(self._on_currency_changed)

    def showEvent(self, event) -> None:
        """Load timestamps on first show."""
        super().showEvent(event)
        if not self._timestamps_loaded:
            self._timestamps_loaded = True
            self._refresh_timestamps()

    def _refresh_timestamps(self) -> None:
        """Load last captured times for all tiles from the database."""
        currency = self.currency_combo.currentText()
        try:
            service = DatabaseCaptureService()
            times = service.get_last_captured(currency)
            for capture_type, tile in self.tiles.items():
                tile.set_last_captured(times.get(capture_type))
        except Exception as e:
            logger.warning(f"Could not load last captured timestamps: {e}")

    def _refresh_tile_timestamp(self, capture_type: str, currency: str) -> None:
        """Refresh the timestamp for a single tile after a successful capture."""
        try:
            service = DatabaseCaptureService()
            times = service.get_last_captured(currency)
            self.tiles[capture_type].set_last_captured(times.get(capture_type))
        except Exception as e:
            logger.warning(f"Could not refresh timestamp for {capture_type}: {e}")

    def _on_currency_changed(self, currency: str) -> None:
        """Refresh timestamps when currency selection changes."""
        self._refresh_timestamps()

    def _on_cancel(self) -> None:
        """Cancel the ongoing Capture All run."""
        self._capture_queue.clear()
        self._capture_all_in_progress = False
        self.cancel_btn.setText("Cancelling...")
        self.cancel_btn.setEnabled(False)
        self.log_viewer.log_warning("Capture All cancelled — waiting for current capture to finish...")

    def _on_tile_capture(self, capture_type: str) -> None:
        """Handle single tile capture."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A capture is already in progress")
            return

        currency = self.currency_combo.currentText()
        self.tiles[capture_type].set_capturing(True)

        self.log_viewer.log_info(f"Starting {capture_type} capture for {currency}...")

        self.worker = CaptureWorker(capture_type, currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_capture_finished)
        self.worker.error.connect(self._on_capture_error)
        self.worker.start()

    def _on_capture_all(self) -> None:
        """Handle capture all button (current currency)."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A capture is already in progress")
            return

        currency = self.currency_combo.currentText()
        capture_types = ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
        self._capture_queue = [(ct, currency) for ct in capture_types]
        self._capture_all_total = len(self._capture_queue)
        self._capture_all_completed = 0
        self._capture_all_in_progress = True
        self._active_capture_all_btn = self.capture_all_btn
        self.capture_all_btn.setEnabled(False)
        self.capture_all_both_btn.setEnabled(False)
        self.cancel_btn.show()
        self.log_viewer.log_info(f"Starting Capture All ({currency})...")
        self._start_next_capture()

    def _on_capture_all_both(self) -> None:
        """Handle capture all for BTC then ETH."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A capture is already in progress")
            return

        capture_types = ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
        self._capture_queue = [(ct, "BTC") for ct in capture_types] + [(ct, "ETH") for ct in capture_types]
        self._capture_all_total = len(self._capture_queue)
        self._capture_all_completed = 0
        self._capture_all_in_progress = True
        self._active_capture_all_btn = self.capture_all_both_btn
        self.capture_all_btn.setEnabled(False)
        self.capture_all_both_btn.setEnabled(False)
        self.cancel_btn.show()
        self.log_viewer.log_info("Starting Capture All (BTC/ETH)...")
        self._start_next_capture()

    def _start_next_capture(self) -> None:
        """Start next capture in queue."""
        if not self._capture_queue:
            self._finish_capture_all()
            return

        capture_type, currency = self._capture_queue.pop(0)
        self.tiles[capture_type].set_capturing(True)
        self.log_viewer.log_info(f"[Capture All] Starting {capture_type} for {currency}...")

        self.worker = CaptureWorker(capture_type, currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_capture_finished)
        self.worker.error.connect(self._on_capture_error)
        self.worker.start()

    def _finish_capture_all(self) -> None:
        """Reset state after capture all completes or is cancelled."""
        self._capture_all_in_progress = False
        self.capture_all_btn.setEnabled(True)
        self.capture_all_both_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.hide()
        if self._active_capture_all_btn is not None:
            if self._active_capture_all_btn is self.capture_all_btn:
                self._active_capture_all_btn.setText("Capture All")
            else:
                self._active_capture_all_btn.setText("Capture All (BTC/ETH)")
            self._active_capture_all_btn = None

    def _update_capture_all_progress(self) -> None:
        """Update the active button label with current progress."""
        if self._active_capture_all_btn is None:
            return
        completed = self._capture_all_completed
        total = self._capture_all_total
        if self._active_capture_all_btn is self.capture_all_btn:
            self._active_capture_all_btn.setText(f"Capture All ({completed}/{total})")
        else:
            self._active_capture_all_btn.setText(f"Capture All (BTC/ETH) ({completed}/{total})")

    def _on_open_charts_folder(self) -> None:
        """Open charts folder."""
        charts_dir = Path(__file__).parent.parent.parent.parent / "output" / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":
            os.startfile(str(charts_dir))
        else:
            subprocess.run(["xdg-open", str(charts_dir)])

        self.log_viewer.log_info(f"Opened charts folder: {charts_dir}")

    def _on_progress(self, message: str) -> None:
        """Handle progress updates."""
        self.log_viewer.log_info(message)

    def _on_capture_finished(self, capture_type: str, currency: str, count: int, chart_paths: List[str]) -> None:
        """Handle successful capture."""
        self.tiles[capture_type].set_capturing(False)
        self.tiles[capture_type].set_success(count, len(chart_paths))
        self.log_viewer.log_info(f"{capture_type} capture complete: {count} records saved")

        if chart_paths:
            self.log_viewer.log_info(f"Generated {len(chart_paths)} chart(s):")
            for path in chart_paths:
                self.log_viewer.log_info(f"  - {path}")
        elif capture_type != "snapshot":
            self.log_viewer.log_info("Need at least 2 data points to generate trend charts")

        if self._capture_all_in_progress:
            self._capture_all_completed += 1
            self._update_capture_all_progress()
            self._refresh_tile_timestamp(capture_type, currency)
            self._start_next_capture()
        elif self._active_capture_all_btn is not None:
            # Was in Capture All mode but cancel was clicked — finish cleanup now
            self._refresh_tile_timestamp(capture_type, currency)
            self._finish_capture_all()
        else:
            # Single tile capture — refresh its timestamp
            self._refresh_tile_timestamp(capture_type, currency)

    def _on_capture_error(self, error_message: str) -> None:
        """Handle capture error."""
        for tile in self.tiles.values():
            if not tile.capture_btn.isEnabled():
                tile.set_capturing(False)
                tile.set_error(error_message)
                break

        self.log_viewer.log_error(f"Capture failed: {error_message}")

        if self._capture_all_in_progress:
            self._capture_all_completed += 1
            self._update_capture_all_progress()
            self._start_next_capture()
        elif self._active_capture_all_btn is not None:
            # Was in Capture All mode but cancel was clicked — finish cleanup now
            self._finish_capture_all()
