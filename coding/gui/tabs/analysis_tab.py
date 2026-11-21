"""
Analysis tab: ONE "Start Analysis" button runs everything for BTC, ETH or both -- the database sync (once), the on-chain
analysis, the on-chain dashboard and the market report -- and writes every step to the output log.

The tab shows no results. "Show Dashboard" opens the unified dashboard (market report and on-chain dashboard in one page)
of the last run in the default browser.
All the work is in ``AnalysisRunService`` (run in a worker thread so the window never freezes); this module only starts it,
shows its progress and opens the files.
"""

import logging
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from coding.gui.components.log_viewer import GuiLogHandler, LogViewer
from coding.gui.components.menthorq_widgets import panel_frame, secondary_button
from coding.gui.theme.colors import Colors
from coding.service.analysis.analysis_run_service import AnalysisRunResult, AnalysisRunService
from coding.service.on_chain.chain_source import CHAIN_SOURCE_LIVE, CHAIN_SOURCE_STORED, DEFAULT_MAX_SNAPSHOT_AGE_MINUTES

logger = logging.getLogger(__name__)

# The logger the log viewer's handler attaches to: the on-chain services log their detail lines here. Removed on close.
GUI_LOG_LOGGER_NAME = "coding.service.on_chain"

CURRENCY_CHOICES = {"BTC": ("BTC",), "ETH": ("ETH",), "BOTH": ("BTC", "ETH")}
FAILURE_MARKER = "FAILED"

DESCRIPTION = ("Start Analysis syncs the local database when the VPS has newer rows (once), runs the on-chain analysis, "
               "builds the on-chain dashboard and the market report, merges them into one dashboard, and logs every step "
               "below. The option chain comes from the newest stored snapshot (live from Deribit when it is older than the limit) "
               "or always live, as chosen. Show Dashboard opens the dashboard of the last run in your browser.")


CHAIN_SOURCE_CHOICES = {
    f"Stored snapshot (live if older than {DEFAULT_MAX_SNAPSHOT_AGE_MINUTES:.0f} min)": CHAIN_SOURCE_STORED,
    "Live from Deribit": CHAIN_SOURCE_LIVE,
}


