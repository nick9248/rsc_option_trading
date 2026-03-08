"""
On Chain Analysis tab for options market analytics.

Provides interface to:
- Load on-chain analysis data
- View formatted text report with max pain, OI, support/resistance
- GEX/DEX analysis with Greeks
- Buy/Sell flow analysis
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
    QSizePolicy,
    QPlainTextEdit,
    QSplitter,
    QCheckBox,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.dialogs.flow_charts_window import FlowChartsWindow
from coding.gui.theme.colors import Colors
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from coding.service.morning_note.morning_note_service import MorningNoteService


logger = logging.getLogger(__name__)


class OnChainAnalysisWorker(QThread):
    """
    Worker thread for fetching and analyzing on-chain data.

    Fetches book summary data and generates analysis report.
    Always includes GEX/DEX and buy/sell flow analysis.
    """

    progress = Signal(str)
    finished = Signal(str)  # Returns report text
    error = Signal(str)

    def __init__(self, currency: str, parent: Optional[QWidget] = None):
        """
        Initialize the worker.

        Args:
            currency: Currency symbol (ETH, BTC).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        """Execute data fetch and analysis, then save report bundle."""
        try:
            repository = DatabaseRepository()

            with DeribitApiService() as api_service:
                service = OnChainAnalysisService(api_service, repository=repository)
                report, analyzer = service.fetch_and_analyze(
                    currency=self.currency,
                    progress_callback=lambda msg: self.progress.emit(msg),
                    return_analyzer=True,
                )

                morning_service = MorningNoteService(service)
                synthesis = morning_service.generate_from_analyzer(analyzer)
                morning_service.save_report_bundle(self.currency, report, synthesis)

                self.finished.emit(report)

        except Exception as error:
            logger.exception("Error during on-chain analysis")
            self.error.emit(str(error))


