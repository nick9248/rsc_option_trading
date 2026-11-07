"""
MenthorQ Charts tab: generates the MenthorQ-style charts (IV*OI all
expiries, IV*OI for one expiry, Net GEX 4 panels, Net DEX all expiries)
from the local database and shows them with their levels.

All work happens in ``MenthorqReplicationService`` (database reads,
calculation, PNG + JSON files), run in a worker thread so the window never
freezes. Display rows come from the pure ``menthorq_chart_summary``
functions; this module only formats them.
"""

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from coding.core.analytics.menthorq_chart_summary import (
    CHART_ALL,
    CHART_IV_TIMES_OPEN_INTEREST,
    CHART_IV_TIMES_OPEN_INTEREST_EXPIRY,
    CHART_NET_DEX,
    CHART_NET_GEX_PANELS,
    chart_file_names,
    comparison_rows,
    data_warnings,
    expiry_choices,
    info_line,
    levels_rows,
)
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH,
    GAMMA_UNDERLYING_FORWARD,
    GAMMA_UNDERLYING_INDEX,
)
from coding.gui.components.log_viewer import GuiLogHandler, LogViewer
from coding.gui.theme.colors import Colors
from coding.service.on_chain.menthorq_replication_service import (
    RUN_MODE_LIVE,
    RUN_MODE_MENTHORQ_DAILY_CLOSE,
    MenthorqReplicationService,
    last_complete_utc_day,
)

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "menthorq_replicate" / "gui"
REFERENCE_DIRECTORY = REPOSITORY_ROOT / "outputs" / "menthorq_replicate"

# Only the replication service's own messages (warnings about missing data,
# "Wrote ...") go to this tab's log, not every on-chain module's.
GUI_LOG_LOGGER_NAME = "coding.service.on_chain.menthorq_replication_service"

CURRENCIES = ("BTC", "ETH")
MODES = (("MenthorQ daily close", RUN_MODE_MENTHORQ_DAILY_CLOSE),
         ("Live (newest stored snapshot)", RUN_MODE_LIVE))
EXPIRY_UNIVERSE_CHOICES = (("Monthly", EXPIRY_UNIVERSE_MONTHLY),
                           ("All listed", EXPIRY_UNIVERSE_ALL),
                           ("Quarterly + front month", EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH))
GAMMA_UNDERLYING_CHOICES = (("Index", GAMMA_UNDERLYING_INDEX), ("Forward", GAMMA_UNDERLYING_FORWARD))
CHART_CHOICES = (("IV*OI all expiries", CHART_IV_TIMES_OPEN_INTEREST),
                 ("IV*OI one expiry", CHART_IV_TIMES_OPEN_INTEREST_EXPIRY),
                 ("Net GEX 4 panels", CHART_NET_GEX_PANELS),
                 ("Net DEX all expiries", CHART_NET_DEX),
                 ("All charts", CHART_ALL))

LEVELS_COLUMN_LABELS = ("Scope", "Expiry", "Call Resistance", "Put Support", "HVL", "Spot",
                        "GEX Expiring %")
COMPARISON_COLUMN_LABELS = ("Item", "Ours", "MenthorQ", "Ours - MenthorQ")
NOT_COMPUTED = "not computed"


@dataclass(frozen=True)
class ChartRequest:
    """What the user asked to generate."""

    currency: str
    mode: str
    menthorq_date: date
    expiry_universe: str
    gamma_underlying: str
    reference_path: Optional[Path]
    output_directory: Path


