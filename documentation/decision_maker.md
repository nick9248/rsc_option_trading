# Decision maker

A deterministic, auditable engine that turns every signal into structured evidence, builds a price map around the
spot, and scores the scenarios that map allows. It lists decision-support items (reference levels and conditions), each
scenario with an uncalibrated evidence score and an ordinal confidence label with the reasons that produced it.

Run it (from repo root, after `scripts/sync_from_vps.py`):

```
.venv\Scripts\python -m scripts.decision_maker --currency BTC
.venv\Scripts\python -m scripts.decision_maker --currency ETH --save --json <file.json>
```

`--save` stores the report in `decision_snapshots` (migration 036, never rewritten); `--json` writes the full report.

## What it is NOT

- **Not a forecast.** It says which scenario the current evidence supports most, not what will happen.
- **Not calibrated.** No recorded decision has been scored against what the price did. The evidence score (0-100) is
  relative support on a stated scale; scores of different scenarios are not normalised and do not add up to 100. They
  are not probabilities.
- **Not an order and not a price target.** The decisions are reference levels (a range for a strategy, a level for a
  hedge) and conditions to watch. Nothing here places or suggests a trade size.
- **No LLM.** Same inputs give the same JSON. An LLM may later explain a report, never produce its numbers.

## Code

| Layer | Path | Role |
|---|---|---|
| Core | `coding/core/analytics/decision/evidence.py` | `EvidenceItem`, the one form every source is converted into |
| Core | `coding/core/analytics/decision/decision_config.py` | every weight, threshold and prior, each with its reason |
| Core | `coding/core/analytics/decision/independence.py` | per-input-group aggregation |
| Core | `coding/core/analytics/decision/price_map.py` | bands, support, resistance, next levels |
| Core | `coding/core/analytics/decision/scenario_engine.py` | freshness, scenarios, scores, confidence, what-if, decisions |
| Core | `coding/core/analytics/decision/decision_report.py` | `DecisionReport` (JSON both ways) |
| Service | `coding/service/decision/evidence_extractors.py` | existing service outputs -> evidence |
| Service | `coding/service/decision/decision_maker_service.py` | runs the sources with one clock reading, builds and saves the report |
| Script | `scripts/decision_maker.py` | CLI |
| DB | `migrations/036_add_decision_snapshots.sql` | `decision_snapshots` (currency, computed_at unique) |

A new source = one extractor that returns `EvidenceItem` values. The engine does not change (tested in
`tests/unit/decision/test_generalization.py`).

## Evidence

Every item has: id, source (human name), input group, kind, a one-sentence statement of what it says and what it does
not say, as-of time (UTC), strength 0..1, quality 0..1 with the reasons that lowered it, and where meaningful a
direction, regime, price and horizon.

Kinds:

- **price_level**: a price where a source puts a level. Only these shape the map. Whether it is support or resistance
  follows only from its side of the spot (a put wall above the spot is a level above the spot).
- **directional**: a lean up or down, or "neutral" (balanced flow).
- **range_regime**: moves damped (`range_bound`) or amplified (`expansion`); no direction.
- **magnet**: a pool of forced orders at a price (liquidations); no direction of its own.

### Input groups (independence)

Items of one input group are derived from the same data and are NOT independent; the engine counts each group once.

| Input group | Sources |
|---|---|
| `deribit_open_interest` | MenthorQ replica levels (call resistance, put support, HVL); open-interest dealer walls, gamma flip and total-GEX sign |
| `deribit_trades` | flow-based dealer walls, flip and total-GEX sign; option-flow strikes; net taker flow (screen, single-leg blocks, multi-leg blocks) |
| `binance_perpetual_candles` | futures taker ratio; liquidation estimate (both computed from the Binance perpetual candles) |

So today there are at most three distinct groups. "high" confidence needs all three on one side. Distinct is not
independent: open interest is the accumulated result of the trades, and all three react to the same price moves.

### Sources and their discounts

| Source | Kind | Quality prior | Discounts |
|---|---|---|---|
| MenthorQ replica | price_level (HVL at half strength) | 0.7 | strike-axis points from open interest, not regime boundaries |
| Dealer GEX, open interest (nearest monthly + largest-OI expiry) | price_level, range_regime | 0.6 / 0.3 | assumption-based (dealers long calls, short puts); strength scaled by the expiry's OI share (full at 25 %; not scaled, with the reason, when the share is not computed) |
| Dealer GEX, flow-based (same expiries) | price_level, range_regime | 0.6 / 0.3 | x0.2 automatically when `DealerFlowLevelsService`'s plausibility check fails |
| Option flow strikes (24 h, top 2 per type and side) | price_level | 0.35 | a traded strike is not a barrier |
| Net option flow, screen and single-leg blocks | directional | 0.5 | intent (open/close/hedge) unknown; measured in contracts |
| Net option flow, multi-leg blocks | directional | 0.075 | x0.15: one leg of a structure is not a view |
| Futures taker ratio (rolling 24 h vs own 30-day mean +- 1 sd) | directional | 0.4 | weak, mean-reverting, horizon 3 days |
| Liquidation estimate (newest snapshot, 2 pools per side) | magnet | 0.25 | research model in its forward test |

