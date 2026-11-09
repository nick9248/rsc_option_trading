"""
Argument wiring of scripts/replicate_menthorq.py (no database: the parser,
the as_of / open-interest instants, and which service method main calls,
with the service's ``run`` replaced by a recorder).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from coding.service.on_chain.menthorq_replication_service import MenthorqReplicationService
from scripts import replicate_menthorq
from scripts.replicate_menthorq import build_parser, main, resolve_instants


def _instants(argv):
    return resolve_instants(build_parser().parse_args(argv))


class _RecordingService(MenthorqReplicationService):
    """The real daily-close guard; ``run`` only records its arguments (no database, no files)."""

    def __init__(self):
        self.runs = []


@pytest.fixture
def recording_service(monkeypatch):
    monkeypatch.setattr(replicate_menthorq, "init_logging", lambda **_keyword_arguments: None)
    service = _RecordingService()

    def record_run(**keyword_arguments):
        service.runs.append(keyword_arguments)
        return {}
    monkeypatch.setattr(service, "run", record_run)
    return service


def test_menthorq_date_goes_through_the_daily_close_with_its_instants_and_run_mode(recording_service):
    main(["--menthorq-date", "2026-08-24", "--output-directory", "out"], service=recording_service)
    assert recording_service.runs == [{
        "currency": "BTC", "as_of": datetime(2026, 8, 24, 23, 59, tzinfo=timezone.utc),
        "output_directory": Path("out"), "expiry_universe": "monthly", "gamma_underlying": "index",
        "reference": None, "open_interest_as_of": datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
        "run_mode": "menthorq_daily_close", "chart_layout": "flat",
    }]


def test_menthorq_date_of_a_utc_day_that_has_not_ended_is_rejected(recording_service):
    today = datetime.now(timezone.utc).date()
    for not_ended in (today, today + timedelta(days=1)):
        with pytest.raises(ValueError, match="has not ended"):
            main(["--menthorq-date", not_ended.isoformat()], service=recording_service)
    assert recording_service.runs == []


def test_as_of_still_calls_run_directly_with_the_as_of_run_mode(recording_service):
    main(["--as-of", "2026-11-06T20:59:00Z", "--output-directory", "out"], service=recording_service)
    assert recording_service.runs == [{
        "currency": "BTC", "as_of": datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc),
        "output_directory": Path("out"), "expiry_universe": "monthly", "gamma_underlying": "index",
        "reference": None, "open_interest_as_of": None,
    }]


def test_as_of_alone_keeps_open_interest_from_the_same_snapshot():
    assert _instants(["--as-of", "2026-11-06T20:59:00Z"]) == (
        datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc), None)


def test_as_of_with_start_of_utc_day_still_works():
    assert _instants(["--as-of", "2026-11-06T23:59:00Z", "--open-interest-at-start-of-utc-day"]) == (
        datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc),
        datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc),
    )


def test_as_of_with_explicit_open_interest_instant_still_works():
    assert _instants(["--as-of", "2026-11-06T20:59:00Z",
                      "--open-interest-as-of", "2026-11-06T15:20:00Z"]) == (
        datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc),
        datetime(2026, 11, 6, 15, 20, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("argv", [
    [],                                                                 # neither instant
    ["--as-of", "2026-11-06T23:59:00Z", "--menthorq-date", "2026-11-06"],  # both
    ["--menthorq-date", "2026-11-06", "--open-interest-as-of", "2026-11-06T15:20:00Z"],
    ["--menthorq-date", "2026-11-06", "--open-interest-at-start-of-utc-day"],
    ["--menthorq-date", "06/11/2026"],                                  # not ISO
])
def test_invalid_combinations_are_rejected(argv, recording_service):
    with pytest.raises(SystemExit):
        main(argv, service=recording_service)
    assert recording_service.runs == []


def test_parse_utc_timestamp_is_shared_by_both_scripts():
    from datetime import datetime, timezone

    from coding.core.analytics.menthorq_replication_calculator import parse_utc_timestamp
    from scripts import menthorq_brief, replicate_menthorq

    assert menthorq_brief.parse_utc_timestamp is parse_utc_timestamp
    assert replicate_menthorq.parse_utc_timestamp is parse_utc_timestamp
    expected = datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)
    assert parse_utc_timestamp("2026-11-06T20:59") == expected            # no offset: UTC
    assert parse_utc_timestamp("2026-11-06T20:59Z") == expected
    assert parse_utc_timestamp("2026-11-06T15:59-05:00") == expected      # converted to UTC
    assert parse_utc_timestamp("2026-11-06T15:59-05:00").tzinfo == timezone.utc

