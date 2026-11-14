"""
Report tab (offscreen Qt, fake service, no database, no VPS): progress, result display, failures and the queued request.
"""

import os
import threading
from datetime import datetime, timezone

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from coding.gui.tabs.report_tab import ReportTab, result_summary
from coding.service.report.report_service import ReportResult, SectionOutcome, SyncStep

NOW = datetime(2026, 11, 14, 10, 29, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def make_result(path, currency="BTC", failed=False, sync_failed=False):
    sections = [SectionOutcome("menthorq", "MenthorQ", True, "built"),
                SectionOutcome("levels", "Levels", not failed, "built" if not failed else "Levels: no snapshot.")]
    return ReportResult(path, currency, NOW, SyncStep("Checked 4 tables against the VPS: all up to date, no sync needed", False,
                                                     failed=sync_failed), sections, size_bytes=2_500_000)


class FakeService:
    """generate() emits two progress lines and returns a result for a small html file; optionally blocks or raises."""

    def __init__(self, folder, failed_section=False, error=None, gate=None, log=None):
        self.folder = folder
        self.failed_section = failed_section
        self.error = error
        self.gate = gate
        self.log = log if log is not None else []

    def generate(self, currency, progress=None, should_stop=None):
        self.log.append(currency)
        progress("Checking whether the local database is behind the VPS...")
        progress("Building MenthorQ...")
        if self.gate is not None:
            assert self.gate.wait(10), "the test never released the fake service"
        if self.error is not None:
            raise self.error
        path = self.folder / f"{currency.lower()}_report.html"
        path.write_text("<html><body>report</body></html>", encoding="utf-8")
        return make_result(path, currency, failed=self.failed_section)


def wait_for_worker(tab, qapp):
    """Wait for the worker thread, then deliver its queued signals (and a queued follow-up worker, if any)."""
    for _ in range(5):
        worker = tab.worker
        assert worker.wait(5000)
        qapp.processEvents()
        if tab.worker is worker:
            return


def test_status_shows_the_summary_after_generate(qapp, tmp_path):
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path))
    tab.generate_button.click()
    wait_for_worker(tab, qapp)
    text = tab.status_label.text()
    assert text.startswith("BTC report built: 2 of 2 sections, 2.5 MB.")
    assert "all up to date, no sync needed" in text


def test_open_button_folder_label_and_view_show_the_written_file(qapp, tmp_path):
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path))
    assert not tab.open_button.isEnabled()
    tab.generate_button.click()
    wait_for_worker(tab, qapp)
    expected = tmp_path / "btc_report.html"
    assert tab.open_button.isEnabled()
    assert str(expected) in tab.folder_label.text()
    assert tab.report_path == expected
    assert os.path.normcase(tab.report_view.url().toLocalFile()) == os.path.normcase(str(expected.resolve()))


def test_a_failed_section_is_named_in_the_status(qapp, tmp_path):
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path, failed_section=True))
    tab.generate_button.click()
    wait_for_worker(tab, qapp)
    assert "1 of 2 sections" in tab.status_label.text() and "Not built: Levels" in tab.status_label.text()


def test_a_raising_service_shows_failed_and_the_buttons_stay_usable(qapp, tmp_path):
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path, error=RuntimeError("database down")))
    tab.generate_button.click()
    wait_for_worker(tab, qapp)
    assert tab.status_label.text() == "Failed: RuntimeError: database down"
    assert tab.generate_button.isEnabled() and not tab.open_button.isEnabled()
    assert not tab.worker.isRunning()


def test_progress_lines_are_shown_while_the_report_is_built(qapp, tmp_path):
    gate = threading.Event()
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path, gate=gate))
    try:
        tab.generate_button.click()
        for _ in range(100):
            qapp.processEvents()
            if "Building MenthorQ..." in tab.status_label.text():
                break
            tab.worker.wait(20)
        assert "Building MenthorQ..." in tab.status_label.text()
    finally:
        gate.set()
        wait_for_worker(tab, qapp)


