"""Levels tab (offscreen Qt, fake service): worker result, requests that wait, redraw without reloading, failure."""

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from coding.core.analytics.option_flow.level_overview import LevelMark, LevelOverview
from coding.gui.tabs.level_overview_tab import (
    NEAREST_MONTHLY, LevelOverviewRequest, LevelOverviewResult, LevelOverviewTab, LevelOverviewWorker)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def make_overview(expiry="27NOV26"):
    return LevelOverview("BTC", 68_338.0, expiry, [
        LevelMark("MenthorQ replica (monthly expiries)", "put support", 68_000.0, "support"),
        LevelMark("Dealer GEX 27NOV26, open interest", "put wall", 68_000.0, "support"),
        LevelMark("Option flow 24h (top 2 strikes)", "puts bought 9M USD", 68_000.0, "buy")], [])


class FakeService:
    calls = []

    def overview(self, currency, expiry, hours):
        FakeService.calls.append((currency, expiry, hours))
        return make_overview(expiry or "27NOV26")

    def expirations(self, currency):
        return ["14NOV26", "27NOV26"]


def test_worker_collects_levels_and_expirations(qapp):
    FakeService.calls = []
    worker = LevelOverviewWorker(LevelOverviewRequest("BTC", "14NOV26", 12), FakeService)
    results = []
    worker.succeeded.connect(results.append)
    worker.run()
    assert FakeService.calls == [("BTC", "14NOV26", 12)] and results[0].expirations == ["14NOV26", "27NOV26"]
    assert results[0].overview.expiry == "14NOV26"


def test_worker_reports_a_failure_as_one_line(qapp):
    worker = LevelOverviewWorker(LevelOverviewRequest("BTC", None, 24), MagicMock(side_effect=LookupError("no snapshot")))
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors == ["LookupError: no snapshot"]


def test_defaults_and_request(qapp):
    tab = LevelOverviewTab(service_factory=FakeService)
    assert tab.current_request() == LevelOverviewRequest("BTC", None, 24)
    assert tab.expiry_combo.itemText(0) == NEAREST_MONTHLY
    assert (tab.band_combo.currentData(), tab.minimum_combo.currentData()) == (1.0, 3)
    tab.close()


def test_result_fills_expiries_keeps_the_choice_and_shows_the_summary(qapp):
    tab = LevelOverviewTab(service_factory=FakeService)
    tab.expiry_combo.blockSignals(True)
    tab.expiry_combo.addItem("27NOV26", "27NOV26")
    tab.expiry_combo.setCurrentIndex(1)
    tab.expiry_combo.blockSignals(False)
    tab._on_succeeded(LevelOverviewResult(make_overview(), ["14NOV26", "27NOV26"]))
    assert [tab.expiry_combo.itemText(i) for i in range(tab.expiry_combo.count())] == [NEAREST_MONTHLY, "14NOV26", "27NOV26"]
    assert tab.current_request().expiry == "27NOV26"
    assert "3 levels from 3 sources" in tab.status_label.text() and "27NOV26" in tab.status_label.text()
    tab.close()


def test_band_and_minimum_redraw_from_the_loaded_levels_without_reading_again(qapp):
    FakeService.calls = []
    tab = LevelOverviewTab(service_factory=FakeService)
    tab._on_succeeded(LevelOverviewResult(make_overview(), ["27NOV26"]))
    tab.chart_view.setHtml = MagicMock()
    tab.minimum_combo.setCurrentIndex(0)
    tab.band_combo.setCurrentIndex(0)
    assert tab.chart_view.setHtml.call_count == 2 and FakeService.calls == []
    tab.close()


def test_a_choice_made_while_a_run_is_in_progress_waits_and_outdated_answers_are_ignored(qapp):
    tab = LevelOverviewTab(service_factory=FakeService)
    running = MagicMock()
    running.isRunning.return_value = True
    tab.worker = running
    tab._start(LevelOverviewRequest("ETH", None, 24))
    assert tab._pending == LevelOverviewRequest("ETH", None, 24)
    tab._on_succeeded(LevelOverviewResult(make_overview(), ["27NOV26"]))
    assert tab.overview is None                                         # the old answer was not shown
    tab.close()


def test_failure_shows_an_error_and_keeps_the_last_page(qapp):
    tab = LevelOverviewTab(service_factory=FakeService)
    tab._on_succeeded(LevelOverviewResult(make_overview(), []))
    tab._on_failed("LookupError: x")
    assert tab.status_label.text() == "Failed: LookupError: x" and tab.overview is not None
    tab.close()
