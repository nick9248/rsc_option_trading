"""
Unit tests for the minimal MenthorQ Charts tab (offscreen Qt, no database).

- The tab has one Generate button, a status line, warnings and a folder
  link: no selectors, no chart viewer.
- The worker makes exactly one service call with the fixed settings (BTC,
  daily close, monthly, gamma at index, nested layout) and reports errors
  as one "Class: message" line instead of raising.
- Building the tab and showing a result never touches the database: the
  module imports no repository/psycopg2, and DatabaseRepository is made to
  fail if anything constructs it.
- A real service result (fake in-memory repository, files in tmp_path) is
  shown: chart count, the expected "listed after the open-interest snapshot"
  note (neutral) or a partial open-interest warning (orange), folder link and
  Open folder.
"""

import ast
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QComboBox, QDateEdit, QLineEdit, QTableWidget, QTabWidget

from coding.gui.tabs import menthorq_charts_tab as tab_module
from coding.gui.tabs.menthorq_charts_tab import MenthorqChartsTab, MenthorqChartsWorker
from coding.service.on_chain.menthorq_replication_service import (
    MenthorqReplicationService,
    last_complete_utc_day,
)

CAPTURED_AT = datetime(2026, 11, 6, 23, 50, tzinfo=timezone.utc)
MENTHORQ_DATE = date(2026, 11, 6)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeRepository:
    """
    Two expiries at index 100: 27NOV26 with a call (K=100) and a put (K=90),
    and 4DEC26 whose only row has no open interest (GEX not computed: listed
    after the open-interest snapshot, an expected note). With
    ``partial_open_interest`` 4DEC26 also has a put with open interest, so
    only SOME of its rows lack it (a real warning).
    """

    def __init__(self, partial_open_interest: bool = False):
        self.partial_open_interest = partial_open_interest

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        extra_rows = ([{"instrument_name": "BTC-4DEC26-90-P", "expiration": "4DEC26", "strike": 90.0,
                        "option_type": "P", "open_interest": 3.0, "mark_iv": 50.0,
                        "underlying_price": 100.0, "mark_price": None}]
                      if self.partial_open_interest else [])
        return min(as_of, CAPTURED_AT), extra_rows + [
            {"instrument_name": "BTC-27NOV26-100-C", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "C", "open_interest": 10.0, "mark_iv": 50.0,
             "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-27NOV26-90-P", "expiration": "27NOV26", "strike": 90.0,
             "option_type": "P", "open_interest": 30.0, "mark_iv": 50.0,
             "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-4DEC26-100-C", "expiration": "4DEC26", "strike": 100.0,
             "option_type": "C", "open_interest": None, "mark_iv": 50.0,
             "underlying_price": 100.0, "mark_price": None},
        ]

    def get_index_price_at_or_before(self, currency, as_of):
        return 100.0, as_of - timedelta(seconds=30)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """A real nested service result with its files, generated without a database."""
    directory = tmp_path_factory.mktemp("menthorq_gui")
    output = MenthorqReplicationService(cast(Any, FakeRepository())).run_menthorq_daily_close(
        "BTC", MENTHORQ_DATE, directory, chart_layout="nested",
        now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))
    return directory / "2026-11-06", output


def _success(folder, output):
    return {"output": output, "folder": folder, "date": MENTHORQ_DATE}


