"""
On Chain Analysis tab for options market analytics.

Provides interface to:
- Load on-chain analysis data
- View formatted text report with max pain, OI, support/resistance
- GEX/DEX analysis with Greeks
- Buy/Sell flow analysis
- Per-strike Levels table + normalized-metric strip
  (institutional_metrics_spec.md section 9(c), Task D3)
"""

import logging
from typing import Dict, Optional, Tuple

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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QRadioButton,
    QButtonGroup,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor, QBrush

from coding.core.analytics.historical_normalizer import NormalizedMetric
from coding.core.analytics.levels_table_builder import build_levels_table
from coding.core.analytics.reporting.historical_context_formatter import (
    METRIC_ORDER,
    format_metric_value,
    metric_label,
)
from coding.core.analytics.results.levels_table_results import LevelsTable, LevelsTableRow
from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.dialogs.flow_charts_window import FlowChartsWindow
from coding.gui.theme.colors import Colors
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from coding.service.on_chain.on_chain_workflow_service import OnChainWorkflowService


logger = logging.getLogger(__name__)

# M9 (refactor_design_spec.md section T9): the logger the GUI's log-viewer
# handler attaches to. Scoped to this subsystem's logger (not the root
# logger) so the handler only sees on-chain-analysis-relevant records and so
# it can be removed cleanly on tab close instead of accumulating on the root
# logger across tab instances.
_GUI_LOG_LOGGER_NAME = "coding.service.on_chain"

# Task D3 (institutional_metrics_spec.md section 9(c)): Levels tab column
# order/labels and index constants (used both for building the table and
# for the sign-convention coloring logic below).
#
# Independent review (round 1, Important #2): the spec's sign-convention
# radio has 3 options (holder/assumed dealer/inferred) but the first
# implementation only ever displayed 2 GEX columns, silently mapping
# "holder" onto the "assumed" column's coloring -- and since holder-side
# gamma exposure (call_gamma + put_gamma) is structurally always >= 0,
# selecting "holder" painted that column uniformly green on every expiry,
# conveying no information. Ruling: a third, always-visible "Net GEX
# (holder)" column displays `LevelsTableRow.net_gex_holder` (already
# computed, previously wasted); the radio then does exactly what its name
# says -- pick which of three REAL, displayed columns' sign drives
# coloring. No column's displayed values change with the radio.
_LEVELS_COLUMN_LABELS = (
    "Strike", "Call OI", "Put OI", "Net GEX (holder)", "Net GEX (assumed)",
    "Net GEX (inferred)", "Net DEX", "VEX", "CEX", "Net taker flow", "Δ1d IV",
)
(
    _COL_STRIKE, _COL_CALL_OI, _COL_PUT_OI, _COL_GEX_HOLDER, _COL_GEX_ASSUMED,
    _COL_GEX_INFERRED, _COL_DEX, _COL_VEX, _COL_CEX, _COL_FLOW, _COL_IV_CHANGE,
) = range(len(_LEVELS_COLUMN_LABELS))

_METRICS_COLUMN_LABELS = ("Metric", "Value", "p30", "z30", "p90", "z90", "Regime")

# Sign-convention radio values (institutional_metrics_spec.md section 10's
# holder/assumed_dealer/inferred convention set).
_CONVENTION_HOLDER = "holder"
_CONVENTION_ASSUMED = "assumed"
_CONVENTION_INFERRED = "inferred"


class _NumericTableWidgetItem(QTableWidgetItem):
    """
    QTableWidgetItem that sorts by a stored numeric value instead of its
    display text.

    Task D3 ("Sortable by any column"): Qt's default sort compares each
    item's display text lexicographically, which would sort "-1,200.00"
    ahead of "95,000.00". This is a Qt sort delegate only (explicitly
    permitted by CLAUDE.md Code Quality Checklist section 3's "sorting
    logic beyond a Qt sort delegate" carve-out) -- it does not compute
    anything, it only compares two already-computed numbers.
    """

    def __init__(self, display_text: str, sort_value: float):
        super().__init__(display_text)
        self._sort_value = sort_value

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _NumericTableWidgetItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


def _format_numeric_cell(value: Optional[float], decimals: int) -> Tuple[str, float]:
    """
    Format one numeric cell's display text, returning it alongside the raw
    sort value (``float("-inf")`` for a missing value, so "N/A" rows sort
    to one end rather than raising on a None compare). Formatting only --
    no computation on ``value`` itself.
    """
    if value is None:
        return "N/A", float("-inf")
    return f"{value:,.{decimals}f}", value


