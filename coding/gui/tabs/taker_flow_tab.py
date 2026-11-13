"""
Futures Flow tab: one Generate button writes the taker buy/sell flow charts
(BTC and ETH perpetuals, last 30 days) into a dated folder, and a link opens
that folder in the file browser.

This is the futures market, not options. All work happens in
``TakerFlowChartService`` (database read, calculation, PNG + JSON files), run in
a worker thread so the window never freezes. The data is the local mirror of the
VPS table ``taker_flow_hourly`` (scripts/sync_from_vps.py); this module only
formats what the service returns.
"""

import html
import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from coding.gui.components.menthorq_widgets import panel_frame, secondary_button
from coding.gui.theme.colors import Colors
from coding.service.futures_flow.taker_flow_chart_service import TakerFlowChartResult, TakerFlowChartService

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "taker_flow" / "gui"

DESCRIPTION = ("Binance BTC and ETH perpetual taker buy/sell flow, last 30 days: daily ratio, daily net "
               "flow and rolling 24-hour ratio. Futures market, not options. Reads the local copy of the "
               "VPS data (sync it first in the Database tab or with scripts/sync_from_vps.py).")


class TakerFlowWorker(QThread):
    """Runs one generation in a background thread."""

    succeeded = Signal(object)   # TakerFlowChartResult
    failed = Signal(str)

    def __init__(self, output_directory: Path, parent: Optional[QWidget] = None):
        """
        Args:
            output_directory: Base folder; the run writes into its ``<YYYY-MM-DD>`` subfolder.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.output_directory = output_directory

    def run(self) -> None:
        """Generate the charts; emit ``succeeded`` or ``failed`` (class: message)."""
        try:
            self.succeeded.emit(TakerFlowChartService().generate(self.output_directory))
        except Exception as error:
            logger.exception("Taker flow chart generation failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


class TakerFlowTab(QWidget):
    """Tab with one Generate button, a status line, data warnings and a folder link."""

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.output_directory = DEFAULT_OUTPUT_DIRECTORY
        self.generated_folder: Optional[Path] = None
        self.worker: Optional[TakerFlowWorker] = None
        self._setup_ui()
        self.generate_button.clicked.connect(self._generate)
        self.folder_link_label.linkActivated.connect(lambda _link: self.open_generated_folder())
        self.open_folder_button.clicked.connect(self.open_generated_folder)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("Futures Flow")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(header)

        description = QLabel(DESCRIPTION)
        description.setWordWrap(True)
        description.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        main_layout.addWidget(description)

        frame = panel_frame()
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(16, 12, 16, 12)
        frame_layout.setSpacing(8)

        button_row = QHBoxLayout()
        self.generate_button = QPushButton("Generate")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self.generate_button)
        self.status_label = QLabel("No charts generated yet.")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        button_row.addWidget(self.status_label, 1)
        frame_layout.addLayout(button_row)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {Colors.WARNING};")
        self.warning_label.setVisible(False)
        frame_layout.addWidget(self.warning_label)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder"))
        self.folder_link_label = QLabel("not generated yet")
        self.folder_link_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.folder_link_label.setOpenExternalLinks(False)
        folder_row.addWidget(self.folder_link_label, 1)
        self.open_folder_button = secondary_button("Open folder")
        self.open_folder_button.setEnabled(False)
        folder_row.addWidget(self.open_folder_button)
        frame_layout.addLayout(folder_row)

        main_layout.addWidget(frame)
        main_layout.addStretch(1)

    def _set_status(self, text: str, color: str) -> None:
        """Status line next to the Generate button."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def _generate(self) -> None:
        """Start a generation in the worker thread."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.generate_button.setEnabled(False)
        self.warning_label.setVisible(False)
        self._set_status("Generating the charts (a few seconds)...", Colors.WARNING)
        self.worker = TakerFlowWorker(self.output_directory)
        self.worker.succeeded.connect(self._on_generation_succeeded)
        self.worker.failed.connect(self._on_generation_failed)
        self.worker.start()

    def _on_generation_succeeded(self, result: TakerFlowChartResult) -> None:
        """Show the chart count, the data warnings and the folder link."""
        chart_count = sum(1 for path in result.files if path.suffix == ".png")
        newest = ((result.newest_hour_utc + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC")
                  if result.newest_hour_utc is not None else "no data")
        color = Colors.SUCCESS if chart_count else Colors.WARNING
        self._set_status(f"Generated {chart_count} chart(s). Data up to {newest}.", color)
        self.warning_label.setText("\n".join(f"Warning: {warning}" for warning in result.warnings))
        self.warning_label.setVisible(bool(result.warnings))
        self._show_folder(result.folder)
        self.generate_button.setEnabled(True)

    def _on_generation_failed(self, message: str) -> None:
        """Show a failed generation as one error line."""
        self._set_status(f"Generation failed: {message}", Colors.ERROR)
        self.generate_button.setEnabled(True)

    def _show_folder(self, folder: Path) -> None:
        """Clickable link to the generated folder."""
        self.generated_folder = folder
        self.open_folder_button.setEnabled(True)
        url = QUrl.fromLocalFile(str(folder)).toString()
        self.folder_link_label.setText(f'<a href="{url}" style="color: {Colors.ACCENT};">{html.escape(str(folder))}</a>')

    def open_generated_folder(self) -> None:
        """Open the generated folder in the system file browser."""
        if self.generated_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.generated_folder)))
