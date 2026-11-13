"""Dealer Levels tab (offscreen Qt, fake service, no database): worker results, expiry list, summary and warnings."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, StrikeLevelRow
from coding.gui.tabs.dealer_flow_levels_tab import (
    DealerFlowLevelsTab, DealerLevelsRequest, DealerLevelsResult, DealerLevelsWorker, summary_html)

NOW = datetime(2026, 11, 13, 21, 25, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def make_levels(gate_passed=True, captured_at=NOW - timedelta(minutes=10), notes=()):
    return DealerFlowLevels(
        currency="BTC", expiry="27NOV26", index_price=68_000.0, chain_captured_at=captured_at,
        flow_start=datetime(2026, 8, 27, tzinfo=timezone.utc), trades_used=100,
        rows=[StrikeLevelRow(68_000.0, -10.0, 5.0, -2_000_000.0, -5_000_000.0, 30.0, 400.0)],
        flow_call_wall=73_000.0, flow_put_wall=68_000.0, flow_gamma_flip=None, oi_call_wall=None, oi_put_wall=68_000.0,
        oi_gamma_flip=None, total_flow_gex=-3_400_000.0, total_oi_gex=None, coverage=1.0, violation_rate=0.098, violations=9,
        options_checked=92, options_without_open_interest=0, gate_passed=gate_passed,
        gate_reason=None if gate_passed else "9.8% of options exceed their open interest (limit 5%)", notes=list(notes))


class FakeService:
    def __init__(self):
        self.calls = []

    def expirations(self, currency):
        return ["14NOV26", "27NOV26"]

    def levels(self, currency, expiry):
        self.calls.append((currency, expiry))
        return make_levels()


def test_worker_lists_expirations_without_computing(qapp, tmp_path):
    service = FakeService()
    worker = DealerLevelsWorker(DealerLevelsRequest("BTC", None), lambda: service, tmp_path)
    results = []
    worker.succeeded.connect(results.append)
    worker.run()
    assert results[0].expirations == ["14NOV26", "27NOV26"] and results[0].levels is None and service.calls == []


def test_worker_computes_one_expiry_and_writes_a_chart_page(qapp, tmp_path):
    service = FakeService()
    worker = DealerLevelsWorker(DealerLevelsRequest("BTC", "27NOV26"), lambda: service, tmp_path)
    results = []
    worker.succeeded.connect(results.append)
    worker.run()
    assert service.calls == [("BTC", "27NOV26")] and results[0].levels.expiry == "27NOV26"
    assert results[0].html_path.parent == tmp_path and "Flow-based GEX" in results[0].html_path.read_text(encoding="utf-8")


def test_worker_reports_a_failure_as_one_line(qapp, tmp_path):
    worker = DealerLevelsWorker(DealerLevelsRequest("BTC", None), MagicMock(side_effect=RuntimeError("database down")), tmp_path)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors == ["RuntimeError: database down"]


def test_summary_shows_both_views_and_the_check():
    passed = summary_html(make_levels(gate_passed=True))
    assert "73,000" in passed and "not computed" in passed and "passed" in passed and "9 of 92" in passed
    failed = summary_html(make_levels(gate_passed=False))
    assert "NOT passed" in failed and "9.8% of options exceed" in failed


def test_tab_fills_expirations_enables_generate_and_shows_the_result(qapp):
    tab = DealerFlowLevelsTab(service_factory=FakeService, clock=lambda: NOW)
    assert not tab.generate_button.isEnabled()
    tab._on_succeeded(DealerLevelsResult(["14NOV26", "27NOV26"]))
    assert [tab.expiry_combo.itemText(i) for i in range(tab.expiry_combo.count())] == ["14NOV26", "27NOV26"]
    assert tab.generate_button.isEnabled() and tab.current_request() == DealerLevelsRequest("BTC", "14NOV26")
    tab._on_succeeded(DealerLevelsResult(["14NOV26", "27NOV26"], make_levels(), Path("chart.html")))
    assert not tab.summary_label.isHidden() and "27NOV26" in tab.status_label.text() and tab.warning_label.isHidden()
    tab.close()


def test_old_chain_snapshot_and_notes_are_warned_about(qapp):
    tab = DealerFlowLevelsTab(service_factory=FakeService, clock=lambda: NOW)
    old = make_levels(captured_at=NOW - timedelta(hours=9), notes=["3 traded options are no longer in the chain and were left out"])
    tab._on_succeeded(DealerLevelsResult(["27NOV26"], old, Path("chart.html")))
    text = tab.warning_label.text()
    assert not tab.warning_label.isHidden() and "no longer in the chain" in text and "newest stored chain snapshot" in text
    tab.close()


def test_empty_chain_asks_for_a_sync_and_failure_keeps_the_button_state(qapp):
    tab = DealerFlowLevelsTab(service_factory=FakeService, clock=lambda: NOW)
    tab._on_succeeded(DealerLevelsResult([]))
    assert "sync the database" in tab.status_label.text() and not tab.generate_button.isEnabled()
    tab._on_failed("ValueError: x")
    assert tab.status_label.text() == "Failed: ValueError: x" and not tab.generate_button.isEnabled()
    tab.close()
