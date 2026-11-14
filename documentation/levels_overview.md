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
