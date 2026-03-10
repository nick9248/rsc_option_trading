# Flow Charts: Net Flow Redesign, Block Filter, Verification Suite

**Date**: 2026-03-10
**Branch**: quality-check
**Status**: Approved

---

## 1. Net Flow Chart Redesign

### Problem
The current `generate_net_flow_chart` renders 4 bars per strike (Call Buying, Call Selling, Put Buying, Put Selling). This is not net flow — it's raw directional flow split four ways. The chart name says "net" but the bars are not net values.

### Solution
Replace 4 traces with 2 traces that show true net flow per option type.

**Chart structure:**
- Orientation: **horizontal** (`orientation="h"`) — strikes on Y-axis (`y=strike_labels`), net volume on X-axis (`x=net_values`)
- 2 traces only:
  - **Call Net Flow** — emerald `#10b981`
  - **Put Net Flow** — indigo `#818cf8`
- Bar direction encodes sign: right of zero = net buying, left of zero = net selling
- One color per trace (no per-bar color array) — color identifies the option type, position identifies sign
- Zero line: **vertical** at `x=0` → use `fig.add_vline(x=0, ...)`
- Spot price: **horizontal** dashed line across the Y-axis → use `fig.add_hline(y=spot_strike_label, ...)` as annotation (categorical axis — use `add_annotation` not `add_hline` for categorical Y)
- Subtitle/annotation text: `"Net = Buy Volume − Sell Volume"`
- Legend: 2 items only (Call Net Flow, Put Net Flow)

**Hover templates (horizontal bars):**
```python
# Strikes are on Y-axis → %{y} for strike, %{x} for value
hovertemplate="<b>Strike: %{y}</b><br>Call Net: %{x:.4f} BTC<extra></extra>"
hovertemplate="<b>Strike: %{y}</b><br>Put Net: %{x:.4f} BTC<extra></extra>"
```

**Data computation (unchanged):**
```
call_net[strike] = call_buy_volume - call_sell_volume
put_net[strike]  = put_buy_volume  - put_sell_volume
```
Values are signed — positive = net buying, negative = net selling. Plotly naturally places positive bars right and negative bars left of x=0 with `barmode="relative"` or `barmode="group"`.

**Files affected:**
- `coding/core/analytics/chart_generator.py` — `generate_net_flow_chart` function

**What stays the same:** function signature, input dict structure, `save_chart` calls, hover JS injection, all callers.

---

## 2. Block / Non-Block / All Filter

### Problem
Traders want to separate institutional block flow from retail flow. Currently all trades are aggregated together with no way to isolate block-size activity.

### Definition of Block Trade
Consistent with existing `MarketWideCalculator.detect_block_trades` convention:
```
is_block = (amount × index_price) >= 100_000   # $100k notional threshold
```
No new DB column needed — derived from existing `amount` and `index_price` fields in `historical_trades`.

### Filter options
| Label | SQL condition |
|-------|--------------|
| All | (none) — current behavior |
| Block | `AND (amount * index_price) >= 100000` |
| Non-Block | `AND (amount * index_price) < 100000` |

### Data flow — per-expiry mode
```
User clicks filter button
  → FlowChartsWindow._on_filter_changed(filter_mode)
  → BuySellFlowAnalyzer(trade_filter=filter_mode).calculate()
  → _fetch_trades() appends SQL condition based on filter_mode
  → chart generators receive filtered flow_data (same dict structure)
  → distribution + net flow charts re-render from filtered flow_data
  → generate_flow_trend_chart called with same filter_clause injected into its own SQL
```

### Data flow — All Expirations mode
`get_aggregated_flow_metrics` reads from `buy_sell_flow_metrics` (pre-aggregated — no `amount`/`index_price` columns). Block filtering cannot be applied through this path.

**Solution**: When filter is **not** "All" AND "All Expirations" is selected, `_generate_aggregate_charts` bypasses `get_aggregated_flow_metrics` and instead:
1. Fetches active expirations from DB
2. Runs `BuySellFlowAnalyzer(trade_filter=filter_mode)` per expiration
3. Aggregates flow_data dicts in Python (summing buy/sell volumes per strike/type)
4. Passes the aggregated result to the chart generators

