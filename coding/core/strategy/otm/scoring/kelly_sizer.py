# coding/core/strategy/otm/scoring/kelly_sizer.py
"""
KellySizer — fractional Kelly position sizing within a fixed USD budget.

Formula: kelly_fraction = (P_win * b - (1 - P_win)) / b
Applied at 1/4 Kelly. Capped at 10% of risk_budget_usd per trade.
Portfolio correlation cap: max 10% of budget in same direction simultaneously.
"""
import logging
from typing import Dict, Optional, Tuple
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)


class KellySizer:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def compute_conviction(self, gate2_score: float,
                            gate3_directional_score: float) -> float:
        """Blend Gate 2 and Gate 3 directional scores 50/50."""
        raw = gate2_score * 0.50 + gate3_directional_score * 0.50
        return max(0.0, min(100.0, raw))

    def _lookup_priors(self, conviction: float) -> Optional[Tuple[float, float]]:
        """Return (p_win, avg_return_multiple) for conviction band. None if < 40."""
        priors = self._config.p_win_priors
        returns = self._config.avg_return_priors
        if conviction >= 90:
            return priors["90_100"], returns["90_100"]
        elif conviction >= 75:
            return priors["75_90"], returns["75_90"]
        elif conviction >= 60:
            return priors["60_75"], returns["60_75"]
        elif conviction >= 40:
            return priors["40_60"], returns["40_60"]
        return None

    def _compute_kelly_fraction(self, p_win: float,
                                  avg_return_multiple: float) -> float:
        """Full Kelly fraction for a binary bet."""
        if avg_return_multiple <= 0:
            return 0.0
        return (p_win * avg_return_multiple - (1.0 - p_win)) / avg_return_multiple

    def _apply_fractional_kelly(self, full_kelly: float) -> float:
        """Apply 1/kelly_divisor (default: 1/4) Kelly."""
        return max(0.0, full_kelly / self._config.kelly_divisor)

    def compute_position_usd(
        self,
        gate2_score: float,
        gate3_directional_score: float,
        existing_same_direction_usd: float = 0.0,
    ) -> Dict:
        conviction = self.compute_conviction(gate2_score, gate3_directional_score)
        priors = self._lookup_priors(conviction)

        if priors is None:
            logger.info("Conviction %.1f < 40 — skipping trade", conviction)
            return {"position_usd": 0.0, "conviction_score": conviction,
                    "p_win_prior": 0.0, "kelly_fraction": 0.0,
                    "skip_reason": "conviction below minimum threshold (40)"}

        p_win, avg_return = priors
        full_kelly = self._compute_kelly_fraction(p_win, avg_return)
        frac_kelly = self._apply_fractional_kelly(full_kelly)

        budget = self._config.risk_budget_usd
        max_per_trade = budget * self._config.max_single_trade_pct
        raw_position = min(frac_kelly * budget, max_per_trade)

        max_correlated = budget * self._config.max_correlated_pct
        remaining_cap = max_correlated - existing_same_direction_usd
        if remaining_cap <= 0:
            logger.info("Portfolio correlation cap reached — skipping trade")
            return {"position_usd": 0.0, "conviction_score": conviction,
                    "p_win_prior": p_win, "kelly_fraction": frac_kelly,
                    "skip_reason": "portfolio correlation cap reached"}

        final_position = min(raw_position, remaining_cap)

        return {
            "position_usd": round(final_position, 2),
            "conviction_score": conviction,
            "p_win_prior": p_win,
            "kelly_fraction": round(frac_kelly, 6),
            "skip_reason": None,
        }

    def compute_take_profit(self, conviction_score: float, dte: int) -> float:
        """Return take-profit multiple based on conviction and DTE."""
        if dte <= 6:
            return 2.0
        if conviction_score >= 75:
            return 8.0 if dte >= 30 else 5.0
        elif conviction_score >= 60:
            return 4.0 if dte >= 30 else 3.0
        return 2.0
