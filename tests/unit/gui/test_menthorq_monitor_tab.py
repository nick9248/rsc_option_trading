"""
Unit tests for the MenthorQ Monitor tab (offscreen Qt, no database).

- The worker makes one chart call and one preview call with the right
  instants; a chart failure does not stop the brief.
- The GUI module imports no repository or database driver, and building the
  tab and showing a Run never constructs a DatabaseRepository.
- A Run with real services on fakes (tiny chain repository, fixed monitor
  records) fills the brief, alert table, info table, chart list and viewer;
  missing data shows as "not computed".
"""

import ast
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, cast
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QUrl
from PySide6.QtWidgets import QApplication, QTableWidget

from coding.gui import main_window
from coding.gui.tabs import menthorq_monitor_tab as tab_module
from coding.gui.tabs.menthorq_monitor_tab import (
    MenthorqMonitorTab,
    MenthorqMonitorWorker,
    MonitorRequest,
    telegram_html_to_rich_text,
)
from coding.service.on_chain.menthorq_monitor.brief_preview_service import (
    MenthorqBriefPreviewService,
)
from coding.service.on_chain.menthorq_replication_service import MenthorqReplicationService
from tests.fakes.fake_menthorq_brief_preview import (
    DAILY_CLOSE_DATE,
    FixedRecordMonitorService,
    TinyChainRepository,
    moving_records,
)