def _format_optional_number(value: Optional[float], fmt: str) -> str:
    """Formatting-only helper: ``fmt.format(value)`` or "N/A" when None."""
    if value is None:
        return "N/A"
    return fmt.format(value)


class OnChainAnalysisWorker(QThread):
    """
    Worker thread for fetching and analyzing on-chain data.

    Fetches book summary data and generates analysis report.
    Always includes GEX/DEX and buy/sell flow analysis.
    """

    progress = Signal(str)
    finished = Signal(object)  # Returns the full OnChainWorkflowOutput (report_text + typed result)
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
        """
        Execute the full analyze -> synthesize -> save workflow.

        H3 (refactor_design_spec.md section T9): one call into
        ``OnChainWorkflowService`` replaces the worker's previous manual
        3-step orchestration (construct ``DatabaseRepository``/
        ``DeribitApiService``/``OnChainAnalysisService``/``MorningNoteService``
        and drive each step itself). This module no longer constructs or
        calls either service/repository directly.

        Task D3: ``finished`` now carries the whole ``OnChainWorkflowOutput``
        (not just ``report_text``) so the tab can populate the Levels table
        and normalized-metric strip from ``output.result`` -- the typed
        ``OnChainAnalysisResult`` this workflow already computes but that
        the GUI previously discarded.
        """
        try:
            workflow = OnChainWorkflowService(self.currency)
            output = workflow.run(progress_callback=lambda msg: self.progress.emit(msg))
            self.finished.emit(output)

        except Exception as error:
            logger.exception("Error during on-chain analysis")
            self.error.emit(str(error))


