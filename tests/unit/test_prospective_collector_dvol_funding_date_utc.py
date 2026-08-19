"""
Regression test for ProspectiveCollector._fetch_dvol / _fetch_funding_rate's
`date` column conversion (Wave H fresh-audit finding, Task Wave-H-C).

`date` is not decorative -- it's the time column HistoricalNormalizer /
repository.py (`_METRIC_HISTORY_WHITELIST` / `_TABLE_TIME_COLUMNS`) reads
for percentile/z-score trailing-window and staleness-gate queries.
`datetime.fromtimestamp()` WITHOUT `tz=timezone.utc` converts the epoch to
the HOST's local wall-clock time, not UTC -- so on a non-UTC host, every
DVOL/funding reading captured in the last few hours of the UTC calendar
day gets filed under the FOLLOWING calendar day.

These tests simulate a host whose local clock reads 5 hours ahead of UTC
(entirely synthetic -- deliberately NOT the real host offset, so the test
is host-independent per the task's instruction to "freeze/mock the
conversion, don't rely on the actual host's offset") and assert both
sites still produce the correct UTC calendar date for a timestamp minutes
before UTC midnight.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.service.data_collection.prospective_collector import ProspectiveCollector


def _make_collector():
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    return collector


class _HostOffsetDateTime(datetime):
    """datetime subclass whose tz-naive fromtimestamp() simulates a host
    5 hours ahead of UTC, while its tz-aware fromtimestamp() (the
    correct, fixed call site) still performs a real UTC conversion."""

    _FAKE_LOCAL_OFFSET = timedelta(hours=5)

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        if tz is not None:
            return datetime.fromtimestamp(ts, tz=tz)
        utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return (utc_dt + cls._FAKE_LOCAL_OFFSET).replace(tzinfo=None)


# 2026-08-08 23:45:00 UTC -- 15 minutes before the UTC calendar day rolls
# over. A naive-local conversion on a host ahead of UTC pushes this into
# 2026-08-09.
_NEAR_MIDNIGHT_UTC = datetime(2026, 8, 8, 23, 45, 0, tzinfo=timezone.utc)
_TIMESTAMP_MS = int(_NEAR_MIDNIGHT_UTC.timestamp() * 1000)
_EXPECTED_DATE = datetime(2026, 8, 8, 23, 45, 0)


def test_fetch_dvol_date_uses_utc_calendar_day_not_host_local():
    collector = _make_collector()
    collector.api.get_volatility_index_data.return_value = {
        "data": [[_TIMESTAMP_MS, 50.0, 51.0, 49.0, 50.5]]
    }

    with patch(
        "coding.service.data_collection.prospective_collector.datetime",
        _HostOffsetDateTime,
    ):
        collector._fetch_dvol("BTC")

    collector.repo.save_dvol.assert_called_once()
    saved_date = collector.repo.save_dvol.call_args.kwargs["date"]
    assert saved_date == _EXPECTED_DATE, (
        f"expected UTC calendar date {_EXPECTED_DATE}, got {saved_date} -- "
        "_fetch_dvol is converting the DVOL timestamp on the host's local "
        "clock instead of UTC"
    )
    assert saved_date.day == 8, "leaked into the following UTC calendar day"


def test_fetch_funding_rate_date_uses_utc_calendar_day_not_host_local():
    collector = _make_collector()
    collector.api.get_ticker.return_value = {
        "funding_8h": 0.01,
        "timestamp": _TIMESTAMP_MS,
    }

    with patch(
        "coding.service.data_collection.prospective_collector.datetime",
        _HostOffsetDateTime,
    ):
        collector._fetch_funding_rate("BTC")

    collector.repo.save_funding_rate.assert_called_once()
    saved_date = collector.repo.save_funding_rate.call_args.kwargs["date"]
    assert saved_date == _EXPECTED_DATE, (
        f"expected UTC calendar date {_EXPECTED_DATE}, got {saved_date} -- "
        "_fetch_funding_rate is converting the ticker timestamp on the "
        "host's local clock instead of UTC"
    )
    assert saved_date.day == 8, "leaked into the following UTC calendar day"
