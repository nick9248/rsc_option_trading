"""
MenthorQ Charts tab: one Generate button writes every MenthorQ-style chart
of the last complete UTC day into a dated, nested folder, and a link opens
that folder in the file browser.

Fixed settings (no selectors): BTC, MenthorQ daily close of the last
complete UTC day (spot and IV at 23:59 UTC, open interest from 00:00 UTC),
gamma at the index, monthly and all-listed expiry universes. Other modes
(live, ETH, other dates, reference comparison) are in
scripts/replicate_menthorq.py and the MenthorQ Monitor tab.

All work happens in ``MenthorqReplicationService`` (database reads,
calculation, PNG + JSON + README files), run in a worker thread so the
window never freezes. This module only formats what it returns.
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from coding.core.analytics.menthorq_chart_summary import data_notes, data_warnings
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY,
    GAMMA_UNDERLYING_INDEX,
)
from coding.gui.components.menthorq_widgets import panel_frame, secondary_button
from coding.gui.theme.colors import Colors
from coding.service.on_chain.menthorq_monitor.brief_preview_service import generated_chart_files
from coding.service.on_chain.menthorq_replication_service import (
    CHART_LAYOUT_NESTED,
    MenthorqReplicationService,
    last_complete_utc_day,
    nested_date_folder,
)

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "menthorq_replicate" / "gui" / "charts"

CURRENCY = "BTC"
DESCRIPTION = ("BTC, MenthorQ daily close of the last complete UTC day (spot and IV at 23:59 UTC, "
               "open interest from 00:00 UTC), gamma at the index, monthly and all listed expiries.")


class MenthorqChartsWorker(QThread):
    """
    Runs one generation in a background thread: a single call to
    ``MenthorqReplicationService.run_menthorq_daily_close`` with the fixed
    settings and the nested layout.
    """

    succeeded = Signal(object)  # {"output": service result, "folder": Path, "date": date}
    failed = Signal(str)

    def __init__(self, menthorq_date: date, output_directory: Path,
                 parent: Optional[QWidget] = None):
        """
        Args:
            menthorq_date: UTC date of the daily close to replay.
            output_directory: Base folder; the run writes into its
                ``<YYYY-MM-DD>`` subfolder.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.menthorq_date = menthorq_date
        self.output_directory = output_directory

    def run(self) -> None:
        """Generate the charts; emit ``succeeded`` or ``failed`` (class: message)."""
        try:
            output = MenthorqReplicationService().run_menthorq_daily_close(
                CURRENCY, self.menthorq_date, self.output_directory,
                expiry_universe=EXPIRY_UNIVERSE_MONTHLY, gamma_underlying=GAMMA_UNDERLYING_INDEX,
                chart_layout=CHART_LAYOUT_NESTED,
            )
            self.succeeded.emit({
                "output": output,
                "folder": nested_date_folder(self.output_directory, self.menthorq_date),
                "date": self.menthorq_date,
            })
        except Exception as error:
            logger.exception("MenthorQ chart generation failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


class MenthorqChartsTab(QWidget):
    """
    Tab with one Generate button, a status line, data warnings and a link
    (plus an Open folder button) to the generated folder.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.output_directory = DEFAULT_OUTPUT_DIRECTORY
        self.generated_folder: Optional[Path] = None
        self.worker: Optional[MenthorqChartsWorker] = None
        self._setup_ui()
        self.generate_button.clicked.connect(self._generate)
        self.folder_link_label.linkActivated.connect(lambda _link: self.open_generated_folder())
        self.open_folder_button.clicked.connect(self.open_generated_folder)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("MenthorQ Charts")
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

        # Expected gaps (e.g. an expiry listed after the open-interest snapshot): neutral color.
        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        self.note_label.setVisible(False)
        frame_layout.addWidget(self.note_label)

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
        """Start a generation for the last complete UTC day in the worker thread."""
        if self.worker is not None and self.worker.isRunning():
            return
        menthorq_date = last_complete_utc_day(datetime.now(timezone.utc))
        self.generate_button.setEnabled(False)
        self.warning_label.setVisible(False)
        self.note_label.setVisible(False)
        self._set_status(f"Generating the charts for {menthorq_date.isoformat()} "
                         "(a few seconds)...", Colors.WARNING)
        self.worker = MenthorqChartsWorker(menthorq_date, self.output_directory)
        self.worker.succeeded.connect(self._on_generation_succeeded)
        self.worker.failed.connect(self._on_generation_failed)
        self.worker.start()

    def _on_generation_succeeded(self, result: Dict[str, Any]) -> None:
        """Show the chart count, the data warnings and the folder link."""
        output = result["output"]
        chart_count = len(generated_chart_files(output))
        self._set_status(f"Generated {chart_count} charts for {result['date'].isoformat()} "
                         f"(BTC, MenthorQ daily close).", Colors.SUCCESS)
        warnings = data_warnings(output)
        self.warning_label.setText("\n".join(f"Warning: {warning}" for warning in warnings))
        self.warning_label.setVisible(bool(warnings))
        notes = data_notes(output)
        self.note_label.setText("\n".join(f"Note: {note}" for note in notes))
        self.note_label.setVisible(bool(notes))
        self._show_folder(result["folder"])
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
        self.folder_link_label.setText(f'<a href="{url}" style="color: {Colors.ACCENT};">{folder}</a>')

    def open_generated_folder(self) -> None:
        """Open the generated folder in the system file browser."""
        if self.generated_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.generated_folder)))
