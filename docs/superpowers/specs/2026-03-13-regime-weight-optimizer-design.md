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
- **One read method added to `DatabaseRepository`** — `get_ohlcv_by_date_range(currency, start, end)` (read-only, no schema impact)
- **Handles data gaps** — collection has not been continuous; missing horizons are skipped per-row, not dropped entirely
- **Python 3.13, existing venv** — no new heavy dependencies beyond `scipy` and `numpy` (already present)

---

## Architecture

Three new files plus one read method on the existing repository:

```
coding/core/database/regime_dataset_builder.py   — DB → raw DataFrame
coding/core/analytics/regime_weight_optimizer.py — optimization logic
scripts/optimize_regime_weights.py               — CLI entrypoint
coding/core/database/repository.py               — add get_ohlcv_by_date_range (read-only)
```

---

## Component 1: RegimeDatasetBuilder

**Location:** `coding/core/database/regime_dataset_builder.py`
**Responsibility:** Query existing tables and produce a raw dataset DataFrame. Read-only.

### Data Sources (read-only)

| Table | Columns used | Horizon range |
|-------|-------------|---------------|
| `regime_detections` | `detected_at`, `currency`, `current_price`, all 5 component scores | All horizons — primary price source for ≤72h |
| `ohlcv_history` | `date`, `currency`, `close`, `instrument_name` | 7d and 30d only (daily candles) |

**Why two sources:** `ohlcv_history` stores daily candles — sufficient for 7d/30d but too coarse for 4h/8h/12h/24h/48h/72h. For short horizons, `regime_detections` itself serves as the price series: if detection ran multiple times per day, each row's `current_price` and `detected_at` form a sub-daily price timeline. This is the correct source because it reflects exactly the prices the system observed.

**Repository methods used:**
- `get_regime_detections(currency, start_time, end_time)` — existing method, no changes needed
- `get_ohlcv_by_date_range(currency, start, end)` — new method added to `DatabaseRepository`

### Data Cleaning

Before forward price lookup, drop any row where `current_price IS NULL` or `current_price = 0`. These rows cannot produce valid returns.

### Forward price lookup logic

**Short horizons (4h, 8h, 12h, 24h, 48h, 72h):** For detection at time `T` and horizon `H` (expressed as `timedelta`):
1. Query `regime_detections` for rows of the same `currency` where `detected_at` is between `T + H×0.9` and `T + H×1.1`
2. If multiple rows fall in the window, select the one closest to `T + H` (i.e., `ORDER BY ABS(EXTRACT(EPOCH FROM (detected_at - (T + H)))) LIMIT 1`)
3. If no row found → `None`
4. `return_H = (found_price - current_price) / current_price × 100`

**Long horizons (7d, 30d):** Use `get_ohlcv_by_date_range(currency, start=T+H×0.9, end=T+H×1.1)`. This method queries `ohlcv_history` filtering by `instrument_name = '{CURRENCY}-PERPETUAL'` (e.g., `BTC-PERPETUAL`) and `date BETWEEN start AND end`. It returns `List[Dict]` where each dict has `{"date": datetime, "close": float}` (date stored as UTC-aware datetime, close as float). If multiple rows remain, select the row where `abs(row["date"] - (T+H))` is minimized (Python-side tiebreaker).

**Tolerance window:** ±10% of horizon duration. Examples:
- 4h → window is [3h 36m, 4h 24m] after T
- 24h → window is [21h 36m, 26h 24m] after T
- 7d → window is [6d 4h 48m, 7d 19h 12m] after T

**Timezone:** Both `ohlcv_history.date` and `regime_detections.detected_at` are stored as `TIMESTAMP WITHOUT TIME ZONE` in PostgreSQL and are returned as timezone-naive datetimes by psycopg2. All datetime operations in `build()` use timezone-naive UTC datetimes throughout — do not mix naive and aware datetimes. Use `datetime.utcnow()` (not `datetime.now(tz=timezone.utc)`) when constructing boundaries for DB queries.

**Coverage note:** If detection runs every 4–6 hours, the 4h horizon may have low coverage. The ±10% window is fixed — do not auto-widen. If a horizon has fewer than 20 matched rows, `summary()` emits a warning.

