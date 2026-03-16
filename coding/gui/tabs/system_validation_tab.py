"""
System Validation Tab

Provides GUI interface to run comprehensive system health checks including:
- API connectivity
- Database health
- Daemon status
- Data freshness
- Collection quality
"""

import json
import logging
from datetime import datetime
from pathlib import Path
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

        # VPS Health section
        vps_group = QGroupBox("VPS Health (last sync)")
        vps_layout = QVBoxLayout()

        vps_controls = QHBoxLayout()
        self.vps_refresh_btn = QPushButton("Check VPS Health")
        self.vps_refresh_btn.setMinimumHeight(36)
        self.vps_refresh_btn.clicked.connect(self._load_vps_health)
        vps_controls.addWidget(self.vps_refresh_btn)
        vps_controls.addStretch()
        vps_layout.addLayout(vps_controls)

        self.vps_status_label = QLabel("Run sync first to get latest VPS health data.")
        self.vps_status_label.setWordWrap(True)
        vps_layout.addWidget(self.vps_status_label)

        self.vps_detail_text = QTextEdit()
        self.vps_detail_text.setReadOnly(True)
        self.vps_detail_text.setFontFamily("Consolas")
        self.vps_detail_text.setFontPointSize(9)
        self.vps_detail_text.setMaximumHeight(200)
        vps_layout.addWidget(self.vps_detail_text)

        vps_group.setLayout(vps_layout)
        layout.addWidget(vps_group)

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

    def _load_vps_health(self):
        """Load and display VPS health from local vps_health.json."""
        health_path = Path(__file__).parents[3] / "logs" / "vps_health.json"

        if not health_path.exists():
            self.vps_status_label.setText(
                "<b style='color:orange'>No VPS health data found.</b> "
                "Run 'Sync from VPS' in the Database tab first."
            )
            self.vps_detail_text.clear()
            return

        try:
            data = json.loads(health_path.read_text())
            timestamp = data.get("timestamp", "unknown")
            passed = data.get("passed", 0)
            total = data.get("total", 0)
            problems = data.get("problems", [])
            results = data.get("results", [])

            # Minutes since last check
            try:
                from datetime import timezone
                checked_at = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                mins_ago = int((datetime.now(timezone.utc) - checked_at).total_seconds() / 60)
                age_str = f"{mins_ago}min ago" if mins_ago < 120 else f"{mins_ago//60}h ago"
            except Exception:
                age_str = "unknown time"

            if problems:
                color = "red"
                summary = f"❌ {len(problems)} problem(s) — {passed}/{total} checks OK"
            else:
                color = "green"
                summary = f"✅ {passed}/{total} checks OK"

            self.vps_status_label.setText(
                f"<b style='color:{color}'>{summary}</b><br>"
                f"Last checked: {timestamp} ({age_str})"
            )

            # Detail lines
            lines = []
            for r in results:
                icon = "OK " if r["ok"] else "ERR"
                lines.append(f"[{icon}] {r['message']}")
            if problems:
                lines.append("")
                lines.append("PROBLEMS:")
                for p in problems:
                    lines.append(f"  - {p}")

            self.vps_detail_text.setPlainText("\n".join(lines))

        except Exception as e:
            self.vps_status_label.setText(f"<b style='color:red'>Failed to read health data: {e}</b>")
            logger.error(f"VPS health load failed: {e}")
