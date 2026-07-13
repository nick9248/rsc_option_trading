"""
Phase 3 forward-testing harness.

Scores the current on-chain snapshot using the Phase 2 validated signals,
stores a directional prediction, and resolves it once the actual 1h return
is available. Accumulates a live track record that can be evaluated against
the Phase 2 OOS benchmarks.

Phase 2 survivors (1h horizon only):
  BTC + ETH: itm/otm_put_oi_pct, itm/otm_call_oi_pct, max_pain_distance_pct
  ETH only:  pc_far_otm_ratio

Correlation sign table (positive composite z = bullish forecast):
  itm_put_oi_pct        sign = -1  (r = -0.119 BTC / -0.105 ETH)
  otm_put_oi_pct        sign = +1  (r = +0.119 BTC / +0.105 ETH)
  itm_call_oi_pct       sign = +1  (r = +0.095 BTC / +0.121 ETH)
  otm_call_oi_pct       sign = -1  (r = -0.095 BTC / -0.121 ETH)
  max_pain_distance_pct sign = -1  (r = -0.099 BTC / -0.121 ETH)
  pc_far_otm_ratio      sign = -1  (r = -0.126 ETH only)

Signal threshold: |composite_z| >= 0.3 to emit a directional call.
Success criteria (Phase 3 gate): N >= 50 signals, hit_rate > 0.55,
information_ratio > 0.30.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ----- metric config -------------------------------------------------------
# (metric_name, directional_sign, weight = |r_avg across BTC+ETH|)
_METRIC_CFG = [
    ("itm_put_oi_pct",        -1.0, 0.112),   # avg(0.119, 0.105)
    ("otm_put_oi_pct",        +1.0, 0.112),
    ("itm_call_oi_pct",       +1.0, 0.108),   # avg(0.095, 0.121)
    ("otm_call_oi_pct",       -1.0, 0.108),
    ("max_pain_distance_pct", -1.0, 0.110),   # avg(0.099, 0.121)
]
_ETH_ONLY_CFG = [
    ("pc_far_otm_ratio", -1.0, 0.126),
]

SIGNAL_THRESHOLD = 0.30   # |composite_z| below this -> 'neutral'
LOOKBACK_HOURS = 720      # 30 days of history for z-score normalisation
SUCCESS_N = 50            # minimum N before evaluating hit rate / IR
SUCCESS_HIT_RATE = 0.55
SUCCESS_IR = 0.30


class ForwardTestingHarness:
    """
    Live forward-testing harness for Phase 3.

    Typical call sequence (called from the prospective collector after each
    on-chain analysis cycle):

        harness = ForwardTestingHarness(repo)

        # After saving each on-chain snapshot:
        harness.record_prediction(currency, snapshot_hour, metrics, spot_price)

        # Once per run (resolve all pending predictions >= 1h old):
        harness.resolve_pending_predictions(currency)

        # Periodically check whether success criteria are met:
        summary = harness.get_track_record(currency)
    """

    def __init__(self, repository):
        """
        Args:
            repository: DatabaseRepository instance.
        """
        self._repo = repository

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_prediction(
        self,
        currency: str,
        snapshot_hour: datetime,
        metrics: Dict[str, Optional[float]],
        spot_price: float,
    ) -> Optional[Dict]:
        """
        Compute and store a directional prediction for this snapshot.

        Does nothing (returns None) if there is insufficient history to
        compute z-scores.  Silently skips if a prediction for
        (currency, snapshot_hour) already exists.

        Args:
            currency: 'BTC' or 'ETH'.
            snapshot_hour: Truncated-to-hour timestamp of the snapshot.
            metrics: Dict of metric values.  Keys: itm_put_oi_pct,
                otm_put_oi_pct, itm_call_oi_pct, otm_call_oi_pct,
                max_pain_distance_pct (+ pc_far_otm_ratio for ETH).
            spot_price: Underlying price at prediction time.

        Returns:
            The prediction dict that was saved, or None on failure/skip.
        """
        cfg = _METRIC_CFG + (_ETH_ONLY_CFG if currency == "ETH" else [])
        metric_names = [m for m, _, _ in cfg]

        history = self._repo.get_recent_onchain_history(
            currency=currency,
            metric_columns=metric_names,
            lookback_hours=LOOKBACK_HOURS,
        )

        if len(history) < 20:
            logger.debug(
                "ForwardTestingHarness: insufficient history for %s (%d rows), skip",
                currency, len(history),
            )
            return None

        z_scores = self._compute_z_scores(history, metrics, cfg)
        composite, signal_dir, confidence = self._composite_signal(z_scores, cfg)

        prediction = {
            "currency": currency,
            "snapshot_hour": snapshot_hour,
            "spot_price_at_prediction": spot_price,
            "signal_direction": signal_dir,
            "signal_score": composite,
            "signal_confidence": confidence,
        }
        for name, _, _ in cfg:
            prediction[name] = metrics.get(name)
            prediction[f"z_{name}"] = z_scores.get(name)

        try:
            self._repo.save_forward_prediction(prediction)
            logger.info(
                "ForwardTestingHarness: recorded %s %s signal=%-8s score=%.3f",
                currency, snapshot_hour.strftime("%Y-%m-%d %H:%M"),
                signal_dir, composite,
            )
        except Exception as exc:
            logger.error("ForwardTestingHarness.record_prediction failed: %s", exc)
            return None

        return prediction

    def resolve_pending_predictions(
        self,
        currency: str,
        older_than_hours: float = 1.0,
    ) -> int:
        """
        Resolve all unresolved predictions older than `older_than_hours`.

        Fetches the current spot price (latest available from DB) and uses
        it to compute the actual 1h return for each pending prediction.

        Args:
            currency: Currency to resolve.
            older_than_hours: Only resolve predictions at least this many
                hours old (default 1.0).

        Returns:
            Number of predictions resolved.
        """
        pending = self._repo.get_unresolved_predictions(currency, older_than_hours)
        if not pending:
            return 0

        spot_now = self._repo.get_latest_spot_price(currency)
        if spot_now is None:
            logger.warning("ForwardTestingHarness: no spot price for %s, cannot resolve", currency)
            return 0

        resolved_at = datetime.now(timezone.utc)
        count = 0
        for pred in pending:
            try:
                self._repo.resolve_prediction(
                    prediction_id=pred["id"],
                    spot_price_at_resolution=spot_now,
                    resolved_at=resolved_at,
                )
                ret_pct = (spot_now - pred["spot_price_at_prediction"]) / pred["spot_price_at_prediction"] * 100
                correct = (
                    (pred["signal_direction"] == "bullish" and ret_pct > 0) or
                    (pred["signal_direction"] == "bearish" and ret_pct < 0)
                ) if pred["signal_direction"] != "neutral" else None
                logger.info(
                    "ForwardTestingHarness: resolved %s %s -> ret=%.3f%% signal=%s correct=%s",
                    currency,
                    pred["snapshot_hour"].strftime("%Y-%m-%d %H:%M"),
                    ret_pct,
                    pred["signal_direction"],
                    correct,
                )
                count += 1
            except Exception as exc:
                logger.error(
                    "ForwardTestingHarness: failed to resolve prediction id=%s: %s",
                    pred["id"], exc,
                )

        return count

    def get_track_record(self, currency: str) -> Dict:
        """
        Return track-record statistics and whether success criteria are met.

        Args:
            currency: Currency to query.

        Returns:
            Dict with statistics and 'criteria_met' bool.
        """
        stats = self._repo.get_forward_test_stats(currency)
        n = stats.get("n_signals", 0)
        hit_rate = stats.get("hit_rate")
        ir = stats.get("information_ratio")

        criteria_met = (
            n >= SUCCESS_N
            and hit_rate is not None and hit_rate > SUCCESS_HIT_RATE
            and ir is not None and ir > SUCCESS_IR
        )

        return {
            **stats,
            "criteria_met": criteria_met,
            "criteria": {
                "min_n": SUCCESS_N,
                "min_hit_rate": SUCCESS_HIT_RATE,
                "min_ir": SUCCESS_IR,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_z_scores(
        history: List[Dict],
        current: Dict[str, Optional[float]],
        cfg: List,
    ) -> Dict[str, Optional[float]]:
        """
        Compute z-score of each metric's current value against its 30d history.

        Metrics with fewer than 10 non-null historical values get z=None.
        """
        z = {}
        for name, _sign, _w in cfg:
            vals = [row[name] for row in history if row.get(name) is not None]
            cur = current.get(name)
            if cur is None or len(vals) < 10:
                z[name] = None
                continue
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = math.sqrt(variance)
            z[name] = (cur - mean) / std if std > 1e-12 else 0.0
        return z

    @staticmethod
    def _composite_signal(
        z_scores: Dict[str, Optional[float]],
        cfg: List,
    ):
        """
        Combine directional-adjusted z-scores into a single composite.

        composite = sum(sign_i * w_i * z_i) / sum(w_i for available metrics)

        Returns: (composite_score, signal_direction, confidence)
        """
        weighted_sum = 0.0
        weight_sum = 0.0
        for name, sign, weight in cfg:
            z = z_scores.get(name)
            if z is not None:
                weighted_sum += sign * weight * z
                weight_sum += weight

        if weight_sum == 0:
            return 0.0, "neutral", 0.0

        composite = weighted_sum / weight_sum
        confidence = min(1.0, abs(composite))

        if abs(composite) >= SIGNAL_THRESHOLD:
            direction = "bullish" if composite > 0 else "bearish"
        else:
            direction = "neutral"

        return composite, direction, confidence
