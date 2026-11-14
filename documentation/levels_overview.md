# Levels overview (all sources on one chart)

`scripts/levels_overview.py` (code: `coding/service/option_flow/level_overview_service.py`, `coding/core/analytics/option_flow/level_overview.py`).

```
.venv\Scripts\python -m scripts.sync_from_vps
.venv\Scripts\python -m scripts.levels_overview --currency BTC [--expiry 27NOV26] [--band-percent 1.0] [--minimum-sources 3]
```

Writes `outputs/levels_overview/<currency>_levels_overview.png` and `.html` and logs every level and every confluence.

## Sources (one lane each)

1. **MenthorQ replica**: call resistance, put support and HVL of all monthly expiries (open interest based, the levels the MenthorQ charts draw).
2. **Dealer GEX, open interest**: call wall, put wall, gamma flip of one expiry (default: nearest monthly), dealers long calls and short puts.
3. **Dealer GEX, flow-based**: the same levels from the taker flow (see `dealer_flow_levels.md`); the lane says "check failed" when its plausibility check fails.
4. **Option flow**: the 2 strikes per type and side (calls/puts bought/sold, last 24 h) with the most notional (screen + blocked, contracts x strike).
5. **Liquidation estimate**: the 2 strongest estimated long levels below and short levels above the reference price of the newest stored snapshot (research model, forward test running until about 2026-11-20).

A source that cannot be computed is left out with a note, never drawn as a made-up level. Only prices within 15 % of the spot are drawn (the footer names the others).

## Confluence

A shaded band marks a price range (default 1 % of the spot) holding levels of at least 3 different sources. This is a coincidence of methods, not a proof: sources 1 to 3 all use the same open interest and trades, so they are not independent.

## GUI tab "Levels" (module 11)

The same levels as an interactive page (`level_overview_page.py` + `level_overview_page.html`, one self-contained file with inline CSS, JS and SVG, no network):

- **Price ladder**: a lane per source, each level at its exact price, the spot as a gold line, a gold band where sources agree. The buttons above the ladder zoom to ±5 %, ±10 %, ±15 % or all levels; levels beyond the zoom are listed at the lane's edge.
- **Ledger**: "Where sources agree" and the full table of every level with its distance from the spot, so every number can be checked. Hovering a level, a table row or an agreement highlights the same item everywhere.
- **Controls** (Qt): currency, dealer expiry, option flow window, agreement band width and the number of sources needed. Band width and sources only rebuild the page from the levels already loaded; the other three read the database again.
- **Change a choice and the page redraws** (the Option Flow and Dealer Levels tabs do the same now: changing the expiry, window, unit or currency redraws the chart; a change made during a run waits and runs next).
