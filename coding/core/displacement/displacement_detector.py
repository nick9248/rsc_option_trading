import logging
from datetime import datetime, timezone
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent

logger = logging.getLogger(__name__)


class DisplacementDetector:
    """
    Detects price displacement events across multiple timeframes.

    Stateful: tracks last event time per asset to enforce cooldown.
    """

    def __init__(self, config: DisplacementConfig):
        self._config = config
        self._last_event_time: dict[str, Optional[datetime]] = {}

    def check(self, asset: str, prices: dict[str, float]) -> Optional[DisplacementEvent]:
        """
        Check if a displacement event has occurred.

        Args:
            asset: "BTC" or "ETH"
            prices: dict with keys "now", "1h_ago", "4h_ago", "24h_ago", "7d_ago"
                    All values are prices in USD.

        Returns:
            DisplacementEvent if triggered, None if no event or in cooldown.
        """
        if self._in_cooldown(asset):
            return None

        now_price = prices["now"]
        drops = self._compute_drops(now_price, prices)
        triggering_tf = self._find_triggering_timeframe(drops)

        if triggering_tf is None:
            return None

        self._last_event_time[asset] = datetime.now(tz=timezone.utc)
        logger.info(
            "Displacement event: %s drop %.1f%% in %s (price: $%,.0f)",
            asset, drops[triggering_tf] * 100, triggering_tf, now_price,
        )

        return DisplacementEvent(
            asset=asset,
            detected_at=datetime.now(tz=timezone.utc),
            current_price=now_price,
            drop_1h_pct=max(drops["1h"], 0.0),
            drop_4h_pct=max(drops["4h"], 0.0),
            drop_24h_pct=max(drops["24h"], 0.0),
            drop_7d_pct=max(drops["7d"], 0.0),
            triggering_timeframe=triggering_tf,
        )

    def _in_cooldown(self, asset: str) -> bool:
        last = self._last_event_time.get(asset)
        if last is None:
            return False
        elapsed_hours = (datetime.now(tz=timezone.utc) - last).total_seconds() / 3600
        return elapsed_hours < self._config.cooldown_hours

    def _compute_drops(self, now_price: float, prices: dict[str, float]) -> dict[str, float]:
        """
        Compute drop fractions for each timeframe.

        A positive value means the price dropped (old > now).
        A negative value means the price rose (old < now).
        """
        return {
            "1h": (prices["1h_ago"] - now_price) / prices["1h_ago"],
            "4h": (prices["4h_ago"] - now_price) / prices["4h_ago"],
            "24h": (prices["24h_ago"] - now_price) / prices["24h_ago"],
            "7d": (prices["7d_ago"] - now_price) / prices["7d_ago"],
        }

    def _find_triggering_timeframe(self, drops: dict[str, float]) -> Optional[str]:
        """
        Return the first timeframe whose drop meets or exceeds its threshold.

        Checks in order: 1h, 4h, 24h, 7d (shortest to longest).
        Returns None if no threshold is met.
        """
        thresholds = {
            "1h": self._config.drop_1h_threshold,
            "4h": self._config.drop_4h_threshold,
            "24h": self._config.drop_24h_threshold,
            "7d": self._config.drop_7d_threshold,
        }
        for tf in ("1h", "4h", "24h", "7d"):
            if drops[tf] >= thresholds[tf]:
                return tf
        return None
