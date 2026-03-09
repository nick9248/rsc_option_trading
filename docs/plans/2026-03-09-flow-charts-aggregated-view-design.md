# Flow Charts: Aggregated View, Duplicate Fix, Chart Info Update

**Date**: 2026-03-09
**Branch**: quality-check

---

## 1. Duplicate Chart Files

**Root cause**: Not a code bug. `fig.write_html()` always overwrites at the same path. The apparent duplicates are old flat files from before the expiry-subfolder refactor (saved in `output/charts/flow_analysis/`) alongside the new files now in `output/charts/flow_analysis/{expiry}/`. They live at different paths and coexist in the filesystem.

**Fix**: Manual deletion of old flat files in `output/charts/flow_analysis/` root (non-subfolder files). No code change required — the current save logic is correct.

---

## 2. Aggregated "All Expirations" View

### UI Change

Add `"All Expirations"` as the **first item** in the existing expiration dropdown. No new buttons or layout changes. Uses `userData = "__ALL__"` as sentinel value.

### Routing

`_on_expiration_changed` checks for `"__ALL__"` and calls `_generate_aggregate_charts()` instead of `_generate_charts_from_db()`.

### New DB Method: `get_aggregated_flow_metrics(currency)`

Location: `coding/core/database/repository.py`

- Fetches the latest snapshot per `(currency, expiration, strike, option_type)` using a subquery on `MAX(captured_at)`
- Groups and SUMs all flow columns by `(strike, option_type)` across all expirations
- Returns the same dict structure as `get_flow_metrics` so existing chart generators work unchanged:
  ```python
  {
      "flow_data": {strike: {"C": {...}, "P": {...}}},
      "spot_price": float,  # median of underlying_price across latest snapshots
  }
  ```

### Trend Chart: All-Expiration Mode

Modify `generate_flow_trend_chart` in `chart_generator.py`:
- Add optional `expiration: Optional[str] = None` (default keeps current behavior)
- When `expiration=None`, remove `AND expiration = %s` from the WHERE clause
- Chart title shows `"All Expirations"` instead of specific expiry string

### Chart Titles

When in all-expiration mode, pass `expiration="All Expirations"` to `generate_flow_distribution_chart` and `generate_net_flow_chart` so titles render correctly.

### Spot Price in Aggregated Mode

Use median `underlying_price` across all latest snapshots as the spot reference line. This keeps the spot marker meaningful even when data spans multiple snapshots.

---

## 3. Chart Info Text

Rewrite all three `_show_chart_info` panels:

**Distribution by Strike**:
- Explain population pyramid layout: calls go right (positive), puts go left (negative)
- 4 bars per strike: Call Buy (emerald), Call Sell (rose), Put Buy (indigo), Put Sell (amber)
- Describe metric toggle: Notional / Volume / Trade Count
- Trader interpretation: which strikes attract most activity, OTM conviction reads

**Net Flow by Strike**:
- 4 traces with single colors matching legend: Call Buying (emerald), Call Selling (rose), Put Buying (indigo), Put Selling (amber)
- Explain positive = net buying, negative = net selling
- Key patterns: OTM call buying + OTM put selling = bullish, reverse = bearish

**Flow Trend Over Time**:
- Hourly aggregation of trades over 7 days
- 5 lines: Call Buy (emerald), Call Sell (rose), Put Buy (indigo/violet), Put Sell (amber), Net Flow (blue thick)
- Regime shift detection, divergence reads, conviction acceleration

---

## Files Affected

| File | Change |
|------|--------|
| `coding/core/database/repository.py` | Add `get_aggregated_flow_metrics(currency)` |
| `coding/core/analytics/chart_generator.py` | Make `expiration` optional in `generate_flow_trend_chart` |
| `coding/gui/dialogs/flow_charts_window.py` | Add "All Expirations" to dropdown, add `_generate_aggregate_charts()`, rewrite `_show_chart_info` |

---

## Out of Scope

- Saving aggregated charts to `output/charts/flow_analysis/` (aggregated view is GUI-only, on-demand)
- Weighting by OI (deferred, raw sum for now)
