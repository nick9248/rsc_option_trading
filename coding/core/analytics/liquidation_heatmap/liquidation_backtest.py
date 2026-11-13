"""
Pure pieces of the liquidation heatmap backtest against real liquidations.

The test is conditional: for each UTC day, the heatmap state known at the start of the day (positions created
before it, nothing later) is swept by the day's REAL price range, and the liquidation volume the levels
would give is compared with the real liquidations of that day. It tests whether the levels are right, not
whether the price path can be predicted. A level is swept by the day's low (longs) or high (shorts).

Because liquidations grow with the size of the move and with volume anyway, a heatmap only counts if it adds
information beyond a naive predictor (move size times volume): see ``partial_spearman``.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Sequence, Tuple, cast

import numpy as np

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, PriceCandle

CANDLES_PER_DAY = int(timedelta(days=1) / STEP)


@dataclass(frozen=True)
class DayRow:
    """One UTC day: what the heatmap predicts, the naive controls and the real liquidations (USD)."""

    day: date
    predicted_long_usd: float
    predicted_short_usd: float
    drop_percent: float             # (open - low) / open * 100, 0 when the low is not below the open
    rise_percent: float             # (high - open) / open * 100, 0 when the high is not above the open
    volume_usd: float               # traded USD of the day
    previous_volume_usd: float      # traded USD of the day before (known at the start of the day)
    real_long_usd: float
    real_short_usd: float


def swept_levels_usd(
    long_btc: np.ndarray, short_btc: np.ndarray, centers: np.ndarray,
    open_price: float, low: float, high: float,
) -> Tuple[float, float]:
    """
    Liquidation volume (USD at the level's price) of the long levels the day's low reached and of the
    short levels its high reached, from the state at the start of the day.

    Long levels count when ``low <= level < open_price``, short levels when ``open_price < level <= high``.
    """
    long_mask = (centers >= low) & (centers < open_price)
    short_mask = (centers > open_price) & (centers <= high)
    long_usd = float(np.nansum(long_btc[long_mask] * centers[long_mask]))
    short_usd = float(np.nansum(short_btc[short_mask] * centers[short_mask]))
    return long_usd, short_usd


def _ranks(values: np.ndarray) -> np.ndarray:
    from scipy.stats import rankdata
    return rankdata(values)


def spearman(x: Sequence[float], y: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """Spearman rank correlation and its p-value; (None, None) when it is undefined (constant input, < 3 points)."""
    from scipy.stats import spearmanr
    x_array, y_array = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x_array) < 3 or np.ptp(x_array) == 0 or np.ptp(y_array) == 0:
        return None, None
    correlation, p_value = cast(Any, spearmanr(x_array, y_array))      # scipy's stub types the result loosely
    return float(correlation), float(p_value)


def partial_spearman(
    x: Sequence[float], y: Sequence[float], control: Sequence[float],
) -> Tuple[Optional[float], Optional[float]]:
    """
    Rank correlation of ``x`` and ``y`` after removing what ``control`` explains of each (linear on ranks).
    Returns the correlation of the two residuals and its p-value (n - 3 degrees of freedom), or (None, None)
    when it is undefined.
    """
    from scipy.stats import t as student
    rank_x, rank_y, rank_c = _ranks(np.asarray(x, float)), _ranks(np.asarray(y, float)), _ranks(np.asarray(control, float))
    count = len(rank_x)
    if count < 5 or np.ptp(rank_x) == 0 or np.ptp(rank_y) == 0:
        return None, None

    def residual(values: np.ndarray) -> np.ndarray:
        if np.ptp(rank_c) == 0:
            return values - values.mean()
        slope = np.cov(values, rank_c, bias=True)[0, 1] / np.var(rank_c)
        return values - slope * rank_c - (values.mean() - slope * rank_c.mean())

    residual_x, residual_y = residual(rank_x), residual(rank_y)
    denominator = np.sqrt((residual_x ** 2).sum() * (residual_y ** 2).sum())
    if denominator == 0:
        return None, None
    correlation = float((residual_x * residual_y).sum() / denominator)
    if abs(correlation) >= 1:
        return correlation, 0.0
    statistic = correlation * np.sqrt((count - 3) / (1 - correlation ** 2))
    return correlation, float(2 * student.sf(abs(statistic), count - 3))


@dataclass(frozen=True)
class DayPrices:
    """Open (the previous close), low, high and traded USD of one complete UTC day."""

    open_price: float
    low: float
    high: float
    volume_usd: float


def daily_price_stats(candles: Sequence[PriceCandle]) -> Dict[date, DayPrices]:
    """
    Per UTC day from 5-minute candles. Only COMPLETE days (all 288 candles, and the candle before the day so
    that the open is known) are returned; the open is the previous candle's close. Traded USD is the sum of
    candle volume times candle close (an approximation of the quote volume).
    """
    by_time = {candle.open_time: candle for candle in candles}
    by_day: Dict[date, list] = {}
    for candle in candles:
        by_day.setdefault(candle.open_time.date(), []).append(candle)
    days: Dict[date, DayPrices] = {}
    for day, day_candles in by_day.items():
        if len(day_candles) != CANDLES_PER_DAY or any(candle.volume_btc is None for candle in day_candles):
            continue
        day_start = datetime(day.year, day.month, day.day, tzinfo=day_candles[0].open_time.tzinfo)
        previous = by_time.get(day_start - STEP)
        if previous is None:
            continue
        days[day] = DayPrices(
            open_price=previous.close,
            low=min(candle.low for candle in day_candles),
            high=max(candle.high for candle in day_candles),
            volume_usd=sum((candle.volume_btc or 0.0) * candle.close for candle in day_candles),
        )
    return days
