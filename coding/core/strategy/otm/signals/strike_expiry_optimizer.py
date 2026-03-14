# coding/core/strategy/otm/signals/strike_expiry_optimizer.py
"""
Gate 4 — Strike & Expiry Optimizer.

Filters surviving contracts by delta range, DTE category, and vega/theta.
Scores each 0-100. Returns list sorted descending by gate4_score.
"""
import logging
import math
from typing import Dict, List, Optional
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)


class StrikeExpiryOptimizer:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def _classify_expiry(self, dte: int) -> str:
        if dte < 1:
            raise ValueError(f"DTE must be >= 1, got {dte}")
        if dte > 90:
            raise ValueError(f"DTE {dte} > 90 — out of scope for this strategy")
        if dte <= 6:
            return "short"
        if dte < 30:
            return "medium"
        return "long"

    def _passes_delta_filter(self, contract: dict, mode: str = "directional") -> bool:
        delta = abs(contract.get("delta", 0.0))
        if mode == "directional":
            return self._config.min_delta_directional <= delta <= self._config.max_delta_directional
        else:  # event
            return self._config.min_delta_event <= delta <= self._config.max_delta_event

    def _passes_eth_call_filter(self, contract: dict, asset: str,
                                  call_score: float) -> bool:
        """ETH calls in [0.25, 0.35] delta are blocked unless call_score > 80."""
        delta = abs(contract.get("delta", 0.0))
        if asset == "ETH" and contract.get("direction") == "call" and 0.25 <= delta <= 0.35:
            return call_score > 80.0
        return True

    def _score_vega_theta(self, contract: dict) -> float:
        """Score vega/theta ratio against DTE-dependent threshold."""
        theta = contract.get("theta", 0.0)
        vega = contract.get("vega", 0.0)
        if theta == 0.0:
            return 0.0
        ratio = abs(vega) / abs(theta)
        dte = contract.get("dte", 14)
        try:
            cat = self._classify_expiry(dte)
        except ValueError:
            return 0.0
        thresholds = {
            "short": self._config.vega_theta_short,
            "medium": self._config.vega_theta_medium,
            "long": self._config.vega_theta_long,
        }
        threshold = thresholds[cat]
        if ratio > threshold * 2:
            return 100.0
        elif ratio >= threshold:
            return 50.0
        return 0.0

    def _score_breakeven(self, contract: dict, garch_fcast_30d: float) -> float:
        underlying = contract.get("underlying_price", 0.0)
        strike = contract.get("strike", 0.0)
        premium = contract.get("entry_premium", 0.0)
        direction = contract.get("direction", "call")

        if direction == "call":
            breakeven = strike + premium
            max_expected = underlying * (1 + self._config.max_breakeven_move_multiplier * garch_fcast_30d)
            return 100.0 if breakeven <= max_expected else 0.0
        else:  # put
            breakeven = strike - premium
            min_expected = underlying * (1 - self._config.max_breakeven_move_multiplier * garch_fcast_30d)
            return 100.0 if breakeven >= min_expected else 0.0

    def _score_gamma_premium(self, contract: dict) -> float:
        """For short-dated (DTE<=7): score gamma/premium ratio."""
        dte = contract.get("dte", 14)
        if dte > 7:
            return 50.0
        premium = contract.get("entry_premium", 0.0)
        gamma = contract.get("gamma", 0.0)
        if premium <= 0:
            return 0.0
        ratio = gamma / premium
        return min(100.0, ratio / 0.00005 * 50.0)

    def _compute_gate4_score(self, contract: dict, garch_fcast_30d: float) -> float:
        vt = self._score_vega_theta(contract)
        be = self._score_breakeven(contract, garch_fcast_30d)
        gp = self._score_gamma_premium(contract)
        return round(0.40 * vt + 0.40 * be + 0.20 * gp, 2)

    def _max_pain_tiebreak(self, c1: dict, c2: dict,
                            max_pain_strike: Optional[float]) -> dict:
        if max_pain_strike is None:
            return c1
        d1 = abs(c1["strike"] - max_pain_strike)
        d2 = abs(c2["strike"] - max_pain_strike)
        return c1 if d1 <= d2 else c2

    def select(
        self,
        contracts: List[dict],
        direction: str,
        call_score: float,
        put_score: float,
        gate2_score: float,
        garch_fcast_30d: float,
        max_pain_strike: Optional[float],
        spot_price: float,
        asset: str,
    ) -> List[dict]:
        mode = "event" if gate2_score < 50 else "directional"

        surviving = []
        for c in contracts:
            c = dict(c)
            c["direction"] = direction

            if not self._passes_delta_filter(c, mode=mode):
                continue
            if not self._passes_eth_call_filter(c, asset=asset, call_score=call_score):
                continue

            try:
                c["expiry_category"] = self._classify_expiry(c.get("dte", 14))
            except ValueError:
                continue

            theta = c.get("theta", 0.0)
            vega = c.get("vega", 0.0)
            c["vega_theta_ratio"] = abs(vega / theta) if theta != 0 else 0.0

            gamma = c.get("gamma", 0.0)
            premium = c.get("entry_premium", 0.0)
            c["gamma_premium_ratio"] = gamma / premium if premium > 0 else 0.0

            if direction == "call":
                c["breakeven_price"] = c["strike"] + premium
            else:
                c["breakeven_price"] = c["strike"] - premium

            c["gate4_score"] = self._compute_gate4_score(c, garch_fcast_30d)
            surviving.append(c)

        surviving.sort(key=lambda x: x["gate4_score"], reverse=True)
        if len(surviving) >= 2:
            if abs(surviving[0]["gate4_score"] - surviving[1]["gate4_score"]) <= 5.0:
                winner = self._max_pain_tiebreak(surviving[0], surviving[1], max_pain_strike)
                if winner is surviving[1]:
                    surviving[0], surviving[1] = surviving[1], surviving[0]

        return surviving