class OnChainAnalysisTab(QWidget):
    """
    Tab widget for on-chain analysis visualization.

    Features:
    - Load analysis for selected currency
    - Display formatted text report
    - Export report to file
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the On Chain Analysis tab.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.report_text: str = ""
        self.worker: Optional[OnChainAnalysisWorker] = None
        self._queue: list = []  # Currencies waiting to be processed

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header
        header = QLabel("On Chain Analysis")
        header.setStyleSheet(
            f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};"
        )
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(header)

        # Controls section
        controls_frame = self._create_controls_frame()
        main_layout.addWidget(controls_frame)

        # Splitter for report and log viewer
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Report display
        report_container = QWidget()
        report_layout = QVBoxLayout(report_container)
        report_layout.setContentsMargins(0, 0, 0, 0)

        report_label = QLabel("Analysis Report")
        report_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};"
        )
        report_layout.addWidget(report_label)

        self.report_display = QPlainTextEdit()
        self.report_display.setReadOnly(True)
        self.report_display.setFont(QFont("Consolas", 10))
        self.report_display.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        self.report_display.setPlaceholderText(
            "Click 'Load Analysis' to generate the on-chain analysis report..."
        )
        self.report_display.setMinimumHeight(300)
        report_layout.addWidget(self.report_display)

        splitter.addWidget(report_container)

        # Log viewer
        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)

        log_label = QLabel("Output")
        log_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};"
        )
        log_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(80)
        log_layout.addWidget(self.log_viewer)

        splitter.addWidget(log_container)
        splitter.setSizes([600, 150])

        main_layout.addWidget(splitter, 1)

    def _create_controls_frame(self) -> QFrame:
        """Create the controls section frame."""
        controls_frame = QFrame()
        controls_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(12)

        # Currency checkboxes (replaces single dropdown — supports queued multi-run)
        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        controls_layout.addWidget(currency_label)

        checkbox_style = f"""
            QCheckBox {{
                color: {Colors.TEXT_SECONDARY};
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {Colors.BORDER};
                background-color: {Colors.INPUT_BACKGROUND};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
        """

        self.btc_checkbox = QCheckBox("BTC")
        self.btc_checkbox.setChecked(True)
        self.btc_checkbox.setStyleSheet(checkbox_style)
        controls_layout.addWidget(self.btc_checkbox)

        self.eth_checkbox = QCheckBox("ETH")
        self.eth_checkbox.setChecked(False)
        self.eth_checkbox.setStyleSheet(checkbox_style)
        controls_layout.addWidget(self.eth_checkbox)

        controls_layout.addStretch()

        # Load button
        self.load_btn = QPushButton("Load Analysis")
        self.load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        controls_layout.addWidget(self.load_btn)

        # Clear button
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        controls_layout.addWidget(self.clear_btn)

        # View Flow Charts button
        self.view_charts_btn = QPushButton("View Flow Charts")
        self.view_charts_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
            QPushButton:disabled {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_DISABLED};
            }}
        """)
        self.view_charts_btn.setEnabled(False)
        self.view_charts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        controls_layout.addWidget(self.view_charts_btn)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        controls_layout.addWidget(self.status_label)

        return controls_frame

    def _setup_logging(self) -> None:
        """Set up logging to the GUI log viewer."""
        self.gui_handler = GuiLogHandler(self.log_viewer)
        root_logger = logging.getLogger()
        root_logger.addHandler(self.gui_handler)

    def _connect_signals(self) -> None:
        """Connect widget signals to slots."""
        self.load_btn.clicked.connect(self._load_analysis)
        self.clear_btn.clicked.connect(self._clear)
        self.view_charts_btn.clicked.connect(self._open_flow_charts)

    def _clear(self) -> None:
        """Clear report display and log viewer."""
        self.report_display.clear()
        self.log_viewer.clear_logs()
        self.report_text = ""
        self.status_label.setText("")

    def _load_analysis(self) -> None:
        """Build queue from selected currencies and start processing."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("Analysis already in progress")
            return

        selected = []
        if self.btc_checkbox.isChecked():
            selected.append("BTC")
        if self.eth_checkbox.isChecked():
            selected.append("ETH")

        if not selected:
            self.log_viewer.log_warning("Select at least one currency (BTC / ETH)")
            return

        self._queue = selected
        self.report_display.clear()
        self.report_text = ""
        self.load_btn.setEnabled(False)
        self.view_charts_btn.setEnabled(False)

        self._start_next_in_queue()

    def _start_next_in_queue(self) -> None:
        """Pop the next currency from the queue and start its worker."""
        if not self._queue:
            # All done
            self.load_btn.setEnabled(True)
            self.view_charts_btn.setEnabled(True)
            self.status_label.setText("Ready")
            self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")
            return

        currency = self._queue.pop(0)
        remaining = len(self._queue)
        queue_info = f" ({remaining} more queued)" if remaining else ""

        self.log_viewer.log_info(
            f"Starting on-chain analysis for {currency}...{queue_info}"
        )
        self.status_label.setText(f"Running {currency}...")
        self.status_label.setStyleSheet(f"color: {Colors.WARNING};")

        self.worker = OnChainAnalysisWorker(currency=currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_analysis_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_analysis_finished(self, report: str) -> None:
        """Append completed report and start next queued currency if any."""
        if self.report_text:
            self.report_text += "\n\n" + "=" * 80 + "\n\n" + report
        else:
            self.report_text = report

        self.report_display.setPlainText(self.report_text)
        self.log_viewer.log_info("Analysis report generated successfully")

        # Continue with next in queue
        self._start_next_in_queue()

    def _on_progress(self, message: str) -> None:
        """Handle progress updates."""
        self.log_viewer.log_info(message)

    def _on_error(self, error_message: str) -> None:
        """Log error and continue with next queued currency if any."""
        self.log_viewer.log_error(f"Failed: {error_message}")
        self.status_label.setText("Error")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")

        # Still attempt remaining currencies in the queue
        self._start_next_in_queue()

    def _open_flow_charts(self) -> None:
        """Open fullscreen flow charts window."""
        # Use BTC if checked, otherwise ETH
        currency = "BTC" if self.btc_checkbox.isChecked() else "ETH"
        repository = DatabaseRepository()

        dialog = FlowChartsWindow(currency, repository, parent=self)
        dialog.exec()