RUN_CLOCK = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
DAILY_CLOSE_INSTANT = datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _cell(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None, f"cell ({row}, {column}) is empty"
    return item.text()


def _request(mode: str, output_directory: Path, reference_path=None) -> MonitorRequest:
    return MonitorRequest(currency="BTC", mode=mode, menthorq_date=DAILY_CLOSE_DATE,
                          expiry_universe="monthly", gamma_underlying="index",
                          reference_path=reference_path, output_directory=output_directory)


def _replication_factory():
    """Real chart service on the tiny in-memory chain; ``now`` fixed after the date."""
    service = MenthorqReplicationService(TinyChainRepository())
    original = service.run_menthorq_daily_close

    def run_daily_close(*arguments, **keywords):
        return original(*arguments, now=RUN_CLOCK, **keywords)

    service.run_menthorq_daily_close = run_daily_close   # type: ignore[method-assign]
    return service


def _preview_factory(records):
    def build(currency, expiry_universe, gamma_underlying):
        return MenthorqBriefPreviewService(FixedRecordMonitorService(records),
                                           clock=lambda: RUN_CLOCK)
    return build


def _run_synchronously(tab: MenthorqMonitorTab) -> Dict[str, Any]:
    """Run the tab's worker in this thread and show its result."""
    request = tab.build_request()
    worker = MenthorqMonitorWorker(request, tab.replication_service_factory,
                                   tab.preview_service_factory, tab.clock)
    results: List[Dict[str, Any]] = []
    worker.succeeded.connect(results.append)
    worker.run()
    assert len(results) == 1
    tab.show_result(request, results[0])
    return results[0]


def _tab(tmp_path: Path, records) -> MenthorqMonitorTab:
    tab = MenthorqMonitorTab()
    tab.output_base_directory = tmp_path
    tab.clock = lambda: RUN_CLOCK
    tab.replication_service_factory = _replication_factory
    tab.preview_service_factory = _preview_factory(records)
    tab.date_edit.setDate(QDate(2026, 11, 6))
    return tab


class TestWorker:
    def test_daily_close_charts_and_brief_at_23_59(self, qapp, tmp_path):
        replication = MagicMock()
        replication.run_menthorq_daily_close.return_value = {"fake": 1}
        preview_service = MagicMock()
        preview_service.build_preview.return_value = "preview"
        factory = MagicMock(return_value=preview_service)
        worker = MenthorqMonitorWorker(_request("menthorq_daily_close", tmp_path),
                                       lambda: replication, factory, lambda: RUN_CLOCK)
        results = []
        worker.succeeded.connect(results.append)
        worker.run()
        replication.run_menthorq_daily_close.assert_called_once_with(
            "BTC", DAILY_CLOSE_DATE, tmp_path, expiry_universe="monthly",
            gamma_underlying="index", reference=None)
        factory.assert_called_once_with("BTC", "monthly", "index")
        preview_service.build_preview.assert_called_once_with(DAILY_CLOSE_INSTANT)
        assert results == [{"reference": None, "chart_output": {"fake": 1}, "chart_error": None,
                            "preview": "preview", "preview_error": None}]

    def test_live_brief_is_as_of_now_and_chart_failure_does_not_stop_it(self, qapp, tmp_path):
        replication = MagicMock()
        replication.run_live.side_effect = LookupError("No BTC snapshot")
        preview_service = MagicMock()
        worker = MenthorqMonitorWorker(_request("live", tmp_path), lambda: replication,
                                       MagicMock(return_value=preview_service), lambda: RUN_CLOCK)
        results = []
        worker.succeeded.connect(results.append)
        worker.run()
        replication.run_menthorq_daily_close.assert_not_called()
        preview_service.build_preview.assert_called_once_with(RUN_CLOCK)
        assert results[0]["chart_error"] == "LookupError: No BTC snapshot"
        assert results[0]["preview"] is preview_service.build_preview.return_value

    def test_unreadable_reference_fails(self, qapp, tmp_path):
        worker = MenthorqMonitorWorker(_request("live", tmp_path, tmp_path / "missing.json"),
                                       MagicMock(), MagicMock(), lambda: RUN_CLOCK)
        errors = []
        worker.failed.connect(errors.append)
        worker.run()
        assert errors and errors[0].startswith("Reference JSON: FileNotFoundError")


class TestNoDatabaseAccessInGui:
    def test_module_imports_no_repository_or_database_driver(self):
        tree = ast.parse(Path(tab_module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith(("psycopg2", "coding.core.database")) for name in imported)
        assert not hasattr(tab_module, "DatabaseRepository")

    def test_building_the_tab_and_showing_a_run_never_constructs_a_repository(
            self, qapp, tmp_path):
        with patch("coding.core.database.repository.DatabaseRepository.__init__",
                   side_effect=AssertionError("database accessed from the GUI")):
            tab = _tab(tmp_path, moving_records())
            result = _run_synchronously(tab)
            assert result["chart_error"] is None and result["preview_error"] is None
            tab.close()


class TestTabDisplay:
    def test_run_fills_brief_alerts_info_charts_and_folder(self, qapp, tmp_path):
        tab = _tab(tmp_path, moving_records(DAILY_CLOSE_INSTANT))
        _run_synchronously(tab)
        folder = tmp_path / "2026-11-06"

        assert "BTC MenthorQ-style levels | Preview" in tab.brief_browser.toPlainText()
        assert "Key levels" in tab.brief_browser.toPlainText()
        assert "of 4,096 characters" in tab.brief_length_label.text()
        assert tab.brief_length_label.text().startswith("Data 2026-11-06 23:59 UTC")

        assert tab.alerts_table.rowCount() == 6
        assert [_cell(tab.alerts_table, row, 4) for row in range(6)] == [
            "YES", "no", "YES", "YES", "YES", "no"]
        assert _cell(tab.alerts_table, 0, 3) == "+4.00% of spot (+4,000 USD)"
        assert "URGENT | BTC MenthorQ-style levels" in tab.alert_browser.toPlainText()

        info = {_cell(tab.info_table, row, 0): _cell(tab.info_table, row, 1)
                for row in range(tab.info_table.rowCount())}
        assert info["Brief data time"] == "2026-11-06 23:59 UTC"
        assert info["Data age at Run (now - snapshot)"] == "9 h 21 min"   # 09:00 - 23:39

        names = [tab.chart_list.item(row).text() for row in range(tab.chart_list.count())]
        assert names[0] == "btc_20261106T2359Z_open_interest_20261106T0000Z_iv_times_open_interest.png"
        assert all((folder / name).is_file() for name in names)
        assert tab.displayed_chart is not None and tab.displayed_chart[0] == folder / names[0]
        tab.chart_list.setCurrentRow(2)
        assert tab.displayed_chart is not None and tab.displayed_chart[0] == folder / names[2]
        assert tab.save_button.isEnabled()

        assert QUrl.fromLocalFile(str(folder)).toString() in tab.folder_link_label.text()
        with patch.object(tab_module, "QDesktopServices") as desktop:
            tab.open_output_folder()
        desktop.openUrl.assert_called_once_with(QUrl.fromLocalFile(str(folder)))

        tab.copy_brief_as_plain_text()
        copied = QApplication.clipboard().text()
        assert copied.startswith("BTC MenthorQ-style levels | Preview")
        assert "<b>" not in copied and "<pre>" not in copied and "<code" not in copied
        assert "No reference JSON given" in _cell(tab.comparison_table, 0, 0)
        tab.close()

    def test_missing_data_is_shown_as_not_computed(self, qapp, tmp_path):
        tab = _tab(tmp_path, {})
        _run_synchronously(tab)
        info = {_cell(tab.info_table, row, 0): _cell(tab.info_table, row, 1)
                for row in range(tab.info_table.rowCount())}
        assert info["Snapshot captured"] == "not computed"
        assert info["Index price"] == "not computed"
        assert info["IV source"] == "not computed"
        assert all(_cell(tab.alerts_table, row, 3).startswith("not computed")
                   for row in range(tab.alerts_table.rowCount()))
        assert [_cell(tab.alerts_table, row, 4) for row in range(6)] == ["no"] * 6
        assert "Monitor has no usable data: inputs not available" in tab.alert_browser.toPlainText()
        assert "0.00%" not in tab.brief_browser.toPlainText()
        tab.close()

    def test_an_expiry_listed_after_the_open_interest_snapshot_is_a_note_in_the_caveats(
            self, qapp, tmp_path, monkeypatch):
        tab = _tab(tmp_path, {})
        monkeypatch.setattr(tab_module, "data_warnings", lambda output, reference: ["real problem"])
        monkeypatch.setattr(tab_module, "data_notes", lambda output: ["Expiry 15NOV26 is not in the "
                                                                      "open-interest snapshot"])
        monkeypatch.setattr(tab_module, "info_line", lambda output: "charts info")
        tab._show_preview(None, "not run", {"fake": "output"}, None)
        assert tab.caveats_browser.toPlainText() == (
            "- real problem\n- Note: Expiry 15NOV26 is not in the open-interest snapshot")
        assert "15NOV26" not in tab.warning_label.text()      # never in the orange line
        tab.close()

    def test_failed_brief_is_reported_and_charts_still_shown(self, qapp, tmp_path):
        tab = _tab(tmp_path, {})

        def failing_factory(currency, expiry_universe, gamma_underlying):
            raise ValueError("MENTHORQ_EXPIRY_UNIVERSE='x' is not one of ...")

        tab.preview_service_factory = failing_factory
        _run_synchronously(tab)
        assert "Brief failed: ValueError" in tab.warning_label.text()
        assert tab.brief_browser.toPlainText().startswith("Brief not computed: ValueError")
        assert tab.chart_list.count() > 0
        assert tab.status_label.text() == "Done with errors"
        tab.close()

    def test_defaults_and_safety_note(self, qapp):
        tab = MenthorqMonitorTab()
        assert "never sends Telegram messages" in tab.safety_label.text()
        assert "never writes to the database" in tab.safety_label.text()
        assert tab.date_edit.isEnabled() and tab.date_edit.maximumDate() == tab.date_edit.date()
        assert not tab.open_folder_button.isEnabled() and not tab.copy_button.isEnabled()
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("live"))
        assert not tab.date_edit.isEnabled()
        tab.close()

    def test_date_maximum_follows_the_utc_midnight_while_the_tab_stays_open(self, qapp, monkeypatch):
        clock = {"now": datetime(2026, 11, 7, 23, 59, 30, tzinfo=timezone.utc)}
        tab = MenthorqMonitorTab()
        tab.clock = lambda: clock["now"]
        tab.refresh_newest_date()
        # 23:59:30 UTC on 7 Nov: 7 Nov has not ended, the newest complete day is 6 Nov.
        assert tab.date_edit.maximumDate() == QDate(2026, 11, 6)
        tab.date_edit.setDate(QDate(2026, 11, 6))
        # 00:00 UTC on 8 Nov: 7 Nov has ended; showing the tab moves the maximum on.
        clock["now"] = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
        tab.show()
        assert tab.date_edit.maximumDate() == QDate(2026, 11, 7)
        assert tab.date_edit.date() == QDate(2026, 11, 6)      # the selected date is kept
        tab.hide()
        # One day later, a Run refreshes the maximum first (the worker is replaced by a stub).
        clock["now"] = datetime(2026, 11, 9, 0, 0, 1, tzinfo=timezone.utc)
        worker_class = MagicMock()
        monkeypatch.setattr(tab_module, "MenthorqMonitorWorker", worker_class)
        tab.start_run()
        assert tab.date_edit.maximumDate() == QDate(2026, 11, 8)
        tab.date_edit.setDate(QDate(2026, 11, 8))               # now selectable
        assert tab.date_edit.date() == QDate(2026, 11, 8)
        worker_class.return_value.start.assert_called_once()
        tab.worker = None
        tab.close()

    def test_log_handler_removed_on_close(self, qapp):
        import logging
        tab = MenthorqMonitorTab()
        targets = [logging.getLogger(name) for name in tab_module.GUI_LOG_LOGGER_NAMES]
        assert all(tab.gui_handler in target.handlers for target in targets)
        tab.close()
        assert not any(tab.gui_handler in target.handlers for target in targets)


def test_rich_text_keeps_pre_blocks_and_breaks_other_lines():
    text = "<b>Title</b>\nline\n<pre>a\nb</pre>\nend\n\n<b>Next</b>\n<pre>c</pre>"
    assert telegram_html_to_rich_text(text) == (
        "<b>Title</b><br>line<pre>a\nb</pre>end<br><br><b>Next</b><pre>c</pre>")
    # The brief's code block (<pre><code class=...>) is kept whole, newlines included.
    coded = '<b>T</b>\n<pre><code class="language-text">a\nb</code></pre>\nend'
    assert telegram_html_to_rich_text(coded) == (
        '<b>T</b><pre><code class="language-text">a\nb</code></pre>end')


def test_tab_is_registered_after_the_charts_tab():
    definitions = {entry["index"]: entry["name"] for entry in main_window.MODULE_DEFS}
    assert definitions[6] == "MenthorQ Charts" and definitions[7] == "MenthorQ Monitor"
    assert main_window._LAST_ACTIVE == 7
    assert main_window._PLACEHOLDER_INDICES == {8, 9, 10}
    assert cast(Any, main_window).MenthorqMonitorTab is MenthorqMonitorTab
