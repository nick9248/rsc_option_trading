"""
Age check of the point-in-time inputs of a MenthorQ-style daily close,
shared by the monitor (``MenthorqMonitorService.compute_daily_close_record``)
and the backtest (``MenthorqLevelsBacktestService``), so both refuse the
same days with the same reasons.

Pure: no database, network or plotting imports.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, List, Mapping

# A daily close (23:59 UTC, open interest from 00:00 UTC) is not computed from
# an input older than this before its own instant (snapshot and index trade
# before 23:59 UTC, open-interest snapshot before 00:00 UTC): the day is then
# incomplete in the database.
DAILY_CLOSE_MAXIMUM_INPUT_AGE = timedelta(minutes=60)


def stale_input_reasons(
    inputs: Mapping[str, Any], as_of: datetime, open_interest_as_of: datetime,
    maximum_age: timedelta = DAILY_CLOSE_MAXIMUM_INPUT_AGE,
) -> List[str]:
    """
    One reason per loaded input older than ``maximum_age`` before its own
    instant: the snapshot and the index trade before ``as_of``, the
    open-interest snapshot before ``open_interest_as_of``. Ages are whole
    minutes, rounded down; exactly ``maximum_age`` is not too old.

    Args:
        inputs: Loaded inputs with ``snapshot_captured_at_utc``,
            ``open_interest_captured_at_utc`` and ``index_price_time_utc``.
        as_of: The record's data instant.
        open_interest_as_of: The open-interest instant.
        maximum_age: The age limit.

    Returns:
        The reasons, e.g. "snapshot 61 min older than 23:59 UTC (limit 60)";
        empty when every input is recent enough.
    """
    limit_minutes = int(maximum_age.total_seconds() // 60)
    checks = (
        ("snapshot", inputs["snapshot_captured_at_utc"], as_of),
        ("open-interest snapshot", inputs["open_interest_captured_at_utc"], open_interest_as_of),
        ("index trade", inputs["index_price_time_utc"], as_of),
    )
    reasons = []
    for name, captured_at, instant in checks:
        age = instant - captured_at
        if age > maximum_age:
            reasons.append(f"{name} {int(age.total_seconds() // 60)} min older than "
                           f"{instant.astimezone(timezone.utc):%H:%M} UTC (limit {limit_minutes})")
    return reasons