class OnChainAnalysisTab(QWidget):
    """
    Tab widget for on-chain analysis visualization.

    Features:
    - Load analysis for selected currency
    - Display formatted text report
    - Per-strike Levels table with sign-convention-aware GEX coloring and
      call-wall/put-support/HVL/max-pain markers (Task D3)
    - Normalized-metric strip (percentile/z-score context, Task C1's output)
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
        self._last_analyzed_currency: Optional[str] = None  # Set when each currency analysis starts

        # Task D3: every analyzed currency's full typed result, keyed by
        # currency, so the expiry selector can offer every expiration seen
        # across a multi-currency queue run, and the Levels table can be
        # rebuilt on demand without re-running the analysis.
        self._results_by_currency: Dict[str, object] = {}
        self._current_levels_table: Optional[LevelsTable] = None

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

        # Levels-view controls: expiry selector + sign-convention radios
        # (Task D3).
        levels_controls_frame = self._create_levels_controls_frame()
        main_layout.addWidget(levels_controls_frame)

        # Splitter for report and log viewer
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Results container: normalized-metric strip (fixed, above the
        # tabs) + a QTabWidget with the Levels table and the Report text.
        results_container = QWidget()
        results_layout = QVBoxLayout(results_container)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(8)

        metrics_label = QLabel("Normalized Metrics")
        metrics_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};"
        )
        results_layout.addWidget(metrics_label)

        self.metrics_table = self._create_metrics_table()
        results_layout.addWidget(self.metrics_table)

        self.results_tabs = QTabWidget()
        self.results_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                background-color: {Colors.SURFACE};
            }}
            QTabBar::tab {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_SECONDARY};
                padding: 8px 16px;
                border: 1px solid {Colors.BORDER};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)

        self.levels_table = self._create_levels_table()
        self.results_tabs.addTab(self.levels_table, "Levels")

        report_container = self._create_report_container()
        self.results_tabs.addTab(report_container, "Report")

        results_layout.addWidget(self.results_tabs, 1)

        splitter.addWidget(results_container)

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

    def _create_report_container(self) -> QWidget:
        """Build the "Report" tab's monospace text dump (unchanged content/behavior)."""
        report_container = QWidget()
        report_layout = QVBoxLayout(report_container)
        report_layout.setContentsMargins(8, 8, 8, 8)

        self.report_display = QPlainTextEdit()
        self.report_display.setReadOnly(True)
        self.report_display.setFont(QFont("Consolas", 10))
        self.report_display.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: none;
                padding: 12px;
            }}
        """)
        self.report_display.setPlaceholderText(
            "Click 'Load Analysis' to generate the on-chain analysis report..."
        )
        self.report_display.setMinimumHeight(300)
        report_layout.addWidget(self.report_display)

        return report_container

    def _create_metrics_table(self) -> QTableWidget:
        """
        Build the fixed normalized-metric strip (Task D3 / institutional_
        metrics_spec.md section 9(c)): one row per Task C1 metric, columns
        Metric | Value | p30 | z30 | p90 | z90 | Regime.
        """
        table = QTableWidget(0, len(_METRICS_COLUMN_LABELS))
        table.setHorizontalHeaderLabels(_METRICS_COLUMN_LABELS)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMaximumHeight(180)
        table.setStyleSheet(self._table_stylesheet())
        return table

    def _create_levels_table(self) -> QTableWidget:
        """
        Build the "Levels" tab's per-strike QTableWidget (Task D3): one row
        per strike for the selected expiry, sortable by any column.
        """
        table = QTableWidget(0, len(_LEVELS_COLUMN_LABELS))
        table.setHorizontalHeaderLabels(_LEVELS_COLUMN_LABELS)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setStyleSheet(self._table_stylesheet())
        return table

    def _table_stylesheet(self) -> str:
        return f"""
            QTableWidget {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                gridline-color: {Colors.BORDER};
            }}
            QHeaderView::section {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_SECONDARY};
                padding: 4px;
                border: 1px solid {Colors.BORDER};
            }}
        """

    def _create_levels_controls_frame(self) -> QFrame:
        """
        Build the expiry selector + sign-convention radio row that drives
        the Levels tab (Task D3).
        """
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        expiry_label = QLabel("Levels expiry:")
        expiry_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(expiry_label)

        self.expiry_combo = QComboBox()
        self.expiry_combo.setMinimumWidth(180)
        self.expiry_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.expiry_combo)

        layout.addSpacing(16)

        convention_label = QLabel("Sign convention:")
        convention_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(convention_label)

        radio_style = f"color: {Colors.TEXT_SECONDARY};"

        self.holder_radio = QRadioButton("Holder")
        self.holder_radio.setStyleSheet(radio_style)
        layout.addWidget(self.holder_radio)

        self.assumed_radio = QRadioButton("Assumed dealer")
        self.assumed_radio.setChecked(True)
        self.assumed_radio.setStyleSheet(radio_style)
        layout.addWidget(self.assumed_radio)

        self.inferred_radio = QRadioButton("Inferred")
        self.inferred_radio.setStyleSheet(radio_style)
        self.inferred_radio.setEnabled(False)  # enabled only when the loaded expiry's inferred view is available
        layout.addWidget(self.inferred_radio)

        self.sign_convention_group = QButtonGroup(self)
        self.sign_convention_group.addButton(self.holder_radio)
        self.sign_convention_group.addButton(self.assumed_radio)
        self.sign_convention_group.addButton(self.inferred_radio)

        layout.addStretch()

        return frame

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
        """
        Set up logging to the GUI log viewer.

        M9 (refactor_design_spec.md section T9): attaches to this
        subsystem's own logger (``coding.service.on_chain``, which every
        on-chain service module logs under and which propagates up from
        ``coding.service.on_chain.on_chain_analysis_service`` etc.), not the
        root logger -- and removed on close (see ``closeEvent``). Attaching
        to the root logger leaked one handler per tab instance for the
        lifetime of the process; scoping to this subsystem's logger plus a
        dedup guard (never add the same handler object twice) fixes it.
        """
        self._log_target_logger = logging.getLogger(_GUI_LOG_LOGGER_NAME)
        self.gui_handler = GuiLogHandler(self.log_viewer)
        if self.gui_handler not in self._log_target_logger.handlers:
            self._log_target_logger.addHandler(self.gui_handler)

    def closeEvent(self, event) -> None:
        """Remove the GUI log handler on close so it does not leak (M9)."""
        log_target_logger = getattr(self, "_log_target_logger", None)
        gui_handler = getattr(self, "gui_handler", None)
        if log_target_logger is not None and gui_handler is not None:
            log_target_logger.removeHandler(gui_handler)
        super().closeEvent(event)

    def _connect_signals(self) -> None:
        """Connect widget signals to slots."""
        self.load_btn.clicked.connect(self._load_analysis)
        self.clear_btn.clicked.connect(self._clear)
        self.view_charts_btn.clicked.connect(self._open_flow_charts)
        self.expiry_combo.currentIndexChanged.connect(self._on_expiry_selection_changed)
        self.sign_convention_group.buttonToggled.connect(self._on_sign_convention_toggled)

    def _clear(self) -> None:
        """Clear report display, tables, and log viewer."""
        self.report_display.clear()
        self.log_viewer.clear_logs()
        self.report_text = ""
        self.status_label.setText("")

        self._results_by_currency = {}
        self._current_levels_table = None
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        self.expiry_combo.blockSignals(False)
        self.levels_table.setRowCount(0)
        self.metrics_table.setRowCount(0)

        # Minor (independent review round 1): reset sign-convention state
        # too -- leaving "Inferred" checked-but-now-disabled (or checked at
        # all) from a previous run's data would be stale once that data is
        # gone.
        self.assumed_radio.setChecked(True)
        self.inferred_radio.setEnabled(False)

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
        self._last_analyzed_currency = currency
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

    def _on_analysis_finished(self, output) -> None:
        """
        Append the completed report, store the typed result for the Levels
        tab / metric strip, and start the next queued currency if any.

        Args:
            output: ``OnChainWorkflowOutput`` (Task D3: the worker now
                emits the whole workflow output, not just ``report_text``,
                so this tab can read ``output.result`` -- the typed
                ``OnChainAnalysisResult`` -- for the Levels table and
                normalized-metric strip).
        """
        report = output.report_text
        if self.report_text:
            self.report_text += "\n\n" + "=" * 80 + "\n\n" + report
        else:
            self.report_text = report

        self.report_display.setPlainText(self.report_text)
        self.log_viewer.log_info("Analysis report generated successfully")

        currency = self._last_analyzed_currency
        if currency is not None:
            self._results_by_currency[currency] = output.result
            self._refresh_expiry_selector()

        # Continue with next in queue
        self._start_next_in_queue()

    def _refresh_expiry_selector(self) -> None:
        """
        Rebuild the expiry combo from every analyzed currency's result,
        preserving the current selection when it still exists (Task D3).
        """
        previous_selection = self.expiry_combo.currentData()

        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        for currency, result in self._results_by_currency.items():
            for expiration in result.expiration_names():
                self.expiry_combo.addItem(f"{currency}  {expiration}", (currency, expiration))
        self.expiry_combo.blockSignals(False)

        if self.expiry_combo.count() == 0:
            # Minor (independent review round 1): a result with zero
            # expirations (expiration_names() empty) leaves the combo
            # empty, so _on_expiry_selection_changed never fires -- but
            # normalized_metrics is a currency-wide field, not per-expiry,
            # so it should still surface for the most recently analyzed
            # currency rather than leaving the strip permanently blank.
            self._current_levels_table = None
            self.levels_table.setRowCount(0)
            latest_result = self._results_by_currency.get(self._last_analyzed_currency)
            if latest_result is not None:
                self._populate_normalized_metrics_table(latest_result.normalized_metrics)
            return

        index_to_select = 0
        if previous_selection is not None:
            for i in range(self.expiry_combo.count()):
                if self.expiry_combo.itemData(i) == previous_selection:
                    index_to_select = i
                    break

        self.expiry_combo.setCurrentIndex(index_to_select)
        self._on_expiry_selection_changed(index_to_select)

    def _on_expiry_selection_changed(self, index: int) -> None:
        """
        Rebuild the Levels table and normalized-metric strip for the
        selected (currency, expiration) (Task D3).

        Building the joined per-strike ``LevelsTable`` is delegated to
        ``build_levels_table`` (pure Core function) -- this method only
        looks up the already-built ``ExpirationBundle``/``OnChainAnalysis
        Result`` and hands them to that function; it performs no join,
        lookup-by-strike, or arithmetic of its own.
        """
        if index < 0:
            return

        data = self.expiry_combo.itemData(index)
        if not data:
            return

        currency, expiration = data
        result = self._results_by_currency.get(currency)
        if result is None:
            return

        # normalized_metrics is currency-wide, not per-expiry -- populate it
        # unconditionally once a result is found, so a missing/absent bundle
        # below (which only affects the Levels table) never leaves this
        # strip blank (Minor, independent review round 1).
        self._populate_normalized_metrics_table(result.normalized_metrics)

        bundle = result.bundle(expiration)
        if bundle is None:
            self._current_levels_table = None
            self.levels_table.setRowCount(0)
            return

        self._current_levels_table = build_levels_table(bundle)
        self._update_sign_convention_availability()
        self._populate_levels_table()

    def _update_sign_convention_availability(self) -> None:
        """Enable/disable the "Inferred" radio per the loaded expiry's gate result."""
        levels_table = self._current_levels_table
        inferred_available = levels_table is not None and levels_table.inferred_available
        self.inferred_radio.setEnabled(inferred_available)
        if not inferred_available and self.inferred_radio.isChecked():
            self.assumed_radio.setChecked(True)

    def _active_sign_convention(self) -> str:
        """Which sign convention the radio group currently selects."""
        if self.holder_radio.isChecked():
            return _CONVENTION_HOLDER
        if self.inferred_radio.isChecked():
            return _CONVENTION_INFERRED
        return _CONVENTION_ASSUMED

    def _on_sign_convention_toggled(self, button, checked: bool) -> None:
        """Recolor the Levels table when the sign-convention radio changes."""
        del button
        if checked:
            self._apply_sign_convention_view()

    def _populate_levels_table(self) -> None:
        """
        Fill the Levels ``QTableWidget`` from ``self._current_levels_table``.

        Cell contents are formatting only (``_format_numeric_cell``) --
        every value already comes precomputed off ``LevelsTableRow``.
        """
        levels_table = self._current_levels_table
        self.levels_table.setSortingEnabled(False)

        if levels_table is None:
            self.levels_table.setRowCount(0)
            self.levels_table.setSortingEnabled(True)
            return

        self.levels_table.setRowCount(len(levels_table.rows))
        for row_index, row in enumerate(levels_table.rows):
            # The Strike cell carries the row's LevelsTableRow as item data
            # (Qt.ItemDataRole.UserRole) -- this is the row's identity
            # anchor. Qt's QTableWidget.sortItems() moves each
            # QTableWidgetItem (text/background/data all together) to its
            # new row position, but it does NOT move vertical-header items
            # or re-run any per-row logic keyed by construction-order index.
            # Reading the row object back off the CURRENT grid position
            # (see _apply_sign_convention_view) is what keeps coloring and
            # markers correctly attached to their row after a user sorts a
            # column -- keying off enumerate(levels_table.rows) instead
            # would silently recolor/mark the wrong rows post-sort.
            self._set_cell(row_index, _COL_STRIKE, row.strike, decimals=0, row_data=row)
            self._set_cell(row_index, _COL_CALL_OI, row.call_oi, decimals=0)
            self._set_cell(row_index, _COL_PUT_OI, row.put_oi, decimals=0)
            self._set_cell(row_index, _COL_GEX_HOLDER, row.net_gex_holder, decimals=2)
            self._set_cell(row_index, _COL_GEX_ASSUMED, row.net_gex_assumed, decimals=2)
            self._set_cell(row_index, _COL_GEX_INFERRED, row.net_gex_inferred, decimals=2)
            self._set_cell(row_index, _COL_DEX, row.net_dex, decimals=2)
            self._set_cell(row_index, _COL_VEX, row.vex, decimals=2)
            self._set_cell(row_index, _COL_CEX, row.cex, decimals=2)
            self._set_cell(row_index, _COL_FLOW, row.net_taker_flow, decimals=2)
            self._set_cell(row_index, _COL_IV_CHANGE, row.delta_1d_iv, decimals=2)

        self.levels_table.setSortingEnabled(True)
        # Qt quirk (verified empirically): a QHeaderView's sort indicator
        # defaults to (section 0, DescendingOrder) the first time sorting
        # is ever toggled on -- since setSortingEnabled(True) immediately
        # re-applies whatever the CURRENT indicator is, every populate call
        # would otherwise silently reverse the builder's ascending-by-strike
        # row order. Pin an explicit ascending-by-strike sort as this
        # table's well-defined initial view -- a real click on any column
        # header (or a later programmatic sortItems call) still re-sorts
        # normally afterward.
        self.levels_table.sortByColumn(_COL_STRIKE, Qt.SortOrder.AscendingOrder)
        self._apply_sign_convention_view()

    def _set_cell(
        self,
        row: int,
        column: int,
        value: Optional[float],
        decimals: int,
        row_data: Optional[LevelsTableRow] = None,
    ) -> None:
        """Set one Levels-table cell: formatting only, via ``_format_numeric_cell``."""
        text, sort_value = _format_numeric_cell(value, decimals)
        item = _NumericTableWidgetItem(text, sort_value)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if row_data is not None:
            item.setData(Qt.ItemDataRole.UserRole, row_data)
        self.levels_table.setItem(row, column, item)

    def _apply_sign_convention_view(self) -> None:
        """
        Recolor the active GEX column and recompute row markers for the
        currently-selected sign convention (Task D3).

        "Row coloring: positive/negative on the ACTIVE GEX column only" --
        all three GEX columns are reset to neutral first so exactly one
        column carries a color signal at a time. Each of the 3 sign
        conventions (holder / assumed dealer / inferred) has its own
        always-visible column ("Net GEX (holder)" / "Net GEX (assumed)" /
        "Net GEX (inferred)") -- independent review round 1, Important #2:
        an earlier version mapped "holder" onto the "assumed" column's
        coloring with no displayed "holder" column at all, which always
        rendered green (holder-side gamma exposure is structurally >= 0)
        regardless of the expiry's real data. No column's displayed
        VALUES change with the radio -- only which column is "active" for
        coloring.

        Iterates the table's CURRENT visual row order (0..rowCount()) and
        reads each row's ``LevelsTableRow`` back off the Strike cell's
        stored item data, rather than ``enumerate(levels_table.rows)`` --
        after a user sorts a column, visual row N no longer corresponds to
        construction-order row N (Qt moves cell items, not a parallel
        "logical row" the GUI tracks itself), so the visual-row-keyed
        version is the only one that stays correct post-sort.
        """
        levels_table = self._current_levels_table
        if levels_table is None:
            return

        convention = self._active_sign_convention()

        neutral_brush = QBrush(QColor(Colors.SURFACE))
        positive_brush = QBrush(QColor(Colors.PROFIT))
        negative_brush = QBrush(QColor(Colors.LOSS))

        for visual_row in range(self.levels_table.rowCount()):
            strike_item = self.levels_table.item(visual_row, _COL_STRIKE)
            row = strike_item.data(Qt.ItemDataRole.UserRole) if strike_item is not None else None
            if row is None:
                continue

            holder_item = self.levels_table.item(visual_row, _COL_GEX_HOLDER)
            assumed_item = self.levels_table.item(visual_row, _COL_GEX_ASSUMED)
            inferred_item = self.levels_table.item(visual_row, _COL_GEX_INFERRED)
            for gex_item in (holder_item, assumed_item, inferred_item):
                if gex_item is not None:
                    gex_item.setBackground(neutral_brush)

            if convention == _CONVENTION_HOLDER:
                active_item, sign_value = holder_item, row.net_gex_holder
            elif convention == _CONVENTION_INFERRED:
                active_item, sign_value = inferred_item, row.net_gex_inferred
            else:
                active_item, sign_value = assumed_item, row.net_gex_assumed

            if active_item is not None and sign_value is not None:
                active_item.setBackground(positive_brush if sign_value >= 0 else negative_brush)

            self._set_row_marker(strike_item, row, convention)

    def _set_row_marker(self, strike_item: QTableWidgetItem, row: LevelsTableRow, convention: str) -> None:
        """
        Set the left-edge marker for one row directly on its Strike cell
        (background highlight + tooltip listing which structural level(s)
        it is) -- call wall / put support / HVL and max pain
        (max pain is convention-independent).

        Call wall / put support / HVL come from the INFERRED key levels
        only when the active convention is INFERRED; every other
        convention -- including HOLDER -- shows the ASSUMED-DEALER-VIEW
        levels. This is NOT "the active convention's key levels" for
        HOLDER (Task Wave-J-F Fix 2: an earlier version of this docstring
        claimed exactly that, which was false). There is no holder-side
        key-levels source to show instead: ``LevelsTableRow`` only carries
        ``*_assumed``/``*_inferred`` call-wall/put-support/HVL fields, and
        holder-side gamma exposure is always >= 0 by construction (see
        ``GexDexRowResult.gamma_exposure_holder``'s docstring) -- there is
        no sign flip across strikes for a "call wall"/"put support" to
        anchor on under that convention, so no meaningful holder-side
        version of these markers exists to compute. The tooltip flags this
        explicitly when HOLDER is active, so a user selecting Holder isn't
        told these markers are holder-derived.

        Deliberately NOT a vertical-header item: QTableWidget.sortItems()
        does not move header items along with a sorted row (see
        ``_apply_sign_convention_view``'s docstring), so a marker anchored
        to the header would silently point at the wrong row after a user
        sorts a column. Anchoring it to the Strike cell -- the same
        QTableWidgetItem whose numeric sort-by-value already proved
        correct under sorting -- keeps the marker attached to its row.
        """
        if strike_item is None:
            return

        if convention == _CONVENTION_INFERRED:
            call_wall, put_support, hvl = (
                row.is_call_wall_inferred, row.is_put_support_inferred, row.is_hvl_inferred,
            )
        else:
            call_wall, put_support, hvl = (
                row.is_call_wall_assumed, row.is_put_support_assumed, row.is_hvl_assumed,
            )

        labels = []
        if call_wall:
            labels.append("Call Wall")
        if put_support:
            labels.append("Put Support")
        if hvl:
            labels.append("HVL")
        if row.is_max_pain:
            labels.append("Max Pain")

        if labels:
            tooltip = " / ".join(labels)
            if convention == _CONVENTION_HOLDER:
                # No holder-side key-levels concept exists (see this
                # method's docstring) -- disclose that these are the
                # assumed-dealer-view levels, not holder-side ones.
                tooltip += " (assumed-dealer view -- no holder-side key levels exist)"
            strike_item.setBackground(QBrush(QColor(Colors.ACCENT)))
            strike_item.setForeground(QBrush(QColor(Colors.BACKGROUND_PRIMARY)))
            strike_item.setToolTip(tooltip)
        else:
            strike_item.setBackground(QBrush(QColor(Colors.SURFACE)))
            strike_item.setForeground(QBrush(QColor(Colors.TEXT_PRIMARY)))
            strike_item.setToolTip("")

    def _populate_normalized_metrics_table(self, metrics: Dict[str, NormalizedMetric]) -> None:
        """
        Fill the normalized-metric strip from Task C1's ``HistoricalNormalizer``
        output (Task D3): Metric | Value | p30 | z30 | p90 | z90 | Regime.

        Row order and the "Metric" column's label both reuse the text
        report's own conventions (``METRIC_ORDER``/``metric_label``,
        historical_context_formatter.py) instead of raw dict keys/
        insertion order (Minor, independent review round 1) -- same "reuse
        the report's formatting" rationale that motivated
        ``format_metric_value`` below: a metric not in the fixed order
        (forward-compatible with a future metric) is still appended after
        the ordered ones, matching ``format_historical_context_section``'s
        own fallback.
        """
        ordered_names = [name for name in METRIC_ORDER if name in metrics]
        ordered_names += [name for name in metrics if name not in METRIC_ORDER]

        self.metrics_table.setRowCount(len(ordered_names))
        for row_index, name in enumerate(ordered_names):
            metric = metrics[name]
            self.metrics_table.setItem(row_index, 0, QTableWidgetItem(metric_label(metric.name)))
            self.metrics_table.setItem(
                row_index, 1, QTableWidgetItem(format_metric_value(metric.value, metric.unit))
            )
            self.metrics_table.setItem(
                row_index, 2, QTableWidgetItem(_format_optional_number(metric.percentile_30d, "{:.1f}"))
            )
            self.metrics_table.setItem(
                row_index, 3, QTableWidgetItem(_format_optional_number(metric.z_30d, "{:+.2f}"))
            )
            self.metrics_table.setItem(
                row_index, 4, QTableWidgetItem(_format_optional_number(metric.percentile_90d, "{:.1f}"))
            )
            self.metrics_table.setItem(
                row_index, 5, QTableWidgetItem(_format_optional_number(metric.z_90d, "{:+.2f}"))
            )
            self.metrics_table.setItem(
                row_index, 6, QTableWidgetItem(metric.regime_30d if metric.regime_30d is not None else "N/A")
            )

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
        """
        Open fullscreen flow charts window for the last analyzed currency.

        H3 (refactor_design_spec.md section T9): hands ``FlowChartsWindow`` a
        service, not a raw ``DatabaseRepository`` -- the dialog's own read
        calls go through the service (see ``FlowChartsWindow``). This module
        does not construct ``DatabaseRepository`` itself (review fix):
        ``OnChainAnalysisService.create_default()`` is the service-layer
        factory that does, mirroring ``OnChainWorkflowService.run``'s own
        lazy dependency construction.
        """
        currency = self._last_analyzed_currency or ("BTC" if self.btc_checkbox.isChecked() else "ETH")
        service = OnChainAnalysisService.create_default()

        dialog = FlowChartsWindow(currency, service, parent=self)
        dialog.exec()