When filter = "All", the existing `get_aggregated_flow_metrics` path is used unchanged (performance is maintained for the default case).

**Owner of the per-expiration aggregation loop**: `FlowChartsWindow._generate_aggregate_charts()` — already has access to `self.repository` and `self.currency`.

**All 3 charts support block filtering in All Expirations mode:**
- Distribution + Net Flow: through the Python aggregation loop above
- Trend: through `generate_flow_trend_chart` with filter injected into its own SQL

### Two independent SQL paths both need the filter
There are **two separate places** where `historical_trades` is queried:
1. `BuySellFlowAnalyzer._fetch_trades()` — powers distribution + net flow charts
2. `generate_flow_trend_chart` — has its own independent SQL query against `historical_trades`

Both must have the `AND (amount * index_price) >= 100000` clause injected when block mode is active. These are parallel changes to two different functions.

### BuySellFlowAnalyzer changes
Add optional `trade_filter: str = "all"` param to `__init__`. In `_fetch_trades()`:
```python
filter_clause = {
    "block":     "AND (amount * index_price) >= 100000",
    "non_block": "AND (amount * index_price) < 100000",
}.get(self.trade_filter, "")
# inject into WHERE block via f-string (safe — not user input, controlled enum)
```

### generate_flow_trend_chart changes
Add optional `trade_filter: str = "all"` param. Inject same filter_clause into its `WHERE` block alongside the existing `expiration` and timestamp conditions.

### GUI changes
Add a 3-button toggle group to the controls bar of `FlowChartsWindow` (next to the expiration dropdown):

```
[All]  [Block]  [Non-Block]
```

- Default: **All** selected
- On click: set `self.current_filter`, regenerate charts
- Filter state persists when switching expirations
- Store as `self.current_filter: str = "all"`

**Files affected:**
- `coding/core/analytics/buy_sell_flow_analyzer.py` — add `trade_filter` param, SQL injection in `_fetch_trades`
- `coding/core/analytics/chart_generator.py` — rewrite `generate_net_flow_chart` (horizontal, 2 traces); add `trade_filter` param to `generate_flow_trend_chart` with SQL injection
- `coding/gui/dialogs/flow_charts_window.py` — add toggle buttons, `_on_filter_changed`, propagate filter; update `_generate_aggregate_charts` to bypass `get_aggregated_flow_metrics` when filter != "all"
- `coding/service/on_chain/on_chain_analysis_service.py` — pass `trade_filter="all"` (backward-compatible default)

---

## 3. Comprehensive Flow Data Verification Suite

### Purpose
End-to-end verification that the numbers in `buy_sell_flow_metrics` (and by extension the charts) correctly reflect the raw trades in `historical_trades`. Checks every transformation step.

### Script location
`scripts/verify_flow_data.py`

### 6 Cross-Checks

**Check 1 — Mathematical consistency** (in `buy_sell_flow_metrics`):
- For each row: `net_flow == round(buy_volume - sell_volume, 8)`
- `buy_sell_ratio == buy_volume / sell_volume` when sell > 0, `NULL` when sell == 0
- Reports: count of rows checked, count of discrepancies
- **PASS/FAIL**

**Check 2 — Trade count reconciliation** (raw trades vs stored metrics):
- For latest `captured_at` per (currency, expiration), per (strike, option_type):
  - COUNT `direction='buy'` in `historical_trades` within the 24h window → compare to `buy_count`
  - COUNT `direction='sell'` → compare to `sell_count`
- Reports: match rate, first 5 mismatches if any
- **PASS/FAIL**

**Check 3 — Volume reconciliation**:
- SUM `amount` per (strike, option_type, direction) from `historical_trades` within 24h window
- Compare to `buy_volume` / `sell_volume` in `buy_sell_flow_metrics`
- Tolerance: 1e-6 (floating point)
- Reports: match rate, largest absolute discrepancy
- **PASS/FAIL**

