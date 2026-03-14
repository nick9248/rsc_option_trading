"""
Gate 1 — Liquidity Filter.

Hard pass/fail. All conditions must pass. Cheap to compute; run first.
Returns (passed: bool, reason: str).
"""
import logging
from typing import Tuple
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)

_FEE_RATE = 0.0003   # Deribit taker fee per leg


class LiquidityGate:
    """
    Applies Gate 1 liquidity checks to a single OTM contract candidate.

    Checks (all must pass):
    1. Bid-ask IV spread — DUAL threshold (relative AND absolute)
    2. Volume / OI ratio
    3. Minimum open interest (asset-specific)
    4. Transaction cost floor
    """

    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def check(self, contract: dict) -> Tuple[bool, str]:
        """
        Check one contract against all Gate 1 conditions.

        Args:
            contract: dict with keys: asset, bid_iv, ask_iv, open_interest,
                      volume_24h, mark_price, underlying_price, contract_qty

        Returns:
            (True, "passed") if all conditions met.
            (False, "<reason>") on first failure — short-circuits.
        """
        bid_iv = contract.get("bid_iv")
        ask_iv = contract.get("ask_iv")

        # ── 1. Null IV check ──────────────────────────────────────────────────
        if bid_iv is None or ask_iv is None:
            return False, "missing bid_iv or ask_iv"

        mid_iv = (bid_iv + ask_iv) / 2.0
        if mid_iv <= 0:
            return False, "mid_iv <= 0 — invalid IV"

        # ── 2a. Relative spread ───────────────────────────────────────────────
        relative_spread = (ask_iv - bid_iv) / mid_iv
        if relative_spread >= self._config.max_bid_ask_spread_relative:
            return False, (
                f"relative spread {relative_spread:.3f} "
                f">= threshold {self._config.max_bid_ask_spread_relative}"
            )

        # ── 2b. Absolute spread (vol pts = diff × 100) ────────────────────────
        absolute_spread_vol_pts = (ask_iv - bid_iv) * 100.0
        if absolute_spread_vol_pts >= self._config.max_bid_ask_spread_absolute:
            return False, (
                f"absolute spread {absolute_spread_vol_pts:.2f} vol pts "
                f">= cap {self._config.max_bid_ask_spread_absolute}"
            )

        # ── 3. Volume / OI ratio ──────────────────────────────────────────────
        oi = contract.get("open_interest", 0)
        volume_24h = contract.get("volume_24h", 0)
        if oi <= 0:
            return False, f"open_interest {oi} <= 0"
        vol_oi_ratio = volume_24h / oi
        if vol_oi_ratio <= self._config.min_volume_oi_ratio:
            return False, (
                f"volume/OI ratio {vol_oi_ratio:.4f} "
                f"<= threshold {self._config.min_volume_oi_ratio} (spec requires >)"
            )

        # ── 4. Minimum OI — asset-specific ───────────────────────────────────
        asset = contract.get("asset", "BTC")
        min_oi = self._config.min_oi_btc if asset == "BTC" else self._config.min_oi_eth
        if oi < min_oi:
            return False, f"open interest {oi} < min {min_oi} for {asset}"

        # ── 5. Transaction cost floor ─────────────────────────────────────────
        mark_price = contract.get("mark_price", 0.0)
        underlying = contract.get("underlying_price", 0.0)
        qty = contract.get("contract_qty", 1)
        entry_premium_usd = mark_price * underlying   # USD per contract
        round_trip_fee = 2.0 * _FEE_RATE * underlying * qty
        required_premium = round_trip_fee * self._config.tx_cost_floor_multiplier
        if (2.0 * entry_premium_usd) <= required_premium:
            return False, (
                f"2× premium {2*entry_premium_usd:.2f} USD "
                f"<= {self._config.tx_cost_floor_multiplier}× fee {required_premium:.2f} USD"
            )

        return True, "passed"
