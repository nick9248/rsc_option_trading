"""
Levels tab: every source's levels on one price ladder (MenthorQ replica, dealer gamma levels from open interest and from the
trades, option flow, liquidation estimate), with bands where several sources agree and a ledger of every number.

The collecting happens in ``LevelOverviewService`` in a worker thread. The page itself (``level_overview_page``) is drawn in the
embedded browser. Changing the band width or the minimum number of sources only rebuilds the page from the levels already
loaded; changing the currency, dealer expiry or flow window reads the database again. Data is the local mirror of the VPS
database (sync it first in the Database tab or with scripts/sync_from_vps.py).
"""

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from coding.core.analytics.option_flow.level_overview import LevelOverview
from coding.core.analytics.option_flow.level_overview_page import build_level_overview_page
from coding.gui.components.menthorq_widgets import panel_frame
from coding.gui.theme.colors import Colors
from coding.service.option_flow.level_overview_service import LevelOverviewService

logger = logging.getLogger(__name__)

CURRENCIES = ["BTC", "ETH"]
FLOW_WINDOW_HOURS = [6, 12, 24, 48]
DEFAULT_FLOW_HOURS = 24
BAND_PERCENTS = [0.5, 1.0, 1.5, 2.0]
DEFAULT_BAND_PERCENT = 1.0
MINIMUM_SOURCES = [2, 3, 4, 5]
DEFAULT_MINIMUM_SOURCES = 3
NEAREST_MONTHLY = "Nearest monthly"
PAGE_BACKGROUND = "#FFFFFF"

DESCRIPTION = ("Every source's levels on one price ladder. A gold band marks a price where several different sources agree. "
               "The sources are not independent (several use the same open interest), so agreement is a coincidence of "
               "methods, not proof. Reads the local copy of the VPS data (sync it first).")


@dataclass(frozen=True)
class LevelOverviewRequest:
    """What to collect: currency, the expiry for the dealer levels (None = nearest monthly) and the option flow window."""

    currency: str
    expiry: Optional[str]
    flow_hours: int


@dataclass
class LevelOverviewResult:
    """What the worker hands back: the levels and the expirations to offer."""

    overview: LevelOverview
    expirations: List[str]


