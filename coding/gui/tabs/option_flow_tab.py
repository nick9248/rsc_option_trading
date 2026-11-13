"""
Option Flow tab: the Laevitas-style buy/sell volume per strike of the Deribit option trades, interactive.

Pick currency, window, expiry (all, or one that traded in the window, expired ones included) and unit, press Generate, and
the chart appears below; hovering a strike lists its eight series. Strike, calls left, puts right, two bars per side
(Buy, Sell), each bar = blocked part next to the axis + screen part.

All work happens in ``OptionFlowService`` (database read, aggregation, Plotly figure) run in a worker thread so the window
never freezes; this module only shows what the service returns. The data is the local mirror of the VPS table
``historical_trades`` (sync it first in the Database tab or with scripts/sync_from_vps.py).
"""

import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from coding.gui.components.menthorq_widgets import panel_frame
from coding.gui.theme.colors import Colors
from coding.service.option_flow.option_flow_service import OptionFlowService, StrikeFlowReport

logger = logging.getLogger(__name__)

CURRENCIES = ["BTC", "ETH"]
WINDOW_HOURS = [6, 12, 24, 48, 72]
DEFAULT_WINDOW_HOURS = 24
UNIT_LABELS = {"Notional (USD)": "notional", "Contracts": "contracts", "Trades": "trades"}
ALL_EXPIRATIONS = "All expirations"
STALE_DATA_HOURS = 3        # warn when the newest stored trade is older than this
HTML_DIRECTORY = Path(tempfile.gettempdir()) / "option_flow"

DESCRIPTION = ("Buy/sell volume per strike of the Deribit option trades (the taker's side), like Laevitas: calls left, "
               "puts right, Buy and Sell bars, each = blocked part (Deribit block trades) next to the axis + screen part. "
               "Notional = contracts x strike. Reads the local copy of the VPS trades (sync it first in the Database "
               "tab or with scripts/sync_from_vps.py).")


@dataclass
class OptionFlowRequest:
    """What to draw."""

    currency: str
    hours: int
    expiry: Optional[str]
    unit: str


@dataclass
class OptionFlowResult:
    """What the worker hands back: the report, the expirations to offer and the chart page on disk."""

    report: StrikeFlowReport
    expirations: List[str]
    html_path: Path