def test_a_second_generate_while_running_is_queued_with_the_currency_chosen_then(qapp, tmp_path):
    gate = threading.Event()
    log = []
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path, gate=gate, log=log))
    tab.currency_combo.setCurrentText("BTC")
    tab.generate_button.click()
    for _ in range(100):                                   # let the first worker start
        if log:
            break
        tab.worker.wait(20)
    first_worker = tab.worker
    tab.currency_combo.setCurrentText("ETH")
    tab.generate_button.click()
    assert tab.worker is first_worker and tab.pending_currency == "ETH"
    assert "queued" in tab.status_label.text()
    tab.currency_combo.setCurrentText("BTC")               # changing the selection later must not change the request
    gate.set()
    first_worker.wait(5000)
    qapp.processEvents()
    assert tab.worker is not first_worker
    wait_for_worker(tab, qapp)
    assert log == ["BTC", "ETH"]
    assert tab.pending_currency is None
    assert tab.status_label.text().startswith("ETH report built")


def test_result_summary_names_the_failed_section_and_the_sync_step(tmp_path):
    text = result_summary(make_result(tmp_path / "x.html", failed=True))
    assert text == ("BTC report built: 1 of 2 sections, 2.5 MB. Not built: Levels (the tab explains why). "
                    "Checked 4 tables against the VPS: all up to date, no sync needed")


def test_result_summary_of_a_complete_report_has_no_not_built_part(tmp_path):
    assert "Not built" not in result_summary(make_result(tmp_path / "x.html"))


# ---------------------------------------------------------------------------
# Plain-text status, stop requests, waiting for a running worker
# ---------------------------------------------------------------------------
import time  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402

from coding.service.report.report_service import ReportStopped  # noqa: E402


class StoppingService:
    """generate() is stopped on request, with a reason."""

    def generate(self, currency, progress=None, should_stop=None):
        raise ReportStopped("stopped before Levels")


class StoppedWithoutReasonService:
    def generate(self, currency, progress=None, should_stop=None):
        raise ReportStopped()


class BlockingService:
    """generate() waits until should_stop() says so, then stops like a report cut short by application exit."""

    def __init__(self):
        self.started = threading.Event()
        self.stop_seen = threading.Event()

    def generate(self, currency, progress=None, should_stop=None):
        self.started.set()
        deadline = time.monotonic() + 5
        while not should_stop():
            if time.monotonic() > deadline:
                raise AssertionError("the stop was never requested")
            time.sleep(0.01)
        self.stop_seen.set()
        raise ReportStopped("stopped before Levels")


def test_status_label_is_plain_text(qapp, tmp_path):
    tab = ReportTab(service_factory=lambda: FakeService(tmp_path))
    assert tab.status_label.textFormat() == Qt.TextFormat.PlainText


def test_a_report_stopped_on_request_shows_failed_stopped_with_the_reason(qapp, tmp_path):
    tab = ReportTab(service_factory=StoppingService)
    tab.generate_button.click()
    wait_for_worker(tab, qapp)
    assert tab.status_label.text() == "Failed: stopped (stopped before Levels)"
    assert not tab.open_button.isEnabled()


def test_a_stop_without_a_reason_shows_failed_stopped(qapp, tmp_path):
    tab = ReportTab(service_factory=StoppedWithoutReasonService)
    tab.generate_button.click()
    wait_for_worker(tab, qapp)
    assert tab.status_label.text() == "Failed: stopped"


def test_wait_for_worker_requests_a_stop_and_returns_when_the_worker_has_ended(qapp, tmp_path):
    service = BlockingService()
    tab = ReportTab(service_factory=lambda: service)
    tab.generate_button.click()
    assert service.started.wait(5)
    worker = tab.worker
    assert worker.isRunning()
    tab._wait_for_worker()
    assert service.stop_seen.is_set()                      # the running generate() saw the request
    assert not worker.isRunning()

