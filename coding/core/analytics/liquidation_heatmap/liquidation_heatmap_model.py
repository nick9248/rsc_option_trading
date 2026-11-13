"""
Estimated liquidation heatmap of a USDT-margined perpetual (research model).

No exchange publishes where each position gets liquidated, so this is a MODEL.
It follows the usual practitioner construction; every step below is an
assumption to be checked against Coinglass screenshots and real liquidations
(see documentation/liquidation_heatmap_research.md), not a verified fact:

1. Every 5-minute step, open interest (OI) in BTC is compared with the OI
   tracked so far. An increase creates new contracts at the step's closing
   price. Every contract has one long and one short side, so both sides get
   the same amount.
2. A new position is spread over leverage tiers (``leverage_tiers`` with
   ``leverage_weights``). Isolated-margin liquidation price (a simplification of
   Binance's formula: no fees, no maintenance tiers):
       long  = entry * (1 - 1/L + maintenance_margin_rate)
       short = entry * (1 + 1/L - maintenance_margin_rate)
3. A level is liquidated when the step's price range reaches it (a long level
   when the low is at or below it, a short level when the high is at or above
   it). A liquidated contract also closes its other side, so the opposite side
   shrinks by the same amount, proportionally over its levels.
4. What is left of an OI change after those liquidations is a voluntary change:
   a rise opens new contracts (step 1), a fall closes contracts proportionally
   on both sides.
5. Optional turnover (``position_half_life_hours``): contracts age out, halving
   every half-life. The aged-out contracts count as closed, so the OI that is still
   there is "new" and is placed at the current price (step 1). Without it every
   position since the warm-up lives until its level is touched.

Alternative creation (``creation_source="volume"``, experiment): positions are
created from traded volume instead of open-interest changes. Taker buy volume
opens longs and taker sell volume opens shorts (aggressive buyers open longs,
aggressive sellers open shorts), times ``volume_open_share`` (the share of volume
that opens positions; a scale factor). Open interest is not used, and a touched
level removes only its own side. The same tiers, touch rule and optional turnover
apply.

Positions that existed before the first step (warm-up) are unknown, so the
state starts empty: the first ``warmup`` steps only build it, and only later
steps are recorded. Missing data is never filled: a step without a candle is an
empty (NaN) row; a step without an OI sample creates and closes nothing (the
change shows up in the next sample).
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Collection, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

STEP = timedelta(minutes=5)


@dataclass(frozen=True)
class PriceCandle:
    """One 5-minute candle (open time, UTC-aware), prices in USDT."""

    open_time: datetime
    high: float
    low: float
    close: float
    volume_btc: Optional[float] = None        # traded volume of the candle (BTC)
    taker_buy_btc: Optional[float] = None     # part of it bought by takers (BTC)


@dataclass(frozen=True)
class HeatmapSettings:
    """
    The model's few settings. The defaults are a starting point (the lowest tier
    follows the price range Coinglass draws, about 3 to 4 % beyond the price
    extremes); they are to be calibrated, not believed.
    """

    bin_size_usd: float = 50.0
    leverage_tiers: Tuple[int, ...] = (25, 50, 100)
    leverage_weights: Tuple[float, ...] = (1 / 3, 1 / 3, 1 / 3)
    maintenance_margin_rate: float = 0.004
    position_half_life_hours: Optional[float] = None
    creation_source: str = "open_interest"      # or "volume"
    volume_open_share: float = 0.1

    def __post_init__(self) -> None:
        if self.bin_size_usd <= 0:
            raise ValueError("bin_size_usd must be positive")
        if len(self.leverage_tiers) == 0 or len(self.leverage_tiers) != len(self.leverage_weights):
            raise ValueError("leverage_tiers and leverage_weights must have the same, non-zero length")
        if any(tier <= 1 for tier in self.leverage_tiers):
            raise ValueError("every leverage tier must be above 1")
        if any(weight < 0 for weight in self.leverage_weights) or not math.isclose(
                sum(self.leverage_weights), 1.0, abs_tol=1e-9):
            raise ValueError("leverage_weights must be non-negative and sum to 1")
        if not 0 <= self.maintenance_margin_rate < 1 / max(self.leverage_tiers):
            raise ValueError("maintenance_margin_rate must be below 1 / the highest leverage tier")
        if self.position_half_life_hours is not None and self.position_half_life_hours <= 0:
            raise ValueError("position_half_life_hours must be positive")
        if self.creation_source not in ("open_interest", "volume"):
            raise ValueError("creation_source must be 'open_interest' or 'volume'")
        if not 0 < self.volume_open_share <= 1:
            raise ValueError("volume_open_share must be in (0, 1]")

    def long_liquidation_price(self, entry: float, leverage: int) -> float:
        """Liquidation price of a long opened at ``entry`` with ``leverage`` (isolated, simplified)."""
        return entry * (1 - 1 / leverage + self.maintenance_margin_rate)

    def short_liquidation_price(self, entry: float, leverage: int) -> float:
        """Liquidation price of a short opened at ``entry`` with ``leverage`` (isolated, simplified)."""
        return entry * (1 + 1 / leverage - self.maintenance_margin_rate)


@dataclass
class HeatmapResult:
    """
    The recorded window. Rows are 5-minute steps (``times``), columns are price
    bins (``bin_centers``). ``long_btc`` and ``short_btc`` hold the estimated
    contracts (BTC) whose liquidation level is in that bin after the step; a row
    without a candle is NaN.
    """

    times: List[datetime]
    bin_centers: np.ndarray
    long_btc: np.ndarray
    short_btc: np.ndarray
    notes: List[str] = field(default_factory=list)

    @property
    def intensity_usd(self) -> np.ndarray:
        """Long plus short contracts per bin and step, in USD at the level's price."""
        return (self.long_btc + self.short_btc) * self.bin_centers[np.newaxis, :]


