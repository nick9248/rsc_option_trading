"""
Analysis tab (offscreen Qt, fake service, no database, no VPS, no browser): one button, the output log, Show Dashboard,
failures and the stop on exit.
"""

import logging
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QTableWidget

from coding.gui.tabs import analysis_tab as tab_module
from coding.gui.tabs.analysis_tab import AnalysisTab
from coding.service.analysis.analysis_run_service import (
    STATUS_FAILED, STATUS_SUCCEEDED, STEP_ANALYSIS, STEP_DASHBOARD, STEP_REPORT, STEP_UNIFIED, AnalysisRunResult, StepOutcome)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def make_result(tmp_path, currencies, failed_step=None):
    """A run result with files under tmp_path; ``failed_step`` names a step that failed for every currency (the unified dashboard is then skipped)."""
    result = AnalysisRunResult()
    for currency in currencies:
        for name, filename in ((STEP_ANALYSIS, "report_folder"), (STEP_DASHBOARD, "dashboard.html"), (STEP_REPORT, "report.html"),
                               (STEP_UNIFIED, "unified_dashboard.html")):
            if name == STEP_UNIFIED and failed_step is not None:
                result.steps.append(StepOutcome(name, currency, "skipped", "skipped"))
            elif name == failed_step:
                result.steps.append(StepOutcome(name, currency, STATUS_FAILED, "RuntimeError: boom"))
            else:
                result.steps.append(StepOutcome(name, currency, STATUS_SUCCEEDED, "done", tmp_path / f"{currency}_{filename}"))
    return result


class FakeService:
    """run() emits two progress lines, then returns a result (or raises); optionally blocks on a gate."""

    def __init__(self, tmp_path, error=None, failed_step=None, gate=None, calls=None):
        self.tmp_path = tmp_path
        self.error = error
        self.failed_step = failed_step
        self.gate = gate
        self.calls = calls if calls is not None else []

    def run(self, currencies, progress, should_stop=None):
        self.calls.append(list(currencies))
        progress("Step 1/4: database sync ...")
        progress("FAILED  BTC on-chain dashboard: boom" if self.failed_step else "Step 2/4: BTC on-chain analysis ...")
        if self.gate is not None:
            assert self.gate.wait(10), "the test never released the fake service"
        if self.error is not None:
            raise self.error
        return make_result(self.tmp_path, currencies, self.failed_step)


def wait_for_worker(tab, qapp):
    """Wait for the worker thread, then deliver its queued signals."""
    assert tab.worker.wait(5000)
    qapp.processEvents()


def log_text(tab):
    return tab.log_viewer.toPlainText()


def test_tab_has_the_requested_controls_and_no_results_widgets(qapp):
    tab = AnalysisTab()
    assert tab.start_button.text() == "Start Analysis"
    assert tab.dashboard_button.text() == "Show Dashboard"
    assert not hasattr(tab, "report_button")                          # one button shows the unified dashboard
    assert [button.text() for button in tab.findChildren(QPushButton)] == ["Start Analysis", "Show Dashboard", "Clear"]
    assert [tab.currency_combo.itemText(index) for index in range(tab.currency_combo.count())] == ["BTC", "ETH", "BOTH"]
    assert not tab.dashboard_button.isEnabled()
    assert tab.findChildren(QTableWidget) == []                      # no Levels / metrics table
    assert len(tab.findChildren(QComboBox)) == 1                     # only the currency choice: no expiry selector
    assert not any("convention" in button.text().lower() for button in tab.findChildren(QPushButton))
    tab.close()


@pytest.mark.parametrize("choice, expected", [("BTC", ["BTC"]), ("ETH", ["ETH"]), ("BOTH", ["BTC", "ETH"])])
def test_the_currency_choice_decides_the_currencies_of_the_one_run(qapp, tmp_path, choice, expected):
    calls = []
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path, calls=calls))
    tab.currency_combo.setCurrentText(choice)
    tab.start_button.click()
    wait_for_worker(tab, qapp)
    assert calls == [expected]                                       # one run, one service call
    tab.close()