class AnalysisWorker(QThread):
    """Runs one analysis (every step, every selected currency) in a background thread."""

    progress = Signal(str)
    succeeded = Signal(object)   # AnalysisRunResult
    failed = Signal(str)

    def __init__(self, currencies: List[str], service_factory: Callable[[], AnalysisRunService],
                 parent: Optional[QWidget] = None):
        """
        Args:
            currencies: "BTC", "ETH" or both, in run order.
            service_factory: Builds the service (a fake in tests).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currencies = currencies
        self.service_factory = service_factory

    def run(self) -> None:
        """
        Emit ``progress`` per step, then ``succeeded`` with the ``AnalysisRunResult``, or ``failed`` when the run itself
        could not start or crashed (a failing step does not do that: it is part of the result).
        ``requestInterruption`` (on application exit) stops the run between steps.
        """
        try:
            self.succeeded.emit(self.service_factory().run(self.currencies, self.progress.emit,
                                                           should_stop=self.isInterruptionRequested))
        except Exception as error:
            logger.exception("Analysis run failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


class AnalysisTab(QWidget):
    """Currency, Start Analysis, Show Dashboard, Clear -- and the output log."""

    def __init__(self, parent: Optional[QWidget] = None,
                 service_factory: Optional[Callable[[], AnalysisRunService]] = None):
        """
        Args:
            parent: Parent widget.
            service_factory: Builds the service (a fake in tests); by default the real service with the chosen chain source.
        """
        super().__init__(parent)
        self.service_factory = service_factory
        self.worker: Optional[AnalysisWorker] = None
        self.dashboard_paths: List[Path] = []
        self._setup_ui()
        self._setup_logging()
        self.start_button.clicked.connect(self.start_analysis)
        self.dashboard_button.clicked.connect(self.show_dashboard)
        self.clear_button.clicked.connect(self.log_viewer.clear_logs)
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self._wait_for_worker)

    def _service_factory_for_run(self) -> Callable[[], AnalysisRunService]:
        """The factory of the service of this run: the injected one, else the real service with the chosen option chain source."""
        if self.service_factory is not None:
            return self.service_factory
        mode = self.chain_source_combo.currentData()
        ask_model = self.ask_model_checkbox.isChecked()
        return lambda: AnalysisRunService(chain_source_mode=mode, ask_model=ask_model)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("Analysis")
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
        selectors = QHBoxLayout()                      # row 1: what to analyse; row 2: the buttons (one row overlapped on narrow windows)
        selectors.setSpacing(10)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        selectors.addWidget(QLabel("Currency"))
        self.currency_combo = QComboBox()
        self.currency_combo.addItems(list(CURRENCY_CHOICES))
        selectors.addWidget(self.currency_combo)
        selectors.addWidget(QLabel("Option chain"))
        self.chain_source_combo = QComboBox()
        for label, mode in CHAIN_SOURCE_CHOICES.items():
            self.chain_source_combo.addItem(label, mode)
        self.chain_source_combo.setToolTip("Stored: the newest snapshot of the local database (the run syncs it first); live from Deribit when it is "
                                           "older than the limit. Live: always fetch from Deribit.")
        self.chain_source_combo.setMinimumWidth(340)
        selectors.addWidget(self.chain_source_combo)
        self.ask_model_checkbox = QCheckBox("Ask Claude for strategies (about 0.2 USD per currency)")
        self.ask_model_checkbox.setToolTip("The model picks among candidate structures that code built and priced, for a few expiries; the picks appear in the "
                                           "Strategy tab. Costs money (about 0.05 USD per expiry); off by default. Needs claude_api_key in .env.")
        selectors.addWidget(self.ask_model_checkbox)
        selectors.addStretch(1)
        self.start_button = QPushButton("Start Analysis")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        controls.addWidget(self.start_button)
        self.dashboard_button = secondary_button("Show Dashboard")
        self.dashboard_button.setEnabled(False)
        controls.addWidget(self.dashboard_button)
        self.clear_button = secondary_button("Clear")
        controls.addWidget(self.clear_button)
        controls.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)       # messages may contain "<"; never rich text
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; border: none;")
        controls.addWidget(self.status_label)
        frame_layout.addLayout(selectors)
        frame_layout.addLayout(controls)
        main_layout.addWidget(frame)

        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        main_layout.addWidget(log_label)
        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(200)
        main_layout.addWidget(self.log_viewer, 1)

    def _setup_logging(self) -> None:
        """Show the on-chain services' own log lines in the output log (removed on close; never added twice)."""
        self._log_target_logger = logging.getLogger(GUI_LOG_LOGGER_NAME)
        self.gui_handler = GuiLogHandler(self.log_viewer)
        if self.gui_handler not in self._log_target_logger.handlers:
            self._log_target_logger.addHandler(self.gui_handler)

    def _set_status(self, text: str, color: str) -> None:
        """Short status next to the buttons."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def start_analysis(self) -> None:
        """Run every step for the selected currency (BTC, ETH or BOTH); ignored with a warning while a run is going."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("An analysis is already running; wait for it to finish.")
            return
        choice = self.currency_combo.currentText()
        if self.worker is not None:
            self.worker.deleteLater()                                   # the finished worker of the last run
        self.start_button.setEnabled(False)
        self.dashboard_button.setEnabled(False)
        self._set_status(f"Running {choice}...", Colors.WARNING)
        self.log_viewer.log_info(f"Starting analysis for {choice}...")
        self.worker = AnalysisWorker(list(CURRENCY_CHOICES[choice]), self._service_factory_for_run(), parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_progress(self, line: str) -> None:
        """One progress line of a step into the output log (failures in the error colour)."""
        text = line.strip()
        if text.startswith(FAILURE_MARKER):
            self.log_viewer.log_error(text)
        else:
            self.log_viewer.log_info(text)

    def _on_succeeded(self, result: AnalysisRunResult) -> None:
        """Enable Show Dashboard when a unified dashboard exists (the service already logged the summary of the steps)."""
        currencies = self.worker.currencies if self.worker is not None else []
        self.dashboard_paths = [path for path in (result.unified_dashboard_path(currency) for currency in currencies) if path is not None]
        self.dashboard_button.setEnabled(bool(self.dashboard_paths))
        if result.all_succeeded:
            self._set_status("All steps succeeded", Colors.SUCCESS)
        else:
            self._set_status(f"{len(result.failed_steps)} step(s) failed; see the log", Colors.WARNING)

    def _on_failed(self, message: str) -> None:
        """The run itself crashed (no step result at all)."""
        self.log_viewer.log_error(f"Analysis failed: {message}")
        self._set_status("Failed", Colors.ERROR)

    def _on_worker_finished(self) -> None:
        """Allow the next run."""
        self.start_button.setEnabled(True)

    @staticmethod
    def _open_files(paths: List[Path]) -> None:
        """Open each file in the default browser."""
        for path in paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def show_dashboard(self) -> None:
        """Open the unified dashboard of every currency of the last run."""
        self._open_files(self.dashboard_paths)

    def _wait_for_worker(self) -> None:
        """
        Ask a running worker to stop and wait for it, so the thread is never destroyed while it runs. It stops between
        steps (and between sync tables and report sections); a step being finished is not cut short.
        """
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            logger.info("Waiting for the running analysis to stop before closing")
            self.worker.wait()

    def closeEvent(self, event) -> None:
        """Remove the log handler (it must not leak) and wait for a running worker."""
        self._wait_for_worker()
        self._log_target_logger.removeHandler(self.gui_handler)
        super().closeEvent(event)
