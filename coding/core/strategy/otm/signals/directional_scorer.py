# coding/core/strategy/otm/signals/directional_scorer.py
"""
Gate 3 — Directional Scorer.
Computes call_score and put_score (0-100) using 8 weighted sub-signals.
BTC: D1+D7 22%, D2 15%, D3 14%, D4 11%, D6+D9 14%, D8 8%, D10 9%, RIS 7%
ETH: D10=0%, rest renormalized.
"""
import logging
from typing import Dict, List, Optional
import numpy as np
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)

_BTC_W = {"D1_D7": 0.22, "D2": 0.15, "D3": 0.14, "D4": 0.11,
           "D6_D9": 0.14, "D8": 0.08, "D10": 0.09, "RIS": 0.07}
_ETH_W_RAW = {k: (0.0 if k == "D10" else v) for k, v in _BTC_W.items()}
_S = sum(_ETH_W_RAW.values())
_ETH_W = {k: v/_S for k, v in _ETH_W_RAW.items()}
_DIRECTIONAL = {"D1_D7", "D2", "D3", "D4", "D6_D9", "D10", "RIS"}


class DirectionalScorer:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def _score_d1_d7(self, gex_dex: dict) -> float:
        net_gex = gex_dex.get("totals", {}).get("net_gex", 0.0)
        vanna = gex_dex.get("second_order", {}).get("vanna", 0.0)
        g = -1.0 if net_gex > 0 else (1.0 if net_gex < 0 else 0.0)
        v = 1.0 if vanna > 0 else (-1.0 if vanna < 0 else 0.0)
        return max(-1.0, min(1.0, (g + v) / 2.0))

    def _score_d2(self, current_rate: float, history: List[float],
                   spot_making_new_30d_low: bool = False,
                   spot_making_higher_highs: bool = False,
                   bearish_divergence: bool = False) -> float:
        if not history:
            return 0.0
        arr = np.array(history, dtype=float)
        pct = float(np.mean(arr <= current_rate) * 100.0)
        bull = self._config.funding_percentile_bull
        bear = self._config.funding_percentile_bear
        if pct < bull and spot_making_new_30d_low:
            return 1.0
        if pct > bear and spot_making_higher_highs and not bearish_divergence:
            return 1.0
        if pct > bear and bearish_divergence:
            return -1.0
        return 0.0

    def _score_d3(self, current_rr: float, rr25_history: List[float]) -> float:
        if len(rr25_history) < 10:
            return 0.0
        arr = np.array(rr25_history, dtype=float)
        std = float(np.std(arr))
        if std == 0:
            return 0.0
        z = (current_rr - float(np.mean(arr))) / std
        t = self._config.rr_z_score_threshold
        return 1.0 if z < -t else (-1.0 if z > t else 0.0)

    def _score_d4(self, current_ratio: float, pc_ratio_history: List[float]) -> float:
        if not pc_ratio_history:
            return 0.0
        arr = np.array(pc_ratio_history, dtype=float)
        pct = float(np.mean(arr <= current_ratio) * 100.0)
        return (1.0 if pct > self._config.pc_ratio_percentile_bull
                else (-1.0 if pct < self._config.pc_ratio_percentile_bear else 0.0))

    def _score_d6_d9(self, block_trades: dict,
                      dex_sign_flipped_positive: bool = False,
                      dex_sign_flipped_negative: bool = False) -> float:
        if not block_trades.get("blocks_detected", False):
            return 0.0
        d = block_trades.get("direction", "")
        if d == "call" and dex_sign_flipped_positive:
            return 1.0
        if d == "put" and dex_sign_flipped_negative:
            return -1.0
        return 0.5 if d == "call" else (-0.5 if d == "put" else 0.0)

    def _score_d8(self, inflow_pct: Optional[float]) -> float:
        if inflow_pct is None:
            return 0.0
        t = self._config.stablecoin_inflow_threshold_pct
        return 1.0 if inflow_pct > t else (-1.0 if inflow_pct < -t else 0.0)

    def _score_d10(self, current_ratio: Optional[float],
                    avg_30d: Optional[float]) -> float:
        if current_ratio is None or avg_30d is None or avg_30d == 0:
            return 0.0
        r = current_ratio / avg_30d
        return 1.0 if r < 0.85 else (-1.0 if r > 1.15 else 0.0)

    def _score_ris(self, rr25_30d_mean: float, rr25_current: float) -> float:
        div = (rr25_30d_mean - rr25_current) * 100.0
        t = self._config.ris_divergence_threshold
        return 1.0 if div > t else (-1.0 if div < -t else 0.0)

    def _detect_regime(self, ohlcv_daily: List[dict]) -> str:
        needed = max(self._config.ema_slow, self._config.trend_sma)
        if len(ohlcv_daily) < needed:
            return "neutral"
        closes = np.array([r["close"] for r in ohlcv_daily], dtype=float)
        def ema(arr, p):
            k = 2.0 / (p + 1); v = float(arr[0])
            for x in arr[1:]: v = x*k + v*(1-k)
            return v
        ef = ema(closes, self._config.ema_fast)
        es = ema(closes, self._config.ema_slow)
        sma = float(np.mean(closes[-self._config.trend_sma:]))
        spot = float(closes[-1])
        if ef > es and spot > sma:
            return "bull"
        if ef < es and spot < sma:
            return "bear"
        return "neutral"

    def _apply_regime_scaling(self, base: Dict[str, float],
                               direction: str, regime: str, asset: str) -> Dict[str, float]:
        if regime == "neutral":
            return dict(base)
        if direction == "call":
            mult = self._config.regime_call_multiplier if regime == "bull" else self._config.regime_put_multiplier
        else:
            mult = self._config.regime_put_multiplier if regime == "bull" else self._config.regime_call_multiplier
        scaled = {k: (v * mult if k in _DIRECTIONAL else v) for k, v in base.items()}
        total = sum(scaled.values())
        return {k: v/total for k, v in scaled.items()} if total > 0 else scaled

    def _apply_d3d4_conflict_rule(self, d3_score: float, d4_score: float,
                                    d3_weight: float, d4_weight: float) -> float:
        raw = d3_score * d3_weight + d4_score * d4_weight
        if (d3_score > 0 and d4_score < 0) or (d3_score < 0 and d4_score > 0):
            return raw * 0.70
        return raw

    def _apply_eth_call_penalty(self, call_score: float, asset: str,
                                  direction: str, delta: float) -> float:
        if asset == "ETH" and direction == "call" and 0.25 <= delta <= 0.35:
            return call_score * 0.85
        return call_score

    def score(self, asset: str, gex_dex: dict,
              current_funding_rate: float, funding_rate_history: List[float],
              vol_surface: dict, rr25_history: List[float],
              pc_ratio_history: List[float], block_trades: dict,
              stablecoin_inflow_pct: Optional[float],
              ibit_pc_ratio: Optional[float], ibit_pc_30d_avg: Optional[float],
              ohlcv_daily: List[dict], spot_close: float,
              spot_making_new_30d_low: bool = False,
              spot_making_higher_highs: bool = False,
              bearish_divergence: bool = False,
              dex_sign_flipped_positive: bool = False,
              dex_sign_flipped_negative: bool = False) -> Dict:
        regime = self._detect_regime(ohlcv_daily)
        base_w = _BTC_W if asset == "BTC" else _ETH_W
        rr25_current = vol_surface.get("rr25", 0.0)
        rr25_mean = float(np.mean(rr25_history)) if rr25_history else 0.0
        pc_ratio = vol_surface.get("pc_by_moneyness", {}).get("pc_ratio_all", 1.0)

        raw = {
            "D1_D7": self._score_d1_d7(gex_dex),
            "D2":    self._score_d2(current_funding_rate, funding_rate_history,
                                    spot_making_new_30d_low=spot_making_new_30d_low,
                                    spot_making_higher_highs=spot_making_higher_highs,
                                    bearish_divergence=bearish_divergence),
            "D3":    self._score_d3(rr25_current, rr25_history),
            "D4":    self._score_d4(pc_ratio, pc_ratio_history),
            "D6_D9": self._score_d6_d9(block_trades, dex_sign_flipped_positive,
                                        dex_sign_flipped_negative),
            "D8":    self._score_d8(stablecoin_inflow_pct),
            "D10":   self._score_d10(ibit_pc_ratio, ibit_pc_30d_avg) if asset == "BTC" else 0.0,
            "RIS":   self._score_ris(rr25_mean, rr25_current),
        }

        def _build_score(direction: str) -> float:
            w = self._apply_regime_scaling(base_w, direction, regime, asset)
            d3d4_scaled = self._apply_d3d4_conflict_rule(
                raw["D3"], raw["D4"], w.get("D3", 0.0), w.get("D4", 0.0)
            )
            total = d3d4_scaled
            for sig in ["D1_D7", "D2", "D6_D9", "D8", "D10", "RIS"]:
                total += raw[sig] * w.get(sig, 0.0)
            return max(0.0, min(100.0, (total + 1.0) / 2.0 * 100.0))

        call_score = round(_build_score("call"), 2)
        put_score  = round(_build_score("put"), 2)

        return {"call_score": call_score, "put_score": put_score,
                "regime": regime, "breakdown": {k: round(v, 4) for k, v in raw.items()}}