class MenthorqChartsWorker(QThread):
    """
    Runs one MenthorQ generation in a background thread: reads the optional
    reference JSON and makes a single call into
    ``MenthorqReplicationService`` (daily close or live).
    """

    succeeded = Signal(object)  # {"output": service result, "reference": dict or None}
    failed = Signal(str)

    def __init__(self, request: ChartRequest, parent: Optional[QWidget] = None):
        """
        Args:
            request: The generation parameters.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.request = request

    def run(self) -> None:
        """Generate the charts; emit ``succeeded`` or ``failed`` (message)."""
        request = self.request
        try:
            reference = (json.loads(request.reference_path.read_text(encoding="utf-8"))
                         if request.reference_path is not None else None)
            service = MenthorqReplicationService()
            if request.mode == RUN_MODE_LIVE:
                output = service.run_live(
                    request.currency, request.output_directory,
                    expiry_universe=request.expiry_universe,
                    gamma_underlying=request.gamma_underlying, reference=reference,
                )
            else:
                output = service.run_menthorq_daily_close(
                    request.currency, request.menthorq_date, request.output_directory,
                    expiry_universe=request.expiry_universe,
                    gamma_underlying=request.gamma_underlying, reference=reference,
                )
            self.succeeded.emit({"output": output, "reference": reference})
        except Exception as error:
            logger.exception("MenthorQ chart generation failed")
            self.failed.emit(f"{type(error).__name__}: {error}")


class ChartScrollArea(QScrollArea):
    """Scroll area that tells its owner when its width changes, to rescale the charts."""

    resized = Signal()

    def resizeEvent(self, event) -> None:
        """Emit ``resized`` after the normal resize handling."""
        super().resizeEvent(event)
        self.resized.emit()


def format_level(value: Optional[float]) -> str:
    """Strike level for display; None is 'not computed', never 0."""
    return NOT_COMPUTED if value is None else f"{value:,.0f}"


def format_comparison_value(value: Any, missing_text: str) -> str:
    """A comparison cell: number with separators, text as is, None as ``missing_text``."""
    if value is None:
        return missing_text
    if isinstance(value, str):
        return value
    return f"{value:,.2f}"


def format_difference(value: Any) -> str:
    """Signed difference, 'same'/'different' as is, None as 'not computed'."""
    if value is None:
        return NOT_COMPUTED
    if isinstance(value, str):
        return value
    return f"{value:+,.2f}"


class MenthorqChartsTab(QWidget):
    """
    Tab that generates and shows the MenthorQ-style charts.

    Controls: currency, mode (MenthorQ daily close for a date, or live from
    the newest stored snapshot), expiry universe, gamma underlying, an
    optional reference JSON, and which chart to show. Shows the chart
    (scaled to the width, scrollable, save-as), its levels, the input
    instants, data warnings and, with a reference, ours vs MenthorQ.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.output_directory = DEFAULT_OUTPUT_DIRECTORY
        self.worker: Optional[MenthorqChartsWorker] = None
        self.output: Optional[Dict[str, Any]] = None
        self.reference: Optional[Dict[str, Any]] = None
        self.displayed_chart_paths: List[Path] = []
        self.displayed_pixmaps: List[tuple] = []  # (QLabel, QPixmap) pairs
        self._setup_ui()
        self._setup_logging()
        self._connect_signals()
        self._on_mode_changed()
        self._on_chart_changed()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header = QLabel("MenthorQ Charts")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(header)

        main_layout.addWidget(self._create_generation_frame())
        main_layout.addWidget(self._create_view_frame())

        self.info_label = QLabel("No charts generated yet.")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        main_layout.addWidget(self.info_label)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {Colors.WARNING};")
        self.warning_label.setVisible(False)
        main_layout.addWidget(self.warning_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.chart_scroll_area = ChartScrollArea()
        self.chart_scroll_area.setWidgetResizable(True)
        self.chart_scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: {Colors.SURFACE}; border: 1px solid {Colors.BORDER};"
            f" border-radius: 8px; }}")
        self.chart_container = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(4, 4, 4, 4)
        self.chart_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chart_scroll_area.setWidget(self.chart_container)
        splitter.addWidget(self.chart_scroll_area)

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
        self.levels_table = self._create_table(LEVELS_COLUMN_LABELS)
        self.results_tabs.addTab(self.levels_table, "Levels")
        self.comparison_table = self._create_table(COMPARISON_COLUMN_LABELS)
        self.results_tabs.addTab(self.comparison_table, "Compare with MenthorQ")
        self.log_viewer = LogViewer()
        self.results_tabs.addTab(self.log_viewer, "Output")
        splitter.addWidget(self.results_tabs)
        splitter.setSizes([600, 600])

        main_layout.addWidget(splitter, 1)

    @staticmethod
    def _frame() -> QFrame:
        """Rounded panel in the style of the other tabs."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
            QLabel {{ border: none; color: {Colors.TEXT_SECONDARY}; }}
            QDateEdit {{
                background-color: {Colors.INPUT_BACKGROUND};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 6px 10px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QDateEdit:disabled {{ color: {Colors.TEXT_DISABLED}; }}
        """)
        return frame

    @staticmethod
    def _combo(choices) -> QComboBox:
        """Combo box whose items carry (label, value) pairs."""
        combo = QComboBox()
        for label, value in choices:
            combo.addItem(label, value)
        return combo

    def _create_generation_frame(self) -> QFrame:
        """Inputs of a generation: currency, mode, date, universe, gamma, reference."""
        frame = self._frame()
        layout = QGridLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.currency_combo = self._combo((currency, currency) for currency in CURRENCIES)
        self.currency_combo.setToolTip(
            "ETH uses the BTC-fitted formulas unchanged; they are not validated for ETH.")
        self.mode_combo = self._combo(MODES)
        self.mode_combo.setToolTip(
            "Daily close: spot and IV at 23:59 UTC, open interest from 00:00 UTC of the date "
            "(MenthorQ's convention as found on three dates).\n"
            "Live: the newest snapshot in the local database (as fresh as the last VPS sync).")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        newest_day = last_complete_utc_day(datetime.now(timezone.utc))
        newest_qdate = QDate(newest_day.year, newest_day.month, newest_day.day)
        self.date_edit.setMaximumDate(newest_qdate)
        self.date_edit.setDate(newest_qdate)
        self.date_edit.setToolTip("UTC date of the MenthorQ chart; at most the last complete UTC day.")
        self.expiry_universe_combo = self._combo(EXPIRY_UNIVERSE_CHOICES)
        self.expiry_universe_combo.setToolTip(
            "Expiries in the all-expiry charts. MenthorQ does not publish its set; no single set "
            "fits every date checked.")
        self.gamma_underlying_combo = self._combo(GAMMA_UNDERLYING_CHOICES)
        self.gamma_underlying_combo.setToolTip("Price the gamma is evaluated at.")

        for column, (label, widget) in enumerate((
                ("Currency", self.currency_combo), ("Mode", self.mode_combo),
                ("Date (UTC)", self.date_edit), ("Expiry universe", self.expiry_universe_combo),
                ("Gamma at", self.gamma_underlying_combo))):
            layout.addWidget(QLabel(label), 0, column)
            layout.addWidget(widget, 1, column)

        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("Reference JSON"))
        self.reference_edit = QLineEdit()
        self.reference_edit.setReadOnly(True)
        self.reference_edit.setPlaceholderText("optional: MenthorQ numbers to compare with")
        reference_row.addWidget(self.reference_edit, 1)
        self.browse_reference_button = self._secondary_button("Browse...")
        reference_row.addWidget(self.browse_reference_button)
        self.clear_reference_button = self._secondary_button("Clear")
        reference_row.addWidget(self.clear_reference_button)
        reference_row.addSpacing(12)
        self.generate_button = QPushButton("Generate")
        self.generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_button.setToolTip("Renders every chart (a few seconds); the chart selector "
                                        "below picks which one is shown.")
        reference_row.addWidget(self.generate_button)
        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(80)
        reference_row.addWidget(self.status_label)
        layout.addLayout(reference_row, 2, 0, 1, 5)
        return frame

    def _create_view_frame(self) -> QFrame:
        """Which generated chart to show, and save-as."""
        frame = self._frame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Chart"))
        self.chart_combo = self._combo(CHART_CHOICES)
        layout.addWidget(self.chart_combo)
        layout.addWidget(QLabel("Expiry"))
        self.expiry_combo = QComboBox()
        self.expiry_combo.setMinimumWidth(170)
        self.expiry_combo.setPlaceholderText("generate first")
        self.expiry_combo.setToolTip("Expiries listed in the snapshot and not settled at the "
                                     "chart instant (filled after Generate).")
        layout.addWidget(self.expiry_combo)
        layout.addStretch()
        self.save_button = self._secondary_button("Save PNG as...")
        layout.addWidget(self.save_button)
        return frame

    @staticmethod
    def _secondary_button(text: str) -> QPushButton:
        """Button in the secondary style used across tabs."""
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}
            QPushButton:hover {{ background-color: {Colors.BUTTON_SECONDARY_HOVER}; }}
            QPushButton:disabled {{ background-color: {Colors.SURFACE}; color: {Colors.TEXT_DISABLED}; }}
        """)
        return button

    @staticmethod
    def _create_table(column_labels) -> QTableWidget:
        """Read-only table in the style of the On Chain Analysis tab."""
        table = QTableWidget(0, len(column_labels))
        table.setHorizontalHeaderLabels(column_labels)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setStyleSheet(f"""
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
        """)
        return table

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        """Send the replication service's log records to this tab's Output view."""
        self._log_target_logger = logging.getLogger(GUI_LOG_LOGGER_NAME)
        self.gui_handler = GuiLogHandler(self.log_viewer)
        if self.gui_handler not in self._log_target_logger.handlers:
            self._log_target_logger.addHandler(self.gui_handler)

    def closeEvent(self, event) -> None:
        """Remove the log handler on close so it does not leak."""
        self._log_target_logger.removeHandler(self.gui_handler)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        """Connect widget signals to slots."""
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.browse_reference_button.clicked.connect(self._browse_reference)
        self.clear_reference_button.clicked.connect(self.reference_edit.clear)
        self.generate_button.clicked.connect(self._generate)
        self.chart_combo.currentIndexChanged.connect(self._on_chart_changed)
        self.expiry_combo.currentIndexChanged.connect(self._refresh_view)
        self.save_button.clicked.connect(self._save_chart_as)
        self.chart_scroll_area.resized.connect(self._rescale_charts)

    def _on_mode_changed(self, _index: Optional[int] = None) -> None:
        """The date only applies to the daily-close mode."""
        self.date_edit.setEnabled(self.mode_combo.currentData() == RUN_MODE_MENTHORQ_DAILY_CLOSE)

    def _on_chart_changed(self, _index: Optional[int] = None) -> None:
        """The expiry selector only applies to the one-expiry IV*OI chart."""
        self.expiry_combo.setEnabled(
            self.chart_combo.currentData() == CHART_IV_TIMES_OPEN_INTEREST_EXPIRY)
        self._refresh_view()

    def _browse_reference(self) -> None:
        """Pick a MenthorQ reference JSON."""
        path, _ = QFileDialog.getOpenFileName(
            self, "MenthorQ reference JSON", str(REFERENCE_DIRECTORY), "JSON files (*.json)")
        if path:
            self.reference_edit.setText(path)

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def build_request(self) -> ChartRequest:
        """The generation parameters currently selected in the controls."""
        selected_date = self.date_edit.date()
        reference_text = self.reference_edit.text().strip()
        return ChartRequest(
            currency=self.currency_combo.currentData(),
            mode=self.mode_combo.currentData(),
            menthorq_date=date(selected_date.year(), selected_date.month(), selected_date.day()),
            expiry_universe=self.expiry_universe_combo.currentData(),
            gamma_underlying=self.gamma_underlying_combo.currentData(),
            reference_path=Path(reference_text) if reference_text else None,
            output_directory=self.output_directory,
        )

    def _generate(self) -> None:
        """Start a generation in the worker thread."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A generation is already running")
            return
        request = self.build_request()
        description = ("live (newest stored snapshot)" if request.mode == RUN_MODE_LIVE
                       else f"MenthorQ daily close {request.menthorq_date.isoformat()}")
        self.log_viewer.log_info(f"Generating {request.currency} charts, {description}, "
                                 f"{request.expiry_universe}, gamma at {request.gamma_underlying}...")
        self.generate_button.setEnabled(False)
        self._set_status("Generating...", Colors.WARNING)
        self.worker = MenthorqChartsWorker(request)
        self.worker.succeeded.connect(self._on_generation_succeeded)
        self.worker.failed.connect(self._on_generation_failed)
        self.worker.start()

    def _set_status(self, text: str, color: str) -> None:
        """Short status text next to the Generate button."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def _on_generation_succeeded(self, result: Dict[str, Any]) -> None:
        """Show a finished generation."""
        self.output = result["output"]
        self.reference = result["reference"]
        output = self.output
        assert output is not None

        previous_expiry = self.expiry_combo.currentText()
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        choices = expiry_choices(output)
        self.expiry_combo.addItems(choices)
        if previous_expiry in choices:
            self.expiry_combo.setCurrentText(previous_expiry)
        self.expiry_combo.blockSignals(False)

        self.info_label.setText(info_line(output))
        warnings = data_warnings(output, self.reference)
        self.warning_label.setText("\n".join(f"Warning: {warning}" for warning in warnings))
        self.warning_label.setVisible(bool(warnings))
        self._fill_comparison_table()
        self._refresh_view()

        self.generate_button.setEnabled(True)
        self._set_status("Ready", Colors.SUCCESS)
        self.log_viewer.log_info(f"Charts written to {self.output_directory}")

    def _on_generation_failed(self, message: str) -> None:
        """Show a failed generation; earlier charts stay visible."""
        self.log_viewer.log_error(f"Failed: {message}")
        self.warning_label.setText(f"Generation failed: {message}")
        self.warning_label.setVisible(True)
        self.generate_button.setEnabled(True)
        self._set_status("Error", Colors.ERROR)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _refresh_view(self, _index: Optional[int] = None) -> None:
        """Show the selected chart(s) and their levels."""
        chart = self.chart_combo.currentData()
        expiration = self.expiry_combo.currentText() or None
        if self.output is None:
            self._show_charts([], "Choose the inputs above and press Generate.")
            self.levels_table.setRowCount(0)
            return
        file_names = chart_file_names(self.output, chart, expiration)
        empty_text = ("Choose an expiry." if chart == CHART_IV_TIMES_OPEN_INTEREST_EXPIRY
                      else "This chart was not generated.")
        self._show_charts([self.output_directory / name for name in file_names], empty_text)
        self._fill_levels_table(levels_rows(self.output, chart, expiration))

    def _clear_charts(self) -> None:
        """Remove every chart label from the scroll area."""
        while self.chart_layout.count():
            item = self.chart_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.displayed_pixmaps = []
        self.displayed_chart_paths = []

    def _show_charts(self, paths: List[Path], empty_text: str) -> None:
        """Load the PNGs into the scroll area, one under the other."""
        self._clear_charts()
        if not paths:
            placeholder = QLabel(empty_text)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-style: italic;")
            self.chart_layout.addWidget(placeholder)
        for path in paths:
            pixmap = QPixmap(str(path))
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            if pixmap.isNull():
                label.setText(f"Could not load {path.name}")
                label.setStyleSheet(f"color: {Colors.ERROR};")
            else:
                label.setToolTip(path.name)
                self.displayed_pixmaps.append((label, pixmap))
            self.chart_layout.addWidget(label)
        self.displayed_chart_paths = list(paths)
        self.save_button.setEnabled(len(self.displayed_pixmaps) == 1)
        self.save_button.setToolTip("" if len(self.displayed_pixmaps) == 1 else
                                    "Select a single chart to save it; all files are in "
                                    f"{self.output_directory}")
        self._rescale_charts()

    def _rescale_charts(self) -> None:
        """Fit every shown chart to the scroll area's width (never enlarged)."""
        width = max(self.chart_scroll_area.viewport().width() - 12, 100)
        for label, pixmap in self.displayed_pixmaps:
            target_width = min(width, pixmap.width())
            label.setPixmap(pixmap.scaledToWidth(target_width,
                                                 Qt.TransformationMode.SmoothTransformation))

    def _fill_levels_table(self, rows: List[Dict[str, Any]]) -> None:
        """One table row per level set of the shown chart(s)."""
        self.levels_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            expiry_text = row["expiration_date"] or row["expiration"] or ""
            if not row["gex_expiring_percent_applies"]:
                expiring_text = ""
            elif row["gex_expiring_percent"] is None:
                expiring_text = NOT_COMPUTED
            else:
                expiring_text = f"{row['gex_expiring_percent']:.2f}%"
            cells = (row["scope"], expiry_text, format_level(row["call_resistance"]),
                     format_level(row["put_support"]), format_level(row["hvl"]),
                     f"{row['spot']:,.2f}", expiring_text)
            for column, text in enumerate(cells):
                self.levels_table.setItem(row_index, column, QTableWidgetItem(text))
            expiry_item = self.levels_table.item(row_index, 1)
            if expiry_item is not None and row["expiration"]:
                expiry_item.setToolTip(f"Deribit expiry {row['expiration']}, settles 08:00 UTC")

    def _fill_comparison_table(self) -> None:
        """Ours vs MenthorQ rows; a hint row when no reference was given."""
        assert self.output is not None
        rows = comparison_rows(self.output, self.reference)
        if not rows:
            self.comparison_table.setRowCount(1)
            self.comparison_table.setItem(0, 0, QTableWidgetItem(
                "No reference JSON given: pick one above and generate again."))
            for column in range(1, len(COMPARISON_COLUMN_LABELS)):
                self.comparison_table.setItem(0, column, QTableWidgetItem(""))
            return
        self.comparison_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            cells = (row["item"], format_comparison_value(row["ours"], NOT_COMPUTED),
                     format_comparison_value(row["reference"], "not in reference"),
                     format_difference(row["difference"]))
            for column, text in enumerate(cells):
                self.comparison_table.setItem(row_index, column, QTableWidgetItem(text))

    def _save_chart_as(self) -> None:
        """Copy the shown PNG to a file the user picks."""
        if len(self.displayed_chart_paths) != 1:
            return
        source = self.displayed_chart_paths[0]
        target, _ = QFileDialog.getSaveFileName(
            self, "Save chart", str(Path.home() / source.name), "PNG image (*.png)")
        if not target:
            return
        shutil.copyfile(source, target)
        self.log_viewer.log_info(f"Saved {source.name} to {target}")
