"""
MenthorQ Monitor tab: one Run shows what the MenthorQ-style monitor would
produce, without sending or storing anything.

A Run (in a worker thread, so the window never freezes):
  - generates the MenthorQ-style charts (``MenthorqReplicationService``) into
    a dated folder under outputs/menthorq_replicate/gui/,
  - builds the Telegram brief and the urgent-alert check
    (``MenthorqBriefPreviewService``: records replayed from the snapshots,
    nothing stored, nothing sent),
and the tab shows the brief as rich text, the alert rules, the inputs, the
chart files and a chart viewer. The tab only formats what the services
return; it has no calculation and no database access.
"""

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QDate, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from coding.core.analytics.menthorq_chart_summary import comparison_rows, data_warnings, info_line
from coding.gui.components.log_viewer import GuiLogHandler, LogViewer
from coding.gui.tabs.menthorq_charts_tab import (
    COMPARISON_COLUMN_LABELS,
    CURRENCIES,
    EXPIRY_UNIVERSE_CHOICES,
    GAMMA_UNDERLYING_CHOICES,
    MODES,
    NOT_COMPUTED,
    ChartScrollArea,
    MenthorqChartsTab,
    format_comparison_value,
    format_difference,
)
from coding.gui.theme.colors import Colors
from coding.service.on_chain.menthorq_monitor.brief_preview_service import (
    BriefPreview,
    build_brief_preview_service,
    generated_chart_files,
    monitor_output_directory,
    preview_as_of_for_daily_close,
    telegram_html_to_plain_text,
)
from coding.service.on_chain.menthorq_replication_service import (
    RUN_MODE_LIVE,
    RUN_MODE_MENTHORQ_DAILY_CLOSE,
    MenthorqReplicationService,
    last_complete_utc_day,
)

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_BASE_DIRECTORY = REPOSITORY_ROOT / "outputs" / "menthorq_replicate" / "gui"
REFERENCE_DIRECTORY = REPOSITORY_ROOT / "outputs" / "menthorq_replicate"

# The replication service's and the monitor package's messages go to this tab's log.
GUI_LOG_LOGGER_NAMES = ("coding.service.on_chain.menthorq_replication_service",
                        "coding.service.on_chain.menthorq_monitor")

ALERT_COLUMN_LABELS = ("Rule", "Enabled", "Threshold", "Current value", "Would fire")
INFO_COLUMN_LABELS = ("Item", "Value")
SAFETY_NOTE = ("This tab never sends Telegram messages and never writes to the database. "
               "It reads the local database and writes chart PNG/JSON files only.")


@dataclass(frozen=True)
class MonitorRequest:
    """What the user asked a Run to produce."""

    currency: str
    mode: str
    menthorq_date: date
    expiry_universe: str
    gamma_underlying: str
    reference_path: Optional[Path]
    output_directory: Path


def telegram_html_to_rich_text(text: str) -> str:
    """
    Telegram HTML (<b>, <i>, <pre>, newlines) as Qt rich text: Qt ignores
    newlines outside <pre>, so they become <br>. A newline right before or
    after a <pre> block is dropped: Qt already starts the block on its own
    line, and keeping it would add an empty line.
    """
    parts = re.split(r"(<pre>.*?</pre>)", text, flags=re.DOTALL)
    rich_parts = []
    for index, part in enumerate(parts):
        if part.startswith("<pre>"):
            rich_parts.append(part)
            continue
        if index > 0 and part.startswith("\n"):
            part = part[1:]
        if index < len(parts) - 1 and part.endswith("\n"):
            part = part[:-1]
        rich_parts.append(part.replace("\n", "<br>"))
    return "".join(rich_parts)


