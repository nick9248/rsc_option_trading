"""
Muting of the monitor's URGENT alerts (the /mute command).

A mute only silences urgent alerts; replies to commands and the weekly
brief are still sent. It is stored as an end instant (table
``menthorq_monitor_state``, migration 032), so it survives a restart and
ends on its own: alerts are muted while ``now < alerts_muted_until``.

Pure: no database, network or plotting imports.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

MUTE_OFF = "off"
MAXIMUM_MUTE_DURATION = timedelta(days=30)
_DURATION_PATTERN = re.compile(r"^(\d+)\s*([mhd])$")
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 24 * 60}


@dataclass
class MenthorqMonitorState:
    """
    Persisted alert state of one currency. ``alerts_muted_until`` None means
    never muted or unmuted; the suppressed fields describe the newest urgent
    message that was not sent because alerts were muted.
    """

    currency: str
    alerts_muted_until: Optional[datetime] = None
    mute_updated_at: Optional[datetime] = None
    last_suppressed_at: Optional[datetime] = None
    last_suppressed_alert_keys: List[str] = field(default_factory=list)


def parse_mute_duration(text: str) -> Optional[timedelta]:
    """
    Read the argument of /mute.

    Args:
        text: e.g. "24h", "3d", "90m" or "off" (case and spaces ignored).

    Returns:
        The duration, or None for "off" (unmute).

    Raises:
        ValueError: when the text is not a positive number with unit m, h
            or d, or is longer than ``MAXIMUM_MUTE_DURATION``.
    """
    cleaned = text.strip().lower()
    if cleaned == MUTE_OFF:
        return None
    match = _DURATION_PATTERN.match(cleaned)
    if match is None:
        raise ValueError(f"{text.strip()!r} is not a duration (use e.g. 24h, 3d, 90m or off)")
    duration = timedelta(minutes=int(match.group(1)) * _UNIT_MINUTES[match.group(2)])
    if duration <= timedelta(0):
        raise ValueError("the duration must be longer than zero")
    if duration > MAXIMUM_MUTE_DURATION:
        raise ValueError(f"the longest mute is {MAXIMUM_MUTE_DURATION.days}d")
    return duration


def is_muted(state: Optional[MenthorqMonitorState], now: datetime) -> bool:
    """True while ``now`` is before the stored mute end."""
    return (state is not None and state.alerts_muted_until is not None
            and now < state.alerts_muted_until)
