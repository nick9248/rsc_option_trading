"""
Argument wiring of scripts/replicate_menthorq.py (no database: only the
parser and the as_of / open-interest instants are exercised).
"""

from datetime import datetime, timezone

import pytest

from scripts.replicate_menthorq import build_parser, resolve_instants


def _instants(argv):
    parser = build_parser()
    return resolve_instants(parser, parser.parse_args(argv))


def test_menthorq_date_sets_2359_and_open_interest_at_midnight_of_the_same_date():
    assert _instants(["--menthorq-date", "2026-08-24"]) == (
        datetime(2026, 8, 24, 23, 59, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
    )


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
def test_invalid_combinations_are_rejected(argv):
    with pytest.raises(SystemExit):
        _instants(argv)


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

