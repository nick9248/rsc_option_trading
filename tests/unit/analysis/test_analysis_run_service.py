"""AnalysisRunService with fakes only: no database, no network, files only under tmp_path."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from coding.service.analysis.analysis_run_service import AnalysisRunService
from coding.service.report.report_service import ReportStopped, SyncStep


class Recorder:
    """Shared call log plus switches for what should fail."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.calls = []
        self.fail = set()                 # e.g. {"analysis:BTC", "report:ETH", "dashboard:BTC", "unified:BTC", "sync"}
        self.stop_in_report = set()
        self.sync_failed = False

    def service(self) -> AnalysisRunService:
        recorder = self

        class FakeReportService:
            def check_and_sync(self, progress, should_stop=None):
                recorder.calls.append("sync")
                if "sync" in recorder.fail:
                    raise RuntimeError("tunnel down")
                return SyncStep("database in step", False, failed=recorder.sync_failed)

            def generate(self, currency, progress=None, skip_sync=False, should_stop=None):
                recorder.calls.append(f"report:{currency}")
                assert skip_sync is True
                if currency in recorder.stop_in_report:
                    raise ReportStopped("stopped")
                if f"report:{currency}" in recorder.fail:
                    raise OSError("disk full")
                return SimpleNamespace(path=recorder.tmp_path / f"{currency}_report.html")

        class FakeWorkflow:
            def __init__(self, currency):
                self.currency = currency

            def run(self, progress_callback=None):
                recorder.calls.append(f"analysis:{self.currency}")
                assert progress_callback is not None
                if f"analysis:{self.currency}" in recorder.fail:
                    raise ValueError("no data")
                return SimpleNamespace(result=f"result-{self.currency}", bundle_path=recorder.tmp_path / self.currency)

        class FakeDashboard:
            def write(self, result, folder):
                currency = result.split("-")[1]
                recorder.calls.append(f"dashboard:{currency}")
                assert folder == recorder.tmp_path / currency
                if f"dashboard:{currency}" in recorder.fail:
                    raise RuntimeError("template broken")
                return folder / "dashboard.html"

        class FakeUnifiedDashboard:
            def write(self, currency, market_report_path, onchain_dashboard_path, folder):
                recorder.calls.append(f"unified:{currency}")
                assert market_report_path == recorder.tmp_path / f"{currency}_report.html"
                assert onchain_dashboard_path == recorder.tmp_path / currency / "dashboard.html"
                assert folder == recorder.tmp_path / currency
                if f"unified:{currency}" in recorder.fail:
                    raise RuntimeError("page structure changed")
                return folder / "unified_dashboard.html"

        return AnalysisRunService(FakeReportService, FakeWorkflow, FakeDashboard, FakeUnifiedDashboard)


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path)


def test_order_and_single_sync_for_both_currencies(recorder):
    result = recorder.service().run(["BTC", "ETH"], lambda line: None)
    assert recorder.calls == ["sync", "analysis:BTC", "dashboard:BTC", "report:BTC", "unified:BTC",
                              "analysis:ETH", "dashboard:ETH", "report:ETH", "unified:ETH"]
    assert recorder.calls.count("sync") == 1
    assert result.all_succeeded
    assert result.failed_steps == []


def test_accessors_return_paths(recorder, tmp_path):
    result = recorder.service().run(["ETH"], lambda line: None)
    assert result.dashboard_path("ETH") == tmp_path / "ETH" / "dashboard.html"
    assert result.report_path("ETH") == tmp_path / "ETH_report.html"
    assert result.unified_dashboard_path("ETH") == tmp_path / "ETH" / "unified_dashboard.html"
    assert result.report_text_folder("ETH") == tmp_path / "ETH"
    assert result.dashboard_path("BTC") is None
    assert result.report_path("BTC") is None
    assert result.unified_dashboard_path("BTC") is None


def test_analysis_failure_skips_dashboard_but_report_and_other_currency_run(recorder):
    recorder.fail.add("analysis:BTC")
    result = recorder.service().run(["BTC", "ETH"], lambda line: None)
    assert "dashboard:BTC" not in recorder.calls
    assert "report:BTC" in recorder.calls
    assert "unified:BTC" not in recorder.calls
    assert recorder.calls[-4:] == ["analysis:ETH", "dashboard:ETH", "report:ETH", "unified:ETH"]
    statuses = {(s.currency, s.name): s for s in result.steps}
    assert statuses[("BTC", "on-chain analysis")].status == "failed"
    assert "ValueError: no data" in statuses[("BTC", "on-chain analysis")].message
    assert statuses[("BTC", "on-chain dashboard")].status == "skipped"
    assert statuses[("BTC", "on-chain dashboard")].message == "skipped: analysis failed"
    assert statuses[("BTC", "report")].status == "succeeded"
    assert statuses[("BTC", "unified dashboard")].status == "skipped"       # it needs the dashboard too
    assert len(result.failed_steps) == 1
    assert result.report_text_folder("BTC") is None
    assert result.dashboard_path("BTC") is None
    assert not result.all_succeeded


