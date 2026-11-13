"""
Dealer Levels tab: the flow-based dealer levels of one expiry next to the standard open-interest based levels.

Flow-based = the dealer is the other side of every taker (dealer position = minus the cumulative taker flow since the
option was listed); gamma exposure per strike gives the call wall, put wall and gamma flip. The plausibility check
(dealer position cannot exceed the open interest) is shown with the result. See
``coding/service/option_flow/dealer_flow_levels_service.py``.

All work happens in ``DealerFlowLevelsService`` in a worker thread; this module only shows what it returns. Data is the local
mirror of the VPS database (sync it first in the Database tab or with scripts/sync_from_vps.py).
"""

import html
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

from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels
from coding.core.analytics.option_flow.dealer_levels_figure import build_dealer_levels_figure
from coding.gui.components.menthorq_widgets import panel_frame
from coding.gui.theme.colors import Colors
from coding.service.option_flow.dealer_flow_levels_service import DealerFlowLevelsService

logger = logging.getLogger(__name__)

CURRENCIES = ["BTC", "ETH"]
STALE_DATA_HOURS = 6        # warn when the chain snapshot is older than this
HTML_DIRECTORY = Path(tempfile.gettempdir()) / "dealer_flow_levels"

DESCRIPTION = ("Dealer gamma levels of one expiry, from the trades: the dealer is assumed to be the other side of every "
               "taker, so the dealer position is minus the cumulative taker flow since listing. Left: that flow-based view; "
               "right: the standard view from open interest (dealers long calls, short puts). The check under the chart "
               "tells whether the flow-based view is plausible (a dealer cannot hold more than the open interest). "
               "No ground truth exists for either view. Reads the local copy of the VPS data (sync it first).")


@dataclass
class DealerLevelsRequest:
    """What to do: list the expirations (``expiry`` None) or compute and draw one."""

    currency: str
    expiry: Optional[str]


@dataclass
class DealerLevelsResult:
    """What the worker hands back."""

    expirations: List[str]
    levels: Optional[DealerFlowLevels] = None
    html_path: Optional[Path] = None