class TestWorker:
    def test_generate_makes_one_service_call_with_the_fixed_settings(self, qapp, tmp_path):
        with patch.object(tab_module, "MenthorqReplicationService") as service_class:
            service_class.return_value.run_menthorq_daily_close.return_value = {"fake": 1}
            worker = MenthorqChartsWorker(MENTHORQ_DATE, tmp_path)
            results = []
            worker.succeeded.connect(results.append)
            worker.run()
        service_class.return_value.run_menthorq_daily_close.assert_called_once_with(
            "BTC", MENTHORQ_DATE, tmp_path, expiry_universe="monthly",
            gamma_underlying="index", chart_layout="nested")
        assert results == [{"output": {"fake": 1}, "folder": tmp_path / "2026-11-06",
                            "date": MENTHORQ_DATE}]

    def test_errors_are_emitted_not_raised(self, qapp, tmp_path):
        with patch.object(tab_module, "MenthorqReplicationService") as service_class:
            service_class.return_value.run_menthorq_daily_close.side_effect = LookupError(
                "No BTC snapshot")
            worker = MenthorqChartsWorker(MENTHORQ_DATE, tmp_path)
            errors = []
            worker.failed.connect(errors.append)
            worker.run()
        assert errors == ["LookupError: No BTC snapshot"]

    def test_generate_button_starts_a_worker_for_the_last_complete_utc_day(self, qapp, tmp_path):
        tab = MenthorqChartsTab()
        tab.output_directory = tmp_path
        with patch.object(tab_module, "MenthorqChartsWorker") as worker_class:
            tab.generate_button.click()
        expected_date = last_complete_utc_day(datetime.now(timezone.utc))
        worker_class.assert_called_once_with(expected_date, tmp_path)
        worker_class.return_value.start.assert_called_once_with()
        assert not tab.generate_button.isEnabled()
        assert tab.status_label.text().startswith(f"Generating the charts for {expected_date}")
        tab.close()


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

    def test_building_the_tab_and_showing_a_result_never_constructs_a_repository(
            self, qapp, generated):
        folder, output = generated
        with patch("coding.core.database.repository.DatabaseRepository.__init__",
                   side_effect=AssertionError("database accessed from the GUI")):
            tab = MenthorqChartsTab()
            tab._on_generation_succeeded(_success(folder, output))
            tab.close()


class TestTabDisplay:
    def test_only_one_button_and_no_selectors_or_viewers(self, qapp):
        tab = MenthorqChartsTab()
        for widget_class in (QComboBox, QDateEdit, QLineEdit, QTableWidget, QTabWidget):
            assert tab.findChildren(widget_class) == [], widget_class.__name__
        assert tab.generate_button.text() == "Generate"
        assert not tab.open_folder_button.isEnabled()
        assert tab.status_label.text() == "No charts generated yet."
        tab.close()

    def test_result_shows_count_the_expected_gap_as_a_neutral_note_and_folder(self, qapp, generated):
        folder, output = generated
        tab = MenthorqChartsTab()
        tab._on_generation_succeeded(_success(folder, output))

        # 3 chart kinds x (monthly, all listed) + one per listed expiry (27NOV26, 4DEC26).
        assert tab.status_label.text() == (
            "Generated 8 charts for 2026-11-06 (BTC, MenthorQ daily close).")
        # 4DEC26 has no open interest at all (listed after the OI snapshot): a note, no orange warning.
        assert tab.warning_label.isHidden()
        assert not tab.note_label.isHidden()
        assert tab.note_label.text() == (
            "Note: Expiry 4DEC26 is not in the open-interest snapshot (listed after it): no open "
            "interest, left out of IV*OI and GEX; its levels show as not computed")
        assert tab.note_label.styleSheet() == f"color: {tab_module.Colors.TEXT_SECONDARY};"
        assert tab.warning_label.styleSheet() == f"color: {tab_module.Colors.WARNING};"

        assert QUrl.fromLocalFile(str(folder)).toString() in tab.folder_link_label.text()
        assert tab.open_folder_button.isEnabled()
        with patch.object(tab_module, "QDesktopServices") as desktop:
            tab.open_folder_button.click()
            tab.folder_link_label.linkActivated.emit("ignored")
        assert desktop.openUrl.call_count == 2
        desktop.openUrl.assert_called_with(QUrl.fromLocalFile(str(folder)))
        tab.close()

    def test_rows_partly_without_open_interest_stay_an_orange_warning(self, qapp, tmp_path):
        output = MenthorqReplicationService(cast(Any, FakeRepository(partial_open_interest=True))
                                            ).run_menthorq_daily_close(
            "BTC", MENTHORQ_DATE, tmp_path, chart_layout="nested",
            now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))
        tab = MenthorqChartsTab()
        tab._on_generation_succeeded(_success(tmp_path / "2026-11-06", output))
        assert not tab.warning_label.isHidden()
        assert tab.warning_label.text() == ("Warning: 1 rows have no open interest in the open-interest "
                                            "snapshot and are left out (4DEC26 1)")
        assert "not validated" not in tab.warning_label.text()   # BTC only
        assert tab.note_label.isHidden()
        tab.close()

    def test_failure_shows_one_error_line_and_re_enables_generate(self, qapp):
        tab = MenthorqChartsTab()
        tab.generate_button.setEnabled(False)
        tab._on_generation_failed("LookupError: No BTC snapshot at or before ...")
        assert tab.generate_button.isEnabled()
        assert tab.status_label.text() == (
            "Generation failed: LookupError: No BTC snapshot at or before ...")
        tab.close()
