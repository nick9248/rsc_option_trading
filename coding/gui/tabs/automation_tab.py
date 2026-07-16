"""
Automation tab.

Manual trigger point for scanner jobs. v1 ships a single job — "Straddle
Scanner (manual)" — that runs StraddleScanService.scan() in a background
thread and displays the ranked results plus a preview of the Telegram-style
alert text. No scheduling, no DB recording, no Telegram send yet (increment
2) — this tab only triggers a single scan and displays its output.

All business logic lives in StraddleScanService; this tab only orchestrates
the worker thread and renders the returned dict. No scan math here.
"""

import logging
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFrame, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QGroupBox,
)
from PySide6.QtCore import Qt, QThread, Signal

from coding.gui.theme.colors import Colors
from coding.service.scanner.straddle_scan_service import StraddleScanService

logger = logging.getLogger(__name__)

# Job registry: dropdown label -> currently only one job exists in v1.
# A future job just adds an entry here plus a branch in _run_job.
_JOBS = ["Straddle Scanner (manual)"]


class StraddleScanWorker(QThread):
    """Runs StraddleScanService.scan() off the GUI thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = StraddleScanService()
            result = service.scan(self.currency)
            self.finished.emit(result)
        except Exception as error:
            logger.exception(f"Straddle scan failed for {self.currency}")
            self.error.emit(str(error))


class AutomationTab(QWidget):
    """Automation tab: pick a job + currency, run it, review the results."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.worker: Optional[StraddleScanWorker] = None
        self._last_scan_result: Optional[Dict[str, Any]] = None
        self._scan_service = StraddleScanService()  # for format_alert only (pure formatting, no I/O)

        self._setup_ui()
        self._connect_signals()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel("Automation")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(header)

        desc = QLabel(
            "Manually trigger a scanner job and review its output. "
            "Nothing here is scheduled, recorded, or sent yet — this is a "
            "local preview of what an automated run would find."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(desc)

        layout.addWidget(self._build_controls())
        layout.addWidget(self._build_results_table(), stretch=1)
        layout.addWidget(self._build_alert_preview())

        self.setLayout(layout)

    def _build_controls(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(12)

        job_label = QLabel("Job:")
        job_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        row.addWidget(job_label)

        self.job_combo = QComboBox()
        self.job_combo.addItems(_JOBS)
        self.job_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.job_combo, stretch=2)

        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        row.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["BTC", "ETH"])
        row.addWidget(self.currency_combo)

        self.run_button = QPushButton("Run")
        self.run_button.setMinimumHeight(32)
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.run_button)

        row.addStretch()

        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        row.addWidget(self.status_label)

        return frame

    def _build_results_table(self) -> QGroupBox:
        group = QGroupBox("Ranked Expiries")
        layout = QVBoxLayout()

        columns = [
            "Expiry", "DTE", "Best Strike", "Cost $ (%)", "Breakevens",
            "IV %ile", "RV/IV", "VRP", "Score", "Chart",
        ]
        self.results_table = QTableWidget(0, len(columns))
        self.results_table.setHorizontalHeaderLabels(columns)
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setMinimumHeight(220)
        layout.addWidget(self.results_table)

        group.setLayout(layout)
        return group

    def _build_alert_preview(self) -> QGroupBox:
        group = QGroupBox("Alert Preview (top result — what a Telegram send would say)")
        layout = QVBoxLayout()

        self.alert_text = QTextEdit()
        self.alert_text.setReadOnly(True)
        self.alert_text.setFontFamily("Consolas")
        self.alert_text.setFontPointSize(9)
        self.alert_text.setMaximumHeight(220)
        self.alert_text.setPlaceholderText("Run a scan to preview the alert text.")
        layout.addWidget(self.alert_text)

        group.setLayout(layout)
        return group

    def _connect_signals(self) -> None:
        self.run_button.clicked.connect(self._run_job)

    # ── Job execution ─────────────────────────────────────────────────────────

    def _run_job(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            logger.warning("A scan is already running")
            return

        job = self.job_combo.currentText()
        currency = self.currency_combo.currentText()

        if job != "Straddle Scanner (manual)":
            # Only one job exists today; guard so a future dropdown addition
            # can't silently fall through without a handler.
            self.status_label.setText(f"No handler for job: {job}")
            self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
            return

        self.results_table.setRowCount(0)
        self.alert_text.clear()
        self.run_button.setEnabled(False)
        self.status_label.setText(f"Scanning {currency}...")
        self.status_label.setStyleSheet(f"color: {Colors.WARNING};")

        self.worker = StraddleScanWorker(currency)
        self.worker.finished.connect(self._on_scan_finished)
        self.worker.error.connect(self._on_scan_error)
        self.worker.start()

    def _on_scan_finished(self, result: Dict[str, Any]) -> None:
        self.run_button.setEnabled(True)
        self._last_scan_result = result

        expiries = result.get("expiries", [])
        if not expiries:
            self.status_label.setText("No qualifying candidates found")
            self.status_label.setStyleSheet(f"color: {Colors.WARNING};")
            self.alert_text.setPlainText(self._scan_service.format_alert(result))
            return

        self.status_label.setText(f"{len(expiries)} expir{'y' if len(expiries) == 1 else 'ies'} ranked")
        self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")

        self._populate_table(expiries)
        self.alert_text.setPlainText(self._scan_service.format_alert(result))

    def _on_scan_error(self, error_message: str) -> None:
        self.run_button.setEnabled(True)
        self.status_label.setText("Scan failed")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
        self.alert_text.setPlainText(f"ERROR: {error_message}")

    # ── Table rendering ───────────────────────────────────────────────────────

    def _populate_table(self, expiries) -> None:
        self.results_table.setRowCount(len(expiries))
        for row, entry in enumerate(expiries):
            best = entry["best"]
            future_price = entry["F"] or 0.0
            cost_pct = (best["cost_usd"] / future_price * 100.0) if future_price else 0.0

            iv_percentile = entry["iv_percentile"]
            iv_percentile_str = f"{iv_percentile:.1f}%" if iv_percentile is not None else "N/A"

            rv_iv_ratio = entry["rv_iv_ratio"]
            rv_iv_str = f"{rv_iv_ratio:.2f}" if rv_iv_ratio is not None else "N/A"

            vrp = entry["vrp"]
            vrp_str = f"{vrp:+.1f}" if vrp is not None else "N/A"

            score = best.get("min_pnl_score")
            score_str = f"${score:,.0f}" if score is not None else "N/A"

            values = [
                entry["expiry"],
                f"{entry['dte']:.0f}",
                f"{best['strike']:,.0f}",
                f"${best['cost_usd']:,.2f} ({cost_pct:.1f}%)",
                f"{best['breakeven_down']:,.0f} / {best['breakeven_up']:,.0f}",
                iv_percentile_str,
                rv_iv_str,
                vrp_str,
                score_str,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.results_table.setItem(row, col, item)

            chart_label = QLabel(f'<a href="{best["deribit_url"]}">Open</a>')
            chart_label.setTextFormat(Qt.TextFormat.RichText)
            chart_label.setOpenExternalLinks(True)
            chart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chart_label.setStyleSheet(f"color: {Colors.ACCENT};")
            self.results_table.setCellWidget(row, len(values), chart_label)