class DealerLevelsWorker(QThread):
    """Lists the expirations or computes one expiry in a background thread."""

    succeeded = Signal(object)   # DealerLevelsResult
    failed = Signal(str)

    def __init__(self, request: DealerLevelsRequest,
                 service_factory: Callable[[], DealerFlowLevelsService] = DealerFlowLevelsService,
                 html_directory: Path = HTML_DIRECTORY, parent: Optional[QWidget] = None):
        """
        Args:
            request: Currency and, to compute, the expiry.
            service_factory: Builds the service (a fake in tests).
            html_directory: Where the chart page is written.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.request = request
        self.service_factory = service_factory
        self.html_directory = html_directory

    def run(self) -> None:
        """Emit ``succeeded`` with a ``DealerLevelsResult`` or ``failed`` (class: message)."""
        try:
            service = self.service_factory()
            expirations = service.expirations(self.request.currency)
            if self.request.expiry is None:
                self.succeeded.emit(DealerLevelsResult(expirations))
                return
            levels = service.levels(self.request.currency, self.request.expiry)
            figure = build_dealer_levels_figure(levels)
            self.html_directory.mkdir(parents=True, exist_ok=True)
            html_path = self.html_directory / f"{levels.currency}_{levels.expiry}_{time.time_ns()}.html"
            figure.write_html(html_path, include_plotlyjs=True, config={"displaylogo": False})
            self.succeeded.emit(DealerLevelsResult(expirations, levels, html_path))
        except Exception as error:
            logger.exception("Dealer levels failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


def _level_text(strike: Optional[float]) -> str:
    """A level as text; "not computed" when there is none."""
    return f"{strike:,.0f}" if strike is not None else "not computed"


def _millions(value: float) -> str:
    """USD as millions with the sign in front, e.g. -$3.4M."""
    return f"{'-' if value < 0 else ''}${abs(value) / 1e6:,.1f}M"


def summary_html(levels: DealerFlowLevels) -> str:
    """The small comparison table shown above the chart (levels of both views and the plausibility check)."""
    total_oi = "not computed" if levels.total_oi_gex is None else _millions(levels.total_oi_gex)
    verdict = ("<span style='color: %s;'>passed</span>" % Colors.SUCCESS if levels.gate_passed else
               "<span style='color: %s;'>NOT passed: %s</span>" % (Colors.WARNING, html.escape(levels.gate_reason or "")))
    return (
        f"<table cellspacing='6'><tr><th></th><th>Call wall</th><th>Put wall</th><th>Gamma flip</th><th>Total GEX per 1%</th></tr>"
        f"<tr><td>Flow-based</td><td>{_level_text(levels.flow_call_wall)}</td><td>{_level_text(levels.flow_put_wall)}</td>"
        f"<td>{_level_text(levels.flow_gamma_flip)}</td><td>{_millions(levels.total_flow_gex)}</td></tr>"
        f"<tr><td>OI-based</td><td>{_level_text(levels.oi_call_wall)}</td><td>{_level_text(levels.oi_put_wall)}</td>"
        f"<td>{_level_text(levels.oi_gamma_flip)}</td><td>{total_oi}</td></tr></table>"
        f"Spot {levels.index_price:,.0f}. Plausibility check of the flow-based view: {verdict} "
        f"({levels.violations} of {levels.options_checked} options exceed their open interest, "
        f"trade coverage {levels.coverage:.1%}).")


class DealerFlowLevelsTab(QWidget):
    """Controls on top, the level table and warnings, then the interactive chart."""

    def __init__(self, parent: Optional[QWidget] = None,
                 service_factory: Callable[[], DealerFlowLevelsService] = DealerFlowLevelsService,
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
        self.worker: Optional[DealerLevelsWorker] = None
        self._expirations_loaded = False
        self._setup_ui()
        self.generate_button.clicked.connect(self._generate)
        self.currency_combo.currentIndexChanged.connect(self._list_expirations)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("Dealer Levels")
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
        self.expiry_combo = QComboBox()
        self.expiry_combo.setMinimumWidth(110)
        for caption, combo in (("Currency", self.currency_combo), ("Expiry", self.expiry_combo)):
            controls.addWidget(QLabel(caption))
            controls.addWidget(combo)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_button.setEnabled(False)
        controls.addWidget(self.generate_button)
        controls.addStretch(1)
        frame_layout.addLayout(controls)

        self.status_label = QLabel("Loading the expirations...")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frame_layout.addWidget(self.status_label)
        self.summary_label = QLabel("")
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.summary_label.setWordWrap(True)
        self.summary_label.setVisible(False)
        frame_layout.addWidget(self.summary_label)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {Colors.WARNING};")
        self.warning_label.setVisible(False)
        frame_layout.addWidget(self.warning_label)
        main_layout.addWidget(frame)

        self.chart_view = QWebEngineView()
        self.chart_view.setMinimumHeight(400)
        main_layout.addWidget(self.chart_view, 1)

    def showEvent(self, event) -> None:
        """List the expirations the first time the tab is shown."""
        super().showEvent(event)
        if not self._expirations_loaded:
            self._expirations_loaded = True
            self._list_expirations()

    def _set_status(self, text: str, color: str) -> None:
        """Status line under the controls."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def current_request(self) -> DealerLevelsRequest:
        """Currency and the selected expiry (None when none is listed)."""
        return DealerLevelsRequest(self.currency_combo.currentText(), self.expiry_combo.currentData())

    def _start(self, request: DealerLevelsRequest) -> None:
        """Run a request in the worker thread (ignored while another is running)."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.generate_button.setEnabled(False)
        self.worker = DealerLevelsWorker(request, self.service_factory, parent=self)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _list_expirations(self) -> None:
        """Fill the expiry list for the selected currency."""
        self._set_status("Loading the expirations...", Colors.WARNING)
        self._start(DealerLevelsRequest(self.currency_combo.currentText(), None))

    def _generate(self) -> None:
        """Compute and draw the selected expiry."""
        request = self.current_request()
        if request.expiry is None:
            return
        self.warning_label.setVisible(False)
        self._set_status("Computing the levels and drawing the chart...", Colors.WARNING)
        self._start(request)

    def _on_succeeded(self, result: DealerLevelsResult) -> None:
        """Fill the list; when a chart came back show it, the level table and the warnings."""
        self._fill_expirations(result.expirations)
        levels = result.levels
        if levels is None or result.html_path is None:
            self._set_status("Pick an expiry and press Generate." if result.expirations else
                             "No option-chain snapshot stored for this currency; sync the database first.", Colors.TEXT_SECONDARY)
            return
        self.chart_view.setUrl(QUrl.fromLocalFile(str(result.html_path.resolve())))
        self._set_status(f"{levels.currency} {levels.expiry}: {levels.trades_used} trades since {levels.flow_start:%Y-%m-%d}, "
                         f"chain snapshot {levels.chain_captured_at:%Y-%m-%d %H:%M} UTC.", Colors.SUCCESS)
        self.summary_label.setText(summary_html(levels))
        self.summary_label.setVisible(True)
        warnings = list(levels.notes)
        if self.clock() - levels.chain_captured_at > timedelta(hours=STALE_DATA_HOURS):
            warnings.append(f"the newest stored chain snapshot is from {levels.chain_captured_at:%Y-%m-%d %H:%M} UTC; "
                            "sync the database for a current one")
        self.warning_label.setText("\n".join(f"Warning: {warning}" for warning in warnings))
        self.warning_label.setVisible(bool(warnings))

    def _fill_expirations(self, expirations: List[str]) -> None:
        """Offer the expirations; keep the selection when it is still listed."""
        selected = self.expiry_combo.currentData()
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        for label in expirations:
            self.expiry_combo.addItem(label, label)
        if selected in expirations:
            self.expiry_combo.setCurrentIndex(expirations.index(selected))
        self.expiry_combo.blockSignals(False)
        self.generate_button.setEnabled(bool(expirations))

    def closeEvent(self, event) -> None:
        """Wait for a running worker so the thread is never destroyed while it runs."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(10000)
        super().closeEvent(event)

    def _on_failed(self, message: str) -> None:
        """Show a failed run as one error line."""
        self._set_status(f"Failed: {message}", Colors.ERROR)
        self.generate_button.setEnabled(self.expiry_combo.count() > 0)
