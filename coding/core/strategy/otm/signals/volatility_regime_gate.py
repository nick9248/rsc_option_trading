"""
Gate 2 — Volatility Regime. Score 0-100.
  V1 (30%): DVOL percentile + dynamic floor
  V2+V4 (40%): VRP (50%) + GJR-GARCH (50%)
  V3 (30%): IV term structure slope

Actions: score>=40 -> new_entries_allowed | 30-39 -> no_new_entries | <=29 -> partial_exit
"""
import logging
import math
from typing import Dict, List, Optional

import numpy as np

from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)


class VolatilityRegimeGate:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def _score_v1(self, current_dvol: float, dvol_history: List[float]) -> float:
        n = len(dvol_history)
        if n < 90:
            logger.warning("DVOL history insufficient (%d days) — V1 score = 50 (neutral)", n)
            return 50.0
        if n < 365:
            logger.warning("DVOL history degraded (%d days, 36m preferred)", n)
        arr = np.array(dvol_history, dtype=float)
        percentile = float(np.mean(arr <= current_dvol) * 100.0)
        dvol_floor = float(np.median(arr)) + self._config.dvol_floor_std_multiplier * float(np.std(arr))
        below_pct = percentile < self._config.dvol_percentile_threshold
        below_floor = current_dvol < dvol_floor
        # Spec requires BOTH conditions for full score; no partial credit.
        if below_pct and below_floor:
            return 100.0
        return 0.0

    def _score_vrp(self, atm_iv_30d: float, rv_30d_parkinson: float) -> float:
        vrp_vol_pts = (atm_iv_30d - rv_30d_parkinson) * 100.0
        return 100.0 if vrp_vol_pts < self._config.vrp_cheap_threshold else 0.0

    def _fit_gjr_garch(self, ohlcv_daily: List[dict]) -> Optional[float]:
        try:
            from arch import arch_model
            closes = [r["close"] for r in ohlcv_daily]
            log_ret = [math.log(closes[i] / closes[i - 1]) * 100.0 for i in range(1, len(closes))]
            res = arch_model(np.array(log_ret), vol="GARCH", p=1, o=1, q=1,
                             dist="normal").fit(disp="off", show_warning=False)
            variance_1d = float(res.forecast(horizon=1, reindex=False).variance.iloc[-1, 0])
            return math.sqrt(variance_1d) * math.sqrt(252) / 100.0
        except Exception as exc:
            logger.warning("GJR-GARCH fit failed: %s", exc)
            return None

    def _score_garch_from_ohlcv(self, ohlcv_daily: List[dict]) -> float:
        n = len(ohlcv_daily)
        if n < 90:
            logger.warning("GARCH: < 90 candles (%d) — sub-signal B = 50 (neutral)", n)
            return 50.0
        if n < 180:
            logger.warning("GARCH: %d candles (< 180), fitting on available data", n)
        fcast = self._fit_gjr_garch(ohlcv_daily)
        return 50.0 if fcast is None else self._score_garch(fcast, None)

    def _score_garch(self, garch_fcast_annualized: float,
                     atm_iv_30d: Optional[float]) -> float:
        if atm_iv_30d is None or atm_iv_30d <= 0:
            return 100.0 if garch_fcast_annualized > 0.65 * self._config.garch_iv_ratio_threshold else 0.0
        return 100.0 if (garch_fcast_annualized / atm_iv_30d) > self._config.garch_iv_ratio_threshold else 0.0

    def _score_v3(self, term_data: Optional[dict]) -> float:
        if term_data is None:
            return 50.0
        slope = term_data.get("spread", 0.0)
        # spec: contango >+5=100, flat [-5,+5]=50, shallow back (-15,-5)=25, deep <-15=0
        if slope > self._config.term_structure_contango_threshold:
            return 100.0
        elif slope >= self._config.term_structure_shallow_back_threshold:
            # flat: slope in [-5, +5] (shallow_back_threshold = -5.0)
            return 50.0
        elif slope > self._config.term_structure_deep_back_threshold:
            # shallow backwardation: slope in (-15, -5)
            return 25.0
        return 0.0  # deep backwardation: slope <= -15

    def _combine_scores(self, v1: float, v2v4: float, v3: float) -> float:
        return round(0.30 * v1 + 0.40 * v2v4 + 0.30 * v3, 2)

    def _determine_action(self, score: float) -> str:
        if score >= self._config.gate2_suppress_threshold:
            return "new_entries_allowed"
        elif score >= self._config.gate2_position_exit_threshold:
            return "no_new_entries"
        return "partial_exit"

    def score(self, dvol_history: List[float], current_dvol: float,
              atm_iv_30d: float, rv_30d_parkinson: float,
              ohlcv_daily: List[dict], term_structure_data: Optional[dict]) -> Dict:
        v1 = self._score_v1(current_dvol, dvol_history)
        vrp_score = self._score_vrp(atm_iv_30d, rv_30d_parkinson)
        garch_fcast = self._fit_gjr_garch(ohlcv_daily) if len(ohlcv_daily) >= 90 else None
        garch_score = self._score_garch(garch_fcast, atm_iv_30d) if garch_fcast is not None else 50.0
        v2v4 = (vrp_score + garch_score) / 2.0
        v3 = self._score_v3(term_structure_data)
        total = self._combine_scores(v1, v2v4, v3)
        return {
            "total_score": total,
            "action": self._determine_action(total),
            "v1_score": v1,
            "v2v4_score": v2v4,
            "v3_score": v3,
            "vrp_score": vrp_score,
            "garch_score": garch_score,
            "garch_fcast_annualized": garch_fcast,
        }