def test_progress_lines_go_to_the_log_and_the_buttons_are_enabled_after_a_good_run(qapp, tmp_path):
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path))
    tab.start_button.click()
    assert not tab.start_button.isEnabled()
    wait_for_worker(tab, qapp)
    assert "Step 1/4: database sync ..." in log_text(tab) and "Step 2/4: BTC on-chain analysis ..." in log_text(tab)
    assert tab.start_button.isEnabled()
    assert tab.dashboard_button.isEnabled()
    assert tab.dashboard_paths == [tmp_path / "BTC_unified_dashboard.html"]
    assert tab.status_label.text() == "All steps succeeded"
    tab.close()


def test_a_failed_step_is_shown_and_there_is_no_dashboard_to_open(qapp, tmp_path):
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path, failed_step=STEP_DASHBOARD))
    tab.start_button.click()
    wait_for_worker(tab, qapp)
    assert "FAILED  BTC on-chain dashboard: boom" in log_text(tab)
    assert not tab.dashboard_button.isEnabled()                        # the unified dashboard needs the dashboard and the report
    assert tab.status_label.text() == "1 step(s) failed; see the log"
    tab.close()


def test_a_crashing_run_is_logged_and_the_button_stays_usable(qapp, tmp_path):
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path, error=RuntimeError("database down")))
    tab.start_button.click()
    wait_for_worker(tab, qapp)
    assert "Analysis failed: RuntimeError: database down" in log_text(tab)
    assert tab.status_label.text() == "Failed"
    assert tab.start_button.isEnabled() and not tab.dashboard_button.isEnabled()
    tab.close()


def test_start_while_running_is_refused_with_a_warning(qapp, tmp_path):
    gate = threading.Event()
    calls = []
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path, gate=gate, calls=calls))
    try:
        tab.start_button.click()
        first_worker = tab.worker
        tab.start_analysis()                                         # the button is disabled; the method guards too
        assert tab.worker is first_worker
        assert "already running" in log_text(tab)
    finally:
        gate.set()
        wait_for_worker(tab, qapp)
    assert len(calls) == 1
    tab.close()


def test_show_dashboard_opens_the_unified_dashboard_of_every_currency_of_the_last_run(qapp, tmp_path):
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path))
    tab.currency_combo.setCurrentText("BOTH")
    tab.start_button.click()
    wait_for_worker(tab, qapp)
    with patch.object(tab_module.QDesktopServices, "openUrl") as open_url:
        tab.dashboard_button.click()
    opened = [Path(call.args[0].toLocalFile()).name for call in open_url.call_args_list]
    assert opened == ["BTC_unified_dashboard.html", "ETH_unified_dashboard.html"]
    tab.close()


def test_wait_for_worker_requests_a_stop_and_returns_when_the_worker_has_ended(qapp):
    stop_seen = threading.Event()

    class StoppableService:
        def run(self, currencies, progress, should_stop=None):
            progress("running")
            for _ in range(500):
                if should_stop():
                    stop_seen.set()
                    break
                threading.Event().wait(0.01)
            return AnalysisRunResult()

    tab = AnalysisTab(service_factory=StoppableService)
    tab.start_button.click()
    tab._wait_for_worker()                                           # what the application exit does
    assert stop_seen.is_set() and not tab.worker.isRunning()
    tab.close()


def test_log_handler_is_attached_per_tab_and_removed_on_close(qapp):
    target = logging.getLogger(tab_module.GUI_LOG_LOGGER_NAME)
    tab_one, tab_two = AnalysisTab(), AnalysisTab()
    assert tab_one.gui_handler in target.handlers and tab_two.gui_handler in target.handlers
    assert tab_one.gui_handler not in logging.getLogger().handlers          # never on the root logger
    tab_one.close()
    tab_two.close()
    assert tab_one.gui_handler not in target.handlers and tab_two.gui_handler not in target.handlers


def test_clear_empties_the_log(qapp, tmp_path):
    tab = AnalysisTab(service_factory=lambda: FakeService(tmp_path))
    tab.start_button.click()
    wait_for_worker(tab, qapp)
    assert log_text(tab)
    tab.clear_button.click()
    assert log_text(tab) == ""
    tab.close()