**Implementation note for `build()`:**
1. Calls `get_regime_detections(currency, start_time=datetime(2020,1,1), end_time=datetime.utcnow())` to fetch all available data (both are timezone-naive UTC).
2. Re-sorts the result by `detected_at` ascending (the DB method returns DESC; an explicit sort is required).
3. The sorted DataFrame serves as both the dataset rows and the forward price pool for short-horizon lookups — no second DB call is made.
4. For each row T, scans the same DataFrame for entries in the tolerance window (since it is sorted ascending, entries with `detected_at > T` are naturally later in the DataFrame).

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

Rows where **all 8 horizons are None** are dropped. All others are kept — the optimizer handles partial availability per row.

If `len(df) < 30` after cleaning, `build()` logs a warning: "Dataset too small for reliable optimization — N detections available, minimum recommended is 30." `optimize()` proceeds regardless; results are informational only.

### Interface

```python
class RegimeDatasetBuilder:
    def __init__(self, repository: DatabaseRepository)
    def build(self, currency: str = "BTC") -> pd.DataFrame
    def summary(self, df: pd.DataFrame) -> str  # returns formatted coverage string; caller prints it
```

`ParameterSet.weights` keys match `MarketRegimeDetector.WEIGHTS` exactly: `"trend"`, `"volatility"`, `"momentum"`, `"onchain"`, `"sentiment"`. The CLI abbreviates `"volatility"` to `"vol"` only in display output.

---

## Component 2: RegimeWeightOptimizer

**Location:** `coding/core/analytics/regime_weight_optimizer.py`
**Responsibility:** Given the raw dataset, find optimal weights and thresholds.

### Parameter vector ordering

The optimization operates on a flat array `x` of length 7, always in this order:

```python
PARAM_ORDER = ["trend", "volatility", "momentum", "onchain", "sentiment",
               "sideways_threshold", "strong_threshold"]
# x[0]=w_trend, x[1]=w_vol, x[2]=w_momentum, x[3]=w_onchain, x[4]=w_sentiment
# x[5]=sideways_threshold, x[6]=strong_threshold
```

### Parameters being optimized (7 total)

| Parameter | Index | Current value | Search bounds |
|-----------|-------|--------------|---------------|
| `w_trend` | 0 | 0.30 | [0.05, 0.60] |
| `w_volatility` | 1 | 0.15 | [0.05, 0.60] |
| `w_momentum` | 2 | 0.20 | [0.05, 0.60] |
| `w_onchain` | 3 | 0.25 | [0.05, 0.60] |
| `w_sentiment` | 4 | 0.10 | [0.05, 0.60] |
| `sideways_threshold` | 5 | 20 | [10, 30] |
| `strong_threshold` | 6 | 55 | [40, 70] |

**Constraints:**
- `w_trend + w_volatility + w_momentum + w_onchain + w_sentiment = 1.0` (equality)
- `strong_threshold - sideways_threshold >= 1` (inequality — prevents degenerate classification)

### Classification in the fitness function

The optimizer reproduces the five-band threshold classification, collapsed to three direction values (+1, 0, -1). The ADX override is intentionally excluded (see below).

```
composite >= +strong_threshold          → +1  (Strong Bullish collapsed to Bullish)
composite >= +sideways_threshold        → +1  (Weak Bullish collapsed to Bullish)
composite >= -sideways_threshold        → 0   (Sideways)
composite >= -strong_threshold          → -1  (Weak Bearish collapsed to Bearish)
else (composite < -strong_threshold)    → -1  (Strong Bearish collapsed to Bearish)
```

Note: `sideways_threshold` and `strong_threshold` are stored as positive magnitudes. The classification applies `+threshold` and `-threshold` symmetrically. The full symmetric band set `[-strong, -sideways, +sideways, +strong]` is fully defined by these two positive values.

The boundaries are inclusive (≥), matching `_classify_regime` logic. Implemented as a top-to-bottom elif chain:
```python
if   composite >= +strong_threshold:   return +1
elif composite >= +sideways_threshold: return +1
elif composite >= -sideways_threshold: return  0
elif composite >= -strong_threshold:   return -1
else:                                  return -1
```

**ADX override intentionally excluded:** The `_classify_regime` ADX override (forces directional label when ADX > 25 and DI spread > 5 in Sideways range) requires `plus_di`/`minus_di` values which are not stored in `regime_detections`. The override fires only for a minority of Sideways cases and its parameters (ADX threshold=25, DI spread=5) are not being optimized. The optimizer calibrates the primary scoring weights against this simplified model; the override remains unchanged in production. This approximation is documented and accepted.

