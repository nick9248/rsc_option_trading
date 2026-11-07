"""
scripts/menthorq_brief.py: --dry-run is mandatory, and the dry run builds
the brief from replayed records without writing anything.
"""

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
