"""The service that reads the two pages of a run and writes unified_dashboard.html next to them."""

from pathlib import Path

import pytest

from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
from coding.service.analysis.unified_dashboard_service import UNIFIED_DASHBOARD_FILE_NAME, UnifiedDashboardService
from tests.unit.onchain_dashboard.test_unified_dashboard import PLOTLY_STAND_IN, make_report_page


def test_writes_the_unified_page_and_leaves_the_inputs_alone(real_result, tmp_path: Path):
    report = tmp_path / "market_report.html"
    dashboard = tmp_path / "dashboard.html"
    report.write_text(make_report_page(), encoding="utf-8")
    dashboard.write_text(build_onchain_dashboard_page(real_result, PLOTLY_STAND_IN), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in (report, dashboard)}

    path = UnifiedDashboardService().write("BTC", report, dashboard, tmp_path)

    assert path == tmp_path / UNIFIED_DASHBOARD_FILE_NAME == tmp_path / "unified_dashboard.html"
    assert "BTC Market Intelligence" in path.read_text(encoding="utf-8")
    assert {p.name: p.read_bytes() for p in (report, dashboard)} == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["dashboard.html", "market_report.html", "unified_dashboard.html"]


def test_a_missing_input_raises_and_writes_nothing(tmp_path: Path):
    with pytest.raises(OSError):
        UnifiedDashboardService().write("BTC", tmp_path / "no_report.html", tmp_path / "no_dashboard.html", tmp_path)
    assert list(tmp_path.iterdir()) == []
