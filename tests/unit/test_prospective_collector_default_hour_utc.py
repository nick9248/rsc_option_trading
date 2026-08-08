"""
Regression test for ProspectiveCollector.collect_hour()'s default hour
computation (Wave G fresh-audit finding, metric-verification agent).

collect_hour(hour=None) used to default to naive-local datetime.now() --
the exact banned bug class this campaign has fixed a dozen times elsewhere.
Dormant on the VPS only because its OS clock happens to already be UTC
(confirmed Task C7); reproduced live on a UTC+2 host, where a test run at
02:04 local (00:04 UTC) wrote snapshot_hour=02:00 instead of 00:00 -- a
real, silent 2-hour mislabel of the daemon's core hour-anchor whenever this
method runs without an explicit hour on a non-UTC host.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def _make_collector():
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    collector.aggregation_service.aggregate_unaggregated_hours.return_value = {"snapshots_created": 0}
    collector._forward_harness = MagicMock()
    collector._volatility_reconstruction = MagicMock()
    return collector


def test_default_hour_uses_utc_not_local_clock():
    """
    Freeze the module's clock to a fixed UTC instant on a host whose local
    tzinfo would differ (Europe/Berlin, UTC+2) and assert the resolved hour
    is the UTC hour, not the local one -- proving the fix by construction
    rather than by trusting the file diff.
    """
    fixed_utc = datetime(2026, 8, 8, 0, 4, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return fixed_utc.astimezone(tz)
            # Naive datetime.now() (no tz) -- simulates a host whose local
            # wall clock reads 2 hours ahead of UTC (Europe/Berlin summer).
            return fixed_utc.replace(tzinfo=None) + (
                fixed_utc.astimezone().utcoffset() or __import__("datetime").timedelta(0)
            )

    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 0, "instruments": 0})

    with patch(
        "coding.service.data_collection.prospective_collector.datetime", _FrozenDateTime
    ):
        result = collector.collect_hour(currencies=["BTC"])

    calls = collector._collect_currency.call_args_list
    assert len(calls) == 1
    called_hour = calls[0].args[1] if len(calls[0].args) > 1 else calls[0].kwargs.get("hour")
    assert called_hour == datetime(2026, 8, 8, 0, 0, 0), (
        f"expected the UTC hour 00:00, got {called_hour} -- default hour "
        "computation is using the local clock, not UTC"
    )


def test_explicit_hour_is_never_overridden():
    """An explicitly-passed hour must pass through untouched regardless of
    the default-computation fix above."""
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 0, "instruments": 0})

    explicit_hour = datetime(2026, 7, 13, 14, 0, 0)
    collector.collect_hour(currencies=["BTC"], hour=explicit_hour)

    calls = collector._collect_currency.call_args_list
    called_hour = calls[0].args[1] if len(calls[0].args) > 1 else calls[0].kwargs.get("hour")
    assert called_hour == explicit_hour
