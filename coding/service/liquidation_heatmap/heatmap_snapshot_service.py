"""
Computes the estimated liquidation levels of Binance BTCUSDT and stores them as an hourly snapshot, at the time they
are computed (point in time). The candles come from the Binance REST API, on the VPS through the read-only relay
(BINANCE_FUTURES_BASE_URL). The model is the research model with the settings fixed in ``RECORDED_SETTINGS`` (volume
creation); it is a recorded forecast, to be checked against the real liquidation events later.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from coding.core.analytics.liquidation_heatmap.level_snapshot import extract_levels
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, HeatmapSettings, simulate_heatmap
from coding.core.database.repository import DatabaseRepository
from coding.service.liquidation_heatmap.binance_history_loader import BinanceHistoryLoader

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
WARMUP_DAYS = 12                        # about six half-lives of the 48 h turnover
MINIMUM_CANDLE_SHARE = 0.95             # fewer candles than this share of the window: no snapshot
MAXIMUM_LAST_CANDLE_AGE = timedelta(minutes=20)


def recorded_settings() -> HeatmapSettings:
    """The configuration that is recorded: volume creation, tiers 25x to 100x in steps of 5, bin 100 USD, 48 h half-life."""
    tiers = tuple(range(25, 101, 5))
    return HeatmapSettings(bin_size_usd=100.0, leverage_tiers=tiers,
                           leverage_weights=tuple(1 / len(tiers) for _ in tiers),
                           position_half_life_hours=48.0, creation_source="volume")


class HeatmapSnapshotService:
    """Computes and stores one snapshot per run."""

    def __init__(
        self,
        loader: BinanceHistoryLoader,
        repository: DatabaseRepository,
        settings: Optional[HeatmapSettings] = None,
        warmup_days: int = WARMUP_DAYS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            loader: Source of the 5-minute candles.
            repository: Where the snapshot is stored.
            settings: Model settings; ``recorded_settings()`` when omitted.
            warmup_days: Days of candles used to build the state.
            clock: Wall clock (UTC-aware).
        """
        self.loader = loader
        self.repository = repository
        self.settings = settings or recorded_settings()
        self.warmup_days = warmup_days
        self.clock = clock

    def snapshot(self) -> bool:
        """
        Compute the levels at the newest closed 5-minute candle and store them.

        Returns:
            True when a snapshot was stored, False when the data is too thin or too old (logged at WARNING; nothing
            is stored, so a gap in the snapshots is a gap and never an estimate) or the snapshot already exists.
        """
        now = self.clock()
        end = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        start = end - timedelta(days=self.warmup_days)
        candles = self.loader.load_candles(SYMBOL, start, end)
        expected = int((end - start) / STEP)
        if len(candles) < MINIMUM_CANDLE_SHARE * expected:
            logger.warning("Liquidation snapshot: only %d of %d candles, no snapshot stored", len(candles), expected)
            return False
        last = candles[-1]
        if end - (last.open_time + STEP) > MAXIMUM_LAST_CANDLE_AGE:
            logger.warning("Liquidation snapshot: the newest candle is %s, no snapshot stored", last.open_time.isoformat())
            return False

        result = simulate_heatmap(candles, {}, self.settings, record_from=last.open_time)
        levels: List[List[float]] = extract_levels(result, 0, last.close)
        if not levels:
            logger.warning("Liquidation snapshot: the model produced no levels, no snapshot stored")
            return False
        return self.repository.save_liquidation_heatmap_snapshot(
            symbol=SYMBOL, as_of_utc=last.open_time + STEP, computed_at=now, reference_price=last.close,
            bin_size_usd=self.settings.bin_size_usd,
            settings={"creation_source": self.settings.creation_source,
                      "leverage_tiers": list(self.settings.leverage_tiers),
                      "bin_size_usd": self.settings.bin_size_usd,
                      "position_half_life_hours": self.settings.position_half_life_hours,
                      "volume_open_share": self.settings.volume_open_share,
                      "maintenance_margin_rate": self.settings.maintenance_margin_rate,
                      "warmup_days": self.warmup_days},
            levels=levels)
