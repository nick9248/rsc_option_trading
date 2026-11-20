"""Tests of the service that writes dashboard.html next to the text report."""

from pathlib import Path

from coding.service.analysis.onchain_dashboard_service import OnChainDashboardService
from tests.unit.onchain_dashboard.conftest import FAKE_PLOTLY


def test_writes_dashboard_and_leaves_other_files_alone(empty_result, tmp_path: Path):
    report = tmp_path / "report.txt"
    report.write_bytes("REPORT TEXT é\n".encode("utf-8"))
    other = tmp_path / "notes.json"
    other.write_text("{}", encoding="utf-8")
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (report, other)}

    path = OnChainDashboardService(plotly_javascript=lambda: FAKE_PLOTLY).write(empty_result, tmp_path)

    assert path == tmp_path / "dashboard.html"
    page = path.read_text(encoding="utf-8")
    assert page.lstrip().lower().startswith("<!doctype html")
    assert FAKE_PLOTLY in page and "BTC</span> on-chain dashboard" in page
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (report, other)} == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["dashboard.html", "notes.json", "report.txt"]


def test_writing_again_replaces_only_the_dashboard(empty_result, tmp_path: Path):
    service = OnChainDashboardService(plotly_javascript=lambda: FAKE_PLOTLY)
    service.write(empty_result, tmp_path)
    (tmp_path / "dashboard.html").write_text("old", encoding="utf-8")
    service.write(empty_result, tmp_path)
    assert "on-chain dashboard" in (tmp_path / "dashboard.html").read_text(encoding="utf-8")