**Check 4 — Notional reconciliation**:
- SUM `amount * index_price` per (strike, option_type, direction) from `historical_trades` within 24h window
- Compare to `buy_notional` / `sell_notional` in `buy_sell_flow_metrics`
- Reports: match rate, largest discrepancy
- **PASS/FAIL**

**Check 5 — Direction semantics (informational only, never fails the suite)**:
- Sample up to 500 trades from `historical_trades` with non-NULL `tick_direction`
- Deribit `tick_direction` encoding:
  - 0 = "Plus tick" (price higher than previous)
  - 1 = "Zero-plus tick" (same price, previous change was up)
  - 2 = "Minus tick" (price lower than previous)
  - 3 = "Zero-minus tick" (same price, previous change was down)
- Hypothesis: `direction='buy'` correlates with `tick_direction IN (0, 1)` (buyer aggressor → price moves up)
- Compute correlation rate and **report it as informational** — no PASS/FAIL threshold
- Note in output: options markets have weaker tick/aggressor correlation than futures (mid-market crosses are common); a low percentage is not necessarily a sign of inverted direction
- If correlation < 45% (below random), flag as WARNING suggesting possible direction inversion
- **INFORMATIONAL** (warning threshold at 45%, not a hard fail)

**Check 6 — Time window verification**:
- For latest `captured_at` in `buy_sell_flow_metrics`, derive the window: `[captured_at - 24h, captured_at]`
- Verify all contributing trades in `historical_trades` fall within this window by cross-referencing timestamps
- Reports: count of out-of-window trades found (should be 0)
- **PASS/FAIL**

### Output format
```
=== Flow Data Verification: BTC ===

[CHECK 1] Mathematical consistency
  Rows checked: 1,240
  Discrepancies: 0
  PASS ✓

[CHECK 2] Trade count reconciliation
  Strike-type pairs checked: 620
  Mismatches: 0
  PASS ✓

[CHECK 3] Volume reconciliation
  Strike-type pairs checked: 620
  Mismatches: 0  (tolerance: 1e-6)
  PASS ✓

[CHECK 4] Notional reconciliation
  Strike-type pairs checked: 620
  Mismatches: 0
  PASS ✓

[CHECK 5] Direction semantics (informational)
  Trades sampled: 500
  Buy+tick correlation: 58.2%
  NOTE: Options markets have weaker aggressor/tick correlation than futures.
        This value is within the expected range for liquid options.

[CHECK 6] Time window verification
  Latest captured_at: 2026-03-10 14:30:00
  Window: 2026-03-09 14:30:00 → 2026-03-10 14:30:00
  Out-of-window trades in metrics: 0
  PASS ✓

=== SUMMARY ===
PASS: 5/5 hard checks
INFORMATIONAL: 1 (Check 5 — direction semantics)
All flow data verified correctly.
```

On FAIL: prints first 5 discrepant rows with expected vs actual values.

### Usage
```bash
python scripts/verify_flow_data.py --currency BTC
python scripts/verify_flow_data.py --currency ETH
python scripts/verify_flow_data.py --currency BTC --expiration 27MAR26
```

---

## Files Affected Summary

| File | Change |
|------|--------|
| `coding/core/analytics/chart_generator.py` | Rewrite `generate_net_flow_chart` (horizontal, 2 traces, corrected hover templates); add `trade_filter` param + SQL injection to `generate_flow_trend_chart` |
| `coding/core/analytics/buy_sell_flow_analyzer.py` | Add `trade_filter` param, inject SQL filter in `_fetch_trades` |
| `coding/gui/dialogs/flow_charts_window.py` | Add Block/Non-Block/All toggle buttons, `_on_filter_changed`, propagate filter; update `_generate_aggregate_charts` to bypass pre-aggregated path when filter != "all" |
| `coding/service/on_chain/on_chain_analysis_service.py` | Pass `trade_filter="all"` (backward-compatible) |
| `scripts/verify_flow_data.py` | New: 6-check verification script |

---

## Out of Scope
- DB migration for `is_block_trade` column (not needed — derived at query time)
- Saving block-filtered charts to `output/charts/flow_analysis/` (filter is on-demand GUI only)
- Deribit API changes