### Current baseline

The `current` `ParameterSet` is built from `MarketRegimeDetector`'s existing constants. Extract at runtime:
```python
sideways_threshold = MarketRegimeDetector.REGIME_THRESHOLDS["Weak Bullish"]   # → 20
strong_threshold   = MarketRegimeDetector.REGIME_THRESHOLDS["Strong Bullish"] # → 55
weights            = MarketRegimeDetector.WEIGHTS  # → {trend:0.30, ...}
```
The thresholds are symmetric — the full band set `[-strong, -sideways, +sideways, +strong]` is defined by two positive magnitudes. `"Weak Bullish"` gives `+20` (`sideways_threshold`) and `"Strong Bullish"` gives `+55` (`strong_threshold`). The negative keys (`"Sideways"`, `"Weak Bearish"`) are not needed because they equal `-sideways_threshold` and `-strong_threshold` respectively.

Evaluate both fitness functions at these values to populate `current.fitness_a` and `current.fitness_b`.

### Fitness Function A — Directional Accuracy

```
directional_threshold = 1.5%  (configurable)

For each (row, available horizon) pair:
  composite = Σ(w_i × score_i)
  predicted = classify(composite, sideways_threshold, strong_threshold)  → +1, 0, -1
  if return_H > +directional_threshold:  actual = +1
  elif return_H < -directional_threshold: actual = -1
  else:                                   actual = 0
  correct = 1 if predicted == actual else 0

fitness_A = mean(correct) across all (row, horizon) pairs where horizon is not None
```

For display, multiply `fitness_A` by 100 to show as percentage (e.g., 0.612 → 61.2%).

### Fitness Function B — Per-Horizon Sharpe (averaged)

To avoid conflating returns across incompatible time scales, Sharpe is computed per horizon and averaged:

```
For each available horizon H in {4h, 8h, 12h, 24h, 48h, 72h, 7d, 30d}:
  For each row with return_H not None:
    position = classify(composite_row, sideways_threshold, strong_threshold)  → +1, 0, -1
    pnl = position × return_H

  pnl_H = list of pnl values for this horizon
  if len(pnl_H) < 2 or np.std(pnl_H, ddof=0) == 0:
    sharpe_H = 0.0  # no data, single point, or degenerate (flat market / all Sideways)
  else:
    sharpe_H = np.mean(pnl_H) / np.std(pnl_H, ddof=0)  # population std (ddof=0)

available_horizons = [H for H where at least 1 row exists]
if len(available_horizons) == 0:
    fitness_B = 0.0
else:
    fitness_B = mean(sharpe_H for H in available_horizons)
```

Raw per-horizon Sharpe, not annualized. Averaging across horizons weights each time frame equally.

### Population of `ParameterSet.fitness_a` and `fitness_b`

Both `fitness_a` and `fitness_b` must always be populated for every `ParameterSet` (including `current`, `accuracy_optimal`, `sharpe_optimal`, and `blended_optimal`), regardless of which objective was optimized. After SLSQP converges on any run, evaluate both fitness functions at the result to fill both fields.

### Grid warm-start sampling

Sample 500 valid parameter combinations using rejection sampling:
1. Draw weights from `np.random.dirichlet([1,1,1,1,1])` — this enforces sum=1 and non-negativity
2. **Reject** the sample if any weight < 0.05 or > 0.60 (bounds violation) — draw again
3. Draw `sideways_threshold` uniformly from [10, 30] and `strong_threshold` uniformly from [40, 70]
4. **Reject** the sample if `strong_threshold - sideways_threshold < 1` — draw again
5. Repeat until 500 valid samples are collected

Evaluate both fitness functions A and B for each sample. Compute blended score as `0.5×A + 0.5×B`. Select the best starting point for each of the three optimization runs from these evaluated values.

### Optimization runs

**Step 2 — SLSQP refinement (one run per objective):**

| Run | Objective | Starting point | Output |
|-----|-----------|---------------|--------|
| 1 | Maximize `fitness_A` | Best grid point for A | `result_accuracy` |
| 2 | Maximize `fitness_B` | Best grid point for B | `result_sharpe` |
| 3 | Maximize `0.5×A + 0.5×B` | Best grid point for blended | `result_blended` |

