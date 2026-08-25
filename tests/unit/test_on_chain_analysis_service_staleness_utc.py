"""
Regression test for OnChainAnalysisService._compute_historical_context_
staleness's comparison clock (Wave H Task H-F, Fix 1).

threshold = datetime.now(most_stale.tzinfo) - timedelta(hours=...) silently
degrades to the LOCAL clock whenever most_stale is naive (tzinfo is None) --
which it always is for this codebase's `timestamp without time zone`
columns (see repository.py's _TABLE_TIME_COLUMNS). datetime.now(tz=None) is
documented to return naive-LOCAL time, not naive-UTC, even though every
naive datetime this codebase writes/reads from those columns is UTC-valued
by convention.

On a UTC+2 host this shifts the 3h staleness threshold by 2h: data that is
genuinely only 2h stale (well inside the 3h threshold) gets compared
against a threshold that is effectively 1h in the *past* relative to real
UTC now, so 2h-old data reads as "older than threshold" and the STALE
prefix fires when it should not -- a false alarm. The fix pins the
comparison clock to an explicit UTC-valued naive `datetime.now(timezone.
utc).replace(tzinfo=None)`, matching this file's own
`_DELTA_FLOW_STALENESS_THRESHOLD_HOURS` / `now_utc_naive` pattern.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


class _FrozenDateTime(datetime):
    """
    Freezes `now` to a fixed UTC instant while simulating a UTC+2 host's
    naive local clock for the tz=None call -- mirrors
    test_prospective_collector_default_hour_utc.py's proven pattern.
    """
    _FIXED_UTC = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    _LOCAL_OFFSET_HOURS = 2

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls._FIXED_UTC.astimezone(tz)
        # Naive datetime.now() (no tz) -- simulates a UTC+2 host's local
        # wall clock, which reads 2 hours AHEAD of the real UTC instant.
        return cls._FIXED_UTC.replace(tzinfo=None) + timedelta(hours=cls._LOCAL_OFFSET_HOURS)


def test_staleness_threshold_uses_utc_not_local_clock_on_non_utc_host():
    """
    Data that is genuinely only 2h old (inside the 3h threshold) must NOT
    be flagged stale, even when the host's naive local clock reads 2h
    ahead of UTC. Before the fix, this exact scenario false-alarmed.
    """
    repo = MagicMock()
    real_utc_now = _FrozenDateTime._FIXED_UTC.replace(tzinfo=None)
    two_hours_stale = real_utc_now - timedelta(hours=2)
    repo.get_metric_freshness.return_value = two_hours_stale

    service = _make_service(repo)

    with patch(
        "coding.service.on_chain.on_chain_analysis_service.datetime", _FrozenDateTime
    ):
        result = service._compute_historical_context_staleness(
            currency="BTC",
            front_month="8AUG26",
            tables_used=[("onchain_analysis_snapshots", "8AUG26")],
        )

    assert result is None, (
        f"expected no staleness flag for 2h-old data against a 3h threshold, "
        f"got {result!r} -- comparison clock is using the local host offset "
        f"instead of UTC"
    )


def test_staleness_threshold_still_fires_for_genuinely_stale_data():
    """Sanity check the opposite direction: data older than the threshold
    (against the real UTC clock) must still be flagged, so the fix isn't
    just suppressing the gate entirely."""
    repo = MagicMock()
    real_utc_now = _FrozenDateTime._FIXED_UTC.replace(tzinfo=None)
    four_hours_stale = real_utc_now - timedelta(hours=4)
    repo.get_metric_freshness.return_value = four_hours_stale

    service = _make_service(repo)

    with patch(
        "coding.service.on_chain.on_chain_analysis_service.datetime", _FrozenDateTime
    ):
        result = service._compute_historical_context_staleness(
            currency="BTC",
            front_month="8AUG26",
            tables_used=[("onchain_analysis_snapshots", "8AUG26")],
        )

    assert result == four_hours_stale
