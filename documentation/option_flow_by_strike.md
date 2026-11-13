# Option flow: buy/sell volume per strike (Deribit)

The "Buy/Sell Volume by strike" view of Laevitas, built from the Deribit option trades the collector stores
(`historical_trades`). Code: `coding/core/analytics/option_flow/` (aggregation and chart), `coding/service/option_flow/`
(service), `scripts/option_flow_by_strike.py` (command line). Status: working and checked against Laevitas; no GUI tab
yet; nothing runs on the VPS (it only reads the table).

## What it shows

Every option trade goes into one of eight series: call or put, bought or sold, screen trade or block trade. For each
strike the series are added up (contracts, notional in USD, number of trades), for the last N hours, for all
expirations or one.

- **Buy / sell** is the side of the TAKER (the aggressor who crossed the spread): Deribit's `direction`. Checked on our
  data: buys print above the mark price (median +0.14 %, blocks +0.84 %), sells below (-0.58 %, -0.89 %).
- **Blocked** is `block_trade_id` set (a block trade, 25 BTC or more per leg in practice, median 25 BTC; in the last
  24 hours blocks were 74 % of the notional). Blocks average 3.8 legs and more than half the legs belong to a combo, so a
  single leg is not necessarily a directional view.
- **Notional = contracts x strike.** This is Laevitas's definition (see the validation below). Contracts x index price,
  the usual spot notional, would give about 4 % less at 77,000 and 35 % less at 108,000.
- One contract = 1 BTC. Chart: calls to the left, puts to the right, the four series of a side as thin bars side by side
  (not stacked), strikes that traded only.

What it can NOT say: whether a trade opens or closes a position, and whether the taker was a customer or a market maker.
A "sell" is sell-side volume, not "they are now short".

## How to run

```
.venv\Scripts\python -m scripts.sync_from_vps                     # the local table is a mirror: sync first
.venv\Scripts\python -m scripts.option_flow_by_strike --hours 24
.venv\Scripts\python -m scripts.option_flow_by_strike --hours 24 --end 2026-11-12T14:49:30Z
.venv\Scripts\python -m scripts.option_flow_by_strike --hours 24 --expiry 13NOV26
```

Writes a PNG and a JSON (contracts, notional and trades per strike and series) into `outputs/option_flow/`.

## Data checks

- The stored trades match Deribit's own history server (`history.deribit.com`, full history since 2020; the main API only
  serves about the last day): 12 one-hour windows over 200 days, 4,921 trades, 0 missing, 0 extra, 0 differences in
  direction or size.
- Deribit public trade history can be backfilled from the history server for any contract since its listing.

## Validation against Laevitas (2026-11-13)

Tooltips read from a Laevitas video (BTC, Deribit, last 24H; stamp "14:19:51 12Nov2026"). With notional = contracts x
strike and a 24-hour window:

| Chart | Best window end (UTC) | Result |
|---|---|---|
| All expirations, strikes 72,000, 77,000 and 108,000 | 14:49:10 to 14:49:50 | all numbers equal to the cent (72,000: all 8 series; 77,000: call buy 4.77, call sell 12.10, put buy 0.10, put sell 0.92, call buy blocked 40.32, put buy blocked 3.85 USD M; 108,000: 140.4K and 10.8K) |
| Expiry 13NOV26, strike 70,000 | 15:01 to 15:03 | error 0.009 M over the five non-zero series (puts buy 10.14, puts sell 9.70, put buy blocked 0.875, put sell blocked 8.90 USD M, calls sell 14K) |

Notes: the window that fits ends about 30 minutes after the stamp on the picture (the reason is not known); the 24-hour
length fits better than 23.5, 24.5 or 25 hours; the two charts were evidently captured about 12 minutes apart; the
"total 81.14 M" read from the video does not equal the sum of the eight series at 72,000 (95.67 M), so it was misread.
The 108,000 case identified the definition: Laevitas's $140.4K of calls bought is exactly 1.3 BTC x 108,000.

Not implemented (Laevitas has them): the Unit, Type, Size and Interval selectors (here: always notional, all types, all
sizes, one window).

## Next

A GUI tab with the same controls; then the flow-based dealer-positioning levels on top of these trades, reusing
`DealerInventoryCalculator` (taker-flow, Glassnode method), and reconciling it with the older per-expiry
`BuySellFlowAnalyzer` (which defines a block as notional of at least $100k instead of the real block flag).
