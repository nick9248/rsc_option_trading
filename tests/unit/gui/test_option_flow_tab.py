"""Option Flow tab (offscreen Qt, fake service, no database): the worker's result, the controls and the warnings."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike
from coding.gui.tabs.option_flow_tab import (
    ALL_EXPIRATIONS, OptionFlowRequest, OptionFlowResult, OptionFlowTab, OptionFlowWorker)
from coding.service.option_flow.option_flow_service import OptionFlowService, StrikeFlowReport

NOW = datetime(2026, 11, 13, 21, 25, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeRepository:
    def get_option_trades_between(self, currency, start_ts, end_ts, expiration=None):
        return [OptionTrade("27NOV26", 70_000.0, "P", "buy", 2.0, False)]

    def get_latest_trade_time(self, currency):
        return NOW - timedelta(hours=5)

    def get_first_block_trade_time(self, currency):
        return NOW - timedelta(days=60)

    def get_traded_expirations(self, currency, start_ts, end_ts):
        return ["27NOV26", "13NOV26"]


def fake_service():
    return OptionFlowService(FakeRepository(), clock=lambda: NOW)


def test_worker_returns_report_expirations_and_a_chart_page(qapp, tmp_path):
    worker = OptionFlowWorker(OptionFlowRequest("BTC", 24, None, "notional"), fake_service, tmp_path)
    results = []
    worker.succeeded.connect(results.append)
    worker.run()
    result = results[0]
    assert result.expirations == ["13NOV26", "27NOV26"]
    assert result.report.flow.cell(70_000.0, "put_buy").notional_usd == 140_000.0
    assert result.html_path.parent == tmp_path and "Puts Buy" in result.html_path.read_text(encoding="utf-8")


def test_worker_reports_a_failure_as_one_line(qapp, tmp_path):
    broken = MagicMock(side_effect=RuntimeError("database down"))
    worker = OptionFlowWorker(OptionFlowRequest("BTC", 24, None, "notional"), broken, tmp_path)
    errors = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors == ["RuntimeError: database down"]


def result_for(report_expiry, newest, skipped=0):
    flow = aggregate_by_strike([OptionTrade("27NOV26", 70_000.0, "P", "buy", 2.0, False)])
    flow.trades_skipped = skipped
    report = StrikeFlowReport("BTC", NOW - timedelta(hours=24), NOW, report_expiry, flow, newest, NOW - timedelta(days=60))
    return OptionFlowResult(report, ["13NOV26", "27NOV26"], Path("chart.html"))


def test_tab_defaults_and_request(qapp):
    tab = OptionFlowTab(service_factory=fake_service, clock=lambda: NOW)
    request = tab.current_request()
    assert (request.currency, request.hours, request.expiry, request.unit) == ("BTC", 24, None, "notional")
    assert tab.expiry_combo.itemText(0) == ALL_EXPIRATIONS
    tab.close()


def test_success_fills_the_expiries_keeps_the_selection_and_warns_about_old_data(qapp):
    tab = OptionFlowTab(service_factory=fake_service, clock=lambda: NOW)
    tab._on_succeeded(result_for("27NOV26", NOW - timedelta(hours=5)))
    assert [tab.expiry_combo.itemText(i) for i in range(tab.expiry_combo.count())] == [ALL_EXPIRATIONS, "13NOV26", "27NOV26"]
    assert tab.current_request().expiry == "27NOV26"
    assert not tab.warning_label.isHidden() and "newest stored trade" in tab.warning_label.text()
    assert tab.generate_button.isEnabled()
    tab.close()


def test_fresh_data_without_skips_shows_no_warning_and_none_means_all_expirations(qapp):
    tab = OptionFlowTab(service_factory=fake_service, clock=lambda: NOW)
    tab._on_succeeded(result_for(None, NOW - timedelta(minutes=30)))
    assert tab.warning_label.isHidden() and tab.current_request().expiry is None
    tab._on_succeeded(result_for(None, None, skipped=2))
    assert "no stored trades" in tab.warning_label.text() and "2 trades were skipped" in tab.warning_label.text()
    tab.close()


def test_failure_shows_an_error_and_reenables_the_button(qapp):
    tab = OptionFlowTab(service_factory=fake_service, clock=lambda: NOW)
    tab.generate_button.setEnabled(False)
    tab._on_failed("ValueError: x")
    assert tab.status_label.text() == "Failed: ValueError: x" and tab.generate_button.isEnabled()
    tab.close()


def test_changing_the_currency_resets_the_expiry_to_all(qapp):
    tab = OptionFlowTab(service_factory=fake_service, clock=lambda: NOW)
    tab._on_succeeded(result_for("27NOV26", NOW - timedelta(minutes=5)))
    assert tab.current_request().expiry == "27NOV26"
    tab.currency_combo.setCurrentIndex(1)
    assert tab.current_request().expiry is None and tab.expiry_combo.count() == 1
    tab.close()
