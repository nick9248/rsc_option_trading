# Buy/sell flow in the on-chain analysis

Rewritten 2026-11-14. The "Flow Charts" window (button "View Flow Charts" in the On-Chain Analysis tab) was removed; its replacement is the **Option flow** section of the report, see `documentation/report.md` and `documentation/option_flow_by_strike.md`. Differences: the new section uses contracts x strike as notional, Deribit's real block flag, every expiration that traded in the window, and the Laevitas layout.

## What remains

`BuySellFlowAnalyzer` (`coding/core/analytics/buy_sell_flow_analyzer.py`) still runs inside "Load Analysis" (`OnChainAnalysisService._calculate_buy_sell_flow`), once per expiration over the trailing 24 hours, from `historical_trades` (written by the collector on the VPS, every 60 seconds). Its result feeds:

- the per-strike net taker flow in the Levels table,
- the flow signals in the synthesis (net sentiment, trend, data-sufficiency gate),
- the flow section of the report,
- three HTML charts per expiration saved under `output/charts/flow_analysis/<expiration>/` (distribution, net flow, trend).

Its definitions differ from the Option flow section of the report: notional = amount x index price; "block" = a trade with notional of at least $100k (not Deribit's block flag). Replacing it would change the synthesis inputs and thresholds, so it needs a separate decision.

## Tests

- `tests/unit/test_buy_sell_flow_analyzer.py`
- `tests/unit/test_on_chain_analysis_service_flow.py`, `test_on_chain_analysis_service_flow_window_utc.py`
- `tests/unit/test_repository_flow_analysis_trades.py`, `test_repository_hourly_flow_volumes.py`
