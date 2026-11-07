"""
Unit tests for the MenthorQ Charts tab (offscreen Qt, no database).

- The worker makes exactly one service call (daily close or live) and
  reports errors instead of raising.
- Building the tab and showing a result never touches the database: the
  module imports no repository/psycopg2, and DatabaseRepository is made to
  fail if anything constructs it.
- A real service result (fake in-memory repository, PNGs in tmp_path) is
  shown: expiry selector, levels table with "not computed", warnings,
  comparison table and the chart image.
"""

import ast
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidget

from coding.gui.tabs import menthorq_charts_tab as tab_module
from coding.gui.tabs.menthorq_charts_tab import ChartRequest, MenthorqChartsTab, MenthorqChartsWorker
from coding.service.on_chain.menthorq_replication_service import (
    MenthorqReplicationService,
    last_complete_utc_day,
)

CAPTURED_AT = datetime(2026, 11, 6, 23, 50, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeRepository:
    """
    Two expiries at index 100: 27NOV26 with a call (K=100) and a put (K=90),
    and 4DEC26 whose only row has no open interest (GEX not computed).
    """

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        return min(as_of, CAPTURED_AT), [
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
    """A real service result with its PNGs, generated without a database."""
    directory = tmp_path_factory.mktemp("menthorq_gui")
    reference = {"timestamp_utc": "2026-11-06T23:59:00Z", "spot_price": 101.0,
                 "iv_times_open_interest": {"levels": {"call_resistance": 100, "put_support": 95,
                                                       "hvl": 90}},
                 "gex_panels": {}}
    output = MenthorqReplicationService(cast(Any, FakeRepository())).run_menthorq_daily_close(
        "BTC", date(2026, 11, 6), directory, expiry_universe="all", reference=reference,
        now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))
    return directory, output, reference


def _cell(table: QTableWidget, row: int, column: int) -> str:
    """Text of a table cell (fails clearly when the cell was never set)."""
    item = table.item(row, column)
    assert item is not None, f"cell ({row}, {column}) is empty"
    return item.text()


def _request(mode, reference_path=None, output_directory=Path("unused")):
    return ChartRequest(currency="BTC", mode=mode, menthorq_date=date(2026, 11, 6),
                        expiry_universe="monthly", gamma_underlying="index",
                        reference_path=reference_path, output_directory=output_directory)


class TestWorker:
    def test_daily_close_makes_one_service_call_with_the_reference(self, qapp, tmp_path):
        reference_path = tmp_path / "reference.json"
        reference_path.write_text(json.dumps({"spot_price": 75752}), encoding="utf-8")
        with patch.object(tab_module, "MenthorqReplicationService") as service_class:
            service_class.return_value.run_menthorq_daily_close.return_value = {"fake": 1}
            worker = MenthorqChartsWorker(_request("menthorq_daily_close", reference_path, tmp_path))
            results = []
            worker.succeeded.connect(results.append)
            worker.run()
        service_class.return_value.run_menthorq_daily_close.assert_called_once_with(
            "BTC", date(2026, 11, 6), tmp_path, expiry_universe="monthly",
            gamma_underlying="index", reference={"spot_price": 75752})
        service_class.return_value.run_live.assert_not_called()
        assert results == [{"output": {"fake": 1}, "reference": {"spot_price": 75752}}]

    def test_live_calls_run_live(self, qapp, tmp_path):
        with patch.object(tab_module, "MenthorqReplicationService") as service_class:
            worker = MenthorqChartsWorker(_request("live", None, tmp_path))
            worker.run()
        service_class.return_value.run_live.assert_called_once_with(
            "BTC", tmp_path, expiry_universe="monthly", gamma_underlying="index", reference=None)
        service_class.return_value.run_menthorq_daily_close.assert_not_called()

    def test_errors_are_emitted_not_raised(self, qapp):
        with patch.object(tab_module, "MenthorqReplicationService") as service_class:
            service_class.return_value.run_live.side_effect = LookupError("No BTC snapshot")
            worker = MenthorqChartsWorker(_request("live"))
            errors = []
            worker.failed.connect(errors.append)
            worker.run()
        assert errors == ["LookupError: No BTC snapshot"]


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
        directory, output, reference = generated
        with patch("coding.core.database.repository.DatabaseRepository.__init__",
                   side_effect=AssertionError("database accessed from the GUI")):
            tab = MenthorqChartsTab()
            tab.output_directory = directory
            tab._on_generation_succeeded({"output": output, "reference": reference})
            for index in range(tab.chart_combo.count()):
                tab.chart_combo.setCurrentIndex(index)
            tab.close()


class TestTabDisplay:
    def test_defaults(self, qapp):
        tab = MenthorqChartsTab()
        newest = last_complete_utc_day(datetime.now(timezone.utc))
        selected = tab.date_edit.date()
        assert (selected.year(), selected.month(), selected.day()) == (
            newest.year, newest.month, newest.day)
        assert tab.date_edit.maximumDate() == tab.date_edit.date()
        assert tab.date_edit.isEnabled()
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("live"))
        assert not tab.date_edit.isEnabled()
        assert [tab.currency_combo.itemData(i) for i in range(tab.currency_combo.count())] == [
            "BTC", "ETH"]
        assert tab.build_request().reference_path is None
        tab.close()

    def test_result_fills_selector_tables_warnings_and_image(self, qapp, generated):
        directory, output, reference = generated
        tab = MenthorqChartsTab()
        tab.output_directory = directory
        tab._on_generation_succeeded({"output": output, "reference": reference})

        assert [tab.expiry_combo.itemText(i) for i in range(tab.expiry_combo.count())] == [
            "27NOV26", "4DEC26"]
        assert "snapshot captured 2026-11-06 23:50:00 UTC" in tab.info_label.text()
        assert "open interest from snapshot 2026-11-06 00:00:00 UTC" in tab.info_label.text()
        assert not tab.warning_label.isHidden()
        assert "GEX not computed for 1 expiry (4DEC26)" in tab.warning_label.text()

        # IV*OI all expiries: one row, hand-calculated levels (see the service test).
        assert tab.levels_table.rowCount() == 1
        assert [_cell(tab.levels_table, 0, column) for column in range(2, 6)] == [
            "100", "90", "90", "100.00"]
        assert len(tab.displayed_pixmaps) == 1
        assert tab.save_button.isEnabled()

        # One expiry whose GEX was not computed: shown as such, never 0.
        tab.chart_combo.setCurrentIndex(tab.chart_combo.findData("iv_times_open_interest_expiry"))
        tab.expiry_combo.setCurrentText("4DEC26")
        assert [_cell(tab.levels_table, 0, column) for column in range(2, 5)] == [
            "not computed"] * 3
        assert _cell(tab.levels_table, 0, 1) == "2026-12-04"

        # Net GEX panels: the single computed expiry carries -100% of the GEX.
        tab.chart_combo.setCurrentIndex(tab.chart_combo.findData("net_gex_panels"))
        assert _cell(tab.levels_table, 0, 6) == "-100.00%"
        assert _cell(tab.levels_table, 1, 1) == ""          # no second expiry
        assert _cell(tab.levels_table, 1, 2) == "not computed"

        # All charts: several images, save-as disabled.
        tab.chart_combo.setCurrentIndex(tab.chart_combo.findData("all"))
        assert len(tab.displayed_pixmaps) == 5
        assert not tab.save_button.isEnabled()

        comparison = {_cell(tab.comparison_table, row, 0):
                      [_cell(tab.comparison_table, row, column) for column in range(1, 4)]
                      for row in range(tab.comparison_table.rowCount())}
        assert comparison["Spot"] == ["100.00", "101.00", "-1.00"]
        assert comparison["All expiries: Put Support"] == ["90.00", "95.00", "-5.00"]
        tab.close()

    def test_failure_shows_message_and_re_enables_generate(self, qapp):
        tab = MenthorqChartsTab()
        tab.generate_button.setEnabled(False)
        tab._on_generation_failed("LookupError: No BTC snapshot at or before ...")
        assert tab.generate_button.isEnabled()
        assert "Generation failed: LookupError" in tab.warning_label.text()
        assert tab.status_label.text() == "Error"
        tab.close()

    def test_log_handler_removed_on_close(self, qapp):
        import logging
        tab = MenthorqChartsTab()
        target = logging.getLogger(tab_module.GUI_LOG_LOGGER_NAME)
        assert tab.gui_handler in target.handlers
        tab.close()
        assert tab.gui_handler not in target.handlers
