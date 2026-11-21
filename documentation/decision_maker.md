# Decision maker (now an evidence report)

**Changed 2026-11-21.** The decision maker used to turn every signal into evidence, score three scenarios (stays inside, breaks
above, breaks below) with an "evidence score" and give each an ordinal confidence label. That scoring was removed: its weights
and thresholds were hand-set priors, no recorded decision had ever been checked against what the price did
(`decision_snapshots` had 0 rows), and its output was misleading (for example "stays below 60,000 ... confidence high" with the
spot already at 58,906 and a mean evidence quality of 0.36).

What remains is the deterministic part: structured evidence from every source (with its strength, quality and caveats), the
freshness check, and a price map of the levels around the spot. It is the input layer of the planned strategy suggestion
(code prepares the facts, a model proposes a strategy per expiry; see the project notes). It places no orders.

Run it (from repo root, after `scripts/sync_from_vps.py`):

```
.venv\Scripts\python -m scripts.decision_maker --currency BTC
.venv\Scripts\python -m scripts.decision_maker --currency ETH --save --json <file.json>
```

`--save` stores the report in `decision_snapshots` (migration 036, never rewritten); `--json` writes the full report.

## What it is NOT

- **Not a forecast, not a ranking of scenarios, not a confidence statement.** It lists levels and evidence.
- **Not calibrated.** Strength and quality are hand-set priors, not probabilities.
- **Not an order and not a price target.** The reference lines are levels and conditions. Nothing places or suggests a trade size.
- **No LLM.** Same inputs give the same JSON.

## In the report
The market report has an "Evidence" tab (last tab, key `decision`; see `documentation/report.md`). It runs `DecisionMakerService`
once at the report's time and shows the same report the script prints: the price map, the reference lines, every evidence item
grouped by input group, and the data caveats. It only reads: it never records a snapshot
(`scripts/decision_maker.py --save` does).

## Code

| Layer | Path | Role |
|---|---|---|
| Core | `coding/core/analytics/decision/evidence.py` | `EvidenceItem`, the one form every source is converted into |
| Core | `coding/core/analytics/decision/decision_config.py` | every weight, threshold and prior, each with its reason |
| Core | `coding/core/analytics/decision/independence.py` | per-input-group aggregation |
| Core | `coding/core/analytics/decision/price_map.py` | bands, support, resistance, next levels |
| Core | `coding/core/analytics/decision/evidence_report.py` | freshness check, price map, reference lines |
| Core | `coding/core/analytics/decision/decision_report.py` | `DecisionReport` (JSON both ways) |
| Service | `coding/service/decision/evidence_extractors.py` | existing service outputs -> evidence |
| Service | `coding/service/decision/decision_maker_service.py` | runs the sources with one clock reading, builds and saves the report |
| Script | `scripts/decision_maker.py` | CLI |
| DB | `migrations/036_add_decision_snapshots.sql` | `decision_snapshots` (currency, computed_at unique) |

A new source = one extractor that returns `EvidenceItem` values. The report builder does not change (tested in
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

### Input groups (independence)

Items of one input group are derived from the same data and are NOT independent; the engine counts each group once.

| Input group | Sources |
|---|---|
| `deribit_open_interest` | MenthorQ replica levels (call resistance, put support, HVL); open-interest dealer walls, gamma flip and total-GEX sign |
| `deribit_trades` | flow-based dealer walls, flip and total-GEX sign; option-flow strikes; net taker flow (screen, single-leg blocks, multi-leg blocks) |
| `binance_perpetual_candles` | futures taker ratio (computed from the Binance perpetual candles) |

So today there are at most three distinct groups. Distinct is not independent: open interest is the accumulated result of the
trades, and all three react to the same price moves.

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

## Recording (later)

`decision_snapshots` keeps every saved report with its `method_version` (now `evidence-report-1`; older rows, if any, hold scenario
scores). A later evaluator (not built) can compare the recorded levels with the price path after `computed_at`. Point in
time: every item must be at or before the report time (the engine refuses lookahead). Not yet bounded for replays of
past instants: `DealerFlowLevelsService` sums trades up to the newest stored one and the newest trade time is unbounded
(clamped to the window end); for live runs both are point in time.

The table is local only (not synced, no VPS job). Applying 036 on the VPS is a separate deployment step.

## Priors worth discussing

- Three distinct groups only: two of the three dominant level sources share the open interest.
- `MINIMUM_LEVEL_WEIGHT` 0.3: on 14 Nov a 65,000 BTC band weighed 0.29 and was not mapped.
- The group aggregation is the agreed quality-weighted mean. Consequence (found in review): a weak item that AGREES with a
  stronger one of the same group lowers the group's contribution, and can drop a band below the 0.3 threshold (HVL
  strength 0.5 at quality 0.7 = 0.35, plus a strength-0.05 flip at quality 0.6 in the same band = 0.19). A max or
  noisy-OR per group would not do this; it is a design change for the owner to decide.
- "Reference range" and "Reference level for a hedge" describe the map, not a recommendation.
