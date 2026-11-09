"""
scripts/menthorq_brief.py: --dry-run is mandatory, and the dry run builds
the brief from replayed records without writing anything.
"""

from datetime import timedelta

import pytest

from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from scripts.menthorq_brief import build_dry_run_output, build_parser
from tests.fakes.fake_menthorq_monitor import BASE_TIME, FakeInputLoader, FakeMonitorRepository


def test_dry_run_flag_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
    assert build_parser().parse_args(["--dry-run"]).dry_run is True


def test_dry_run_output_contains_the_brief_and_writes_nothing():
    repository = FakeMonitorRepository()
    service = MenthorqMonitorService(repository=repository, input_loader=FakeInputLoader())
    output = build_dry_run_output(service, BASE_TIME)
    assert "BTC MenthorQ-style levels | Dry run (references replayed, not stored)" in output
    assert "Call Resistance  110,000" in output
    assert "[brief length" in output
    assert "no urgent alert would fire" in output     # identical replayed inputs: no change
    assert repository.records == [] and repository.deliveries == []


def test_stale_data_shows_only_the_data_stale_alert_like_the_collector():
    # Snapshot 3 h before the data time > the 120 min limit: the collector sends data_stale only.
    service = MenthorqMonitorService(repository=FakeMonitorRepository(),
                                     input_loader=FakeInputLoader(snapshot_age=timedelta(hours=3)))
    output = build_dry_run_output(service, BASE_TIME)
    alert = output.split("--- urgent alert that would be sent (before cooldown) ---\n")[1]
    assert alert.startswith("<b>URGENT | BTC MenthorQ monitor data is STALE</b>\n"
                            "Record 2026-11-06 09:10 UTC | snapshot 2026-11-06 06:10 UTC\n"
                            "Snapshot is 3 h 0 min older than the data time; levels may be out of date.\n"
                            "Movement alerts are paused until fresh data arrives.")


def test_not_computed_record_shows_the_no_usable_data_alert():
    # Same rule as the collector: losing every input is reported, never silent.
    service = MenthorqMonitorService(repository=FakeMonitorRepository(),
                                     input_loader=FakeInputLoader(available=False))
    output = build_dry_run_output(service, BASE_TIME)
    assert "no urgent alert would fire" not in output
    assert "<b>URGENT | BTC MenthorQ monitor data is STALE</b>" in output
    assert "Monitor has no usable data: inputs not available: No BTC option-chain snapshot" in output
