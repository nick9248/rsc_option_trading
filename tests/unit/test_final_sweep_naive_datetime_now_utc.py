"""
Regression tests for the final recurring-pattern sweep done at the close
of the onchain-overhaul campaign (post Wave J): a handful of naive
`datetime.now()` sites (host-local, ambiguous during DST / off by the
host's UTC offset) that the campaign's many prior fix rounds never
reached, because they live in files/methods those rounds' scopes didn't
cover.

Each site is fixed to `datetime.now(timezone.utc)` (kept aware, for a
site only ever consumed via `.timestamp()`) or
`datetime.now(timezone.utc).replace(tzinfo=None)` (for a naive `TIMESTAMP`
DB column, matching this codebase's established convention -- see e.g.
Wave-J-E's identical fix for hourly_snapshots.captured_at).

Simulates a host 5 hours ahead of UTC (deliberately synthetic, not the
real host offset, so these tests are host-independent).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository
from coding.service.data_collection.prospective_collector import ProspectiveCollector


_FAKE_LOCAL_OFFSET = timedelta(hours=5)
# A fixed UTC instant close to a calendar-day boundary, so a naive-local
# conversion on a host ahead of UTC would visibly land on the wrong day.
_FIXED_UTC = datetime(2026, 8, 24, 23, 0, 0, tzinfo=timezone.utc)


class _HostOffsetDateTime(datetime):
    """datetime subclass whose naive now() simulates a host 5h ahead of
    UTC, while its aware now(tz=...) still returns the real UTC instant --
    the exact discrimination the fix under test relies on."""

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _FIXED_UTC.astimezone(tz)
        return (_FIXED_UTC + _FAKE_LOCAL_OFFSET).replace(tzinfo=None)


class _FakeCursor:
    """Minimal cursor stub recording whatever was passed to execute/executemany."""

    def __init__(self, captured: dict):
        self._captured = captured

    def execute(self, sql, params=None):
        self._captured["execute_params"] = params

    def executemany(self, sql, rows):
        self._captured["executemany_rows"] = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestRepositorySaveSnapshotCapturedAtUTC:
    def test_default_captured_at_is_utc_not_host_local(self):
        repo = DatabaseRepository.__new__(DatabaseRepository)
        captured = {}
        repo._db_cursor = MagicMock(return_value=_FakeCursor(captured))

        with patch("coding.core.database.repository.datetime", _HostOffsetDateTime):
            repo.save_snapshot(
                currency="BTC",
                data=[{"instrument_name": "BTC-1JAN27-100-C", "open_interest": 1.0}],
            )

        rows = captured["executemany_rows"]
        assert len(rows) == 1
        captured_at = rows[0][0]
        assert captured_at == _FIXED_UTC.replace(tzinfo=None), (
            f"expected UTC-valued naive captured_at {_FIXED_UTC.replace(tzinfo=None)}, "
            f"got {captured_at} -- default is using the host-local clock, not UTC"
        )

    def test_explicit_captured_at_is_never_overridden(self):
        repo = DatabaseRepository.__new__(DatabaseRepository)
        captured = {}
        repo._db_cursor = MagicMock(return_value=_FakeCursor(captured))
        explicit = datetime(2026, 1, 1, 12, 0, 0)

        repo.save_snapshot(
            currency="BTC",
            data=[{"instrument_name": "BTC-1JAN27-100-C", "open_interest": 1.0}],
            captured_at=explicit,
        )

        assert captured["executemany_rows"][0][0] == explicit


class TestProspectiveCollectorFetchBookSummaryCapturedAtUTC:
    def test_fetch_book_summary_passes_utc_captured_at(self):
        collector = ProspectiveCollector.__new__(ProspectiveCollector)
        collector.api = MagicMock()
        collector.api.get_book_summary.return_value = []
        collector.repo = MagicMock()
        collector.repo.save_snapshot.return_value = 0

        with patch("coding.service.data_collection.prospective_collector.datetime", _HostOffsetDateTime):
            collector._fetch_book_summary("BTC", hour=datetime(2026, 8, 24, 23, 0, 0))

        _, kwargs = collector.repo.save_snapshot.call_args
        assert kwargs["captured_at"] == _FIXED_UTC.replace(tzinfo=None), (
            f"expected UTC-valued naive captured_at, got {kwargs.get('captured_at')} -- "
            "call site is using the host-local clock, not UTC"
        )
