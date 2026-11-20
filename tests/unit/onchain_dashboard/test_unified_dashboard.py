"""
The unified dashboard: a real on-chain dashboard page (built from the production pipeline's result) and a real report page
of the same code, merged into one page. Pure: no database, no network, no browser.
"""

import json
import re
from datetime import datetime, timezone

import pytest

from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
from coding.core.analytics.report.report_page import ReportHeader, ReportSection, build_report_page
from coding.core.analytics.unified_dashboard import build_unified_dashboard_page

# The merge finds the plotly script by the banner real plotly.js starts with, so the stand-in has it too.
PLOTLY_STAND_IN = "/*! plotly.js v0 stand-in */"


def make_report_page(currency: str = "BTC") -> str:
    header = ReportHeader(currency, datetime(2026, 11, 15, tzinfo=timezone.utc), [("Newest stored trade", "2026-11-14 10:00 UTC")])
    return build_report_page(header, [ReportSection(key="levels", title="Levels", body_html="<p>levels body</p>")], PLOTLY_STAND_IN)


@pytest.fixture(scope="module")
def onchain_page(real_result):
    return build_onchain_dashboard_page(real_result, PLOTLY_STAND_IN)


def tab_labels(page: str):
    return re.findall(r'role="tab" id="tab-[^"]+" data-key="[^"]+"[^>]*>([^<]*)<', page)


def test_one_page_with_the_overview_and_the_tabs_of_both_inputs(onchain_page):
    page = build_unified_dashboard_page(make_report_page(), onchain_page, "BTC")
    assert page.lstrip().lower().startswith("<!doctype html")
    labels = tab_labels(page)
    assert labels[0] == "Overview"
    assert "Levels" in labels                                        # from the market report
    assert "Expiries" in labels and "Data quality" in labels         # from the on-chain dashboard
    assert 'id="theme-btn"' in page                                  # the light / dark switch
    assert page.count(PLOTLY_STAND_IN) == 1                          # one copy of plotly.js for the whole page
    assert "%%" not in page                                          # every placeholder of the template was filled


def test_chart_data_of_both_inputs_is_embedded_once_as_valid_json(onchain_page):
    page = build_unified_dashboard_page(make_report_page(), onchain_page, "BTC")
    blocks = re.findall(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.DOTALL)
    assert len(blocks) == 1
    data = json.loads(blocks[0])
    assert set(data) == {"figures", "pages", "switchers"}
    assert any(key.startswith("o-") for key in data["switchers"]) or any(key.startswith("o-") for key in data["figures"])


def test_titles_and_names_follow_the_currency(onchain_page):
    page = build_unified_dashboard_page(make_report_page("ETH"), onchain_page, "ETH")
    assert "<h1>ETH Market Intelligence</h1>" in page
    assert "Ethereum derivatives" in page and "Bitcoin" not in page
    assert "BTC Market Intelligence" not in page


def test_an_input_that_is_not_a_report_page_is_refused_with_a_plain_message(onchain_page):
    with pytest.raises(ValueError, match="no embedded report data"):
        build_unified_dashboard_page("<html><body>not a report</body></html>", onchain_page, "BTC")
    with pytest.raises(ValueError, match="no embedded report data"):
        build_unified_dashboard_page(make_report_page(), "", "BTC")


def test_the_page_is_the_same_for_the_same_inputs(onchain_page):
    report = make_report_page()
    assert build_unified_dashboard_page(report, onchain_page, "BTC") == build_unified_dashboard_page(report, onchain_page, "BTC")