The open interest per expiry (`ExpiryOpenInterestService`) picks the dealer expiries, sets their horizon (days left)
and scales their strength; it is also listed in the report's `context`.

The spot is the newest index price at or before the report time; its time is in `context.spot_time` and a spot older
than 3 h is a data caveat.

Freshness (engine, every item): older than its group's limit (3 h for the three groups, 6 h default) -> quality x0.5;
older than 4 limits -> x0.1. The reason is added to the item.

## Price map

Price levels within 15 % of the spot are grouped into bands of 1 % of the spot (a band closes one width after its first
price). A band's weight = sum over its input groups of (quality-weighted mean strength x quality-weighted mean quality),
each group capped at 1. A band weighing at least 0.3 is meaningful. Support = the nearest meaningful band below the spot,
resistance = the nearest above; the next band beyond each is kept. A band whose price range contains the spot is "at the
spot": it is reported, but bounds neither side. A side without one is "unmapped" and the
report says so; no level is invented.

## Scenarios and score

Generated from the map: "stays inside [support, resistance]" (or "stays above support" / "stays below resistance" when one
side is unmapped), "breaks above resistance" (with the next mapped level, if any), "breaks below support". No level on
either side: no scenario.

Each item gets a signed stance per scenario (times its strength):

- support/resistance band items: inside +1, the break through them -1;
- bullish: break up +1, break down -1, inside -0.5 (bearish mirrored); neutral: inside +1; on a one-sided map a lean
  toward the unmapped side does not oppose inside ("stays above support" is open upward);
- range_bound: inside +1, each break -0.5; expansion: inside -1 (-0.5 on a one-sided map), each break +0.5;
- magnet beyond a boundary: that break +1; between the boundaries: inside +0.5; times proximity
  (1 - distance / 10 % of the spot).

Per input group the contribution = weight x quality-weighted mean value x quality-weighted mean quality, capped at +-1.
Net = support - opposition; score = 100 x net / (net + 1). Ties are broken in the fixed order inside, break up, break down.

## Confidence (low / medium / high)

One point (+1, 0 or -1) per criterion, each with its reason in the report:

1. distinct input groups agreeing (contribution > 0.05): >= 3 +1, <= 1 -1;
2. conflict, per item (quality x |value| of opposing items over all items speaking to it): > 35 % -1, < 10 % +1;
3. mean quality of the supporting items: >= 0.6 +1, < 0.35 -1;
4. stale supporting items: any -1;
5. margin to the next scenario (top only): >= 20 points +1, < 10 -1; a non-top scenario gets -1.

Label: low when at most one group agrees, the points are <= -1 or the score is 0; high when at least three groups agree
and the points are >= 2 and no supporting item is stale; otherwise medium.

"What would change this": each item is flipped (directional, regime) or removed (levels, magnets) alone and the whole
report recomputed; the items that change the top scenario are listed.

## Recording and calibration (later)

`decision_snapshots` keeps every saved report with its `method_version`. A later evaluator (not built) can score each
recorded scenario against the price path after `computed_at` and replace the priors in `decision_config.py`. Point in
time: every item must be at or before the report time (the engine refuses lookahead). Not yet bounded for replays of
past instants: `DealerFlowLevelsService` sums trades up to the newest stored one and the newest trade time is unbounded
(clamped to the window end); for live runs both are point in time.

The table is local only (not synced, no VPS job). Applying 036 on the VPS is a separate deployment step.

## Priors worth discussing

- Three distinct groups only: two of the three dominant level sources share the open interest.
- Level bands support "inside" AND oppose the break through them, so with a mapped range "inside" is favoured unless
  directional evidence is strong.
- `MINIMUM_LEVEL_WEIGHT` 0.3: on 14 Nov a 65,000 BTC band weighed 0.29 and was not mapped.
- The taker ratio is read as aggressor pressure in its own direction (above band = bullish), not as a contrarian signal.
- The liquidation estimate and the taker ratio share one group (both from Binance perpetual candles).
- The group aggregation is the agreed quality-weighted mean. Consequence (found in review): a weak item that AGREES with a
  stronger one of the same group lowers the group's contribution, and can drop a band below the 0.3 threshold (HVL
  strength 0.5 at quality 0.7 = 0.35, plus a strength-0.05 flip at quality 0.6 in the same band = 0.19). A max or
  noisy-OR per group would not do this; it is a design change for the owner to decide.
- Balanced (neutral) flow and a taker ratio inside its band support "inside": in a quiet market the absence of a
  signal counts as agreement and can supply the third group for "high".
- "Reference range for a strategy" and "Reference level for a hedge" are listed whatever the top scenario is; they
  describe the map, not a recommendation.
