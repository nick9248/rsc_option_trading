"""
Scores real liquidation events against the level snapshots that were stored BEFORE them (no hindsight).

For each event the latest snapshot computed at or before the event (and recent enough) gives a percentile (see
``level_percentile``). The price of an OKX event is its BANKRUPTCY price, while the model's levels are TRIGGER prices
(they include the maintenance margin): for a long the trigger is above the bankruptcy price, for a short below. The event
price is therefore moved by ``price_shift`` (default 0.4 %, the model's maintenance margin rate) toward the trigger
before it is scored. The same events are also scored against a snapshot computed ``placebo``
earlier (a stale heatmap): if the current snapshot is not clearly better than a stale one, the levels carry no
information about where liquidations happen. The two are compared only on events that have BOTH scores. Chance level is a mean percentile of 0.5 and a top-decile share of 0.1.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.liquidation_heatmap.level_snapshot import (
    LevelSnapshot,
    PoolIntensity,
    RecordedLiquidation,
    build_pool,
    percentile_in_pool,
)

MAXIMUM_SNAPSHOT_AGE = timedelta(hours=2)
TOP_DECILE = 0.9
TRIGGER_PRICE_SHIFT = 0.004      # bankruptcy price -> trigger price: the model's maintenance margin rate


@dataclass(frozen=True)
class ScoringResult:
    """The scored events and how many were left out, by reason."""

    scored: List["ScoredEvent"]
    without_snapshot: int       # no snapshot computed in the 2 hours before the event (a recorder outage looks like this)
    outside_pool: int           # the event's price is not on its side of the snapshot's reference price within 5 %


@dataclass(frozen=True)
class ScoredEvent:
    """A real liquidation with its percentile against the current snapshot and against a stale one."""

    event: RecordedLiquidation
    percentile: float
    placebo_percentile: Optional[float]


def snapshot_before(snapshots: Sequence[LevelSnapshot], moment: datetime,
                    maximum_age: timedelta = MAXIMUM_SNAPSHOT_AGE,
                    times: Optional[Sequence[datetime]] = None) -> Optional[LevelSnapshot]:
    """
    The latest snapshot computed at or before ``moment`` and not older than ``maximum_age`` (snapshots in time order).
    ``times`` is the list of their ``computed_at`` when the caller has it already (it is rebuilt otherwise).
    """
    index = bisect_right(times if times is not None else [snapshot.computed_at for snapshot in snapshots], moment) - 1
    if index < 0 or moment - snapshots[index].computed_at > maximum_age:
        return None
    return snapshots[index]


def trigger_price(event: RecordedLiquidation, price_shift: float) -> float:
    """The event's price moved from the bankruptcy price toward the trigger price: up for a long, down for a short."""
    return event.price * (1 + price_shift) if event.liquidated_side == "long" else event.price * (1 - price_shift)


def score_events(events: Sequence[RecordedLiquidation], snapshots: Sequence[LevelSnapshot],
                 placebo: timedelta = timedelta(hours=24),
                 price_shift: float = TRIGGER_PRICE_SHIFT) -> ScoringResult:
    """
    Score each event that has a recent snapshot before it and a price inside that snapshot's pool; the others are
    counted by reason in the result.

    Args:
        events: Real liquidations (their prices are bankruptcy prices).
        snapshots: Stored level snapshots.
        placebo: Age of the stale snapshot used as the control.
        price_shift: Share by which the event price is moved toward the trigger price (see the module docstring).
    """
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.computed_at)
    times = [snapshot.computed_at for snapshot in ordered]
    pools: Dict[Tuple[datetime, str], Optional[PoolIntensity]] = {}      # one pool per snapshot and side, reused by every event

    def pool_of(snapshot: LevelSnapshot, side: str) -> Optional[PoolIntensity]:
        key = (snapshot.computed_at, side)
        if key not in pools:
            pools[key] = build_pool(snapshot.levels, side, snapshot.reference_price, snapshot.bin_size_usd)
        return pools[key]

    scored: List[ScoredEvent] = []
    without_snapshot = outside_pool = 0
    for event in events:
        current = snapshot_before(ordered, event.event_time, times=times)
        if current is None:
            without_snapshot += 1
            continue
        price = trigger_price(event, price_shift)
        value = percentile_in_pool(pool_of(current, event.liquidated_side), price)
        if value is None:
            outside_pool += 1
            continue
        stale = snapshot_before(ordered, event.event_time - placebo, times=times)
        stale_value = percentile_in_pool(pool_of(stale, event.liquidated_side), price) if stale is not None else None
        scored.append(ScoredEvent(event, value, stale_value))
    return ScoringResult(scored, without_snapshot, outside_pool)


def summarise_scores(scored: Sequence[ScoredEvent]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Per side and overall: number of events, mean percentile, share of events in the top decile and the USD-weighted
    mean percentile; and the paired comparison with the stale-snapshot control, computed on the SAME events (those
    that have both scores): their number, the mean percentile and top-decile share of the current snapshot, and the same
    two for the stale one.
    """
    summary: Dict[str, Dict[str, Optional[float]]] = {}
    for label, sides in (("long", ("long",)), ("short", ("short",)), ("all", ("long", "short"))):
        rows = [item for item in scored if item.event.liquidated_side in sides]
        paired = [item for item in rows if item.placebo_percentile is not None]
        controls = [item.placebo_percentile for item in paired]
        total_usd = sum(item.event.usd for item in rows)

        def mean(values: Sequence[float]) -> Optional[float]:
            return sum(values) / len(values) if values else None

        def top_share(values: Sequence[float]) -> Optional[float]:
            return sum(value >= TOP_DECILE for value in values) / len(values) if values else None

        summary[label] = {
            "events": len(rows),
            "mean_percentile": mean([item.percentile for item in rows]),
            "top_decile_share": top_share([item.percentile for item in rows]),
            "usd_weighted_mean": (sum(item.percentile * item.event.usd for item in rows) / total_usd) if total_usd else None,
            "paired_events": len(paired),
            "paired_mean_percentile": mean([item.percentile for item in paired]),
            "paired_top_decile_share": top_share([item.percentile for item in paired]),
            "control_mean_percentile": mean([value for value in controls if value is not None]),
            "control_top_decile_share": top_share([value for value in controls if value is not None]),
        }
    return summary
