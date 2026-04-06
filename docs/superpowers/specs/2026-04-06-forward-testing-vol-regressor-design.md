# Forward Testing — Vol Regressor (24h Realized Volatility)

**Date:** 2026-04-06  
**Status:** Approved  
**Branch:** understanding/on_chain_analysis  

---

## Overview

Add a Forward Testing tab to the GUI for manually tracking ML model predictions against real outcomes. First experiment: the volatility regressor predicts 24h annualized realized vol; the next day the user verifies whether the actual move matched.

The tab is designed to host multiple forward testing experiments over time. Each experiment is a separate tile.

---

## 1. Database

**Migration:** `migrations/012_add_vol_predictions.sql`

```sql
CREATE TABLE vol_predictions (
    id                   SERIAL PRIMARY KEY,
    predicted_at         TIMESTAMP NOT NULL,
    currency             VARCHAR(10) NOT NULL,
    model_id             VARCHAR(100) NOT NULL,
    predicted_vol_24h    DECIMAL(8,4) NOT NULL,
    predicted_daily_move DECIMAL(8,4) NOT NULL,
    verified_at          TIMESTAMP,
    actual_vol_24h       DECIMAL(8,4),
    actual_price_change  DECIMAL(8,4),
    within_1sigma        BOOLEAN,
    error_pct            DECIMAL(8,4),
    UNIQUE(predicted_at, currency)
);
```

**Column notes:**
- `predicted_vol_24h` — annualized % from the model
- `predicted_daily_move` — `predicted_vol_24h / sqrt(365)`, the ±1σ daily range
- `verified_at` — NULL until the user runs verification the next day
- `actual_vol_24h` — realized vol computed from hourly_snapshots: `std(log_returns) * sqrt(24 * 365)`
- `actual_price_change` — `abs(close_end - close_start) / close_start` over the 24h window
- `within_1sigma` — TRUE if `actual_price_change <= predicted_daily_move`
- `error_pct` — `predicted_vol_24h - actual_vol_24h` (positive = over-predicted)

---

## 2. Service Layer

**New file:** `coding/service/ml/forward_testing_service.py`

```
ForwardTestingService
  ├── make_prediction(currency: str) -> dict
  ├── verify_prediction(currency: str) -> dict
  ├── get_history(currency: str, limit: int = 14) -> list[dict]
  └── get_scorecard(currency: str) -> dict
```

### `make_prediction(currency)`
1. Call `MLPredictor.predict_volatility(currency)`
2. Compute `predicted_daily_move = predicted_vol_24h / sqrt(365)`
3. Upsert into `vol_predictions` (ON CONFLICT on `predicted_at, currency` → overwrite)
4. `predicted_at` = current UTC timestamp truncated to the hour
5. Return prediction dict for GUI display

### `verify_prediction(currency)`
1. Find the most recent unverified row (`verified_at IS NULL`) for the currency
2. Fetch `hourly_snapshots` for the 24h window: `[predicted_at, predicted_at + 24h]`
3. Require at least 20 hourly rows — if fewer exist, return error (window not complete yet)
4. Compute actual realized vol: `std(log(close[i] / close[i-1])) * sqrt(24 * 365)`
5. Compute `actual_price_change = abs(close[-1] - close[0]) / close[0]`
6. Set `within_1sigma`, `error_pct`, `verified_at = now()`
7. Update the row and return result dict

### `get_history(currency, limit=14)`
- Returns last `limit` rows for currency, newest first
- Each row includes all columns; unverified rows have `actual_vol_24h = None`

### `get_scorecard(currency)`
- Queries only verified rows (`verified_at IS NOT NULL`)
- Returns:
  - `n_verified`: count of verified predictions
  - `hit_rate`: % of rows where `within_1sigma = TRUE`
  - `mean_error`: mean of `abs(error_pct)` — average magnitude of error regardless of direction
  - `bias`: mean of `error_pct` — signed average (positive = model over-predicts vol, negative = under-predicts)

---

## 3. GUI

### Tab structure
**New directory:** `coding/gui/forward_testing/`
- `__init__.py`
- `forward_testing_tab.py` — tab container, QVBoxLayout, holds tiles, scrollable for future growth
- `vol_regressor_tile.py` — the vol regressor tile widget

**Tab name in navigation:** `Forward Testing`

### Tile layout: "Vol Regressor — 24h Realized Volatility"

```
┌──────────────────────────────────────────────────────────┐
│  Vol Regressor — 24h Realized Volatility                 │
├──────────────┬──────────────┬──────────────┬────────────-┤
│  Hit Rate    │  Mean Error  │  Bias        │  N Tests    │
│  68.4%       │  3.2%        │  +1.1%       │  19         │
├──────────────────────────────────────────────────────────┤
│  Date   │ CCY │ Pred Vol │ Daily ±1σ │ Actual │ Result   │
│  Apr 05 │ BTC │  42.1%   │  ±2.2%    │  38.7% │  PASS    │
│  Apr 05 │ ETH │  67.3%   │  ±3.5%    │  71.2% │  PASS    │
│  Apr 04 │ BTC │  45.0%   │  ±2.4%    │  58.1% │  FAIL    │
│  Apr 04 │ ETH │  51.2%   │  ±2.7%    │   —    │  pending │
├──────────────────────────────────────────────────────────┤
│ [Predict BTC] [Predict ETH]  [Verify BTC] [Verify ETH]  │
│ Status: Ready                                            │
└──────────────────────────────────────────────────────────┘
```

**Behaviour:**
- Scorecard row is always visible; shows `—` if no verified predictions yet
- Table shows last 14 rows (7 per currency), newest first
- Unverified rows: Actual = `—`, Result = `pending`
- Buttons disable + show spinner during worker execution (inference ~90s)
- Status label below buttons shows last action result or error
- After predict or verify completes, scorecard and table refresh automatically

### Worker pattern
Each button spawns a `QThread` worker that calls the service method. On completion, emits signal → GUI updates scorecard + table. Follows same worker pattern as existing GUI tabs.

---

## 4. Data Flow

```
User clicks "Predict BTC"
  → PredictWorker(QThread)
    → ForwardTestingService.make_prediction("BTC")
      → MLPredictor.predict_volatility("BTC")
      → INSERT into vol_predictions
    → emit result
  → GUI refreshes table + scorecard

Next day: User clicks "Verify BTC"
  → VerifyWorker(QThread)
    → ForwardTestingService.verify_prediction("BTC")
      → SELECT unverified row
      → SELECT hourly_snapshots [predicted_at .. predicted_at+24h]
      → compute actual_vol_24h, actual_price_change, within_1sigma, error_pct
      → UPDATE vol_predictions
    → emit result
  → GUI refreshes table + scorecard
```

---

## 5. File Checklist

| File | Action |
|---|---|
| `migrations/012_add_vol_predictions.sql` | New |
| `coding/service/ml/forward_testing_service.py` | New |
| `coding/gui/forward_testing/__init__.py` | New |
| `coding/gui/forward_testing/forward_testing_tab.py` | New |
| `coding/gui/forward_testing/vol_regressor_tile.py` | New |
| `coding/gui/main_window.py` (or navigation) | Edit — add Forward Testing tab |
| `scripts/run_migration.py` or direct SQL | Run migration 012 |

---

## 6. Out of Scope

- Automated prediction/verification (manual only, by design)
- VPS-side inference (VPS lacks backfilled OHLCV data)
- Regime classifier forward testing (not ready — accuracy too low)
- Push notifications or alerts when verification window opens