def _bin_grid(candles: Sequence[PriceCandle], settings: HeatmapSettings) -> Tuple[float, np.ndarray]:
    """Origin and bin centers covering every level the tiers can produce."""
    lowest_tier = min(settings.leverage_tiers)
    reach = 1 / lowest_tier + 0.01
    low = min(candle.low for candle in candles) * (1 - reach)
    high = max(candle.high for candle in candles) * (1 + reach)
    origin = math.floor(low / settings.bin_size_usd) * settings.bin_size_usd
    count = int(math.ceil((high - origin) / settings.bin_size_usd)) + 1
    centers = origin + (np.arange(count) + 0.5) * settings.bin_size_usd
    return origin, centers


def simulate_heatmap(
    candles: Sequence[PriceCandle],
    open_interest_btc: Mapping[datetime, float],
    settings: HeatmapSettings,
    record_from: datetime,
    record_at: Optional[Collection[datetime]] = None,
) -> HeatmapResult:
    """
    Run the model over ``candles`` (5-minute, any gaps allowed) and record the
    steps from ``record_from`` on.

    Args:
        candles: 5-minute candles, oldest first. The steps before ``record_from``
            are the warm-up that builds the state.
        open_interest_btc: OI in BTC by sample time (UTC-aware, 5-minute grid). The
            sample stamped ``t`` is taken as the OI at the close of the candle
            that opened at ``t - 5 min`` (an assumption about the source's stamp).
        settings: Model settings.
        record_from: First recorded step (candle open time).
        record_at: When given, only these steps (candle open times) are recorded, in time
            order, and ``record_from`` is ignored: a long backtest then keeps a few rows
            instead of every step. A time that is not a step of the candle grid is skipped.

    Returns:
        The recorded window as a ``HeatmapResult``.

    Raises:
        ValueError: when ``candles`` is empty or not in time order.
    """
    if not candles:
        raise ValueError("no candles")
    if any(later.open_time <= earlier.open_time for earlier, later in zip(candles, candles[1:])):
        raise ValueError("candles must be in strictly increasing time order")

    origin, centers = _bin_grid(candles, settings)
    bin_size = settings.bin_size_usd
    long_state = np.zeros(len(centers))
    short_state = np.zeros(len(centers))
    candle_by_time = {candle.open_time: candle for candle in candles}

    grid_start, grid_end = candles[0].open_time, candles[-1].open_time
    steps = int((grid_end - grid_start) / STEP) + 1
    if record_at is None:
        first_recorded = max(0, int(math.ceil((record_from - grid_start) / STEP)))
        record_indices: Dict[int, int] = {index: index - first_recorded for index in range(first_recorded, steps)}
    else:
        wanted = sorted(moment for moment in set(record_at)
                        if grid_start <= moment <= grid_end and (moment - grid_start) % STEP == timedelta(0))
        record_indices = {int((moment - grid_start) / STEP): row for row, moment in enumerate(wanted)}
    recorded_times = [grid_start + STEP * index for index in sorted(record_indices)]
    long_rows = np.full((len(recorded_times), len(centers)), np.nan, dtype=np.float32)
    short_rows = np.full((len(recorded_times), len(centers)), np.nan, dtype=np.float32)

    baseline_oi: Optional[float] = None       # OI tracked so far (BTC), after liquidations
    decay_per_step = (0.5 ** (STEP.total_seconds() / 3600 / settings.position_half_life_hours)
                      if settings.position_half_life_hours else 1.0)
    steps_without_oi = 0
    steps_without_volume = 0
    from_volume = settings.creation_source == "volume"
    for index in range(steps):
        step_time = grid_start + STEP * index
        candle = candle_by_time.get(step_time)
        if candle is None:
            continue

        # 3. liquidations: a level the price range reached is gone, and so is its other side.
        reached_by_low, reached_by_high = centers >= candle.low, centers <= candle.high
        touched_long = touched_short = 0.0
        if not from_volume:                     # only the open-interest accounting needs the amounts
            touched_long = long_state[reached_by_low].sum()
            touched_short = short_state[reached_by_high].sum()
        long_state[reached_by_low] = 0.0
        short_state[reached_by_high] = 0.0
        if from_volume:
            # Volume creation: a touched level removes only its own side; no open-interest accounting.
            if decay_per_step < 1.0:
                long_state *= decay_per_step
                short_state *= decay_per_step
            if candle.volume_btc is None or candle.taker_buy_btc is None:
                steps_without_volume += 1
            else:
                bought = candle.taker_buy_btc * settings.volume_open_share
                sold = max(0.0, candle.volume_btc - candle.taker_buy_btc) * settings.volume_open_share
                for tier, weight in zip(settings.leverage_tiers, settings.leverage_weights):
                    long_bin = int((settings.long_liquidation_price(candle.close, tier) - origin) // bin_size)
                    short_bin = int((settings.short_liquidation_price(candle.close, tier) - origin) // bin_size)
                    long_state[long_bin] += bought * weight
                    short_state[short_bin] += sold * weight
            if index in record_indices:
                long_rows[record_indices[index]] = long_state
                short_rows[record_indices[index]] = short_state
            continue

        remaining_short = short_state.sum()
        if touched_long > 0 and remaining_short > 0:
            short_state *= max(0.0, 1 - touched_long / remaining_short)
        remaining_long = long_state.sum()
        if touched_short > 0 and remaining_long > 0:
            long_state *= max(0.0, 1 - touched_short / remaining_long)
        if baseline_oi is not None:
            baseline_oi -= touched_long + touched_short

        if decay_per_step < 1.0 and baseline_oi is not None:
            aged = 0.5 * ((1 - decay_per_step) * long_state.sum() + (1 - decay_per_step) * short_state.sum())
            long_state *= decay_per_step
            short_state *= decay_per_step
            baseline_oi -= aged

        # 1. and 4.: what is left of the OI change.
        sample_time = step_time + STEP
        oi_now = open_interest_btc.get(sample_time)
        if oi_now is None:
            steps_without_oi += 1
        elif baseline_oi is None:
            baseline_oi = oi_now
        else:
            residual = oi_now - baseline_oi
            if residual > 0:
                for tier, weight in zip(settings.leverage_tiers, settings.leverage_weights):
                    long_bin = int((settings.long_liquidation_price(candle.close, tier) - origin) // bin_size)
                    short_bin = int((settings.short_liquidation_price(candle.close, tier) - origin) // bin_size)
                    long_state[long_bin] += residual * weight
                    short_state[short_bin] += residual * weight
            elif residual < 0 and baseline_oi > 0:
                factor = max(0.0, oi_now / baseline_oi)
                long_state *= factor
                short_state *= factor
            baseline_oi = oi_now

        if index in record_indices:
            long_rows[record_indices[index]] = long_state
            short_rows[record_indices[index]] = short_state

    notes = []
    if steps_without_volume:
        notes.append(f"{steps_without_volume} step(s) had no volume data and created nothing")
    if steps_without_oi:
        notes.append(f"{steps_without_oi} step(s) had no open-interest sample (their change was applied at the next sample)")
    missing_candles = steps - len(candle_by_time)
    if missing_candles:
        notes.append(f"{missing_candles} step(s) had no candle (empty rows)")
    return HeatmapResult(times=recorded_times, bin_centers=centers, long_btc=long_rows,
                         short_btc=short_rows, notes=notes)
