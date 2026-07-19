# coding/gui/tabs/system_validation_tab.py — full file replacement
"""
System Validation Tab (Health Check)

GUI presentation for the shared health-check registry
(coding/service/health). Local checks run on demand via "Run
Validation"; VPS checks are read from the last-synced
logs/vps_health.json via "Check VPS Health" (the VPS side runs
automatically on cron — see scripts/check_vps_health.py).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QGroupBox, QTreeWidget, QTreeWidgetItem,
)
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont

from coding.core.health.models import CheckResult, CheckStatus
from scripts.validate_system import SystemValidator

logger = logging.getLogger(__name__)

_STATUS_ICON = {CheckStatus.PASS: "✓", CheckStatus.WARN: "⚠", CheckStatus.FAIL: "✗"}
_CATEGORY_ICON = {CheckStatus.PASS: "🟢", CheckStatus.WARN: "🟡", CheckStatus.FAIL: "🔴"}
_STATUS_RANK = {CheckStatus.PASS: 0, CheckStatus.WARN: 1, CheckStatus.FAIL: 2}


class ValidationWorker(QThread):
    """Worker thread for running the LOCAL health-check registry."""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def run(self):
        """Run validation."""
        try:
            validator = SystemValidator()
            self._setup_log_capture()
            grouped = validator.validate_all()
            self.finished.emit(grouped)
        except Exception as e:
            logger.exception(f"Validation failed: {e}")
            self.error.emit(str(e))

    def _setup_log_capture(self):
        """Setup logging to capture output for GUI."""
        class SignalHandler(logging.Handler):
            def __init__(self, signal):
                super().__init__()
                self.signal = signal

            def emit(self, record):
                self.signal.emit(self.format(record))

        root_logger = logging.getLogger()
        handler = SignalHandler(self.progress)
        handler.setFormatter(logging.Formatter('%(message)s'))
        root_logger.addHandler(handler)


class SystemValidationTab(QWidget):
    """System health tab: category tree of every module's checks, plus VPS health."""

    def __init__(self):
        """Initialize system validation tab."""
        super().__init__()
        self.worker = None
        self.init_ui()

    def init_ui(self):
        """Initialize user interface."""
        layout = QVBoxLayout()

        header = QLabel("System Health")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        desc = QLabel(
            "Every module in one place: API, database (local freshness/gaps/"
            "completeness + VPS sync/continuity), scanner activity, Telegram "
            "delivery, forward-test harnesses, IV-percentile window "
            "freshness, and morning note synthesis."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(10)

        controls_layout = QHBoxLayout()
        self.run_button = QPushButton("Run Validation")
        self.run_button.clicked.connect(self.run_validation)
        self.run_button.setMinimumHeight(40)
        controls_layout.addWidget(self.run_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_output)
        controls_layout.addWidget(self.clear_button)

        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        self.summary_strip = QHBoxLayout()
        self.summary_strip.addStretch()
        layout.addLayout(self.summary_strip)

        self.status_label = QLabel("Click 'Run Validation' to check system health")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        tree_group = QGroupBox("Checks by Category")
        tree_layout = QVBoxLayout()
        self.results_tree = QTreeWidget()
        self.results_tree.setColumnCount(1)
        self.results_tree.setHeaderHidden(True)
        self.results_tree.setMinimumHeight(300)
        tree_layout.addWidget(self.results_tree)
        tree_group.setLayout(tree_layout)
        layout.addWidget(tree_group, 1)

        log_group = QGroupBox("Raw Log")
        log_layout = QVBoxLayout()
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setFontFamily("Consolas")
        self.output_text.setFontPointSize(9)
        self.output_text.setMaximumHeight(150)
        log_layout.addWidget(self.output_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

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

        self.output_text.clear()
        self.results_tree.clear()
        self._clear_summary_strip()
        self.status_label.setText("Running validation...")
        self.run_button.setEnabled(False)

        self.log(f"Starting system validation at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        self.worker = ValidationWorker()
        self.worker.finished.connect(self.on_validation_complete)
        self.worker.error.connect(self.on_validation_error)
        self.worker.progress.connect(self.log)
        self.worker.start()

    def on_validation_complete(self, grouped: Dict[str, List[CheckResult]]):
        """Handle validation completion."""
        self.run_button.setEnabled(True)
        self._populate_tree(grouped)
        self._populate_summary_strip(grouped)

        all_results = [r for results in grouped.values() for r in results]
        passed = sum(1 for r in all_results if r.status == CheckStatus.PASS)
        warnings = sum(1 for r in all_results if r.status == CheckStatus.WARN)
        failed = sum(1 for r in all_results if r.status == CheckStatus.FAIL)

        if failed:
            status, color = f"FAILED: {failed} critical issues found", "red"
        elif warnings:
            status, color = f"WARNING: {warnings} warnings found", "orange"
        else:
            status, color = f"SUCCESS: All {passed} checks passed", "green"

        self.status_label.setText(
            f"<b style='color: {color}'>{status}</b><br>"
            f"Passed: {passed} | Warnings: {warnings} | Failed: {failed}"
        )
        self.log(f"\nValidation completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Validation complete: {status}")

    def on_validation_error(self, error_msg: str):
        """Handle validation error."""
        self.run_button.setEnabled(True)
        self.status_label.setText(f"<b style='color: red'>ERROR: {error_msg}</b>")
        self.log(f"\nERROR: {error_msg}")
        logger.error(f"Validation error: {error_msg}")

    def _populate_tree(self, grouped: Dict[str, List[CheckResult]]):
        """Rebuild the category tree from a fresh grouped result set."""
        self.results_tree.clear()
        for category, results in grouped.items():
            worst = max((r.status for r in results), key=lambda s: _STATUS_RANK[s])
            passed_count = sum(1 for r in results if r.status == CheckStatus.PASS)

            parent = QTreeWidgetItem([f"{_CATEGORY_ICON[worst]} {category} ({passed_count}/{len(results)})"])
            self.results_tree.addTopLevelItem(parent)
            if worst != CheckStatus.PASS:
                parent.setExpanded(True)

            for result in results:
                child = QTreeWidgetItem([f"{_STATUS_ICON[result.status]} {result.message}"])
                parent.addChild(child)

    def _populate_summary_strip(self, grouped: Dict[str, List[CheckResult]]):
        """Rebuild the colored per-category summary chips."""
        self._clear_summary_strip()
        for category, results in grouped.items():
            worst = max((r.status for r in results), key=lambda s: _STATUS_RANK[s])
            passed_count = sum(1 for r in results if r.status == CheckStatus.PASS)

            chip = QLabel(f"{_CATEGORY_ICON[worst]} {category} ({passed_count}/{len(results)})")
            chip.setStyleSheet(
                "padding: 3px 8px; border-radius: 4px; font-size: 11px; "
                "background-color: rgba(128,128,128,40);"
            )
            self.summary_strip.insertWidget(self.summary_strip.count() - 1, chip)

    def _clear_summary_strip(self):
        """Remove all chips from the summary strip (keeps the trailing stretch)."""
        while self.summary_strip.count() > 1:
            item = self.summary_strip.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def log(self, message: str):
        """Append message to the raw log."""
        self.output_text.append(message)
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_output(self):
        """Clear tree, chips, and raw log."""
        self.output_text.clear()
        self.results_tree.clear()
        self._clear_summary_strip()
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
            tables = data.get("tables", {})

            try:
                checked_at = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                mins_ago = int((datetime.now() - checked_at).total_seconds() / 60)
                age_str = f"{mins_ago}min ago" if mins_ago < 120 else f"{mins_ago // 60}h ago"
            except Exception:
                age_str = "unknown time"

            if problems:
                color, summary = "red", f"{len(problems)} problem(s) — {passed}/{total} checks OK"
            else:
                color, summary = "green", f"{passed}/{total} checks OK"

            self.vps_status_label.setText(
                f"<b style='color:{color}'>{summary}</b><br>"
                f"Last checked: {timestamp} ({age_str})"
            )

            lines = []
            for r in results:
                icon = "OK " if r["ok"] else "ERR"
                lines.append(f"[{icon}] {r['message']}")
            if tables:
                lines.append("")
                lines.append("VPS TABLE ROW COUNTS (used by Database — VPS Sync check):")
                for table, info in tables.items():
                    lines.append(f"  {table}: {info.get('rows', '?')} rows")
            if problems:
                lines.append("")
                lines.append("PROBLEMS:")
                for p in problems:
                    lines.append(f"  - {p}")

            self.vps_detail_text.setPlainText("\n".join(lines))

        except Exception as e:
            self.vps_status_label.setText(f"<b style='color:red'>Failed to read health data: {e}</b>")
            logger.error(f"VPS health load failed: {e}")
