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

## Levels section of the Report (module 6)

The Report's Levels tab shows the same levels as an interactive page, with the settings fixed there (nearest monthly dealer expiry, 24 h option flow, band 1 % and 3 sources). See `documentation/report.md`. The page (`level_overview_page.py` + `level_overview_page.html`) is one self-contained file with inline CSS, JS and SVG, no network:

- **Price line**: one horizontal price axis, a row per source, each level a diamond at its exact price with its price and name written next to it (hollow = that source failed its check), the spot as a black line, a highlighter band where sources agree. A ruler follows the pointer. The buttons above zoom to ±5 %, ±10 %, ±15 % or all levels; levels beyond the zoom are listed at the edge of their row.
- **Ledger**: "Where sources agree" and the full table of every level with its distance from the spot, so every number can be checked. Hovering a level, a table row or an agreement highlights the same item everywhere.
- **Expiry selector.** The dealer lanes (Dealer GEX, open interest and flow) follow the chosen expiry. One page is built per expiry for the 4 expiries with the most open interest plus the nearest monthly one (5 at most); the default is the nearest monthly. The other lanes do not depend on the expiry. Other expiries, windows, band widths and source counts: `scripts/levels_overview.py`.
