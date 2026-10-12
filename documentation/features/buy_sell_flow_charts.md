# Buy/Sell Flow Charts

Rewritten 2026-10-12. The first version (2026-02-15) described a `buy_sell_flow_metrics` table written by `save_flow_metrics`. Both are gone: migration 028 dropped the table, and the flow is now computed on demand from the stored trades.

## What it shows

The aggressor side of every option trade (`direction` buy or sell) over the trailing 24 hours, per strike and per call/put. It shows where the market is buying or selling, and how that changes over time.

## Data flow

```
historical_trades  (written by the daemon, every 30 minutes)
        |
        v
DatabaseRepository.get_trades_for_flow_analysis(currency, expiration, start_ts, end_ts, trade_filter)
        |
        v
BuySellFlowAnalyzer(...).calculate()          coding/core/analytics/buy_sell_flow_analyzer.py
        |
        v
OnChainAnalysisService                        coding/service/on_chain/on_chain_analysis_service.py
        |
        v
FlowChartsWindow  (Plotly charts)             coding/gui/dialogs/flow_charts_window.py
```

Nothing is cached or stored: the numbers are always current and have one definition.

### Service methods (`OnChainAnalysisService`)

| Method | Returns |
|--------|---------|
| `get_active_expirations_with_flow(currency)` | Expirations not yet expired that traded in the last 24 hours, highest open interest first (open interest comes from `onchain_analysis_snapshots`). |
| `get_flow_metrics(currency, expiration)` | `{"flow_data": {strike: {"C"|"P": metrics}}, "spot_price": float}` for one expiration, trailing 24 hours. |
| `get_aggregated_flow_metrics(currency)` | The same, summed over all active expirations. |
| `get_filtered_aggregate_flow(currency, trade_filter)` | Aggregate with `trade_filter` `"all"`, `"block"` or `"non_block"`. |
| `generate_flow_trend_chart_figure(...)` | Flow trend figure (7-day lookback by default). |

If no index price is known for the currency, the flow is not computed and an empty result is returned (no placeholder spot price).

## The window

Opened with the "View Flow Charts" button in the On-Chain Analysis tab. It is a maximised modal window with:

- an expiration selector: "All Expirations" first, then each active expiration with its open interest;
- a filter: All, Block, Non-Block;
- three tabs: Distribution by Strike, Net Flow by Strike, Flow Trend Over Time;
- an info button with explanations of each chart.

### Distribution by Strike

Puts extend left and calls right. Up to four bars per strike (call buy, call sell, put buy, put sell), grouped side by side (`barmode="group"`). A toggle switches the measure between notional, volume and trade count. The spot price is marked.

### Net Flow by Strike

Buy volume minus sell volume per strike, with separate traces for calls and puts. Positive means buyers dominated, negative means sellers.

### Flow Trend Over Time

Hourly time series of the flows over the last 7 days.

## Reports

Running "Load Analysis" also saves one text report per expiration to `output/data/onchain_analysis/{currency}/{expiration}/report_{timestamp}.txt` (`OnChainAnalysisService._save_reports_per_expiration`), and the morning-note bundle to `output/data/onchain_analysis/{currency}/report/{timestamp}/`.

## Tests

- `tests/unit/test_buy_sell_flow_analyzer.py`
- `tests/unit/test_on_chain_analysis_service_flow.py`, `test_on_chain_analysis_service_flow_window_utc.py`
- `tests/unit/test_repository_flow_analysis_trades.py`, `test_repository_hourly_flow_volumes.py`
- `tests/unit/gui/test_flow_charts_window.py`
