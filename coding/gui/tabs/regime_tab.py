"""
Market Regime Detection tab.

Provides interface to:
- Detect current market regime for BTC or ETH
- View regime classification and confidence
- See component scores (trend, volatility, momentum, on-chain, sentiment)
- View detailed reasoning and metrics
"""

import logging
from typing import Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
    QGroupBox,
    QGridLayout,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.theme.colors import Colors
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.regime.regime_detection_service import RegimeDetectionService
from coding.core.database.repository import DatabaseRepository


logger = logging.getLogger(__name__)


class RegimeDetectionWorker(QThread):
    """Worker thread for regime detection."""

    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent: Optional[QWidget] = None):
        """
        Initialize the worker.

        Args:
            currency: Currency symbol (BTC, ETH).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        """Execute regime detection."""
        try:
            with DeribitApiService() as api_service:
                repository = DatabaseRepository()
                service = RegimeDetectionService(
                    api_service=api_service,
                    repository=repository
                )

                self.progress.emit(f"Detecting market regime for {self.currency}...")
                result = service.detect_regime(self.currency)

                if "error" in result:
                    self.error.emit(result["error"])
                else:
                    self.finished.emit(result)

        except Exception as e:
            logger.error(f"Regime detection failed: {e}", exc_info=True)
            self.error.emit(str(e))


class RegimeTab(QWidget):
    """Tab for market regime detection."""

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the regime detection tab.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.worker: Optional[RegimeDetectionWorker] = None
        self.latest_result: Optional[dict] = None

        self._init_ui()
        self._setup_logging()

    def _init_ui(self) -> None:
        """Initialize the user interface."""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header_label = QLabel("Market Regime Detection")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(header_label)

        # Control panel
        control_frame = self._create_control_panel()
        layout.addWidget(control_frame)

        # Results section
        results_frame = self._create_results_section()
        layout.addWidget(results_frame)

        # Log viewer
        self.log_viewer = LogViewer()
        layout.addWidget(self.log_viewer, stretch=1)

        self.setLayout(layout)

    def _setup_logging(self) -> None:
        """Set up logging to the GUI log viewer."""
        gui_handler = GuiLogHandler(self.log_viewer)
        gui_handler.setFormatter(
            logging.Formatter("%(message)s")
        )
        logger.addHandler(gui_handler)

    def _create_control_panel(self) -> QFrame:
        """Create the control panel with currency selector and detect button."""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            f"background-color: {Colors.SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: 4px;"
        )

        layout = QHBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)

        # Currency selector
        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["BTC", "ETH"])
        self.currency_combo.setStyleSheet(
            f"background-color: {Colors.INPUT_BACKGROUND}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"padding: 5px; "
            f"min-width: 100px;"
        )
        layout.addWidget(self.currency_combo)

        layout.addStretch()

        # Detect button
        self.detect_button = QPushButton("Detect Regime")
        self.detect_button.setStyleSheet(
            f"background-color: {Colors.BUTTON_PRIMARY}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: none; "
            f"padding: 8px 20px; "
            f"border-radius: 4px; "
            f"font-weight: bold;"
        )
        self.detect_button.clicked.connect(self._on_detect_clicked)
        layout.addWidget(self.detect_button)

        frame.setLayout(layout)
        return frame

    def _create_results_section(self) -> QFrame:
        """Create the results display section."""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            f"background-color: {Colors.SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: 4px;"
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)

        # Regime summary
        summary_group = QGroupBox("Regime Summary")
        summary_group.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: bold;")
        summary_layout = QGridLayout()

        # Regime label
        self.regime_label = QLabel("--")
        regime_font = QFont()
        regime_font.setPointSize(18)
        regime_font.setBold(True)
        self.regime_label.setFont(regime_font)
        self.regime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.regime_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; padding: 10px;")
        summary_layout.addWidget(self.regime_label, 0, 0, 1, 2)

        # Confidence and Score
        self.confidence_label = QLabel("Confidence: --")
        self.confidence_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        summary_layout.addWidget(self.confidence_label, 1, 0)

        self.score_label = QLabel("Score: --")
        self.score_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        summary_layout.addWidget(self.score_label, 1, 1)

        self.price_label = QLabel("Price: --")
        self.price_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        summary_layout.addWidget(self.price_label, 2, 0)

        self.detected_at_label = QLabel("Detected: --")
        self.detected_at_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        summary_layout.addWidget(self.detected_at_label, 2, 1)

        summary_group.setLayout(summary_layout)
        layout.addWidget(summary_group)

        # Component scores table
        scores_group = QGroupBox("Component Scores")
        scores_group.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: bold;")
        scores_layout = QVBoxLayout()

        self.scores_table = QTableWidget()
        self.scores_table.setColumnCount(2)
        self.scores_table.setHorizontalHeaderLabels(["Component", "Score"])
        self.scores_table.setRowCount(5)
        self.scores_table.setStyleSheet(
            f"background-color: {Colors.INPUT_BACKGROUND}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"gridline-color: {Colors.BORDER};"
        )
        self.scores_table.horizontalHeader().setStyleSheet(
            f"background-color: {Colors.SURFACE}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"font-weight: bold;"
        )
        self.scores_table.horizontalHeader().setStretchLastSection(True)
        self.scores_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Set component names
        components = ["Trend", "Volatility", "Momentum", "On-Chain", "Sentiment"]
        for i, component in enumerate(components):
            self.scores_table.setItem(i, 0, QTableWidgetItem(component))
            self.scores_table.setItem(i, 1, QTableWidgetItem("--"))

        scores_layout.addWidget(self.scores_table)
        scores_group.setLayout(scores_layout)
        layout.addWidget(scores_group)

        # Reasoning text
        reasoning_group = QGroupBox("Analysis")
        reasoning_group.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: bold;")
        reasoning_layout = QVBoxLayout()

        self.reasoning_text = QTextEdit()
        self.reasoning_text.setReadOnly(True)
        self.reasoning_text.setMaximumHeight(100)
        self.reasoning_text.setStyleSheet(
            f"background-color: {Colors.INPUT_BACKGROUND}; "
            f"color: {Colors.TEXT_SECONDARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"padding: 5px;"
        )
        self.reasoning_text.setText("Run detection to see analysis...")

        reasoning_layout.addWidget(self.reasoning_text)
        reasoning_group.setLayout(reasoning_layout)
        layout.addWidget(reasoning_group)

        frame.setLayout(layout)
        return frame

    def _on_detect_clicked(self) -> None:
        """Handle detect button click."""
        if self.worker and self.worker.isRunning():
            logger.warning("Detection already in progress")
            return

        currency = self.currency_combo.currentText()

        self.detect_button.setEnabled(False)
        self.detect_button.setText("Detecting...")

        # Clear previous results
        self._clear_results()

        # Start worker
        self.worker = RegimeDetectionWorker(currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_detection_finished)
        self.worker.error.connect(self._on_detection_error)
        self.worker.start()

    def _on_progress(self, message: str) -> None:
        """Handle progress updates."""
        logger.info(message)

    def _on_detection_finished(self, result: dict) -> None:
        """Handle detection completion."""
        self.latest_result = result
        self._display_results(result)

        self.detect_button.setEnabled(True)
        self.detect_button.setText("Detect Regime")

        logger.info("Regime detection completed successfully")

    def _on_detection_error(self, error_message: str) -> None:
        """Handle detection error."""
        logger.error(f"Detection failed: {error_message}")

        self.detect_button.setEnabled(True)
        self.detect_button.setText("Detect Regime")

    def _display_results(self, result: dict) -> None:
        """Display detection results."""
        # Regime summary
        regime = result.get("regime", "Unknown")
        confidence = result.get("confidence", 0)
        composite_score = result.get("composite_score", 0)
        current_price = result.get("current_price", 0)
        detected_at = result.get("detected_at", datetime.now())

        # Color code regime
        regime_color = self._get_regime_color(regime)
        self.regime_label.setText(regime)
        self.regime_label.setStyleSheet(f"color: {regime_color}; padding: 10px;")

        self.confidence_label.setText(f"Confidence: {confidence:.1f}%")
        self.score_label.setText(f"Score: {composite_score:.1f}")
        self.price_label.setText(f"Price: ${current_price:,.2f}")
        self.detected_at_label.setText(f"Detected: {detected_at.strftime('%Y-%m-%d %H:%M:%S')}")

        # Component scores
        component_scores = result.get("component_scores", {})
        components = ["trend", "volatility", "momentum", "onchain", "sentiment"]
        for i, component in enumerate(components):
            score = component_scores.get(component, 0)
            score_item = QTableWidgetItem(f"{score:.1f}")
            score_color = self._get_score_color(score)
            score_item.setForeground(score_color)
            self.scores_table.setItem(i, 1, score_item)

        # Reasoning
        reasoning = result.get("reasoning", "")
        self.reasoning_text.setText(reasoning)

    def _clear_results(self) -> None:
        """Clear displayed results."""
        self.regime_label.setText("--")
        self.regime_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; padding: 10px;")
        self.confidence_label.setText("Confidence: --")
        self.score_label.setText("Score: --")
        self.price_label.setText("Price: --")
        self.detected_at_label.setText("Detected: --")

        for i in range(5):
            self.scores_table.setItem(i, 1, QTableWidgetItem("--"))

        self.reasoning_text.setText("Run detection to see analysis...")

    def _get_regime_color(self, regime: str) -> str:
        """Get color for regime display."""
        if "Strong Bullish" in regime:
            return Colors.PROFIT
        elif "Weak Bullish" in regime:
            return "#00cc66"  # Light green
        elif "Sideways" in regime:
            return Colors.TEXT_SECONDARY
        elif "Weak Bearish" in regime:
            return "#ff9966"  # Light orange
        elif "Strong Bearish" in regime:
            return Colors.LOSS
        else:
            return Colors.TEXT_PRIMARY

    def _get_score_color(self, score: float):
        """Get color for score display."""
        from PySide6.QtGui import QColor

        if score > 50:
            return QColor(Colors.PROFIT)
        elif score > 20:
            return QColor("#00cc66")  # Light green
        elif score > -20:
            return QColor(Colors.TEXT_SECONDARY)
        elif score > -50:
            return QColor("#ff9966")  # Light orange
        else:
            return QColor(Colors.LOSS)
