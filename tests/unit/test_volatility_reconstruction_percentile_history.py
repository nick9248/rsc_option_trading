"""
Pass 2 of volatility reconstruction (per-expiry IV percentile) must read the
trailing 90-day history even when the requested range is a single hour, but
only write rows inside the requested range.

Regression: the hourly collector calls reconstruct_range(start=hour, end=hour).
Pass 2 used to read only rows inside [start, end], so each expiry had one row,
never reached IV_PERCENTILE_EXPIRY_MIN_HISTORY, and iv_percentile_expiry stayed
NULL in every row.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from coding.service.on_chain.volatility_reconstruction_service import (
    VolatilityReconstructionService,
)

EXPIRATION = "27DEC26"
FIRST_HOUR = datetime(2026, 10, 1, 0, 0, 0)  # naive UTC, per the database convention


class InMemoryRepository:
    """Minimal stand-in: serves atm_iv rows by range and records percentile updates."""

    def __init__(self, atm_iv_values):
        self.rows = [
            {"snapshot_hour": FIRST_HOUR + timedelta(hours=index), "expiration": EXPIRATION, "atm_iv": value}
            for index, value in enumerate(atm_iv_values)
        ]
        self.updates = {}

    def get_volatility_snapshots_for_percentile_backfill(self, currency, start, end):
        return [row for row in self.rows if start <= row["snapshot_hour"] <= end]

    def update_iv_percentile_expiry(self, snapshot_hour, currency, expiration, iv_percentile_expiry):
        self.updates[snapshot_hour] = iv_percentile_expiry


def _service(repository):
    service = VolatilityReconstructionService.__new__(VolatilityReconstructionService)
    service.repo = repository
    return service


def test_single_hour_range_uses_earlier_history():
    # Hours 0..5 have atm_iv 50, 60, 40, 70, 55, then the target hour 5 has 65.
    repository = InMemoryRepository([50.0, 60.0, 40.0, 70.0, 55.0, 65.0])
    target_hour = FIRST_HOUR + timedelta(hours=5)

    updated = _service(repository)._backfill_iv_percentile_expiry(
        "BTC", target_hour, target_hour, lambda message: None
    )

    # History strictly before the target: 50, 60, 40, 70, 55. Below 65: 50, 60, 40, 55 -> 4/5.
    assert updated == 1
    assert repository.updates == {target_hour: 80.0}


def test_only_rows_inside_range_are_updated():
    repository = InMemoryRepository([50.0, 60.0, 40.0, 70.0, 55.0, 65.0, 45.0])
    start = FIRST_HOUR + timedelta(hours=5)
    end = FIRST_HOUR + timedelta(hours=6)

    _service(repository)._backfill_iv_percentile_expiry("BTC", start, end, lambda message: None)

    assert sorted(repository.updates) == [start, end]
    # Hour 6 (45.0): history is hours 0..5 = 50, 60, 40, 70, 55, 65; below 45 is only 40 -> 1/6.
    assert abs(repository.updates[end] - (1 / 6) * 100) < 1e-9


def test_history_older_than_ninety_days_is_ignored():
    # Five old observations 100 days before the target must not count as history.
    repository = InMemoryRepository([])
    target_hour = FIRST_HOUR
    for day in range(5):
        repository.rows.append({
            "snapshot_hour": target_hour - timedelta(days=100 + day),
            "expiration": EXPIRATION,
            "atm_iv": 50.0,
        })
    repository.rows.append({"snapshot_hour": target_hour, "expiration": EXPIRATION, "atm_iv": 60.0})

    updated = _service(repository)._backfill_iv_percentile_expiry(
        "BTC", target_hour, target_hour, lambda message: None
    )

    assert updated == 0
    assert repository.updates == {}


def test_too_little_history_writes_nothing():
    repository = InMemoryRepository([50.0, 60.0, 40.0, 70.0, 65.0])  # only 4 earlier rows
    target_hour = FIRST_HOUR + timedelta(hours=4)

    updated = _service(repository)._backfill_iv_percentile_expiry(
        "BTC", target_hour, target_hour, lambda message: None
    )

    assert updated == 0