**SLSQP setup:** `scipy.optimize.minimize` minimizes, so pass the negated objective. Concrete constraint specification:

```python
bounds = [(0.05, 0.60)] * 5 + [(10, 30), (40, 70)]

constraints = [
    {"type": "eq",   "fun": lambda x: x[0]+x[1]+x[2]+x[3]+x[4] - 1.0},
    {"type": "ineq", "fun": lambda x: x[6] - x[5] - 1.0},  # strong - sideways >= 1
]

result = scipy.optimize.minimize(
    fun=lambda x: -objective(x),   # negate to convert max → min
    x0=best_grid_point,
    method="SLSQP",
    bounds=bounds,
    constraints=constraints,
)
```

### Interface

```python
class RegimeWeightOptimizer:
    def __init__(self, df: pd.DataFrame, directional_threshold: float = 1.5)
    def optimize(self) -> OptimizationResult

@dataclass
class ParameterSet:
    weights: dict[str, float]      # keys: trend, volatility, momentum, onchain, sentiment
    sideways_threshold: float
    strong_threshold: float
    fitness_a: float               # accuracy score (0–1) for this parameter set
    fitness_b: float               # averaged per-horizon Sharpe for this parameter set

@dataclass
class OptimizationResult:
    current: ParameterSet          # baseline — hand-tuned values evaluated on the dataset
    accuracy_optimal: ParameterSet
    sharpe_optimal: ParameterSet
    blended_optimal: ParameterSet
    dataset_rows: int
    horizon_coverage: dict[str, int]  # non-None row count per horizon, e.g. {"4h": 138, "8h": 135, ...}
    # CLI displays as "138/dataset_rows (97%)" — dataset_rows is the denominator
```

`current` is computed by evaluating the existing hard-coded weights and thresholds through the same fitness functions — same code path, no special treatment.

---

## Component 3: CLI Script

**Location:** `scripts/optimize_regime_weights.py`

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

────────────────────────────────────────────────────────────
CURRENT (hand-tuned):
  trend=0.30  vol=0.15  momentum=0.20  onchain=0.25  sentiment=0.10
  sideways=±20  strong=±55
  Accuracy: 61.2%   Sharpe: 0.43

ACCURACY-OPTIMAL:
  trend=X.XX  vol=X.XX  momentum=X.XX  onchain=X.XX  sentiment=X.XX
  sideways=±XX  strong=±XX
  Accuracy: XX.X% (+X.X pp)   Sharpe: X.XX

SHARPE-OPTIMAL:
  trend=X.XX  vol=X.XX  momentum=X.XX  onchain=X.XX  sentiment=X.XX
  sideways=±XX  strong=±XX
  Accuracy: XX.X%   Sharpe: X.XX (+X.XX)

BLENDED (50/50):
  trend=X.XX  vol=X.XX  momentum=X.XX  onchain=X.XX  sentiment=X.XX
  sideways=±XX  strong=±XX
  Accuracy: XX.X%   Sharpe: X.XX
  (no delta shown — blended objective has no single natural baseline to diff against)
────────────────────────────────────────────────────────────
To apply: update WEIGHTS and REGIME_THRESHOLDS in market_regime_detector.py
```

---

## What This Does NOT Do

- Does not write to any database table
- Does not modify `MarketRegimeDetector` — results are informational only
- Does not train an ML model — that is a future task
- Does not optimize the internal scoring bucket thresholds within each component — only top-level weights and regime classification thresholds
- Does not reproduce the ADX override in the fitness function (documented approximation)

---

## Testing

- **`RegimeDatasetBuilder`**: mock repository, verify forward-price lookup tolerances for both sources, verify gap handling, verify tiebreaker (closest match selected), verify NULL `current_price` rows are dropped, verify PERPETUAL instrument filter for long horizons
- **`RegimeWeightOptimizer`**: synthetic dataset with known optimal weights → verify optimizer recovers them within tolerance; verify per-horizon Sharpe std=0 guard returns 0.0; verify `strong - sideways >= 1` constraint is enforced; verify both `fitness_a` and `fitness_b` are populated on all four `ParameterSet` results
- **Fitness functions**: hand-computed cases for both A and B verify correctness; verify Fitness B averages across horizons rather than pooling
- **Integration**: run full pipeline with a small fixture dataset (10 rows, 3 horizons)