class MenthorqMonitorWorker(QThread):
    """
    One Run in a background thread: the charts, then the brief preview.
    A failure of one part is reported in the result and does not stop the
    other part.
    """

    succeeded = Signal(object)   # dict, see ``run``
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        request: MonitorRequest,
        replication_service_factory: Callable[[], Any] = MenthorqReplicationService,
        preview_service_factory: Callable[[str, str, str], Any] = build_brief_preview_service,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        parent: Optional[QWidget] = None,
    ):
        """
        Args:
            request: The Run parameters.
            replication_service_factory: Builds the chart service.
            preview_service_factory: Builds the brief preview service from
                (currency, expiry universe, gamma underlying).
            clock: Wall clock (UTC-aware): the brief instant of a live Run.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.request = request
        self.replication_service_factory = replication_service_factory
        self.preview_service_factory = preview_service_factory
        self.clock = clock

    def run(self) -> None:
        """
        Emit ``succeeded`` with {"reference", "chart_output", "chart_error",
        "preview", "preview_error"}, or ``failed`` when the reference JSON
        cannot be read.
        """
        request = self.request
        try:
            reference = (json.loads(request.reference_path.read_text(encoding="utf-8"))
                         if request.reference_path is not None else None)
        except Exception as error:
            logger.exception("MenthorQ reference JSON could not be read")
            self.failed.emit(f"Reference JSON: {type(error).__name__}: {error}")
            return
        result: Dict[str, Any] = {"reference": reference, "chart_output": None, "chart_error": None,
                                  "preview": None, "preview_error": None}

        self.progress.emit("Generating charts...")
        try:
            service = self.replication_service_factory()
            if request.mode == RUN_MODE_LIVE:
                result["chart_output"] = service.run_live(
                    request.currency, request.output_directory,
                    expiry_universe=request.expiry_universe,
                    gamma_underlying=request.gamma_underlying, reference=reference)
            else:
                result["chart_output"] = service.run_menthorq_daily_close(
                    request.currency, request.menthorq_date, request.output_directory,
                    expiry_universe=request.expiry_universe,
                    gamma_underlying=request.gamma_underlying, reference=reference)
        except Exception as error:
            logger.exception("MenthorQ chart generation failed")
            result["chart_error"] = f"{type(error).__name__}: {error}"

        self.progress.emit("Building the brief...")
        try:
            preview_service = self.preview_service_factory(
                request.currency, request.expiry_universe, request.gamma_underlying)
            as_of = (self.clock() if request.mode == RUN_MODE_LIVE
                     else preview_as_of_for_daily_close(request.menthorq_date))
            result["preview"] = preview_service.build_preview(as_of)
        except Exception as error:
            logger.exception("MenthorQ brief preview failed")
            result["preview_error"] = f"{type(error).__name__}: {error}"
        self.succeeded.emit(result)


class MenthorqMonitorTab(QWidget):
    """
    Tab that previews the MenthorQ-style monitor: charts, Telegram brief,
    urgent-alert check and inputs, for a daily close or live.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.output_base_directory = DEFAULT_OUTPUT_BASE_DIRECTORY
        self.replication_service_factory: Callable[[], Any] = MenthorqReplicationService
        self.preview_service_factory: Callable[[str, str, str], Any] = build_brief_preview_service
        self.clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self.worker: Optional[MenthorqMonitorWorker] = None
        self.output_directory: Optional[Path] = None
        self.chart_files: List[Path] = []
        self.displayed_chart: Optional[Tuple[Path, QPixmap]] = None
        self.brief_html: Optional[str] = None
        self._setup_ui()
        self._setup_logging()
        self._connect_signals()
        self._on_mode_changed()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header = QLabel("MenthorQ Monitor")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header_row.addWidget(header)
        header_row.addSpacing(16)
        self.safety_label = QLabel(SAFETY_NOTE)
        self.safety_label.setWordWrap(True)
        self.safety_label.setStyleSheet(f"color: {Colors.ACCENT}; font-style: italic;")
        header_row.addWidget(self.safety_label, 1)
        main_layout.addLayout(header_row)

        main_layout.addWidget(self._create_run_frame())
        main_layout.addWidget(self._create_folder_frame())

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(f"color: {Colors.WARNING};")
        self.warning_label.setVisible(False)
        main_layout.addWidget(self.warning_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._create_chart_panel())
        splitter.addWidget(self._create_results_tabs())
        splitter.setSizes([650, 650])
        main_layout.addWidget(splitter, 1)

    def _create_run_frame(self) -> QFrame:
        """Inputs of a Run: currency, mode, date, universe, gamma, reference, Run."""
        frame = MenthorqChartsTab._frame()
        layout = QGridLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.currency_combo = MenthorqChartsTab._combo((currency, currency) for currency in CURRENCIES)
        self.currency_combo.setToolTip(
            "The monitor runs on BTC. ETH uses the BTC-fitted formulas unchanged (not validated).")
        self.mode_combo = MenthorqChartsTab._combo(MODES)
        self.mode_combo.setToolTip(
            "Daily close: charts at 23:59 UTC with open interest from 00:00 UTC; the brief as "
            "Telegram would have had it at 23:59 UTC of that date.\n"
            "Live: charts from the newest stored snapshot; the brief as of now.")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        newest_day = last_complete_utc_day(datetime.now(timezone.utc))
        newest_qdate = QDate(newest_day.year, newest_day.month, newest_day.day)
        self.date_edit.setMaximumDate(newest_qdate)
        self.date_edit.setDate(newest_qdate)
        self.date_edit.setToolTip("UTC date; at most the last complete UTC day.")
        self.expiry_universe_combo = MenthorqChartsTab._combo(EXPIRY_UNIVERSE_CHOICES)
        self.expiry_universe_combo.setToolTip("Expiries in the levels (charts and brief). The "
                                              "collector uses MENTHORQ_EXPIRY_UNIVERSE, default "
                                              "monthly.")
        self.gamma_underlying_combo = MenthorqChartsTab._combo(GAMMA_UNDERLYING_CHOICES)
        self.gamma_underlying_combo.setToolTip("Price the gamma is evaluated at.")

        for column, (label, widget) in enumerate((
                ("Currency", self.currency_combo), ("Mode", self.mode_combo),
                ("Date (UTC)", self.date_edit), ("Expiry universe", self.expiry_universe_combo),
                ("Gamma at", self.gamma_underlying_combo))):
            layout.addWidget(QLabel(label), 0, column)
            layout.addWidget(widget, 1, column)

        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("Compare with reference JSON"))
        self.reference_edit = QLineEdit()
        self.reference_edit.setReadOnly(True)
        self.reference_edit.setPlaceholderText("optional: MenthorQ numbers to compare the charts with")
        reference_row.addWidget(self.reference_edit, 1)
        self.browse_reference_button = MenthorqChartsTab._secondary_button("Browse...")
        reference_row.addWidget(self.browse_reference_button)
        self.clear_reference_button = MenthorqChartsTab._secondary_button("Clear")
        reference_row.addWidget(self.clear_reference_button)
        reference_row.addSpacing(12)
        self.run_button = QPushButton("Run")
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_button.setToolTip("Charts + brief + alert check (takes a few seconds; "
                                   "nothing is sent or stored).")
        reference_row.addWidget(self.run_button)
        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(150)
        reference_row.addWidget(self.status_label)
        layout.addLayout(reference_row, 2, 0, 1, 5)
        return frame

    def _create_folder_frame(self) -> QFrame:
        """Link to the output folder of the last Run, and an Open folder button."""
        frame = MenthorqChartsTab._frame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.addWidget(QLabel("Output folder"))
        self.folder_link_label = QLabel("no Run yet")
        self.folder_link_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.folder_link_label.setOpenExternalLinks(False)
        self.folder_link_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(self.folder_link_label, 1)
        self.open_folder_button = MenthorqChartsTab._secondary_button("Open folder")
        self.open_folder_button.setEnabled(False)
        layout.addWidget(self.open_folder_button)
        return frame

    def _create_chart_panel(self) -> QWidget:
        """Generated chart files (click to view), the chart viewer and Save as."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        files_label = QLabel("Generated charts (click to view)")
        files_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        layout.addWidget(files_label)
        self.chart_list = QListWidget()
        self.chart_list.setMaximumHeight(140)
        self.chart_list.setStyleSheet(
            f"QListWidget {{ background-color: {Colors.SURFACE}; color: {Colors.TEXT_PRIMARY};"
            f" border: 1px solid {Colors.BORDER}; border-radius: 8px; }}"
            f" QListWidget::item:selected {{ background-color: {Colors.SURFACE_ACTIVE};"
            f" color: {Colors.ACCENT}; }}")
        layout.addWidget(self.chart_list)

        self.chart_scroll_area = ChartScrollArea()
        self.chart_scroll_area.setWidgetResizable(True)
        self.chart_scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: {Colors.SURFACE}; border: 1px solid {Colors.BORDER};"
            f" border-radius: 8px; }}")
        self.chart_label = QLabel("Choose the inputs above and press Run.")
        self.chart_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.chart_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-style: italic;")
        self.chart_scroll_area.setWidget(self.chart_label)
        layout.addWidget(self.chart_scroll_area, 1)

        save_row = QHBoxLayout()
        save_row.addStretch()
        self.save_button = MenthorqChartsTab._secondary_button("Save PNG as...")
        self.save_button.setEnabled(False)
        save_row.addWidget(self.save_button)
        layout.addLayout(save_row)
        return panel

    @staticmethod
    def _text_browser() -> QTextBrowser:
        """Read-only rich-text panel in the surface style."""
        browser = QTextBrowser()
        browser.setOpenLinks(False)
        browser.document().setDefaultStyleSheet(
            f"pre {{ font-family: Consolas, monospace; color: {Colors.TEXT_PRIMARY}; }}"
            f" b {{ color: {Colors.ACCENT}; }}")
        browser.setStyleSheet(
            f"QTextBrowser {{ background-color: {Colors.SURFACE}; color: {Colors.TEXT_PRIMARY};"
            f" border: 1px solid {Colors.BORDER}; border-radius: 8px; padding: 6px; }}")
        return browser

    def _create_results_tabs(self) -> QTabWidget:
        """Brief, alerts, info, comparison and log."""
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
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

        brief_page = QWidget()
        brief_layout = QVBoxLayout(brief_page)
        brief_layout.setContentsMargins(8, 8, 8, 8)
        brief_top = QHBoxLayout()
        self.brief_length_label = QLabel("")
        self.brief_length_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        brief_top.addWidget(self.brief_length_label, 1)
        self.copy_button = MenthorqChartsTab._secondary_button("Copy as plain text")
        self.copy_button.setEnabled(False)
        brief_top.addWidget(self.copy_button)
        brief_layout.addLayout(brief_top)
        self.brief_browser = self._text_browser()
        self.brief_browser.setPlainText("The brief appears here after Run.")
        brief_layout.addWidget(self.brief_browser, 1)
        tabs.addTab(brief_page, "Brief")

        alerts_page = QWidget()
        alerts_layout = QVBoxLayout(alerts_page)
        alerts_layout.setContentsMargins(8, 8, 8, 8)
        alerts_note = QLabel("Urgent-alert rules at the brief instant, with the collector's "
                             "thresholds, BEFORE the cooldown (a preview does not read the "
                             "delivery log). Enabled = MENTHORQ_ALERTS_ENABLED in the local .env.")
        alerts_note.setWordWrap(True)
        alerts_note.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        alerts_layout.addWidget(alerts_note)
        self.alerts_table = MenthorqChartsTab._create_table(ALERT_COLUMN_LABELS)
        alerts_layout.addWidget(self.alerts_table, 1)
        alert_message_label = QLabel("Urgent message that would be sent")
        alert_message_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        alerts_layout.addWidget(alert_message_label)
        self.alert_browser = self._text_browser()
        self.alert_browser.setPlainText("Run first.")
        alerts_layout.addWidget(self.alert_browser, 1)
        tabs.addTab(alerts_page, "Alerts")

        info_page = QWidget()
        info_layout = QVBoxLayout(info_page)
        info_layout.setContentsMargins(8, 8, 8, 8)
        self.info_table = MenthorqChartsTab._create_table(INFO_COLUMN_LABELS)
        info_layout.addWidget(self.info_table, 2)
        caveats_label = QLabel("Warnings and data caveats")
        caveats_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        info_layout.addWidget(caveats_label)
        self.caveats_browser = self._text_browser()
        info_layout.addWidget(self.caveats_browser, 1)
        tabs.addTab(info_page, "Info")

        self.comparison_table = MenthorqChartsTab._create_table(COMPARISON_COLUMN_LABELS)
        tabs.addTab(self.comparison_table, "Compare with MenthorQ")
        self.log_viewer = LogViewer()
        tabs.addTab(self.log_viewer, "Output")
        self.results_tabs = tabs
        return tabs

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        """Send the services' log records to this tab's Output view."""
        self.gui_handler = GuiLogHandler(self.log_viewer)
        self._log_target_loggers = [logging.getLogger(name) for name in GUI_LOG_LOGGER_NAMES]
        for target in self._log_target_loggers:
            if self.gui_handler not in target.handlers:
                target.addHandler(self.gui_handler)

    def closeEvent(self, event) -> None:
        """Remove the log handler on close so it does not leak."""
        for target in self._log_target_loggers:
            target.removeHandler(self.gui_handler)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        """Connect widget signals to slots."""
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.browse_reference_button.clicked.connect(self._browse_reference)
        self.clear_reference_button.clicked.connect(self.reference_edit.clear)
        self.run_button.clicked.connect(self.start_run)
        self.folder_link_label.linkActivated.connect(lambda _link: self.open_output_folder())
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.chart_list.currentRowChanged.connect(self._show_chart)
        self.chart_scroll_area.resized.connect(self._rescale_chart)
        self.save_button.clicked.connect(self._save_chart_as)
        self.copy_button.clicked.connect(self.copy_brief_as_plain_text)

    def _on_mode_changed(self, _index: Optional[int] = None) -> None:
        """The date only applies to the daily-close mode."""
        self.date_edit.setEnabled(self.mode_combo.currentData() == RUN_MODE_MENTHORQ_DAILY_CLOSE)

    def _browse_reference(self) -> None:
        """Pick a MenthorQ reference JSON."""
        path, _ = QFileDialog.getOpenFileName(
            self, "MenthorQ reference JSON", str(REFERENCE_DIRECTORY), "JSON files (*.json)")
        if path:
            self.reference_edit.setText(path)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def build_request(self) -> MonitorRequest:
        """The Run parameters currently selected (output folder dated by the Run)."""
        selected_date = self.date_edit.date()
        menthorq_date = date(selected_date.year(), selected_date.month(), selected_date.day())
        mode = self.mode_combo.currentData()
        reference_text = self.reference_edit.text().strip()
        return MonitorRequest(
            currency=self.currency_combo.currentData(),
            mode=mode,
            menthorq_date=menthorq_date,
            expiry_universe=self.expiry_universe_combo.currentData(),
            gamma_underlying=self.gamma_underlying_combo.currentData(),
            reference_path=Path(reference_text) if reference_text else None,
            output_directory=monitor_output_directory(
                self.output_base_directory, mode == RUN_MODE_LIVE, menthorq_date, self.clock()),
        )

    def start_run(self) -> None:
        """Start a Run in the worker thread."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A Run is already in progress")
            return
        request = self.build_request()
        description = ("live" if request.mode == RUN_MODE_LIVE
                       else f"daily close {request.menthorq_date.isoformat()}")
        self.log_viewer.log_info(f"Run: {request.currency}, {description}, {request.expiry_universe}, "
                                 f"gamma at {request.gamma_underlying} -> {request.output_directory}")
        self.run_button.setEnabled(False)
        self._set_status("Running...", Colors.WARNING)
        self.worker = MenthorqMonitorWorker(
            request, self.replication_service_factory, self.preview_service_factory, self.clock)
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_run_succeeded)
        self.worker.failed.connect(self.show_failure)
        self.worker.start()

    def _on_progress(self, text: str) -> None:
        """Progress text from the worker."""
        self._set_status(text, Colors.WARNING)

    def _on_run_succeeded(self, result: Dict[str, Any]) -> None:
        """Worker finished: show its result for its request."""
        assert self.worker is not None
        self.show_result(self.worker.request, result)

    def _set_status(self, text: str, color: str) -> None:
        """Short status text next to the Run button."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; border: none;")

    def show_failure(self, message: str) -> None:
        """A Run that could not start (e.g. unreadable reference JSON)."""
        self.log_viewer.log_error(f"Failed: {message}")
        self.warning_label.setText(f"Run failed: {message}")
        self.warning_label.setVisible(True)
        self.run_button.setEnabled(True)
        self._set_status("Error", Colors.ERROR)

    def show_result(self, request: MonitorRequest, result: Dict[str, Any]) -> None:
        """Show a finished Run (charts and preview; either may have failed)."""
        errors = []
        if result["chart_error"]:
            errors.append(f"Charts failed: {result['chart_error']}")
        if result["preview_error"]:
            errors.append(f"Brief failed: {result['preview_error']}")
        self.warning_label.setText("\n".join(errors))
        self.warning_label.setVisible(bool(errors))

        chart_output = result["chart_output"]
        self._show_output_folder(request.output_directory if chart_output is not None else None)
        self._show_chart_files(request.output_directory, chart_output)
        self._fill_comparison_table(chart_output, result["reference"])
        self._show_preview(result["preview"], result["preview_error"], chart_output,
                           result["reference"])

        self.run_button.setEnabled(True)
        self._set_status("Done with errors" if errors else "Ready",
                         Colors.ERROR if errors else Colors.SUCCESS)

    # ------------------------------------------------------------------
    # Output folder and charts
    # ------------------------------------------------------------------

    def _show_output_folder(self, directory: Optional[Path]) -> None:
        """Clickable link to the folder the charts were written to."""
        self.output_directory = directory
        self.open_folder_button.setEnabled(directory is not None)
        if directory is None:
            self.folder_link_label.setText("no charts written")
            return
        url = QUrl.fromLocalFile(str(directory)).toString()
        self.folder_link_label.setText(
            f'<a href="{url}" style="color: {Colors.ACCENT};">{directory}</a>')

    def open_output_folder(self) -> None:
        """Open the output folder in the system file browser."""
        if self.output_directory is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_directory)))

    def _show_chart_files(self, directory: Path, chart_output: Optional[Dict[str, Any]]) -> None:
        """List the generated PNGs and show the first one."""
        self.chart_files = ([directory / name for name in generated_chart_files(chart_output)]
                            if chart_output is not None else [])
        self.chart_list.blockSignals(True)
        self.chart_list.clear()
        self.chart_list.addItems([path.name for path in self.chart_files])
        self.chart_list.blockSignals(False)
        if self.chart_files:
            self.chart_list.setCurrentRow(0)
        else:
            self._show_chart(-1)

    def _show_chart(self, row: int) -> None:
        """Load the chart of list row ``row`` into the viewer (-1: none)."""
        if row < 0 or row >= len(self.chart_files):
            self.displayed_chart = None
            self.chart_label.setPixmap(QPixmap())
            self.chart_label.setText("No chart generated." if self.output_directory is None
                                     else "Choose a chart.")
            self.save_button.setEnabled(False)
            return
        path = self.chart_files[row]
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.displayed_chart = None
            self.chart_label.setPixmap(QPixmap())
            self.chart_label.setText(f"Could not load {path.name}")
            self.save_button.setEnabled(False)
            return
        self.displayed_chart = (path, pixmap)
        self.chart_label.setToolTip(path.name)
        self.save_button.setEnabled(True)
        self._rescale_chart()

    def _rescale_chart(self) -> None:
        """Fit the shown chart to the viewer's width (never enlarged)."""
        if self.displayed_chart is None:
            return
        _, pixmap = self.displayed_chart
        width = min(max(self.chart_scroll_area.viewport().width() - 12, 100), pixmap.width())
        self.chart_label.setPixmap(pixmap.scaledToWidth(
            width, Qt.TransformationMode.SmoothTransformation))

    def _save_chart_as(self) -> None:
        """Copy the shown PNG to a file the user picks."""
        if self.displayed_chart is None:
            return
        source, _ = self.displayed_chart
        target, _ = QFileDialog.getSaveFileName(
            self, "Save chart", str(Path.home() / source.name), "PNG image (*.png)")
        if not target:
            return
        shutil.copyfile(source, target)
        self.log_viewer.log_info(f"Saved {source.name} to {target}")

    def _fill_comparison_table(
        self, chart_output: Optional[Dict[str, Any]], reference: Optional[Dict[str, Any]]
    ) -> None:
        """Ours vs MenthorQ rows; a hint row when there is nothing to compare."""
        rows = comparison_rows(chart_output, reference) if chart_output is not None else []
        if not rows:
            hint = ("No reference JSON given: pick one above and Run again." if reference is None
                    else "No charts were generated, so nothing to compare.")
            self._fill_table(self.comparison_table, [(hint, "", "", "")])
            return
        self._fill_table(self.comparison_table, [
            (row["item"], format_comparison_value(row["ours"], NOT_COMPUTED),
             format_comparison_value(row["reference"], "not in reference"),
             format_difference(row["difference"])) for row in rows])

    # ------------------------------------------------------------------
    # Brief, alerts, info
    # ------------------------------------------------------------------

    @staticmethod
    def _fill_table(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
        """Replace the table's rows with ``rows`` (texts)."""
        table.setRowCount(len(rows))
        for row_index, cells in enumerate(rows):
            for column, text in enumerate(cells):
                table.setItem(row_index, column, QTableWidgetItem(text))

    def _show_preview(
        self,
        preview: Optional[BriefPreview],
        preview_error: Optional[str],
        chart_output: Optional[Dict[str, Any]],
        reference: Optional[Dict[str, Any]],
    ) -> None:
        """Brief, alert table, urgent message, info rows and caveats."""
        chart_warnings = data_warnings(chart_output, reference) if chart_output is not None else []
        # The charts' own inputs (daily close: open interest from 00:00 UTC), one part per row.
        chart_rows = ([("Charts" if index == 0 else "", part)
                       for index, part in enumerate(info_line(chart_output).split(" | "))]
                      if chart_output is not None else [("Charts", NOT_COMPUTED)])
        if preview is None:
            self.brief_html = None
            self.copy_button.setEnabled(False)
            self.brief_length_label.setText("")
            self.brief_browser.setPlainText(f"Brief {NOT_COMPUTED}: {preview_error}")
            self._fill_table(self.alerts_table, [])
            self.alert_browser.setPlainText(f"Alert check {NOT_COMPUTED}.")
            self._fill_table(self.info_table,
                             [("Brief", f"{NOT_COMPUTED}: {preview_error}")] + chart_rows)
            self._show_caveats(chart_warnings)
            return

        self.brief_html = preview.brief_html
        self.copy_button.setEnabled(True)
        self.brief_length_label.setText(
            f"Data {preview.as_of_utc.strftime('%Y-%m-%d %H:%M UTC')} | "
            f"{preview.brief_length:,} of 4,096 characters")
        self.brief_browser.setHtml(telegram_html_to_rich_text(preview.brief_html))

        self._fill_table(self.alerts_table, [
            (check.rule, "yes" if check.enabled else "no", check.threshold, check.current_value,
             "YES" if check.would_fire else "no") for check in preview.alert_checks])
        for row_index, check in enumerate(preview.alert_checks):
            item = self.alerts_table.item(row_index, 4)
            if item is not None and check.would_fire:
                item.setForeground(QColor(Colors.WARNING))
        if preview.urgent_alert_html is None:
            self.alert_browser.setPlainText("No urgent alert would fire.")
        else:
            self.alert_browser.setHtml(telegram_html_to_rich_text(preview.urgent_alert_html))

        self._fill_table(self.info_table, list(preview.info_rows) + chart_rows)
        self._show_caveats(list(preview.warnings) + chart_warnings)

    def _show_caveats(self, warnings: List[str]) -> None:
        """Bulleted warnings, or 'none'."""
        if not warnings:
            self.caveats_browser.setPlainText("none")
            return
        self.caveats_browser.setPlainText("\n".join(f"- {warning}" for warning in warnings))

    def copy_brief_as_plain_text(self) -> None:
        """Put the brief, as Telegram displays it, on the clipboard."""
        if self.brief_html is None:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(telegram_html_to_plain_text(self.brief_html))
        self.log_viewer.log_info("Brief copied to the clipboard as plain text")
