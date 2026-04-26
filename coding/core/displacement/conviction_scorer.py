import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent

logger = logging.getLogger(__name__)


class ConvictionScorer:
    """
    Scores a displacement event using 6 market signals → probability (0-100).

    Without a trained model: equal weights (average of 6 signals).
    With a trained model (joblib): uses logistic regression probabilities.
    Exposes _last_dvol_sigma and _last_term_inversion_pct for the scanner service.
    """

    def __init__(self, config: DisplacementConfig, model_path: Optional[Path] = None):
        self._config = config
        self._model = None
        self._last_dvol_sigma: float = 0.0
        self._last_term_inversion_pct: float = 0.0
        if model_path and model_path.exists():
            self._load_model(model_path)

    def score(
        self,
        event: DisplacementEvent,
        market_data: dict,
    ) -> tuple[float, dict[str, float]]:
        """
        Score the displacement event.

        market_data keys:
            funding_rate: float        — current 8h perpetual funding rate (e.g. -0.008)
            dvol_current: float        — current DVOL value
            dvol_history: List[float]  — historical DVOL values
            ohlcv_history: List[dict]  — daily candles, [{"close": float}, ...] newest first
            options_chain: List[dict]  — current options chain

        Returns:
            (probability_0_to_100, signal_breakdown_dict)
        """
        breakdown = {
            "drop_magnitude": self._score_drop_magnitude(event, market_data["ohlcv_history"]),
            "drop_speed": self._score_drop_speed(event),
            "funding_rate": self._score_funding_rate(market_data["funding_rate"]),
            "dvol_spike": self._score_dvol_spike(
                market_data["dvol_current"], market_data["dvol_history"]
            ),
            "max_pain": self._score_max_pain(event.current_price, market_data["options_chain"]),
            "term_structure": self._score_term_structure(market_data["options_chain"]),
        }

        if self._model is not None:
            features = [[v for v in breakdown.values()]]
            try:
                prob = float(self._model.predict_proba(features)[0][1]) * 100
            except Exception as e:
                logger.warning(f"Model prediction failed, using equal weights: {e}")
                prob = sum(breakdown.values()) / 6.0
        else:
            prob = sum(breakdown.values()) / 6.0

        return round(prob, 2), breakdown

    # ── Signal implementations ─────────────────────────────────────

    def _score_drop_magnitude(self, event: DisplacementEvent, ohlcv_history: list[dict]) -> float:
        closes = [row["close"] for row in ohlcv_history]
        if len(closes) < 2:
            return 50.0
        drops = []
        for i in range(len(closes) - 1):
            if closes[i + 1] > 0:
                drop = (closes[i + 1] - closes[i]) / closes[i + 1]
                if drop > 0:
                    drops.append(drop)
        if not drops:
            return 50.0
        current_drop = abs(event.drop_24h_pct)
        percentile = sum(1 for d in drops if d <= current_drop) / len(drops) * 100
        return round(min(100.0, percentile), 2)

    def _score_drop_speed(self, event: DisplacementEvent) -> float:
        if abs(event.drop_24h_pct) < 0.001:
            return 50.0
        ratio = abs(event.drop_1h_pct) / abs(event.drop_24h_pct)
        return round(min(100.0, max(0.0, ratio * 200)), 2)

    def _score_funding_rate(self, funding_rate: float) -> float:
        score = 50.0 - (funding_rate * 5000)
        return round(min(100.0, max(0.0, score)), 2)

    def _score_dvol_spike(self, dvol_current: float, dvol_history: list[float]) -> float:
        if len(dvol_history) < 10:
            return 50.0
        mean = sum(dvol_history) / len(dvol_history)
        variance = sum((x - mean) ** 2 for x in dvol_history) / len(dvol_history)
        std = math.sqrt(variance)
        if std < 0.001:
            return 50.0
        sigma = (dvol_current - mean) / std
        self._last_dvol_sigma = sigma

        low = self._config.dvol_sweet_spot_low
        high = self._config.dvol_sweet_spot_high
        if sigma < 0:
            return 0.0
        elif sigma < low:
            return round(sigma / low * 60, 2)
        elif sigma <= high:
            return 100.0
        else:
            return round(max(0.0, 100.0 - (sigma - high) / 1.5 * 100), 2)

    def _score_max_pain(self, current_price: float, options_chain: list[dict]) -> float:
        max_pain_price = self._compute_max_pain(options_chain)
        if max_pain_price is None or max_pain_price <= 0:
            return 50.0
        distance = (max_pain_price - current_price) / max_pain_price
        if distance <= 0:
            return 0.0
        return round(min(100.0, distance / self._config.max_pain_distance_full_score * 100), 2)

    def _compute_max_pain(self, options_chain: list[dict]) -> Optional[float]:
        strikes: dict[float, dict[str, float]] = defaultdict(lambda: {"call_oi": 0.0, "put_oi": 0.0})
        for opt in options_chain:
            strike = opt.get("strike")
            if not strike:
                continue
            oi = opt.get("open_interest", 0.0) or 0.0
            if opt.get("option_type") == "call":
                strikes[strike]["call_oi"] += oi
            else:
                strikes[strike]["put_oi"] += oi
        if not strikes:
            return None
        sorted_strikes = sorted(strikes.keys())
        min_pain = float("inf")
        max_pain_strike = sorted_strikes[0]
        for test_price in sorted_strikes:
            call_pain = sum(
                (test_price - s) * data["call_oi"]
                for s, data in strikes.items()
                if s < test_price
            )
            put_pain = sum(
                (s - test_price) * data["put_oi"]
                for s, data in strikes.items()
                if s > test_price
            )
            total = call_pain + put_pain
            if total < min_pain:
                min_pain = total
                max_pain_strike = test_price
        return max_pain_strike

    def _score_term_structure(self, options_chain: list[dict]) -> float:
        by_expiry: dict[int, list[float]] = defaultdict(list)
        for opt in options_chain:
            dte = opt.get("dte", 0)
            iv = opt.get("mark_iv", 0.0)
            if dte >= 7 and iv and iv > 0:
                by_expiry[dte].append(iv)
        if len(by_expiry) < 2:
            return 50.0
        sorted_dtes = sorted(by_expiry.keys())
        front_iv = sum(by_expiry[sorted_dtes[0]]) / len(by_expiry[sorted_dtes[0]])
        back_iv = sum(by_expiry[sorted_dtes[1]]) / len(by_expiry[sorted_dtes[1]])
        if back_iv < 0.001:
            return 50.0
        inversion_pct = (front_iv - back_iv) / back_iv
        self._last_term_inversion_pct = inversion_pct
        if inversion_pct >= 0.05:
            return 100.0
        elif inversion_pct >= 0:
            return round(50.0 + inversion_pct / 0.05 * 50, 2)
        else:
            return round(max(0.0, 50.0 + inversion_pct / 0.05 * 50), 2)

    def _load_model(self, model_path: Path) -> None:
        try:
            import joblib
            self._model = joblib.load(model_path)
            logger.info(f"Loaded conviction model from {model_path}")
        except Exception as e:
            logger.warning(f"Could not load conviction model: {e}")
            self._model = None
