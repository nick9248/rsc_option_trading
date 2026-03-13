# Regime Weight Optimizer — Design Spec

**Date:** 2026-03-13
**Status:** Approved
**Goal:** Use historical regime detection snapshots to find optimal component weights and classification thresholds via multi-horizon, multi-objective optimization.

---

## Problem Statement

The current `MarketRegimeDetector` uses hand-tuned weights `[trend=0.30, vol=0.15, momentum=0.20, onchain=0.25, sentiment=0.10]` and fixed thresholds (sideways=±20, strong=±55). These were designed by reasoning, not validated against real outcomes. Historical data has now accumulated in the database — enough to measure how well different parameter combinations actually predicted price direction.

**This spec covers dataset preparation and weight/threshold optimization only.** Model training (ML classifiers, neural nets) is a separate future task.

---

## Constraints

- **DB is read-only** — no new tables, no schema changes, no writes of any kind
- **No changes to `MarketRegimeDetector`** — optimized values are reported as output; the user decides whether to apply them
- **Handles data gaps** — collection has not been continuous; missing horizons are skipped per-row, not dropped entirely
- **Python 3.13, existing venv** — no new heavy dependencies beyond `scipy` and `numpy` (already present)

---

## Architecture

Three new files, nothing else changes:

```
coding/core/database/regime_dataset_builder.py   — DB → raw DataFrame
coding/core/analytics/regime_weight_optimizer.py — optimization logic
scripts/optimize_regime_weights.py               — CLI entrypoint
```

---

## Component 1: RegimeDatasetBuilder

**Location:** `coding/core/database/regime_dataset_builder.py`
**Responsibility:** Query existing tables and produce a raw dataset DataFrame. Read-only.

### Data Sources (read-only)

| Table | Columns used |
|-------|-------------|
| `regime_detections` | `detected_at`, `currency`, `current_price`, `trend_score`, `volatility_score`, `momentum_score`, `onchain_score`, `sentiment_score` |
| `ohlcv_history` | `date`, `currency`, `close` — for forward price lookup |

### Output schema (one row per detection)

| Column | Type | Description |
|--------|------|-------------|
| `detected_at` | datetime | Snapshot timestamp |
| `currency` | str | BTC or ETH |
| `current_price` | float | Price at detection time |
| `trend_score` | float | Stored component score |
| `volatility_score` | float | Stored component score |
| `momentum_score` | float | Stored component score |
| `onchain_score` | float | Stored component score |
| `sentiment_score` | float | Stored component score |
| `return_4h` | float \| None | % price change at T+4h |
| `return_8h` | float \| None | % price change at T+8h |
| `return_12h` | float \| None | % price change at T+12h |
| `return_24h` | float \| None | % price change at T+24h |
| `return_48h` | float \| None | % price change at T+48h |
| `return_72h` | float \| None | % price change at T+72h |
| `return_7d` | float \| None | % price change at T+7d |
| `return_30d` | float \| None | % price change at T+30d |

### Forward price lookup logic

For a given detection at time `T` and horizon `H`:
1. Query `ohlcv_history` for the closest `close` price where `date` is between `T+H×0.9` and `T+H×1.1` (±10% tolerance window)
2. If no record found in that window → `None` (gap in data)
3. `return_H = (future_close - current_price) / current_price * 100`

Rows where **all 8 horizons are None** are dropped. All others are kept — the optimizer handles partial availability per row.

### Interface

```python
class RegimeDatasetBuilder:
    def __init__(self, repository: DatabaseRepository)
    def build(self, currency: str = "BTC") -> pd.DataFrame
    def summary(self, df: pd.DataFrame) -> str  # prints coverage stats
```

---

## Component 2: RegimeWeightOptimizer

**Location:** `coding/core/analytics/regime_weight_optimizer.py`
**Responsibility:** Given the raw dataset, find optimal weights and thresholds.

### Parameters being optimized (7 total)

| Parameter | Current value | Search bounds |
|-----------|--------------|---------------|
| `w_trend` | 0.30 | [0.05, 0.60] |
| `w_volatility` | 0.15 | [0.05, 0.60] |
| `w_momentum` | 0.20 | [0.05, 0.60] |
| `w_onchain` | 0.25 | [0.05, 0.60] |
| `w_sentiment` | 0.10 | [0.05, 0.60] |
| `sideways_threshold` | 20 | [10, 30] |
| `strong_threshold` | 55 | [40, 70] |

**Constraint:** `w_trend + w_volatility + w_momentum + w_onchain + w_sentiment = 1.0`

### Fitness Function A — Directional Accuracy

