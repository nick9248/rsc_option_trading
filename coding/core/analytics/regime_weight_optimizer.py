"""
Regime weight optimizer.

Finds optimal weights and thresholds for MarketRegimeDetector via SLSQP.
Operates on a DataFrame produced by RegimeDatasetBuilder.
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from scipy.optimize import minimize

from coding.core.analytics.market_regime_detector import MarketRegimeDetector

logger = logging.getLogger(__name__)

PARAM_ORDER = ["trend", "volatility", "momentum", "onchain", "sentiment",
               "sideways_threshold", "strong_threshold"]

HORIZON_COLS = ["return_4h", "return_8h", "return_12h", "return_24h",
                "return_48h", "return_72h", "return_7d", "return_30d"]

HORIZON_LABELS = ["4h", "8h", "12h", "24h", "48h", "72h", "7d", "30d"]

WEIGHT_BOUNDS = (0.05, 0.60)
SIDEWAYS_BOUNDS = (10.0, 30.0)
STRONG_BOUNDS = (40.0, 70.0)
GRID_SAMPLES = 500


def _classify(composite: float, sideways: float, strong: float) -> int:
    """
    Classify composite score into +1 (Bullish), 0 (Sideways), or -1 (Bearish).

    Uses symmetric thresholds: sideways and strong are positive magnitudes.
    Applied as ±threshold symmetrically. Matches _classify_regime logic
    (excluding the ADX override which requires DI values not stored in regime_detections).
    """
    if composite >= strong:
        return +1
    elif composite >= sideways:
        return +1
    elif composite >= -sideways:
        return 0
    elif composite >= -strong:
        return -1
    else:
        return -1


@dataclass
class ParameterSet:
    """A set of regime detector parameters with their evaluated fitness scores."""
    weights: dict          # keys: trend, volatility, momentum, onchain, sentiment
    sideways_threshold: float
    strong_threshold: float
    fitness_a: float       # directional accuracy (0–1)
    fitness_b: float       # averaged per-horizon Sharpe


@dataclass
class OptimizationResult:
    """Result of the full optimization run."""
    current: ParameterSet          # baseline — hand-tuned values evaluated on the dataset
    accuracy_optimal: ParameterSet
    sharpe_optimal: ParameterSet
    blended_optimal: ParameterSet
    dataset_rows: int
    horizon_coverage: dict         # {"4h": count, ...} — non-None row count per horizon


class RegimeWeightOptimizer:
    """
    Finds optimal weights and thresholds for MarketRegimeDetector via SLSQP.

    Operates on a DataFrame produced by RegimeDatasetBuilder. Runs a
    Dirichlet grid warm-start (500 samples) followed by three SLSQP runs:
    accuracy-optimal, Sharpe-optimal, and blended (50/50).

    Args:
        df: Dataset from RegimeDatasetBuilder.
        directional_threshold: % return required to classify as directional (default 1.5).
    """

    def __init__(self, df: pd.DataFrame, directional_threshold: float = 1.5) -> None:
        self._df = df
        self._dir_thresh = directional_threshold
        # Precompute score matrix and return matrix for speed
        self._scores = df[["trend_score", "volatility_score", "momentum_score",
                           "onchain_score", "sentiment_score"]].to_numpy(dtype=float)
        self._returns = df[HORIZON_COLS].to_numpy(dtype=float)  # NaN for missing

    def _fitness_a(self, x: np.ndarray) -> float:
        """
        Directional accuracy across all (row, horizon) pairs with data.

        Args:
            x: Parameter vector [w_trend, w_vol, w_mom, w_onchain, w_sent, sideways, strong].

        Returns:
            Accuracy in [0, 1].
        """
        weights = x[:5]
        sideways = x[5]
        strong = x[6]

        composites = self._scores @ weights
        correct_count = 0
        total = 0

        for i, ret_row in enumerate(self._returns):
            composite = composites[i]
            predicted = _classify(composite, sideways, strong)
            for ret in ret_row:
                if np.isnan(ret):
                    continue
                if ret > self._dir_thresh:
                    actual = +1
                elif ret < -self._dir_thresh:
                    actual = -1
                else:
                    actual = 0
                correct_count += (predicted == actual)
                total += 1

        return correct_count / total if total > 0 else 0.0

    def _fitness_b(self, x: np.ndarray) -> float:
        """
        Per-horizon Sharpe averaged across available horizons only.

        Horizons with zero rows are excluded from the average entirely.
        Horizons with exactly one row contribute 0.0 (can't compute Sharpe).
        Uses population std (ddof=0).

        Args:
            x: Parameter vector [w_trend, w_vol, w_mom, w_onchain, w_sent, sideways, strong].

        Returns:
            Average per-horizon Sharpe ratio.
        """
        weights = x[:5]
        sideways = x[5]
        strong = x[6]

        composites = self._scores @ weights
        predictions = np.array([_classify(c, sideways, strong) for c in composites])

        sharpes = []
        for h_idx in range(len(HORIZON_COLS)):
            ret_col = self._returns[:, h_idx]
            mask = ~np.isnan(ret_col)
            if mask.sum() == 0:
                continue              # no data for this horizon — exclude from average
            if mask.sum() < 2:
                sharpes.append(0.0)   # single point — guard, contributes 0
                continue
            pnl = predictions[mask] * ret_col[mask]
            std = np.std(pnl, ddof=0)
            sharpes.append(0.0 if std == 0.0 else float(np.mean(pnl) / std))

        return float(np.mean(sharpes)) if sharpes else 0.0

    def _eval_both(self, x: np.ndarray) -> tuple[float, float]:
        """Evaluate both fitness functions and return (fitness_a, fitness_b)."""
        return self._fitness_a(x), self._fitness_b(x)

    def _x_to_param_set(self, x: np.ndarray, fa: float, fb: float) -> ParameterSet:
        """Convert parameter vector and fitness values to a ParameterSet."""
        return ParameterSet(
            weights={k: float(x[i]) for i, k in enumerate(PARAM_ORDER[:5])},
            sideways_threshold=float(x[5]),
            strong_threshold=float(x[6]),
            fitness_a=fa,
            fitness_b=fb,
        )

    def _run_slsqp(self, x0: np.ndarray, objective_fn) -> np.ndarray:
        """
        Run SLSQP minimization on the negated objective.

        Constraints:
          - Weights sum to 1.0 (equality)
          - strong_threshold - sideways_threshold >= 1 (inequality)
        """
        bounds = [WEIGHT_BOUNDS] * 5 + [SIDEWAYS_BOUNDS, STRONG_BOUNDS]
        constraints = [
            {"type": "eq",   "fun": lambda x: x[0]+x[1]+x[2]+x[3]+x[4] - 1.0},
            {"type": "ineq", "fun": lambda x: x[6] - x[5] - 1.0},  # strong - sideways >= 1
        ]
        result = minimize(
            fun=lambda x: -objective_fn(x),   # negate to convert max → min
            x0=x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 500},
        )
        return result.x

    def optimize(self) -> OptimizationResult:
        """
        Run the full optimization pipeline.

        Steps:
        1. Evaluate current hand-tuned parameters as baseline.
        2. Sample 500 grid points via rejection sampling (Dirichlet weights).
        3. Run three SLSQP refinements: accuracy, Sharpe, blended.
        4. Return OptimizationResult with all four ParameterSets.

        Returns:
            OptimizationResult with current, accuracy_optimal, sharpe_optimal,
            blended_optimal, dataset_rows, and horizon_coverage.
        """
        # ── Horizon coverage ──────────────────────────────────────────────────
        coverage = {
            label: int(np.sum(~np.isnan(self._returns[:, i])))
            for i, label in enumerate(HORIZON_LABELS)
        }

        # ── Current baseline ──────────────────────────────────────────────────
        cur_w = MarketRegimeDetector.WEIGHTS
        cur_sideways = float(MarketRegimeDetector.REGIME_THRESHOLDS["Weak Bullish"])
        cur_strong = float(MarketRegimeDetector.REGIME_THRESHOLDS["Strong Bullish"])
        x_current = np.array([
            cur_w["trend"], cur_w["volatility"], cur_w["momentum"],
            cur_w["onchain"], cur_w["sentiment"],
            cur_sideways, cur_strong,
        ])
        fa_cur, fb_cur = self._eval_both(x_current)
        current = self._x_to_param_set(x_current, fa_cur, fb_cur)

        # ── Grid warm-start ───────────────────────────────────────────────────
        logger.info(f"Running grid warm-start with {GRID_SAMPLES} samples...")
        grid_points = self._sample_grid(GRID_SAMPLES)

        best_x_a, best_a = x_current.copy(), fa_cur
        best_x_b, best_b = x_current.copy(), fb_cur
        best_x_blend, best_blend = x_current.copy(), 0.5 * fa_cur + 0.5 * fb_cur

        for x in grid_points:
            fa, fb = self._eval_both(x)
            blend = 0.5 * fa + 0.5 * fb
            if fa > best_a:
                best_a, best_x_a = fa, x.copy()
            if fb > best_b:
                best_b, best_x_b = fb, x.copy()
            if blend > best_blend:
                best_blend, best_x_blend = blend, x.copy()

        # ── SLSQP runs ────────────────────────────────────────────────────────
        logger.info("Running SLSQP: accuracy objective...")
        x_acc = self._run_slsqp(best_x_a, self._fitness_a)
        fa_acc, fb_acc = self._eval_both(x_acc)

        logger.info("Running SLSQP: Sharpe objective...")
        x_shr = self._run_slsqp(best_x_b, self._fitness_b)
        fa_shr, fb_shr = self._eval_both(x_shr)

        logger.info("Running SLSQP: blended objective...")
        x_blend = self._run_slsqp(
            best_x_blend,
            lambda x: 0.5 * self._fitness_a(x) + 0.5 * self._fitness_b(x)
        )
        fa_bld, fb_bld = self._eval_both(x_blend)

        return OptimizationResult(
            current=current,
            accuracy_optimal=self._x_to_param_set(x_acc, fa_acc, fb_acc),
            sharpe_optimal=self._x_to_param_set(x_shr, fa_shr, fb_shr),
            blended_optimal=self._x_to_param_set(x_blend, fa_bld, fb_bld),
            dataset_rows=len(self._df),
            horizon_coverage=coverage,
        )

    def _sample_grid(self, n: int) -> list[np.ndarray]:
        """
        Sample n valid parameter combinations using rejection sampling.

        Weights drawn from Dirichlet([1,1,1,1,1]), rejected if any weight
        falls outside [0.05, 0.60]. Thresholds drawn uniformly from their
        bounds, rejected if strong - sideways < 1.
        """
        rng = np.random.default_rng(seed=42)
        samples = []
        attempts = 0
        while len(samples) < n and attempts < n * 50:
            attempts += 1
            w = rng.dirichlet([1.0, 1.0, 1.0, 1.0, 1.0])
            if np.any(w < WEIGHT_BOUNDS[0]) or np.any(w > WEIGHT_BOUNDS[1]):
                continue
            sideways = rng.uniform(*SIDEWAYS_BOUNDS)
            strong = rng.uniform(*STRONG_BOUNDS)
            if strong - sideways < 1.0:
                continue
            samples.append(np.array([*w, sideways, strong]))
        if len(samples) < n:
            logger.warning(
                f"Grid warm-start: only collected {len(samples)}/{n} valid samples"
            )
        return samples
