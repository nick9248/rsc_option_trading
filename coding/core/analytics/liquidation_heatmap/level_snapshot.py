"""
The levels of a heatmap state, as stored in an hourly snapshot, and the score of a real liquidation against them.

A snapshot keeps the bins within +-10 % of the reference price as ``[price, long_usd, short_usd]`` rows (USD at the
level's price). A real liquidation event is scored by the percentile of the snapshot's intensity at the event's
price among all prices of the same side near the reference price: 0.5 is what a random price would get (no skill),
close to 1 means the event happened at one of the strongest levels. Only snapshots computed BEFORE the event are
used (see the evaluation script), so there is no hindsight.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

import numpy as np

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import HeatmapResult

SNAPSHOT_WINDOW = 0.10          # share of the reference price kept in a snapshot
SCORE_WINDOW_PERCENT = 5.0      # prices within this percent of the reference are the "random price" pool
SMOOTHING_USD = 100.0           # an event counts the levels within this distance


@dataclass(frozen=True)
class RecordedLiquidation:
    """A real liquidation of a perpetual: the side that was liquidated, the price and the size."""

    venue: str
    instrument: str
    event_time: datetime
    liquidated_side: str        # "long" (a forced sell) or "short" (a forced buy)
    price: float
    size_btc: float
    raw_size: float             # the venue's own size unit (contracts)

    @property
    def usd(self) -> float:
        """Size times price."""
        return self.size_btc * self.price


@dataclass(frozen=True)
class LevelSnapshot:
    """One stored hourly snapshot of the estimated levels (point in time)."""

    symbol: str
    as_of_utc: datetime
    computed_at: datetime
    reference_price: float
    bin_size_usd: float
    levels: List[List[float]]


def extract_levels(result: HeatmapResult, row: int, reference_price: float,
                   window: float = SNAPSHOT_WINDOW) -> List[List[float]]:
    """
    The non-empty bins of a recorded step within ``window`` of ``reference_price`` as
    ``[price, long_usd, short_usd]`` rows, lowest price first. An empty list when the row has no data.
    """
    long_btc, short_btc = result.long_btc[row], result.short_btc[row]
    if not np.isfinite(long_btc).any():
        return []
    centers = result.bin_centers
    long_usd, short_usd = long_btc * centers, short_btc * centers
    keep = (np.abs(centers - reference_price) <= window * reference_price) & ((long_usd > 0) | (short_usd > 0))
    return [[round(float(centers[i]), 1), round(float(long_usd[i]), 2), round(float(short_usd[i]), 2)]
            for i in np.where(keep)[0]]


def _intensity_at(prices: np.ndarray, values: np.ndarray, at: float, smoothing: float) -> float:
    return float(values[np.abs(prices - at) <= smoothing].sum())


def level_percentile(
    levels: Sequence[Sequence[float]], side: str, price: float, reference_price: float,
    bin_size_usd: float, window_percent: float = SCORE_WINDOW_PERCENT, smoothing_usd: float = SMOOTHING_USD,
) -> Optional[float]:
    """
    Percentile (0 to 1) of the side's smoothed level intensity at ``price`` among the prices of the pool
    (a grid of ``bin_size_usd`` steps within ``window_percent`` of ``reference_price``); ties count half.
    None when the price is outside the pool, there are no levels or the side is unknown.
    """
    if side not in ("long", "short") or not levels:
        return None
    if abs(price - reference_price) > window_percent / 100 * reference_price:
        return None
    array = np.asarray(levels, dtype=float)
    prices, values = array[:, 0], array[:, 1 if side == "long" else 2]
    pool = np.arange(reference_price * (1 - window_percent / 100), reference_price * (1 + window_percent / 100) + 1e-9,
                     bin_size_usd)
    pool_intensity = np.array([_intensity_at(prices, values, float(point), smoothing_usd) for point in pool])
    event_intensity = _intensity_at(prices, values, price, smoothing_usd)
    return float(((pool_intensity < event_intensity).sum() + 0.5 * (pool_intensity == event_intensity).sum()) / len(pool))
