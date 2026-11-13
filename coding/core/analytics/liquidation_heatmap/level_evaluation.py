"""
Scores real liquidation events against the level snapshots that were stored BEFORE them (no hindsight).

For each event the latest snapshot computed at or before ``event_time - latency`` (the snapshot must be recent enough)
gives a percentile (see ``level_percentile``). The same events are also scored against a snapshot computed ``placebo``
earlier (a stale heatmap): if the current snapshot is not clearly better than a stale one, the levels carry no
information about where liquidations happen. Chance level is a mean percentile of 0.5 and a top-decile share of 0.1.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from coding.core.analytics.liquidation_heatmap.level_snapshot import (
    LevelSnapshot,
    RecordedLiquidation,
    level_percentile,
)

MAXIMUM_SNAPSHOT_AGE = timedelta(hours=2)
TOP_DECILE = 0.9


@dataclass(frozen=True)
class ScoredEvent:
    """A real liquidation with its percentile against the current snapshot and against a stale one."""

    event: RecordedLiquidation
    percentile: float
    placebo_percentile: Optional[float]


def snapshot_before(snapshots: Sequence[LevelSnapshot], moment: datetime,
                    maximum_age: timedelta = MAXIMUM_SNAPSHOT_AGE) -> Optional[LevelSnapshot]:
    """The latest snapshot computed at or before ``moment`` and not older than ``maximum_age`` (snapshots in time order)."""
    index = bisect_right([snapshot.computed_at for snapshot in snapshots], moment) - 1
    if index < 0 or moment - snapshots[index].computed_at > maximum_age:
        return None
    return snapshots[index]


def score_events(events: Sequence[RecordedLiquidation], snapshots: Sequence[LevelSnapshot],
                 placebo: timedelta = timedelta(hours=24)) -> List[ScoredEvent]:
    """
    Score each event that has a recent snapshot before it and a price inside that snapshot's pool; events without one
    are left out (the caller counts them from the difference of lengths).
    """
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.computed_at)
    scored: List[ScoredEvent] = []
    for event in events:
        current = snapshot_before(ordered, event.event_time)
        if current is None:
            continue
        value = level_percentile(current.levels, event.liquidated_side, event.price, current.reference_price,
                                 current.bin_size_usd)
        if value is None:
            continue
        stale = snapshot_before(ordered, event.event_time - placebo)
        stale_value = (level_percentile(stale.levels, event.liquidated_side, event.price, stale.reference_price,
                                        stale.bin_size_usd) if stale is not None else None)
        scored.append(ScoredEvent(event, value, stale_value))
    return scored


def summarise_scores(scored: Sequence[ScoredEvent]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Per side and overall: number of events, mean percentile, share of events in the top decile and the same two
    numbers for the stale-snapshot control (on the events that have one), and the USD-weighted mean percentile.
    """
    summary: Dict[str, Dict[str, Optional[float]]] = {}
    for label, sides in (("long", ("long",)), ("short", ("short",)), ("all", ("long", "short"))):
        rows = [item for item in scored if item.event.liquidated_side in sides]
        if not rows:
            summary[label] = {"events": 0, "mean_percentile": None, "top_decile_share": None, "usd_weighted_mean": None,
                              "control_events": 0, "control_mean_percentile": None, "control_top_decile_share": None}
            continue
        controls = [item.placebo_percentile for item in rows if item.placebo_percentile is not None]
        total_usd = sum(item.event.usd for item in rows)
        summary[label] = {
            "events": len(rows),
            "mean_percentile": sum(item.percentile for item in rows) / len(rows),
            "top_decile_share": sum(item.percentile >= TOP_DECILE for item in rows) / len(rows),
            "usd_weighted_mean": (sum(item.percentile * item.event.usd for item in rows) / total_usd) if total_usd else None,
            "control_events": len(controls),
            "control_mean_percentile": (sum(controls) / len(controls)) if controls else None,
            "control_top_decile_share": (sum(value >= TOP_DECILE for value in controls) / len(controls)) if controls else None,
        }
    return summary
