"""/mute argument parsing and the muted-until rule (pure)."""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.menthorq_monitor.alert_mute import (
    MAXIMUM_MUTE_DURATION,
    MenthorqMonitorState,
    is_muted,
    parse_mute_duration,
)

NOW = datetime(2026, 11, 6, 10, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize("text, expected", [
    ("24h", timedelta(hours=24)), ("3d", timedelta(days=3)), ("90m", timedelta(minutes=90)),
    (" 2 H ", timedelta(hours=2)), ("30d", timedelta(days=30)), ("1m", timedelta(minutes=1)),
])
def test_durations_by_hand(text, expected):
    assert parse_mute_duration(text) == expected


def test_off_means_unmute():
    assert parse_mute_duration("off") is None
    assert parse_mute_duration(" OFF ") is None


@pytest.mark.parametrize("text, message", [
    ("soon", "is not a duration"), ("24", "is not a duration"), ("h", "is not a duration"),
    ("-1h", "is not a duration"), ("1.5h", "is not a duration"), ("2w", "is not a duration"),
    ("", "is not a duration"), ("0d", "longer than zero"), ("31d", "longest mute is 30d"),
    ("721h", "longest mute is 30d"),
])
def test_invalid_durations_raise(text, message):
    with pytest.raises(ValueError, match=message):
        parse_mute_duration(text)


def test_maximum_is_thirty_days():
    assert MAXIMUM_MUTE_DURATION == timedelta(days=30)
    assert parse_mute_duration("720h") == MAXIMUM_MUTE_DURATION


def test_muted_strictly_before_the_end():
    state = MenthorqMonitorState(currency="BTC", alerts_muted_until=NOW + timedelta(hours=1))
    assert is_muted(state, NOW) is True
    assert is_muted(state, NOW + timedelta(minutes=59, seconds=59)) is True
    assert is_muted(state, NOW + timedelta(hours=1)) is False       # ends on its own
    assert is_muted(MenthorqMonitorState(currency="BTC"), NOW) is False
    assert is_muted(None, NOW) is False


@pytest.mark.parametrize("text", ["99999999999d", "99999999999999999999999m", "43201m"])
def test_huge_numbers_raise_the_friendly_error_not_overflow(text):
    # 30 d = 43,200 min: one minute more is refused; a huge number never reaches timedelta.
    with pytest.raises(ValueError, match="longest mute is 30d"):
        parse_mute_duration(text)


@pytest.mark.parametrize("text, expected", [
    ("024h", timedelta(hours=24)), ("0003d", timedelta(days=3)), ("43200m", timedelta(days=30)),
])
def test_leading_zeros_and_the_exact_maximum_in_minutes(text, expected):
    assert parse_mute_duration(text) == expected


def test_zeros_with_leading_zeros_are_still_zero():
    with pytest.raises(ValueError, match="longer than zero"):
        parse_mute_duration("000h")