class LevelOverviewWorker(QThread):
    """Collects the levels in a background thread."""

    succeeded = Signal(object)   # LevelOverviewResult
    failed = Signal(str)

    def __init__(self, request: LevelOverviewRequest,
                 service_factory: Callable[[], LevelOverviewService] = LevelOverviewService, parent: Optional[QWidget] = None):
        """
        Args:
            request: Currency, expiry and flow window.
            service_factory: Builds the service (a fake in tests).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.request = request
        self.service_factory = service_factory

    def run(self) -> None:
        """Emit ``succeeded`` with a ``LevelOverviewResult`` or ``failed`` (class: message)."""
        try:
            service = self.service_factory()
            overview = service.overview(self.request.currency, self.request.expiry, self.request.flow_hours)
            self.succeeded.emit(LevelOverviewResult(overview, service.expirations(self.request.currency)))
        except Exception as error:
            logger.exception("Level overview failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


class LevelOverviewTab(QWidget):
    """Controls on top, a status line, then the levels page."""

    def __init__(self, parent: Optional[QWidget] = None,
                 service_factory: Callable[[], LevelOverviewService] = LevelOverviewService):
        """
        Args:
            parent: Parent widget.
            service_factory: Builds the service (a fake in tests).
        """
        super().__init__(parent)
        self.service_factory = service_factory
        self.worker: Optional[LevelOverviewWorker] = None
        self.overview: Optional[LevelOverview] = None
        self._pending: Optional[LevelOverviewRequest] = None    # the newest request made while a worker was running
        self._loaded_once = False
        self._setup_ui()
        self.refresh_button.clicked.connect(self._collect)
        for combo in (self.currency_combo, self.expiry_combo, self.flow_combo):
            combo.currentIndexChanged.connect(self._collect)
        for combo in (self.band_combo, self.minimum_combo):
            combo.currentIndexChanged.connect(self._redraw)
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self._wait_for_worker)

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)
        header = QLabel("Levels")
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
        self.expiry_combo.setMinimumWidth(130)
        self.expiry_combo.addItem(NEAREST_MONTHLY, None)
        self.flow_combo = QComboBox()
        for hours in FLOW_WINDOW_HOURS:
            self.flow_combo.addItem(f"{hours} h", hours)
        self.flow_combo.setCurrentIndex(FLOW_WINDOW_HOURS.index(DEFAULT_FLOW_HOURS))
        self.band_combo = QComboBox()
        for percent in BAND_PERCENTS:
            self.band_combo.addItem(f"{percent:.1f}% of spot", percent)
        self.band_combo.setCurrentIndex(BAND_PERCENTS.index(DEFAULT_BAND_PERCENT))
        self.minimum_combo = QComboBox()
        for count in MINIMUM_SOURCES:
            self.minimum_combo.addItem(f"{count} sources", count)
        self.minimum_combo.setCurrentIndex(MINIMUM_SOURCES.index(DEFAULT_MINIMUM_SOURCES))
        for caption, combo in (("Currency", self.currency_combo), ("Dealer expiry", self.expiry_combo),
                               ("Option flow window", self.flow_combo), ("Agreement band", self.band_combo),
                               ("Agreement needs", self.minimum_combo)):
            controls.addWidget(QLabel(caption))
            controls.addWidget(combo)
        self.refresh_button = QPushButton("Reload data")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        frame_layout.addLayout(controls)
        self.status_label = QLabel("Loading the levels...")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frame_layout.addWidget(self.status_label)
        main_layout.addWidget(frame)

        self.chart_view = QWebEngineView()
        self.chart_view.page().setBackgroundColor(QColor(PAGE_BACKGROUND))
        self.chart_view.setMinimumHeight(450)
        main_layout.addWidget(self.chart_view, 1)

    def showEvent(self, event) -> None:
        """Collect the levels the first time the tab is shown."""
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self._collect()

    def _set_status(self, text: str, color: str) -> None:
        """Status line under the controls."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def current_request(self) -> LevelOverviewRequest:
        """The request the controls describe."""
        return LevelOverviewRequest(self.currency_combo.currentText(), self.expiry_combo.currentData(), self.flow_combo.currentData())

    def _collect(self) -> None:
        """Read the levels again for the current choices."""
        self._set_status("Collecting the levels of every source...", Colors.WARNING)
        self._start(self.current_request())

    def _start(self, request: LevelOverviewRequest) -> None:
        """Run a request in the worker thread; one made while another runs waits and runs next."""
        if self.worker is not None and self.worker.isRunning():
            self._pending = request
            return
        self.refresh_button.setEnabled(False)
        self.worker = LevelOverviewWorker(request, self.service_factory, parent=self)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._run_pending)
        self.worker.start()

    def _run_pending(self) -> None:
        """Start the request made while the last worker ran, if any."""
        pending, self._pending = self._pending, None
        if pending is not None:
            self._start(pending)
        else:
            self.refresh_button.setEnabled(True)

    def _on_succeeded(self, result: LevelOverviewResult) -> None:
        """Keep the levels, refresh the expiry list and draw the page."""
        if self._pending is not None:                       # the choices changed meanwhile: this answer is outdated
            return
        self.overview = result.overview
        self._fill_expirations(result.expirations, self.current_request().expiry)
        self._redraw()

    def _fill_expirations(self, expirations: List[str], selected: Optional[str]) -> None:
        """Offer the dealer expirations; keep the selection when it is still listed."""
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        self.expiry_combo.addItem(NEAREST_MONTHLY, None)
        for label in expirations:
            self.expiry_combo.addItem(label, label)
        self.expiry_combo.setCurrentIndex(max(0, self.expiry_combo.findData(selected)))
        self.expiry_combo.blockSignals(False)

    def _redraw(self) -> None:
        """Rebuild the page from the levels already loaded (band width and minimum sources change only this)."""
        if self.overview is None:
            return
        overview = self.overview
        self.chart_view.setHtml(build_level_overview_page(overview, self.band_combo.currentData(), self.minimum_combo.currentData()))
        sources = len({mark.source for mark in overview.marks})
        self._set_status(f"{overview.currency}: {len(overview.marks)} levels from {sources} sources, dealer expiry {overview.expiry}, "
                         f"spot {overview.spot:,.0f}.", Colors.SUCCESS if overview.marks else Colors.WARNING)

    def _on_failed(self, message: str) -> None:
        """Show a failed run as one error line (the page from the last good run stays)."""
        self._set_status(f"Failed: {message}", Colors.ERROR)

    def _wait_for_worker(self) -> None:
        """Wait for a running worker so the thread is never destroyed while it runs."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(10000)

    def closeEvent(self, event) -> None:
        """Also wait when the tab itself is closed."""
        self._wait_for_worker()
        super().closeEvent(event)