class OptionFlowWorker(QThread):
    """Reads, aggregates and draws in a background thread."""

    succeeded = Signal(object)   # OptionFlowResult
    failed = Signal(str)

    def __init__(self, request: OptionFlowRequest, service_factory: Callable[[], OptionFlowService] = OptionFlowService,
                 html_directory: Path = HTML_DIRECTORY, parent: Optional[QWidget] = None):
        """
        Args:
            request: Currency, window, expiry and unit.
            service_factory: Builds the service (a fake in tests).
            html_directory: Where the chart page is written.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.request = request
        self.service_factory = service_factory
        self.html_directory = html_directory

    def run(self) -> None:
        """Emit ``succeeded`` with an ``OptionFlowResult`` or ``failed`` (class: message)."""
        try:
            request = self.request
            service = self.service_factory()
            report = service.strike_flow(request.currency, request.hours, request.expiry)
            expirations = service.traded_expirations(request.currency, request.hours, report.window_end)
            figure = service.build_figure(report, request.unit)
            self.html_directory.mkdir(parents=True, exist_ok=True)
            html_path = self.html_directory / f"{request.currency}_{request.hours}h_{request.expiry or 'all'}_{request.unit}_{time.time_ns()}.html"
            figure.write_html(html_path, include_plotlyjs=True, config={"displaylogo": False})
            self.succeeded.emit(OptionFlowResult(report, expirations, html_path))
        except Exception as error:
            logger.exception("Option flow chart failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


class OptionFlowTab(QWidget):
    """Controls on top, status and data-age warning, then the interactive chart."""

    def __init__(self, parent: Optional[QWidget] = None,
                 service_factory: Callable[[], OptionFlowService] = OptionFlowService,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """
        Args:
            parent: Parent widget.
            service_factory: Builds the service (a fake in tests).
            clock: Wall clock (UTC-aware), for the data-age warning.
        """
        super().__init__(parent)
        self.service_factory = service_factory
        self.clock = clock
        self.worker: Optional[OptionFlowWorker] = None
        self._setup_ui()
        self.generate_button.clicked.connect(self._generate)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("Option Flow")
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
        self.currency_combo = QComboBox()
        self.currency_combo.addItems(CURRENCIES)
        self.window_combo = QComboBox()
        for hours in WINDOW_HOURS:
            self.window_combo.addItem(f"Last {hours} h", hours)
        self.window_combo.setCurrentIndex(WINDOW_HOURS.index(DEFAULT_WINDOW_HOURS))
        self.expiry_combo = QComboBox()
        self.expiry_combo.addItem(ALL_EXPIRATIONS, None)
        self.unit_combo = QComboBox()
        for label, unit in UNIT_LABELS.items():
            self.unit_combo.addItem(label, unit)
        for caption, combo in (("Currency", self.currency_combo), ("Window", self.window_combo),
                               ("Expiry", self.expiry_combo), ("Unit", self.unit_combo)):
            controls.addWidget(QLabel(caption))
            controls.addWidget(combo)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        controls.addWidget(self.generate_button)
        controls.addStretch(1)
        frame_layout.addLayout(controls)

        self.status_label = QLabel("Nothing generated yet. The expiry list fills after the first Generate.")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frame_layout.addWidget(self.status_label)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {Colors.WARNING};")
        self.warning_label.setVisible(False)
        frame_layout.addWidget(self.warning_label)
        main_layout.addWidget(frame)

        self.chart_view = QWebEngineView()
        self.chart_view.setMinimumHeight(400)
        main_layout.addWidget(self.chart_view, 1)

    def _set_status(self, text: str, color: str) -> None:
        """Status line under the controls."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def current_request(self) -> OptionFlowRequest:
        """The request the controls describe."""
        return OptionFlowRequest(self.currency_combo.currentText(), self.window_combo.currentData(),
                                 self.expiry_combo.currentData(), self.unit_combo.currentData())

    def _generate(self) -> None:
        """Start a run in the worker thread."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.generate_button.setEnabled(False)
        self.warning_label.setVisible(False)
        self._set_status("Reading the trades and drawing the chart...", Colors.WARNING)
        self.worker = OptionFlowWorker(self.current_request(), self.service_factory, parent=self)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_succeeded(self, result: OptionFlowResult) -> None:
        """Show the chart, refresh the expiry list and warn when the stored data is old."""
        report = result.report
        self._fill_expiries(result.expirations, report.expiry)
        self.chart_view.setUrl(QUrl.fromLocalFile(str(result.html_path.resolve())))
        flow = report.flow
        self._set_status(f"{flow.trades_used} trades, {len(flow.strikes)} strikes, "
                         f"{report.window_start:%Y-%m-%d %H:%M} to {report.window_end:%Y-%m-%d %H:%M} UTC.",
                         Colors.SUCCESS if flow.trades_used else Colors.WARNING)
        warnings = []
        if report.newest_trade is None:
            warnings.append("no stored trades for this currency; sync the database first")
        elif self.clock() - report.newest_trade > timedelta(hours=STALE_DATA_HOURS):
            warnings.append(f"the newest stored trade is from {report.newest_trade:%Y-%m-%d %H:%M} UTC; the end of the "
                            "window is later, so sync the database for the latest trades")
        if report.first_block_trade is None or report.window_start < report.first_block_trade:
            warnings.append("block trades were not stored for the whole window, so some blocks appear as screen volume")
        if flow.trades_skipped:
            warnings.append(f"{flow.trades_skipped} trades were skipped (unknown side or size)")
        self.warning_label.setText("\n".join(f"Warning: {warning}" for warning in warnings))
        self.warning_label.setVisible(bool(warnings))
        self.generate_button.setEnabled(True)

    def _fill_expiries(self, expirations: List[str], selected: Optional[str]) -> None:
        """Offer the expirations of the window; keep the selected one even if it is no longer listed."""
        labels = list(expirations)
        if selected is not None and selected not in labels:
            labels.append(selected)
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        self.expiry_combo.addItem(ALL_EXPIRATIONS, None)
        for label in labels:
            self.expiry_combo.addItem(label, label)
        self.expiry_combo.setCurrentIndex(0 if selected is None else self.expiry_combo.findData(selected))
        self.expiry_combo.blockSignals(False)

    def closeEvent(self, event) -> None:
        """Wait for a running worker so the thread is never destroyed while it runs."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(10000)
        super().closeEvent(event)

    def _on_failed(self, message: str) -> None:
        """Show a failed run as one error line."""
        self._set_status(f"Failed: {message}", Colors.ERROR)
        self.generate_button.setEnabled(True)
