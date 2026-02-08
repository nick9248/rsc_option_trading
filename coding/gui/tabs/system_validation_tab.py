"""
System Validation Tab

Provides GUI interface to run comprehensive system health checks including:
- API connectivity
- Database health
- Daemon status
- Data freshness
- Collection quality
"""

import logging
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QGroupBox
)
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont

from scripts.validate_system import SystemValidator

logger = logging.getLogger(__name__)


class ValidationWorker(QThread):
    """Worker thread for running system validation."""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def run(self):
        """Run validation."""
        try:
            validator = SystemValidator()

            # Redirect logs to GUI
            self._setup_log_capture()

            results = validator.validate_all()
            self.finished.emit(results)

        except Exception as e:
            logger.exception(f"Validation failed: {e}")
            self.error.emit(str(e))

    def _setup_log_capture(self):
        """Setup logging to capture output for GUI."""
        # Add handler to send logs to progress signal
        class SignalHandler(logging.Handler):
            def __init__(self, signal):
                super().__init__()
                self.signal = signal

            def emit(self, record):
                msg = self.format(record)
                self.signal.emit(msg)

        # Get the root logger
        root_logger = logging.getLogger()
        handler = SignalHandler(self.progress)
        handler.setFormatter(logging.Formatter('%(message)s'))
        root_logger.addHandler(handler)


class SystemValidationTab(QWidget):
    """System validation and health check tab."""

    def __init__(self):
        """Initialize system validation tab."""
        super().__init__()

        self.worker = None
        self.init_ui()

    def init_ui(self):
        """Initialize user interface."""
        layout = QVBoxLayout()

        # Header
        header = QLabel("System Health Validation")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Description
        desc = QLabel(
            "Run comprehensive checks on all system components:\n"
            "• API connectivity and authentication\n"
            "• Database tables and connections\n"
            "• Data collection daemon status\n"
            "• Data freshness and quality\n"
            "• Historical data coverage"
        )
        layout.addWidget(desc)

        layout.addSpacing(10)

        # Controls
        controls_layout = QHBoxLayout()

        self.run_button = QPushButton("Run Validation")
        self.run_button.clicked.connect(self.run_validation)
        self.run_button.setMinimumHeight(40)
        controls_layout.addWidget(self.run_button)

        self.clear_button = QPushButton("Clear Output")
        self.clear_button.clicked.connect(self.clear_output)
        controls_layout.addWidget(self.clear_button)

        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        # Output area
        output_group = QGroupBox("Validation Output")
        output_layout = QVBoxLayout()

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFontFamily("Consolas")
        self.output_text.setFontPointSize(9)
        output_layout.addWidget(self.output_text)

        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        # Status area
        status_group = QGroupBox("Summary")
        status_layout = QVBoxLayout()

        self.status_label = QLabel("Click 'Run Validation' to check system health")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        self.setLayout(layout)

    def run_validation(self):
        """Run system validation."""
        if self.worker and self.worker.isRunning():
            logger.warning("Validation already running")
            return

        # Clear previous output
        self.output_text.clear()
        self.status_label.setText("Running validation...")
        self.run_button.setEnabled(False)

        # Start validation
        self.log(f"Starting system validation at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log("=" * 80)

        self.worker = ValidationWorker()
        self.worker.finished.connect(self.on_validation_complete)
        self.worker.error.connect(self.on_validation_error)
        self.worker.progress.connect(self.log)
        self.worker.start()

    def on_validation_complete(self, results: dict):
        """Handle validation completion."""
        self.run_button.setEnabled(True)

        # Update status
        passed = len(results["passed"])
        warnings = len(results["warnings"])
        failed = len(results["failed"])

        if failed > 0:
            status = f"❌ FAILED: {failed} critical issues found"
            color = "red"
        elif warnings > 0:
            status = f"⚠️ WARNING: {warnings} warnings found"
            color = "orange"
        else:
            status = f"✅ SUCCESS: All {passed} checks passed"
            color = "green"

        self.status_label.setText(
            f"<b style='color: {color}'>{status}</b><br>"
            f"Passed: {passed} | Warnings: {warnings} | Failed: {failed}"
        )

        self.log("\n" + "=" * 80)
        self.log(f"Validation completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        logger.info(f"Validation complete: {status}")

    def on_validation_error(self, error_msg: str):
        """Handle validation error."""
        self.run_button.setEnabled(True)
        self.status_label.setText(f"<b style='color: red'>ERROR: {error_msg}</b>")
        self.log(f"\n❌ ERROR: {error_msg}")
        logger.error(f"Validation error: {error_msg}")

    def log(self, message: str):
        """Append message to output."""
        self.output_text.append(message)
        # Auto-scroll to bottom
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_output(self):
        """Clear output text."""
        self.output_text.clear()
        self.status_label.setText("Output cleared")
