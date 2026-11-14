"""
Report tab: one Generate button builds the market report of a currency -- MenthorQ replica, futures flow, option flow,
dealer levels and the levels overview -- as ONE self-contained HTML file with a tab per section, and shows it here.

Generate first brings the local database in step with the VPS when it is behind, then builds the sections (all in
``ReportService``, run in a worker thread so the window never freezes). This module only shows the progress and the result.
A Generate pressed while a report is being built waits and runs next.
"""

import html
import logging
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from coding.gui.components.menthorq_widgets import panel_frame, secondary_button
from coding.gui.theme.colors import Colors
from coding.service.report.report_service import CURRENCIES, ReportResult, ReportService

logger = logging.getLogger(__name__)

PAGE_BACKGROUND = "#FFFFFF"
PROGRESS_LINES_SHOWN = 4

DESCRIPTION = ("One report per currency with five tabs: MenthorQ replica charts, futures taker flow, option flow by strike, "
               "dealer levels (flow-based and open-interest based) and every level on one price line. Generate first syncs "
               "the local database when the VPS has newer rows, then writes one HTML file you can open in any browser.")


class ReportWorker(QThread):
    """Generates one report in a background thread."""

    progress = Signal(str)
    succeeded = Signal(object)   # ReportResult
    failed = Signal(str)

    def __init__(self, currency: str, service_factory: Callable[[], ReportService] = ReportService,
                 parent: Optional[QWidget] = None):
        """
        Args:
            currency: "BTC" or "ETH".
            service_factory: Builds the service (a fake in tests).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currency = currency
        self.service_factory = service_factory

    def run(self) -> None:
        """Emit ``progress`` per step, then ``succeeded`` with the ``ReportResult`` or ``failed`` (class: message)."""
        try:
            self.succeeded.emit(self.service_factory().generate(self.currency, progress=self.progress.emit))
        except Exception as error:
            logger.exception("Report generation failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


def result_summary(result: ReportResult) -> str:
    """The status line of a finished report (plain text)."""
    built = sum(1 for outcome in result.sections if outcome.built)
    text = f"{result.currency} report built: {built} of {len(result.sections)} sections, {result.size_bytes / 1e6:.1f} MB. "
    if result.failed_sections:
        text += f"Not built: {', '.join(result.failed_sections)} (the tab explains why). "
    return text + result.sync.summary


class ReportTab(QWidget):
    """Currency and Generate on top, status and file links, then the report itself."""

    def __init__(self, parent: Optional[QWidget] = None, service_factory: Callable[[], ReportService] = ReportService):
        """
        Args:
            parent: Parent widget.
            service_factory: Builds the service (a fake in tests).
        """
        super().__init__(parent)
        self.service_factory = service_factory
        self.worker: Optional[ReportWorker] = None
        self.pending_currency: Optional[str] = None      # a Generate pressed while a report was being built
        self.report_path: Optional[Path] = None
        self.progress_lines: List[str] = []
        self._setup_ui()
        self.generate_button.clicked.connect(self.generate)
        self.open_button.clicked.connect(self.open_in_browser)
        self.folder_label.linkActivated.connect(lambda _link: self.open_folder())
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self._wait_for_worker)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("Report")
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
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Currency"))
        self.currency_combo = QComboBox()
        self.currency_combo.addItems(list(CURRENCIES))
        controls.addWidget(self.currency_combo)
        self.generate_button = QPushButton("Generate report")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        controls.addWidget(self.generate_button)
        self.open_button = secondary_button("Open in browser")
        self.open_button.setEnabled(False)
        controls.addWidget(self.open_button)
        controls.addStretch(1)
        frame_layout.addLayout(controls)

        self.status_label = QLabel("No report generated yet.")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frame_layout.addWidget(self.status_label)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("File"))
        self.folder_label = QLabel("not generated yet")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.folder_label.setOpenExternalLinks(False)
        folder_row.addWidget(self.folder_label, 1)
        frame_layout.addLayout(folder_row)
        main_layout.addWidget(frame)

        self.report_view = QWebEngineView()
        self.report_view.page().setBackgroundColor(QColor(PAGE_BACKGROUND))
        self.report_view.setMinimumHeight(450)
        main_layout.addWidget(self.report_view, 1)

    def _set_status(self, text: str, color: str) -> None:
        """Status line under the controls."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def generate(self) -> None:
        """Build a report of the selected currency; while one is being built, the newest request waits and runs next."""
        currency = self.currency_combo.currentText()
        if self.worker is not None and self.worker.isRunning():
            self.pending_currency = currency
            self._set_status(f"{self._progress_text()}\n{currency} report queued: it starts when this one is done.", Colors.WARNING)
            return
        self.progress_lines = []
        self._set_status(f"Generating the {currency} report (sync check, then five sections; about a minute)...", Colors.WARNING)
        self.worker = ReportWorker(currency, self.service_factory, parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._run_pending)
        self.worker.start()

    def _progress_text(self) -> str:
        """The newest progress lines."""
        return "\n".join(self.progress_lines[-PROGRESS_LINES_SHOWN:])

    def _on_progress(self, line: str) -> None:
        """Show the newest steps."""
        self.progress_lines.append(line.strip())
        self._set_status(self._progress_text(), Colors.WARNING)

    def _on_succeeded(self, result: ReportResult) -> None:
        """Show the report, the summary and the file link."""
        self.report_path = result.path
        self._set_status(result_summary(result), Colors.WARNING if result.failed_sections or result.sync.failed else Colors.SUCCESS)
        url = QUrl.fromLocalFile(str(result.path.parent)).toString()
        self.folder_label.setText(f'<a href="{html.escape(url)}" style="color: {Colors.ACCENT};">{html.escape(str(result.path))}</a>')
        self.open_button.setEnabled(True)
        # The page is several MB: load it from the file (setHtml is limited to 2 MB).
        self.report_view.setUrl(QUrl.fromLocalFile(str(result.path.resolve())))

    def _on_failed(self, message: str) -> None:
        """Show a failed run as one error line (the last good report stays)."""
        self._set_status(f"Failed: {message}", Colors.ERROR)

    def _run_pending(self) -> None:
        """Start the request made while the last report was built, if any."""
        pending, self.pending_currency = self.pending_currency, None
        if pending is not None:
            self.currency_combo.setCurrentText(pending)
            self.generate()

    def open_in_browser(self) -> None:
        """Open the report in the default browser."""
        if self.report_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report_path)))

    def open_folder(self) -> None:
        """Open the report folder in the file browser."""
        if self.report_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report_path.parent)))

    def _wait_for_worker(self) -> None:
        """Wait for a running worker so the thread is never destroyed while it runs."""
        self.pending_currency = None
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()

    def closeEvent(self, event) -> None:
        """Also wait when the tab itself is closed."""
        self._wait_for_worker()
        super().closeEvent(event)
