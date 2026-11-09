"""
Compare the latest MenthorQ-style record with an earlier one (the previous
run, 24 hours ago, 7 days ago).

Conventions:
  - Every "%" here is percent of the LATEST spot (e.g. a 3,000 USD HVL move
    with spot 100,000 is 3.0%), so level moves, distances and the alert
    thresholds share one unit.
  - An expiry "settled since" the comparison point is one that was in the
    earlier record's expiry list and whose 08:00 UTC settlement lies in
    (earlier as_of, latest as_of]. When any expiry settled in between, a
    level change is labelled as coinciding with that roll-off: the open
    interest of that expiry left the sum, which can move a level on its own.
    The label states the coincidence; it does not prove the cause.
  - A "crossing" of HVL is a change of gamma regime (spot above HVL <->
    below HVL) between the two records, whether spot or HVL moved.

Pure: no database, network or plotting imports.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from coding.core.analytics.menthorq_monitor.level_record import (
    GAMMA_REGIME_NEGATIVE,
    GAMMA_REGIME_POSITIVE,
    LEVEL_NAMES,
    MenthorqLevelRecord,
)
from coding.core.analytics.menthorq_replication_calculator import parse_expiry_settlement

COMPARISON_PREVIOUS = "previous"
COMPARISON_ONE_DAY = "24h"
COMPARISON_SEVEN_DAYS = "7d"
# How far back the "24h" comparison looks (its reference record is at or before
# latest minus this offset).
ONE_DAY_COMPARISON_OFFSET = timedelta(hours=24)

CROSSED_BELOW_HVL = "crossed_below_hvl"   # positive -> negative gamma
CROSSED_ABOVE_HVL = "crossed_above_hvl"   # negative -> positive gamma


@dataclass(frozen=True)
class LevelChange:
    """One level in the earlier and the latest record."""

    reference_value: Optional[float]
    latest_value: Optional[float]
    change_usd: Optional[float]
    change_percent_of_spot: Optional[float]


@dataclass
class RecordComparison:
    """
    The latest record against one earlier record. ``reference`` is None
    when no earlier record exists for that comparison point; every field
    is then None or empty.
    """

    label: str
    reference: Optional[MenthorqLevelRecord]
    level_changes: Dict[str, LevelChange] = field(default_factory=dict)
    spot_change_usd: Optional[float] = None
    spot_change_percent: Optional[float] = None
    net_gex_change: Optional[float] = None
    net_gex_sign_flipped: Optional[bool] = None
    regime_before: Optional[str] = None
    regime_after: Optional[str] = None
    hvl_crossing: Optional[str] = None
    settled_expiries: List[str] = field(default_factory=list)
    new_expiries: List[str] = field(default_factory=list)

    @property
    def has_expiry_roll(self) -> bool:
        """True when an expiry of the earlier record settled in between."""
        return bool(self.settled_expiries)


def percent_of_spot(value_usd: Optional[float], spot: Optional[float]) -> Optional[float]:
    """``value_usd`` as percent of ``spot``; None when either is missing or spot <= 0."""
    if value_usd is None or spot is None or spot <= 0:
        return None
    return 100.0 * value_usd / spot


def settled_expiries_between(
    expirations: List[str], start_exclusive: datetime, end_inclusive: datetime
) -> List[str]:
    """Expiries whose 08:00 UTC settlement lies in (start, end], chronological."""
    settled = []
    for expiration in expirations:
        settlement = parse_expiry_settlement(expiration)
        if settlement is not None and start_exclusive < settlement <= end_inclusive:
            settled.append((settlement, expiration))
    return [expiration for _, expiration in sorted(settled)]


def _difference(latest: Optional[float], reference: Optional[float]) -> Optional[float]:
    if latest is None or reference is None:
        return None
    return latest - reference


def _sign_flipped(latest: Optional[float], reference: Optional[float]) -> Optional[bool]:
    """True when both are non-zero with opposite signs; None when either is missing."""
    if latest is None or reference is None:
        return None
    return (latest > 0 > reference) or (latest < 0 < reference)


def _hvl_crossing(regime_before: Optional[str], regime_after: Optional[str]) -> Optional[str]:
    if regime_before == GAMMA_REGIME_POSITIVE and regime_after == GAMMA_REGIME_NEGATIVE:
        return CROSSED_BELOW_HVL
    if regime_before == GAMMA_REGIME_NEGATIVE and regime_after == GAMMA_REGIME_POSITIVE:
        return CROSSED_ABOVE_HVL
    return None


def compare_records(
    latest: MenthorqLevelRecord, reference: Optional[MenthorqLevelRecord], label: str
) -> RecordComparison:
    """
    Changes from ``reference`` to ``latest``.

    Args:
        latest: The newest record.
        reference: An earlier record of the same currency, mode and
            universe, or None.
        label: "previous", "24h" or "7d".

    Raises:
        ValueError: when ``reference`` is not earlier than ``latest``.
    """
    if reference is None:
        return RecordComparison(label=label, reference=None)
    if reference.as_of_utc >= latest.as_of_utc:
        raise ValueError("reference must be earlier than latest")

    level_changes = {}
    for level_name in LEVEL_NAMES:
        change = _difference(latest.level(level_name), reference.level(level_name))
        level_changes[level_name] = LevelChange(
            reference_value=reference.level(level_name),
            latest_value=latest.level(level_name),
            change_usd=change,
            change_percent_of_spot=percent_of_spot(change, latest.spot),
        )
    spot_change = _difference(latest.spot, reference.spot)
    return RecordComparison(
        label=label,
        reference=reference,
        level_changes=level_changes,
        spot_change_usd=spot_change,
        spot_change_percent=(100.0 * spot_change / reference.spot
                             if spot_change is not None and reference.spot else None),
        net_gex_change=_difference(latest.total_net_gex, reference.total_net_gex),
        net_gex_sign_flipped=_sign_flipped(latest.total_net_gex, reference.total_net_gex),
        regime_before=reference.gamma_regime,
        regime_after=latest.gamma_regime,
        hvl_crossing=_hvl_crossing(reference.gamma_regime, latest.gamma_regime),
        settled_expiries=settled_expiries_between(
            reference.expirations, reference.as_of_utc, latest.as_of_utc),
        new_expiries=[expiration for expiration in latest.expirations
                      if expiration not in reference.expirations],
    )


@dataclass(frozen=True)
class SpotDistance:
    """Signed distance from spot to a level: level - spot (positive = level above spot)."""

    level_value: Optional[float]
    distance_usd: Optional[float]
    distance_percent_of_spot: Optional[float]


def spot_distances(record: MenthorqLevelRecord) -> Dict[str, SpotDistance]:
    """Distance from spot to Call Resistance, Put Support and HVL."""
    distances = {}
    for level_name in LEVEL_NAMES:
        value = record.level(level_name)
        distance = _difference(value, record.spot)
        distances[level_name] = SpotDistance(
            level_value=value,
            distance_usd=distance,
            distance_percent_of_spot=percent_of_spot(distance, record.spot),
        )
    return distances
