"""Tests of ReportService with a fake synchronizer, repository and section builders (no database, no SSH)."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.report.report_page import ReportSection
from coding.service.database.vps_sync_service import SyncCheckResult, VpsSyncResult, WatermarkComparison
from coding.service.report.report_service import SYNC_HINT, ReportService, failure_for

NOW = datetime(2026, 11, 14, 10, 29, 5, tzinfo=timezone.utc)


class Recorder:
    """Call order shared by the fakes."""

    def __init__(self):
        self.events = []


class FakeRepository:
    def __init__(self, recorder=None, trade_time=NOW - timedelta(minutes=3), snapshot_time=NOW - timedelta(minutes=9)):
        self.recorder = recorder
        self.trade_time = trade_time
        self.snapshot_time = snapshot_time

    def get_latest_trade_time(self, currency):
        return self.trade_time

    def get_latest_option_chain_capture_at_or_before(self, currency, instant):
        return self.snapshot_time


class FakeSynchronizer:
    def __init__(self, recorder=None, sync_ran=False, error=None, sync_result=None):
        self.recorder = recorder
        self.sync_ran = sync_ran
        self.error = error
        self.sync_result = sync_result
        self.calls = 0

    def sync_if_behind(self, table_names=(), progress_callback=None):
        self.calls += 1
        if self.recorder is not None:
            self.recorder.events.append("sync")
        if self.error is not None:
            raise self.error
        comparison = WatermarkComparison("historical_trades", "timestamp", NOW, NOW)
        return SyncCheckResult([comparison], self.sync_ran, self.sync_result)


def good_builder(key, title="Title"):
    def build(context):
        return ReportSection(key=key, title=title, body_html=f"<p>body of {key}</p>")
    return key, title, build


def failing_builder(key, error):
    def build(context):
        raise error
    return key, key.title(), build


def make_service(tmp_path, builders=None, synchronizer=None, repository_factory=None, clock=lambda: NOW):
    return ReportService(
        repository_factory=repository_factory or FakeRepository, synchronizer=synchronizer or FakeSynchronizer(),
        output_directory=tmp_path, clock=clock, builders=builders or [good_builder("one", "One"), good_builder("two", "Two")],
        plotly_javascript=lambda: "/*js*/")


def written_page(result):
    return result.path.read_text(encoding="utf-8")


def test_generate_writes_exactly_one_file_with_the_timestamped_name(tmp_path):
    result = make_service(tmp_path).generate("BTC")
    files = list(tmp_path.iterdir())
    assert files == [result.path]
    assert re.fullmatch(r"btc_market_report_\d{8}T\d{6}Z\.html", result.path.name)
    assert result.path.name == "btc_market_report_20261114T102905Z.html"
    assert all(outcome.built for outcome in result.sections) and result.failed_sections == []
    assert result.size_bytes == result.path.stat().st_size > 0


def test_header_shows_the_stored_data_times(tmp_path):
    page = written_page(make_service(tmp_path).generate("BTC"))
    assert "<dt>Newest stored trade</dt><dd>2026-11-14 10:26 UTC</dd>" in page
    assert "<dt>Chain snapshot</dt><dd>2026-11-14 10:20 UTC</dd>" in page


def test_header_says_none_stored_when_the_repository_has_no_data(tmp_path):
    service = make_service(tmp_path, repository_factory=lambda: FakeRepository(trade_time=None, snapshot_time=None))
    assert written_page(service.generate("BTC")).count("none stored") == 2


def test_header_says_not_read_when_the_repository_raises(tmp_path):
    class Broken(FakeRepository):
        def get_latest_trade_time(self, currency):
            raise RuntimeError("down")
    page = written_page(make_service(tmp_path, repository_factory=Broken).generate("BTC"))
    assert "not read (RuntimeError)" in page


def test_a_lookup_error_and_an_unexpected_error_leave_the_report_written_with_both_sections_failed(tmp_path):
    builders = [good_builder("fine", "Fine"),
                failing_builder("lookup", LookupError("No BTC option-chain snapshot is stored")),
                failing_builder("broken", RuntimeError("boom")),
                good_builder("also-fine", "Also fine")]
    result = make_service(tmp_path, builders=builders).generate("BTC")
    assert [outcome.built for outcome in result.sections] == [True, False, False, True]
    assert result.failed_sections == ["Lookup", "Broken"]
    page = written_page(result)
    assert page.count("This section could not be built") == 2
    assert SYNC_HINT in page and "This is unexpected" in page
    assert "body of fine" in page and "body of also-fine" in page


def test_failure_for_gives_the_sync_hint_for_missing_data_and_the_unexpected_text_otherwise():
    missing = failure_for("Dealer levels", LookupError("No BTC option-chain snapshot with an unexpired expiry"))
    assert missing.fix == SYNC_HINT and "No BTC option-chain snapshot" in missing.what
    unexpected = failure_for("Levels", RuntimeError("boom"))
    assert "RuntimeError: boom" in unexpected.what and "unexpected" in unexpected.fix


def test_header_says_already_in_step_when_no_sync_was_needed(tmp_path):
    page = written_page(make_service(tmp_path, synchronizer=FakeSynchronizer(sync_ran=False)).generate("BTC"))
    assert "already in step with the VPS" in page


def test_header_says_synced_just_now_when_a_sync_ran(tmp_path):
    synchronizer = FakeSynchronizer(sync_ran=True, sync_result=VpsSyncResult(True, 1200, [], 3.0))
    result = make_service(tmp_path, synchronizer=synchronizer).generate("BTC")
    assert "synced from the VPS just now" in written_page(result) and "(with errors)" not in written_page(result)
    assert result.sync.sync_ran is True and not result.sync.failed


def test_a_sync_that_ran_with_errors_is_reported_as_failed(tmp_path):
    synchronizer = FakeSynchronizer(sync_ran=True, sync_result=VpsSyncResult(False, 10, ["trades"], 1.0))
    result = make_service(tmp_path, synchronizer=synchronizer).generate("BTC")
    assert result.sync.failed and "synced from the VPS just now (with errors)" in written_page(result)


def test_a_failing_sync_check_still_builds_the_report_and_says_so(tmp_path):
    synchronizer = FakeSynchronizer(error=RuntimeError("no ssh"))
    result = make_service(tmp_path, synchronizer=synchronizer).generate("BTC")
    assert result.sync.failed and result.sync.sync_ran is None
    assert "no ssh" in result.sync.summary
    assert "sync check failed" in written_page(result)
    assert all(outcome.built for outcome in result.sections)


def test_skip_sync_never_calls_the_synchronizer(tmp_path):
    synchronizer = FakeSynchronizer()
    result = make_service(tmp_path, synchronizer=synchronizer).generate("BTC", skip_sync=True)
    assert synchronizer.calls == 0 and not result.sync.failed
    assert "sync skipped" in written_page(result)


def test_the_sync_check_happens_before_the_repository_is_created(tmp_path):
    recorder = Recorder()

    def factory():
        recorder.events.append("repository")
        return FakeRepository(recorder)

    make_service(tmp_path, synchronizer=FakeSynchronizer(recorder), repository_factory=factory).generate("BTC")
    assert recorder.events == ["sync", "repository"]


def test_unknown_currency_is_refused_before_anything_is_done(tmp_path):
    synchronizer = FakeSynchronizer()
    with pytest.raises(ValueError, match="currency"):
        make_service(tmp_path, synchronizer=synchronizer).generate("SOL")
    assert synchronizer.calls == 0 and list(tmp_path.iterdir()) == []


def test_a_naive_clock_is_refused(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        make_service(tmp_path, clock=lambda: datetime(2026, 11, 14, 10, 0)).generate("BTC")


def test_every_builder_gets_the_same_instant_and_its_own_scratch_folder(tmp_path):
    contexts = []

    def recording(key):
        def build(context):
            contexts.append(context)
            return ReportSection(key=key, title=key)
        return key, key, build

    ticks = iter(NOW + timedelta(seconds=second) for second in range(100))
    make_service(tmp_path, builders=[recording("a"), recording("b"), recording("c")], clock=lambda: next(ticks)).generate("ETH")
    assert [context.now for context in contexts] == [NOW] * 3
    assert {context.currency for context in contexts} == {"ETH"}
    assert len({context.working_directory for context in contexts}) == 3


def test_plotly_is_only_read_when_a_built_section_has_a_figure(tmp_path):
    calls = []

    def figure_builder(context):
        return ReportSection(key="chart", title="Chart", figures={"f": {"data": [], "layout": {}}})

    service = make_service(tmp_path, builders=[good_builder("one"), ("chart", "Chart", figure_builder)])
    service.plotly_javascript = lambda: calls.append("read") or "/*js*/"
    assert "/*js*/" in written_page(service.generate("BTC")) and calls == ["read"]
    service2 = make_service(tmp_path / "second")
    service2.plotly_javascript = lambda: calls.append("again") or "/*js*/"
    service2.generate("BTC")
    assert calls == ["read"]
