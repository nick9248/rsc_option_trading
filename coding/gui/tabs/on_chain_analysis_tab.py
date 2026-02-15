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
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.dialogs.flow_charts_window import FlowChartsWindow
from coding.gui.theme.colors import Colors
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


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
        """Execute data fetch and analysis."""
        try:
            # Always initialize repository for buy/sell flow
            repository = DatabaseRepository()

            with DeribitApiService() as api_service:
                service = OnChainAnalysisService(api_service, repository=repository)
                report = service.fetch_and_analyze(
                    currency=self.currency,
                    progress_callback=lambda msg: self.progress.emit(msg)
                )
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

        # Currency selection
        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        controls_layout.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["ETH", "BTC"])
        self.currency_combo.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        controls_layout.addWidget(self.currency_combo)

        controls_layout.addStretch()

        # Load button
        self.load_btn = QPushButton("Load Analysis")
        self.load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        controls_layout.addWidget(self.load_btn)

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
        self.view_charts_btn.clicked.connect(self._open_flow_charts)

    def _load_analysis(self) -> None:
        """Load on-chain analysis data."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A request is already in progress")
            return

        currency = self.currency_combo.currentText()

        self.log_viewer.log_info(f"Starting on-chain analysis for {currency}...")
        self.log_viewer.log_info("Note: Analysis includes GEX/DEX and Buy/Sell Flow")

        self.load_btn.setEnabled(False)
        self.view_charts_btn.setEnabled(False)
        self.status_label.setText("Loading...")
        self.status_label.setStyleSheet(f"color: {Colors.WARNING};")
        self.report_display.clear()

        self.worker = OnChainAnalysisWorker(currency=currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_analysis_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_analysis_finished(self, report: str) -> None:
        """Handle successful analysis completion."""
        self.report_text = report
        self.report_display.setPlainText(report)

        self.load_btn.setEnabled(True)
        self.view_charts_btn.setEnabled(True)
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")

        self.log_viewer.log_info("Analysis report generated successfully")

    def _on_progress(self, message: str) -> None:
        """Handle progress updates."""
        self.log_viewer.log_info(message)

    def _on_error(self, error_message: str) -> None:
        """Handle error."""
        self.load_btn.setEnabled(True)
        self.status_label.setText("Error")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
        self.log_viewer.log_error(f"Failed: {error_message}")

    def _open_flow_charts(self) -> None:
        """Open fullscreen flow charts window."""
        currency = self.currency_combo.currentText()
        repository = DatabaseRepository()

        dialog = FlowChartsWindow(currency, repository, parent=self)
        dialog.exec()