For a given parameter set and dataset row:
1. Recompute `composite = Σ(w_i × score_i)`
2. Classify: `composite ≥ strong` → Strong Bullish, `composite ≥ sideways` → Weak Bullish, `composite ≥ -sideways` → Sideways, `composite ≥ -strong` → Weak Bearish, else Strong Bearish
3. Map to direction: Bullish variants → `+1`, Sideways → `0`, Bearish variants → `-1`
4. For each available horizon `H`:
   - If `return_H > +1.5%` → actual = `+1`
   - If `return_H < -1.5%` → actual = `-1`
   - Else → actual = `0`
5. Score = 1 if predicted direction == actual direction, else 0

`fitness_A = mean(score) across all (row, horizon) pairs where horizon is not None`

### Fitness Function B — Simulated Sharpe

For a given parameter set and dataset row:
1. Compute direction as above (`+1`, `0`, `-1`)
2. For each available horizon `H`: `pnl_H = direction × return_H`
3. Collect all `pnl` values across all rows and horizons

`fitness_B = mean(pnl) / std(pnl)` (raw Sharpe, not annualized — horizons are mixed)

### Optimization runs

The optimizer runs SLSQP (Sequential Least Squares Programming via `scipy.optimize.minimize`) three times, each starting from the best point found by a coarse grid search:

| Run | Objective | Output |
|-----|-----------|--------|
| 1 | Maximize `fitness_A` | `result_accuracy` |
| 2 | Maximize `fitness_B` | `result_sharpe` |
| 3 | Maximize `0.5×A + 0.5×B` | `result_blended` |

**Grid search:** Before each SLSQP run, sample ~500 random valid parameter combinations (Dirichlet distribution for weights, uniform for thresholds) and start SLSQP from the best-scoring point. This avoids local optima.

### Interface

```python
class RegimeWeightOptimizer:
    def __init__(self, df: pd.DataFrame, directional_threshold: float = 1.5)
    def optimize(self) -> OptimizationResult

@dataclass
class OptimizationResult:
    current_fitness_a: float
    current_fitness_b: float
    accuracy_optimal: ParameterSet
    sharpe_optimal: ParameterSet
    blended_optimal: ParameterSet

@dataclass
class ParameterSet:
    weights: dict[str, float]      # {trend, volatility, momentum, onchain, sentiment}
    sideways_threshold: float
    strong_threshold: float
    fitness_a: float
    fitness_b: float
```

---

## Component 3: CLI Script

**Location:** `scripts/optimize_regime_weights.py`
**Responsibility:** Wire everything together, print results.

### Usage

```bash
python -m scripts.optimize_regime_weights --currency BTC
python -m scripts.optimize_regime_weights --currency ETH
python -m scripts.optimize_regime_weights --currency BTC --directional-threshold 2.0
```

### Output (console)

```
=== REGIME WEIGHT OPTIMIZER ===
Currency: BTC
Dataset: 142 detections (2025-11-01 → 2026-03-13)

Horizon coverage:
  4h:  138/142 (97%)   8h:  135/142 (95%)   12h: 130/142 (92%)
  24h: 121/142 (85%)   48h:  98/142 (69%)   72h:  81/142 (57%)
  7d:   44/142 (31%)   30d:   9/142  (6%)

────────────────────────────────────────────────
CURRENT (hand-tuned):
  trend=0.30  vol=0.15  momentum=0.20  onchain=0.25  sentiment=0.10
  sideways=±20  strong=±55
  Accuracy: 61.2%   Sharpe: 0.43

ACCURACY-OPTIMAL:
  trend=X.XX  vol=X.XX  momentum=X.XX  onchain=X.XX  sentiment=X.XX
  sideways=±XX  strong=±XX
  Accuracy: XX.X%   Sharpe: X.XX
  Δ accuracy: +X.X pp

SHARPE-OPTIMAL:
  ...
  Accuracy: XX.X%   Sharpe: X.XX
  Δ Sharpe: +X.XX

BLENDED:
  ...
  Accuracy: XX.X%   Sharpe: X.XX
────────────────────────────────────────────────
To apply: update WEIGHTS and REGIME_THRESHOLDS in market_regime_detector.py
```

---

## What This Does NOT Do

- Does not write to any database table
- Does not modify `MarketRegimeDetector` — results are informational
- Does not train an ML model — that is a future task
- Does not optimize the individual scoring functions (bucket thresholds inside each component) — only the top-level weights and regime classification thresholds

---

## Testing

Unit tests cover:
- `RegimeDatasetBuilder`: mock DB, verify forward-price lookup tolerances, verify gap handling
- `RegimeWeightOptimizer`: synthetic dataset with known optimal weights, verify optimizer recovers them
- Fitness functions A and B: hand-computed cases verify correctness

Integration test: run full pipeline with a small fixture dataset end-to-end.