def test_report_failure_does_not_affect_others(recorder):
    recorder.fail.add("report:BTC")
    result = recorder.service().run(["BTC", "ETH"], lambda line: None)
    assert [s.label for s in result.failed_steps] == ["BTC report"]
    assert result.dashboard_path("BTC") is not None
    assert result.report_path("BTC") is None
    assert result.report_path("ETH") is not None
    assert result.unified_dashboard_path("BTC") is None
    assert result.unified_dashboard_path("ETH") is not None


def test_dashboard_failure_is_isolated(recorder):
    recorder.fail.add("dashboard:BTC")
    result = recorder.service().run(["BTC", "ETH"], lambda line: None)
    assert [s.label for s in result.failed_steps] == ["BTC on-chain dashboard"]
    assert "report:BTC" in recorder.calls
    assert "unified:BTC" not in recorder.calls                              # the merge needs both files
    assert result.report_path("ETH") is not None
    assert result.unified_dashboard_path("ETH") is not None


def test_unified_dashboard_failure_is_isolated_and_keeps_the_other_files(recorder):
    recorder.fail.add("unified:BTC")
    result = recorder.service().run(["BTC", "ETH"], lambda line: None)
    assert [s.label for s in result.failed_steps] == ["BTC unified dashboard"]
    assert "RuntimeError: page structure changed" in result.failed_steps[0].message
    assert result.dashboard_path("BTC") is not None and result.report_path("BTC") is not None
    assert result.unified_dashboard_path("BTC") is None
    assert result.unified_dashboard_path("ETH") is not None


def test_sync_failure_is_reported_and_nothing_stops(recorder):
    recorder.sync_failed = True
    result = recorder.service().run(["BTC"], lambda line: None)
    assert [s.name for s in result.failed_steps] == ["database sync"]
    assert recorder.calls[1:] == ["analysis:BTC", "dashboard:BTC", "report:BTC", "unified:BTC"]


def test_sync_exception_is_isolated(recorder):
    recorder.fail.add("sync")
    result = recorder.service().run(["BTC"], lambda line: None)
    assert [s.name for s in result.failed_steps] == ["database sync"]
    assert "analysis:BTC" in recorder.calls


def test_stop_between_steps(recorder):
    state = {"checks": 0}

    def should_stop():
        state["checks"] += 1
        return "analysis:BTC" in recorder.calls          # stop right after the BTC analysis

    result = recorder.service().run(["BTC", "ETH"], lambda line: None, should_stop)
    assert recorder.calls == ["sync", "analysis:BTC"]
    stopped = [s for s in result.steps if s.status == "stopped"]
    assert len(stopped) == 7
    assert all(s.message == "not run: stopped" for s in stopped)
    assert not result.all_succeeded


def test_stop_before_anything_runs(recorder):
    result = recorder.service().run(["BTC"], lambda line: None, lambda: True)
    assert recorder.calls == []
    assert all(s.status == "stopped" for s in result.steps)


def test_report_stopped_is_a_stop_not_a_failure(recorder):
    recorder.stop_in_report.add("BTC")
    result = recorder.service().run(["BTC", "ETH"], lambda line: None)
    assert result.failed_steps == []
    assert recorder.calls == ["sync", "analysis:BTC", "dashboard:BTC", "report:BTC"]
    assert [s.status for s in result.steps[-6:]] == ["stopped"] * 6      # BTC report and unified dashboard, then all of ETH


def test_progress_has_step_lines_failure_and_summary(recorder):
    recorder.fail.add("analysis:BTC")
    lines = []
    recorder.service().run(["BTC"], lines.append)
    assert "Step 1/5: database sync ..." in lines
    assert "Step 2/5: BTC on-chain analysis ..." in lines
    assert "FAILED  BTC on-chain analysis: ValueError: no data" in lines
    assert "Step 3/5: BTC on-chain dashboard skipped: analysis failed" in lines
    assert "Step 4/5: BTC report done" in lines
    assert "Step 5/5: BTC unified dashboard skipped: the dashboard or the report is missing" in lines
    assert lines[-1].startswith("Analysis run summary:")
    assert "2 succeeded, 1 failed, 2 skipped, 0 not run (stopped)." in lines[-1]


def test_summary_text(recorder):
    result = recorder.service().run(["BTC"], lambda line: None)
    text = result.summary()
    assert "SUCCEEDED  BTC on-chain analysis: done" in text
    assert "SUCCEEDED  BTC unified dashboard: done" in text
    assert text.endswith("5 succeeded, 0 failed, 0 skipped, 0 not run (stopped).")


@pytest.mark.parametrize("currencies", [[], ["SOL"], ["BTC", "DOGE"], ["BTC", "BTC"], ["btc"]])
def test_invalid_currencies(recorder, currencies):
    with pytest.raises(ValueError):
        recorder.service().run(currencies, lambda line: None)
    assert recorder.calls == []
