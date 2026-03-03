# Synthesis Engine: Production Specification v2.0

> **File**: `coding/core/analytics/synthesis.py`
> **Entry point**: `MorningNoteService.generate()` → `SynthesisMapper.build_all()` → `SynthesisEngine.run()`

---

## Changelog from v1.0

| # | What Changed | Why |
|---|-------------|-----|
| 1 | Unified VRP thresholds: scoring and narrative both use ±5 pts | v1 had scoring at ±5 and narrative at ±3 — VRP of +4 scored "neutral" but narrative said "sell premium" |
| 2 | Skew scorer reclassified as **fear indicator** (separate axis from buy/sell vol) | v1 grouped skew under vol buy/sell axis, but +2 skew fed into EXPLOSIVE which recommends buying vol — semantic contradiction |
| 3 | Vol regime classifier now uses VRP, term structure, and skew (not just IV pctile + skew) | v1 computed VRP and term structure scores but never used them in regime classification |
| 4 | Vanna scorer is now IV-regime-conditional | v1 assumed "IV will drop" universally — wrong when IV is cheap |
| 5 | Vanna/charm scorer returns 0 on zero input instead of −1 | v1 returned −1 (bearish) when data was missing/zero — phantom signal |
| 6 | RANGE_BOUND split into 3 sub-types with differentiated narratives | v1 merged 5 distinct states (bearish+suppressed through neutral+elevated) into one regime |
| 7 | TRANSITION requires minimum magnitude (`abs(near) + abs(far) >= 2`) | v1 fired on razor-thin disagreements (0.31 vs −0.31) |
| 8 | Added **fragility detection** as post-scoring confidence adjustment (not a score) | v1 had no protection against "all bullish + extreme crowding" blowup setup. v2.0 initially added it as a scorer but that double-counted funding |
| 9 | Expiries with DTE ≤ 2 get score clamping to ±1.0 (not weight scaling) | v1 gave full weight to 0-DTE noise. v2.0 initially used weight scaling but that cancels out mathematically in the weighted average |
| 10 | GEX thresholds use `gex/spot` (DEX already used `dex/spot`) | v1 used absolute dollar thresholds. v2.0 initially used `gex/(spot*100)` but the math was off by 10x |
| 11 | Funding risk factor threshold raised to `abs(funding_8h) > 0.03%` | v1 used 0.01% (~11% ann.) which fires constantly in trending markets |
| 12 | Removed dead code from spec: `RISK_OFF`, `score_gex`, `score_vwap_vs_mark`, `large_oi_changes` | These are not implemented; keeping them in the spec creates confusion |
| 13 | Charm scorer weight scales with gamma environment | v1 used flat 0.3 regardless of whether GEX dampens or amplifies charm |
| 14 | Term structure scorer integrated into vol regime (was orphaned in v1) | v1 computed the score but never used it anywhere |
| 15 | `futures_basis` dict must be ordered by DTE (ascending) | v1 relied on dict order implicitly without specifying the requirement |
| 16 | Risk factors use `funding_8h` consistently for both threshold and annualization | v1 mixed `funding_8h` for threshold and `funding_rate` for display |
| 17 | Best-sell and best-buy expiry fallbacks documented | v1 didn't specify what happens when no expiry matches the filter |
| 18 | Iron Condor recommendation now skew-aware | v1 recommended symmetric IC even with extreme skew |

---

## Architecture Overview

```
OnChainAnalyzer (live run)
         │
         ├─ .gex_dex_structured           ← per expiry
         ├─ .volatility_surface_structured ← per expiry
         ├─ .buy_sell_flow_structured      ← per expiry
         ├─ .market_wide_structured        ← global
         └─ .parsed_data                  ← raw instruments
                   │
                   ▼
         SynthesisMapper.build_all()
                   │
         ┌─────────┴──────────┐
         ▼                    ▼
  MarketWideMetrics    List[ExpiryMetrics]
         │                    │
         └─────────┬──────────┘
                   ▼
         SynthesisEngine.run()
                   │
         ┌─────────▼──────────┐
         │   ScoringEngine    │  raw numbers → (score, weight, reasoning)
         └─────────┬──────────┘
                   ▼
         ┌─────────▼──────────┐
         │ RegimeClassifier   │  scores → Signal + VolRegime + MarketRegime
         └─────────┬──────────┘
                   ▼
         ┌─────────▼──────────┐
         │ NarrativeGenerator │  regimes → filled text templates
         └─────────┬──────────┘
                   ▼
         Executive Summary (string)
```

**Important**: The synthesis does **not** parse `report.txt`. Both the report text and the synthesis read from the same structured dicts on `OnChainAnalyzer`. They are siblings generated from the same data, not a chain.

---

## Stage 1: SynthesisMapper — Raw Dicts → Typed Dataclasses

`SynthesisMapper.build_all(analyzer)` produces two objects consumed by `SynthesisEngine`:

### MarketWideMetrics

| Field | Source | Transformation |
|-------|--------|----------------|
| `spot_price` | `market_wide_structured["spot_price"]` | fallback to `analyzer.underlying_price` |
| `dvol` | `market_wide_structured["dvol"]` | percentage points (e.g. 58.7) |
| `iv_percentile_365d` | `market_wide_structured["iv_percentile_365d"]` | 0–100 |
| `funding_rate` | `market_wide_structured["funding_rate"]` | **x100** (decimal → %) |
| `funding_8h` | `market_wide_structured["funding_8h"]` | **x100** (decimal → %) |
| `rv_10d / rv_20d / rv_30d` | `market_wide_structured["rv_*"]` | **x100** (decimal → %) |
| `vrp` | `market_wide_structured["vrp"]` | pts (DVOL - 30d RV) |
| `cone_10d_pctile` | `market_wide_structured["cone_10d_pctile"]` | 0–100 |
| `cone_20d_pctile` | `market_wide_structured["cone_20d_pctile"]` | 0–100 |
| `cone_30d_pctile` | `market_wide_structured["cone_30d_pctile"]` | 0–100 |
| `term_structure_shape` | `market_wide_structured["shape"]` | `"CONTANGO"` or `"BACKWARDATION"` (no other values) |
| `term_structure_spread` | `market_wide_structured["spread"]` | absolute pts (for scoring) |
| `term_structure_spread_signed` | `market_wide_structured["spread_signed"]` | signed pts (back - front, for display) |
| `iv_by_dte` | `market_wide_structured["iv_by_dte"]` | `{dte_int: iv_float}` |
| `futures_basis` | `market_wide_structured["futures_basis"]` | `{label: annualized_pct}` **must be ordered by DTE ascending** |
| `perp_oi` | `market_wide_structured["perp_oi"]` | raw notional value |
| `perp_funding_trend` | `market_wide_structured["perp_funding_trend"]` | `"Stable"` etc. |
| `btc_eth_price_corr` | `market_wide_structured["btc_eth_price_corr"]` | -1.0 to +1.0 |
| `btc_eth_dvol_corr` | `market_wide_structured["btc_eth_dvol_corr"]` | -1.0 to +1.0 |
| `block_trades` | `market_wide_structured["block_trades"]` | list of dicts |

**Validation rules:**
- `term_structure_shape` must be exactly `"CONTANGO"` or `"BACKWARDATION"`. If the source provides any other value (e.g., `"FLAT"`), the mapper must normalize: `spread < 0.5` → `"CONTANGO"` with `spread = 0`.
- `futures_basis` dict **must** be insertion-ordered by DTE ascending. `basis_values[0]` = front (nearest), `basis_values[-1]` = back (furthest). If the source provides unordered data, the mapper must re-sort before constructing the dict.

### ExpiryMetrics (one per expiration)

Built by `build_expiry_metrics(analyzer, expiration)`. Returns `None` if GEX data or instrument list is missing — those expiries are silently excluded.

| Field | Source |
|-------|--------|
| `dte` | Parsed from expiry string (e.g. `"27MAR26"`) vs `datetime.now()` |
| `total_oi` | Sum of `open_interest` across all instruments in `parsed_data[expiration]` |
| `notional` | `total_oi x analyzer.underlying_price` |
| `max_pain` | `analyzer.calculate_max_pain(strike_data)["max_pain_strike"]` |
| `pc_ratio` | `analyzer.calculate_put_call_ratio(strike_data)["ratio"]` (capped at 99 if inf) |
| `total_gex` | `gex_dex_structured[exp]["total_net_gex"]` |
| `total_dex` | `gex_dex_structured[exp]["total_net_dex"]` |
| `gex_environment` | `"Positive"` if `total_gex >= 0` else `"Negative"` (derived, not from dict) |
| `call_resistance_strike` | `gex_dex_structured[exp]["key_levels"]["call_resistance"]["strike"]` |
| `call_resistance_gex` | `gex_dex_structured[exp]["key_levels"]["call_resistance"]["net_gex"]` |
| `put_support_strike` | `gex_dex_structured[exp]["key_levels"]["put_support"]["strike"]` |
| `put_support_gex` | `gex_dex_structured[exp]["key_levels"]["put_support"]["net_gex"]` |
| `hvl_strike` | `gex_dex_structured[exp]["key_levels"]["hvl"]` |
| `atm_iv` | `volatility_surface_structured[exp]["atm_iv"]` |
| `skew_25d` | `volatility_surface_structured[exp]["skew_25d"]["skew"]` (put_25d - call_25d) |
| `put_25d_iv` | `volatility_surface_structured[exp]["skew_25d"]["put_25d_iv"]` |
| `call_25d_iv` | `volatility_surface_structured[exp]["skew_25d"]["call_25d_iv"]` |
| `net_vanna` | `volatility_surface_structured[exp]["second_order_greeks"]["net_vanna"]` |
| `net_charm` | `volatility_surface_structured[exp]["second_order_greeks"]["net_charm"]` |
| `pc_atm / near_otm / far_otm` | `volatility_surface_structured[exp]["pc_by_moneyness"][zone]["ratio"]` |
| `flow_bias` | `buy_sell_flow_structured[exp]["bias_interpretation"]` |
| `flow_trend` | `buy_sell_flow_structured[exp]["flow_trend"]` |
| `top_buy_strikes` | `buy_sell_flow_structured[exp]["top_buy_strikes"]` |
| `top_sell_strikes` | `buy_sell_flow_structured[exp]["top_sell_strikes"]` |

**Removed from v1**: `vwap_iv`, `mark_iv` (hardcoded to 0.0, no data source), `large_oi_changes` (never populated). These fields should not exist on the dataclass.

---

## Stage 2: ScoringEngine — Numbers → Scores

Every scorer returns `(score: float, weight: float, reasoning: str)`.

- **Score range**: -2.0 to +2.0
- **Weight range**: 0.0–1.0 (importance multiplier in the weighted average)

Score semantic axes differ by category:
- **Directional scorers**: +2 = strong bullish, -2 = strong bearish
- **Vol richness scorers** (IV percentile, VRP): +2 = sell vol (expensive), -2 = buy vol (cheap)
- **Vol structure scorers** (term structure): +2 = steep contango (back rich), -2 = steep backwardation (front stressed)
- **Fear indicator** (skew): +2 = extreme fear/hedging, -2 = extreme complacency

These axes are **not interchangeable**. Each category feeds into different classifiers as documented in Stage 3.

### Directional Scorers

#### `score_pc_ratio(pc_ratio, dte)`

OI-based put/call ratio. DTE-aware weight reduction for settlement-day noise.

| P/C Range | Score | Base Weight | Interpretation |
|-----------|-------|-------------|----------------|
| < 0.40 | +1.0 | 0.5 | Extreme call dominance — contrarian caution (see below) |
| 0.40–0.60 | +2.0 | 0.7 | Strong call dominance |
| 0.60–0.80 | +1.0 | 0.7 | Bullish call lean |
| 0.80–1.00 | 0.0 | 0.5 | Balanced |
| 1.00–1.30 | -1.0 | 0.7 | Moderate put lean |
| 1.30–2.00 | -2.0 | 0.7 | Extreme hedging / fear |
| > 2.00 | -1.0 | 0.5 | Extreme put dominance — contrarian caution (see below) |

**Contrarian dampening at extremes**: At P/C < 0.40 or > 2.00, the score magnitude is REDUCED (not increased) and weight drops to 0.5. Rationale: Extreme option positioning works on the same contrarian logic as extreme futures positioning. P/C < 0.40 means call buyers dominate to a degree that historically signals a crowded long / euphoric top. P/C > 2.00 means put hedging is so extreme it often signals a fear washout / bottom. Both extremes reduce the reliability of the direct positioning signal, so the score is dampened rather than amplified. This is consistent with how `score_funding` treats extreme positioning as contrarian.

**DTE score clamping**: If `dte <= 2`, clamp the score to **±1.0** (scores of ±2.0 become ±1.0; scores already within ±1.0 are unchanged). Rationale: 0-2 DTE P/C ratios are dominated by hedging rolls and settlement mechanics. Note: this clamps the SCORE, not the weight — a weight-only multiplier would cancel out in the weighted average when all expiries in a bucket share the same DTE range.

#### `score_dex(total_dex, spot)`

DEX = sum of (delta x OI) across all open options. This is the **market's** (buyers') aggregate option delta. Dealers, as counterparty, hold the opposite.

Positive DEX → market is net long delta → **dealers are net short delta** → dealers buy underlying to hedge back to neutral → upward hedging pressure → **bullish**.
Negative DEX → market is net short delta → **dealers are net long delta** → dealers sell underlying to reduce hedge → downward pressure → **bearish**.

**Thresholds are normalized by spot price** to remain calibrated as BTC price changes:

| DEX / spot | Score | Base Weight | Interpretation |
|------------|-------|-------------|----------------|
| > +0.005 | +2.0 | 0.8 | Strong bullish dealer pressure |
| +0.001 to +0.005 | +1.0 | 0.8 | Moderate bullish |
| -0.001 to +0.001 | 0.0 | 0.5 | Neutral |
| -0.005 to -0.001 | -1.0 | 0.8 | Moderate bearish |
| < -0.005 | -2.0 | 0.8 | Strong bearish dealer pressure |

*At BTC $100K: thresholds are ~±100 and ±500 (equivalent to v1). At $50K: ~±50 and ±250. Scales automatically.*

**DTE score clamping**: If `dte <= 2`, clamp the score to **±1.0**. Settlement-day DEX is mechanically noisy. Score clamping (not weight scaling) is used because a weight multiplier cancels out in the weighted average when all expiries in a bucket share the same DTE range.

#### `score_max_pain_gravity(max_pain, spot, dte)`

Distance of max pain from spot price. Effect is strongest near expiration and weakens with time.

| Distance (% from spot) | Score | Weight |
|------------------------|-------|--------|
| > +10% | +2.0 | see below |
| +5% to +10% | +1.0 | see below |
| -5% to +5% | 0.0 | 0.2 |
| -10% to -5% | -1.0 | see below |
| < -10% | -2.0 | see below |

**DTE-scaled weight** (for non-neutral scores):
| DTE | Weight |
|-----|--------|
| 0–7 | 0.5 |
| 8–14 | 0.4 |
| 15–30 | 0.3 |
| > 30 | 0.15 |

Rationale: Max pain gravity is empirically strongest in the final week. Beyond 30 DTE, the OI landscape shifts significantly before expiry, making the gravitational pull unreliable.

#### `score_funding(funding_8h)`

**Uses `funding_8h` only** (not `funding_rate`). This is the 8-hour rate after mapper's x100 transformation, so values are in percent (e.g., -0.0148 = -0.0148% per 8h).

Annualized rate = `funding_8h x 3 x 365`.

Positive funding → longs pay → crowded long → contrarian bearish.
Negative funding → shorts pay → crowded short → contrarian bullish.

| Ann. Rate | Score | Weight | Interpretation |
|-----------|-------|--------|----------------|
| < -20% | +2.0 | 0.6 | Extremely crowded short |
| -20% to -10% | +1.0 | 0.5 | Crowded short |
| -10% to -5% | 0.0 | 0.3 | Mild positioning |
| -5% to +5% | 0.0 | 0.3 | Neutral leverage |
| +5% to +10% | 0.0 | 0.3 | Mild positioning |
| +10% to +20% | -1.0 | 0.5 | Crowded long |
| > +20% | -2.0 | 0.6 | Extremely crowded long |

**Important**: Funding is the only **contrarian** scorer in the directional pipeline. All others (DEX, P/C, flow, max pain, vanna/charm, futures basis) are direct/flow-based signals. When all direct signals strongly agree AND funding is extreme, this represents a **fragility setup** — see `score_fragility` below.

#### `score_flow(flow_bias, flow_trend)`

Combines current bias with trend acceleration.

**Base score** (from `bias_interpretation`):
| Bias | Score |
|------|-------|
| Heavy Buying | +2.0 |
| Moderate Buying | +1.0 |
| Mixed/Neutral | 0.0 |
| Moderate Selling | -1.0 |
| Heavy Selling | -2.0 |

**Trend adjustment** (added to base, clamped to +/-2):
| Trend | Adjustment |
|-------|-----------|
| Accelerating Buy Pressure | +0.50 |
| Steady Buy Pressure | +0.25 |
| Reversing to Buy Pressure | +0.50 |
| Decelerating Buy Pressure | -0.25 |
| Reversing to Sell Pressure | -0.50 |
| Accelerating Sell Pressure | -0.50 |
| Steady Sell Pressure | -0.25 |
| Decelerating Sell Pressure | +0.25 |
| Mixed/Neutral Flow | 0.00 |

**Unrecognized strings**: If `flow_bias` or `flow_trend` does not exactly match any key in the map, default to 0.0 **and log a warning**. String matching is case-sensitive and must be exact. The upstream `BuySellFlowCalculator` must produce these exact strings.

Weight: **0.6**.

Weight hierarchy for reference: DEX (0.8) > P/C ratio (0.7) > flow (0.6) > futures basis (0.3-0.6) > funding (0.3-0.6) > max pain (0.15-0.5) > vanna/charm (0.15-0.4).

#### `score_vanna_charm(net_vanna, net_charm, iv_pctile, gex_total, spot)`

Second-order Greek structural drift. IV-regime-conditional for vanna; gamma-adjusted weight for charm.

**Vanna signal** (IV-regime-conditional):
- If `net_vanna == 0`: vanna_signal = **0.0** (no data, no signal)
- If `iv_pctile > 60` (IV elevated, likely to mean-revert down):
  - Positive net_vanna → +1.0 (IV drops → dealer delta drops → dealers buy underlying → bullish)
  - Negative net_vanna → -1.0
- If `iv_pctile < 40` (IV depressed, likely to mean-revert up):
  - Positive net_vanna → **-1.0** (IV rises → dealer delta rises → dealers sell underlying → bearish)
  - Negative net_vanna → **+1.0**
- If `40 <= iv_pctile <= 60` (no clear IV direction): vanna_signal = **0.0**

**Charm signal**:
- If `net_charm == 0`: charm_signal = **0.0** (no data, no signal)
- Positive net_charm → +1.0 (time decay pushes dealer delta positive → bullish drift)
- Negative net_charm → -1.0

**Combined** = `(vanna_signal + charm_signal) / 2`

**Gamma-adjusted weight**:
```
gex_normalized = gex_total / spot
if gex_normalized < -50:     # deeply negative gamma (~$5M at $100K) — charm/vanna amplified
    weight = 0.4
elif gex_normalized > 50:    # strongly positive gamma (~$5M at $100K) — charm/vanna dampened
    weight = 0.15
else:
    weight = 0.3             # normal
```

Rationale: In negative GEX environments, second-order Greeks drive larger hedging flows because dealers are short gamma and chase delta. In positive GEX, dealers naturally absorb these drifts.

#### `score_futures_basis(basis_front)`

Annualized futures premium. Contango → bullish structural demand; backwardation → stress.

`basis_front` = first value in the ordered `futures_basis` dict (nearest DTE).

| Front Basis (ann. %) | Score | Weight |
|----------------------|-------|--------|
| > 10% | +2.0 | 0.5 |
| 5%–10% | +1.0 | 0.5 |
| -2% to +5% | 0.0 | 0.3 |
| -5% to -2% | -1.0 | 0.5 |
| < -5% | -2.0 | 0.6 |

#### Fragility Detection (post-scoring confidence adjustment)

**NEW in v2.0.** Detects fragile crowding setups where all flow signals agree but positioning is extreme.

**This is NOT a scorer.** It does not add a score to the directional weighted average (doing so would double-count funding, which already contributes a -1.0/-2.0 via `score_funding`). Instead, fragility is a **post-hoc confidence multiplier** applied AFTER the directional avg_score is computed.

**How it works:**

1. Compute `directional_avg_excl_funding` — the directional weighted average using all scorers EXCEPT `score_funding`. This isolates the pure flow/positioning consensus.
2. Check if flow consensus and positioning are dangerously aligned:

```
funding_ann_rate = funding_8h * 3 * 365
bullish_fragile = directional_avg_excl_funding > 0.8 AND funding_ann_rate > 15%
bearish_fragile = directional_avg_excl_funding < -0.8 AND funding_ann_rate < -15%
```

3. If fragile, reduce confidence (NOT avg_score):

| Condition | Confidence Multiplier | Fragility Level |
|-----------|----------------------|-----------------|
| fragile AND abs(funding_ann_rate) > 25% | x 0.5 | HIGH |
| fragile AND abs(funding_ann_rate) 15-25% | x 0.7 | MODERATE |
| Otherwise | x 1.0 | NONE |

**What this achieves:**
- The directional SIGNAL (BULLISH, BEARISH, etc.) remains determined by the full scorer set including funding. Funding's contrarian score already dampens the avg_score.
- Fragility further reduces CONFIDENCE without distorting the weighted average. A BULLISH signal with 15% confidence reads very differently from BULLISH with 45% confidence.
- No double-counting: funding enters the avg_score once via `score_funding`. Fragility uses a separate mechanism (confidence multiplier) keyed off a separate calculation (avg excluding funding).

Rationale: The most dangerous market setups are when everyone agrees (high directional consensus) and everyone is positioned accordingly (extreme funding). The system must express uncertainty in these conditions. Reducing confidence (not score) is the correct mechanism — it tells the trader "the direction signal is probably right but the setup is fragile, size accordingly."

---

### Volatility Scorers

These scorers use the vol richness axis: +2 = expensive (sell vol edge), -2 = cheap (buy vol edge).

#### `score_iv_percentile(iv_pctile)`

| IV Percentile | Score | Weight |
|---------------|-------|--------|
| > 90th | +2.0 | 0.8 |
| 75th–90th | +1.0 | 0.7 |
| 25th–75th | 0.0 | 0.5 |
| 10th–25th | -1.0 | 0.7 |
| < 10th | -2.0 | 0.8 |

#### `score_vrp(vrp, rv_10d, rv_20d, rv_30d, cone_30d_pctile)`

VRP = DVOL - 30d RV. Positive = IV expensive relative to realized = sell vol.

**Stale-data correction** (two distinct cases):

**Case 1: `cone_30d_pctile > 85` (extreme spike inflated 30d RV):**
A single large move has inflated the 30d window. The 10d and 20d windows may not contain the spike, so they give a better forward estimate:
```
forward_rv  = (rv_10d + rv_20d) / 2
dvol_approx = vrp + rv_30d          # recover DVOL from VRP definition
forward_vrp = dvol_approx - forward_rv
```
The effective VRP for scoring becomes `forward_vrp`.

**Case 2: `cone_30d_pctile < 15` (abnormally quiet period, depressed 30d RV):**
The 10d and 20d windows are nested inside the same quiet 30d period — averaging them does NOT correct the baseline (they are equally quiet). Instead:
- Do NOT apply a forward VRP correction. The effective VRP for scoring remains the **raw primary VRP**.
- Append a narrative warning: "30d RV at Xth percentile — abnormally quiet period. If realized vol reverts to historical norms, VRP will compress. Treat current sell-vol edge as potentially overstated."
- The warning appears in the vol assessment but does not alter the score.

The stale correction threshold is **85** for the high case only. The low case (< 15) is a **narrative-only flag**, not a scoring correction.

| Effective VRP (pts) | Score | Weight |
|---------------------|-------|--------|
| > +10 | +2.0 | 0.8 |
| +5 to +10 | +1.0 | 0.7 |
| -5 to +5 | 0.0 | 0.5 |
| -10 to -5 | -1.0 | 0.7 |
| < -10 | -2.0 | 0.8 |

**Narrative uses the same thresholds** (see Stage 4 Vol Assessment). Both scoring and narrative use +/-5 as the neutral band. The narrative reports the **raw primary VRP** value in the header for transparency, but the template selection and scoring use the same ±5 boundaries to avoid contradictions.

---

### Vol Structure Scorer

#### `score_term_structure(shape, spread, iv_by_dte)`

Axis: +2 = steep contango (back months richest), -2 = steep backwardation (front-end stress).

**Kink detection**: If the 3 nearest DTE IVs span > 15 points, the reasoning string flags front-end distortion. This does not alter the numerical score.

| Shape | Spread (abs pts) | Score | Weight |
|-------|-----------------|-------|--------|
| CONTANGO | > 10 | +2.0 | 0.5 |
| CONTANGO | 5–10 | +1.0 | 0.4 |
| CONTANGO | < 5 | 0.0 | 0.3 |
| BACKWARDATION | > 10 | -2.0 | 0.6 |
| BACKWARDATION | 5–10 | -1.0 | 0.5 |
| BACKWARDATION | < 5 | 0.0 | 0.3 |

This score feeds into the vol regime classifier (see Stage 3).

---

### Fear Indicator

#### `score_skew(skew_25d)`

Axis: +2 = extreme fear/hedging demand, -2 = extreme complacency. **This is NOT on the buy/sell vol axis.** Skew measures tail risk pricing, not overall vol richness.

25-delta skew = `put_25d_iv - call_25d_iv`. Positive = puts more expensive = hedging demand.

| Skew (%) | Score | Weight | Interpretation |
|----------|-------|--------|----------------|
| > 12% | +2.0 | 0.6 | Extreme fear — crash hedging elevated |
| 8%–12% | +1.0 | 0.6 | Heavy hedging demand |
| 4%–8% | 0.0 | 0.4 | Normal hedging level |
| 0%–4% | -1.0 | 0.5 | Complacent — puts relatively cheap |
| < 0% | -2.0 | 0.6 | Inverted — rare, extreme complacency or bullish mania |

Skew feeds into:
1. **Vol regime classifier** — skew >= 1 is one condition for EXPLOSIVE
2. **Risk factors** — skew > 12% triggers a risk flag
3. **Trade recommendations** — skew > 10% enables risk reversal opportunity
4. **Vol assessment narrative** — determines which side is rich/cheap

It does NOT feed into directional scoring or vol richness scoring.

---

## Stage 3: RegimeClassifier — Scores → Regimes

### Direction Classification

Runs on all directional scores combined from:

1. **Market-wide** (applied once): funding + futures basis
2. **Top-3 expiries by OI** (excluding expiries with DTE = 0): P/C ratio + DEX + max pain gravity + flow + vanna/charm

Each per-expiry score applies the **DTE score clamping** documented in Stage 2 (scores clamped to ±1.0 for DTE ≤ 2).

**Near-term / far-term scoring** (for transition detection and timeframe display):
- Near-term (0–7 DTE, up to 3 expiries): P/C ratio + DEX + max pain gravity + flow + vanna/charm (full set, DTE-adjusted)
- Far-term (>30 DTE, up to 3 expiries): P/C ratio + DEX + max pain gravity + flow + vanna/charm (full set)
- **Mid-term uses the overall direction** — it is not classified separately.

```
weighted_sum = SUM(score x weight)
total_weight = SUM(weight)
avg_score    = weighted_sum / total_weight
confidence   = abs(avg_score) / 2.0   # normalized 0–1
```

| avg_score | Signal |
|-----------|--------|
| > 1.0 | STRONG_BULLISH |
| 0.3–1.0 | BULLISH |
| -0.3–0.3 | NEUTRAL |
| -1.0 to -0.3 | BEARISH |
| < -1.0 | STRONG_BEARISH |

**Signal enum integer values** (required for TRANSITION math in Stage 3):

```python
class Signal(IntEnum):
    STRONG_BEARISH = -2
    BEARISH = -1
    NEUTRAL = 0
    BULLISH = 1
    STRONG_BULLISH = 2
```

Must use `IntEnum` (not `Enum`) so `.value` returns these integers. The TRANSITION logic relies on sign multiplication (`near.value * far.value < 0`) and magnitude addition (`abs(near.value) + abs(far.value) >= 2`).

### Vol Regime Classification

Decision tree. Inputs: GEX total from largest expiry by OI, normalized by spot.

```
gex_normalized = gex_total / spot

if gex_normalized > 20 AND iv_pctile_score <= 0:
    → SUPPRESSED   (positive gamma + cheap/normal vol = dealers pin range)

elif gex_normalized < -20 AND iv_pctile_score >= 1 AND skew_score >= 1:
    → EXPLOSIVE    (negative gamma + expensive vol + fear = crash risk)

elif iv_pctile_score >= 1 AND (vrp_score >= 1 OR term_structure_score <= -1):
    → ELEVATED     (high IV confirmed by VRP or stressed term structure)

elif iv_pctile_score >= 1:
    → ELEVATED     (high IV, mixed confirmation)

else:
    → NORMAL
```

**Normalization math**: `gex_total / spot`. At BTC $100K: threshold 20 = $2M GEX. At $50K: threshold 20 = $1M. Scales linearly with price.
- VRP and term structure now participate: ELEVATED requires either VRP confirmation (premium is genuinely rich, not just percentile noise) or term structure stress. This prevents ELEVATED from firing on IV percentile alone when VRP says "no edge."
- The old `score_gex` method (which always returned score 0) is removed. GEX enters the system only through `classify_vol_regime` and through `score_vanna_charm` weight adjustment.

### Market Regime Classification

Two inputs: `overall_direction` (Signal enum) + `vol_regime` (VolRegime enum).

**TRANSITION check** (overrides matrix if triggered):

```
magnitude = abs(near_direction.value) + abs(far_direction.value)
conflicting = near_direction.value * far_direction.value < 0

if conflicting AND magnitude >= 2:
    → TRANSITION
```

The `magnitude >= 2` requirement ensures at least one side is STRONG or both sides are at least moderate. This prevents mild disagreements (barely BULLISH vs barely BEARISH) from triggering defensive portfolio actions.

When one timeframe has **no expiries** (e.g., no 0-7 DTE), its direction defaults to NEUTRAL (value=0). Since 0 x anything = 0, which is not < 0, TRANSITION cannot fire — this is correct behavior (you need data on both sides to detect a conflict).

**Regime matrix:**

| Direction | Vol Regime | Market Regime |
|-----------|-----------|---------------|
| BEARISH / STRONG_BEARISH | EXPLOSIVE | VOLATILE_BEARISH |
| BEARISH / STRONG_BEARISH | ELEVATED | VOLATILE_BEARISH |
| BEARISH / STRONG_BEARISH | SUPPRESSED | RANGE_BOUND_BEARISH |
| BEARISH / STRONG_BEARISH | NORMAL | TRENDING_DOWN |
| BULLISH / STRONG_BULLISH | EXPLOSIVE | VOLATILE_BULLISH |
| BULLISH / STRONG_BULLISH | ELEVATED | VOLATILE_BULLISH |
| BULLISH / STRONG_BULLISH | SUPPRESSED | RANGE_BOUND_BULLISH |
| BULLISH / STRONG_BULLISH | NORMAL | TRENDING_UP |
| NEUTRAL | EXPLOSIVE | TRANSITION |
| NEUTRAL | ELEVATED | RANGE_BOUND_ELEVATED |
| NEUTRAL | SUPPRESSED | RANGE_BOUND_NEUTRAL |
| NEUTRAL | NORMAL | RANGE_BOUND_NEUTRAL |

**Changes from v1:**
- `RISK_OFF` removed entirely (was unreachable dead code).
- `RANGE_BOUND` split into 3 sub-types:
  - `RANGE_BOUND_NEUTRAL` — no directional lean, vol suppressed or normal. Classic sell-premium setup with balanced wings.
  - `RANGE_BOUND_BULLISH` — bullish lean but vol suppressed by positive gamma. Grind-higher in range. Slightly skew the short strikes upward.
  - `RANGE_BOUND_BEARISH` — bearish lean but vol suppressed. Grind-lower in range. Slightly skew the short strikes downward.
  - `RANGE_BOUND_ELEVATED` — no direction but vol is expensive. Pure vol-selling opportunity — widest wings, most aggressive premium capture.

---

## Stage 4: NarrativeGenerator — Regimes → Text

### Regime Narrative

One paragraph template per `MarketRegime`, filled with key levels from the **largest expiry by OI**:

| Regime | Template focus |
|--------|----------------|
| RANGE_BOUND_NEUTRAL | GEX dampening + put support + call resistance levels + balanced premium selling |
| RANGE_BOUND_BULLISH | Vol suppressed but bullish lean + skew strikes higher + sell put spreads preferred |
| RANGE_BOUND_BEARISH | Vol suppressed but bearish lean + skew strikes lower + sell call spreads preferred |
| RANGE_BOUND_ELEVATED | High IV + no direction = best premium selling environment + wide wings |
| TRENDING_UP | DEX and flow driving upside + long call spreads. Key resistance from GEX call wall |
| TRENDING_DOWN | DEX and flow driving downside + put support as key watch level + put spread advice |
| VOLATILE_BULLISH | Outsized upside + violent pullback warning + buy vol on dips |
| VOLATILE_BEARISH | Cascading liquidation risk + long puts / long straddles only |
| TRANSITION | Conflicting signals + estimated transition window (days to next GEX sign change) + reduce sizing |

**Transition window estimation**: Filters expiries by `MIN_OI = 500` before scanning for GEX sign changes across the term structure. If fewer than 2 meaningful expiries remain, returns `"unclear -- insufficient OI data"`. The first DTE where GEX sign flips relative to the previous expiry is the estimated transition point.

### Vol Assessment

Template selected by **raw primary VRP** (DVOL - 30d RV). Uses the same ±5 boundaries as the VRP scorer:

| VRP (pts) | Template |
|-----------|----------|
| > +10 | `sell_strong` — sell premium in best expiry |
| +5 to +10 | `sell_moderate` — size conservatively |
| -5 to +5 | `neutral` — no edge in vol, use directional structures |
| -10 to -5 | `buy_moderate` — straddles / strangles |
| < -10 | `buy_strong` — buy vol across curve |

**VRP adjustment note** (appended to vol assessment when applicable):
- If `cone_30d_pctile > 85`: Note that 30d RV may be inflated by a prior extreme move. Show forward VRP estimate using 10d/20d avg RV as a model check. State whether forward VRP confirms or conflicts with primary VRP. Primary VRP drives the recommendation.
- If `cone_30d_pctile < 15`: Note that 30d RV is unusually low. VRP may compress if realized vol reverts to mean.
- Otherwise: "30d RV within normal range. VRP is representative."

**Rich/cheap side determination**:
- `skew > 8%` → "OTM puts are rich — selling put premium has edge"
- `skew 4-8%` → "Skew is normal — no clear rich/cheap side"
- `skew < 4%` → "OTM puts are cheap relative to calls — tail risk underpriced"

Note: This assessment describes which side of the vol surface has edge. It is NOT a directional recommendation. "Puts are rich" means selling OTM puts has positive expected value from a vol perspective, not that the trader should take directional put exposure.

### Risk Factors

Threshold-triggered list (any triggered items are joined with ` | `):

| Condition | Flag |
|-----------|------|
| `cone_30d_pctile > 90` | Extreme recent move — repeat or mean-revert risk |
| `gex_total / spot < -50` | Deeply negative GEX (~$5M at $100K) — cascading stop-outs possible |
| `largest_expiry.dte <= 3` | Pin risk + gamma spike near major expiry |
| `abs(funding_8h) > 0.03%` | Crowded positioning at squeeze risk |
| `skew_25d > 12%` | Crash risk priced in — extreme hedging demand |
| `fragility_multiplier < 1.0` | Directional consensus + extreme positioning — reversal risk elevated |

**Funding risk factor detail**: Threshold is `abs(funding_8h) > 0.03%` per 8h (~32.85% annualized). Uses `funding_8h` for both threshold check AND annualized display: `ann_rate = funding_8h x 3 x 365`. No mixing of `funding_rate` and `funding_8h`. Threshold is set to fire only on genuinely extreme positioning, not routine trending-market rates.

**GEX risk factor**: Normalized threshold `gex_total / spot < -50` (equivalent to ~$5M at BTC $100K, ~$2.5M at $50K).

If nothing triggers: "No elevated risk factors detected."

### Trade Recommendations

Rule-based, multiple can fire simultaneously:

| Condition | Recommendation |
|-----------|----------------|
| RANGE_BOUND_* + IV > 70th | **PRIMARY** Short Iron Condor — skew-adjusted (see below) |
| EXPLOSIVE regime OR IV < 30th | **PRIMARY** Long Straddle/Strangle (far-term expiry) |
| TRENDING_UP or VOLATILE_BULLISH | **SECONDARY** Bull Call Spread (far-term expiry) |
| TRENDING_DOWN or VOLATILE_BEARISH | **SECONDARY** Bear Put Spread (far-term expiry) |
| skew > 10% AND not any bearish regime (TRENDING_DOWN, VOLATILE_BEARISH, RANGE_BOUND_BEARISH) | **OPPORTUNISTIC** Risk Reversal (sell OTM put, buy OTM call) |
| TRANSITION | **DEFENSIVE** Reduce sizing + long straddle on far-term expiry |

**Iron Condor skew adjustment**:
- `skew > 8%`: Puts are rich — keep the short put at 25-delta (where premium is fattest) and push the long put protection further OTM (e.g., from 10-delta to 5-delta). This widens the put credit spread while retaining the premium edge on the rich side. Do NOT move the short strike to 15-delta — that reduces premium collected from the overpriced side.
- `skew < 2%`: Calls relatively expensive — keep short call at 25-delta, push long call protection further OTM to widen the call credit spread.
- `skew 2-8%`: Normal — symmetric wings at GEX support/resistance levels.
- For RANGE_BOUND_BULLISH: additionally shift the center of the IC upward (higher short put, higher short call).
- For RANGE_BOUND_BEARISH: shift center downward.

**Best sell expiry** = highest ATM IV expiry where DTE is 5–30 and OI > 2,000. If no expiry matches: fallback to the nearest meaningful expiry (DTE >= 1, OI > 500).

**Best buy expiry** = highest volume expiry where DTE > 14. Volume is a better proxy for execution quality (tight spreads) than OI. If no expiry matches: fallback to the largest OI expiry with DTE > 14. If still nothing: use the furthest-DTE expiry.

---

## Stage 5: Assembly — Final Output Structure

```
================================================================================
EXECUTIVE SYNTHESIS -- BTC OPTIONS MARKET
Generated: YYYY-MM-DD HH:MM:SS
================================================================================
BTC $XX,XXX | Regime: [MARKET_REGIME]
Direction: [SIGNAL] | Vol: [VOL_REGIME]
--------------------------------------------------------------------------------
DVOL: X.X%  | IV Pctile: XXth  | ATM IV (front): ~X.X%
10d RV: X.X%  | 20d RV: X.X%  | 30d RV: X.X% (XXth cone)
VRP: +X.Xpts  | Term Structure: CONTANGO (+X.Xpts)
Perp Funding: X.XXXX%  | 8h: X.XXXX%
--------------------------------------------------------------------------------

[REGIME NARRATIVE PARAGRAPH]

NEAR-TERM (0-7 DTE): [direction] bias | GEX [X]M ([dampening/amplifying]) | OI: X,XXX contracts
  [expiry] ([X]d): MaxPain $XX,XXX ([+X.X]%) | P/C X.XX | ATM IV X.X% | Skew [+X.X]% | Flow: [bias]

MID-TERM (7-30 DTE): [overall_direction] bias | ...

FAR-TERM (30+ DTE): [far_direction] bias | ...

VOL ASSESSMENT: [vol template paragraph]

RISK FACTORS: [threshold-triggered items]

INSTITUTIONAL FLOW (Block Trades): [X] buys ($X.XM) | [X] sells ($X.XM)

TRADE RECOMMENDATIONS:
[PRIMARY / SECONDARY / OPPORTUNISTIC / DEFENSIVE lines]

SCORING DETAIL:
  Direction: [SIGNAL] (confidence: XX%)
  Fragility: [NONE / MODERATE / HIGH]
  Near-term: [SIGNAL] | Far-term: [SIGNAL]
  Vol Regime: [vol_regime]
  Market Regime: [market_regime]
  Effective VRP: +X.Xpts | Skew: +X.X%
```

---

## Key Design Decisions

### 1. GEX is a regime input, not a directional score
GEX has no directional scorer. It enters the system through:
- Vol regime classification (SUPPRESSED vs EXPLOSIVE thresholds)
- Vanna/charm weight adjustment (amplifying vs dampening environment)
- Risk factor flags (deeply negative GEX)
Direction comes from DEX, P/C ratio, flow, max pain, vanna/charm, funding, and futures basis.

### 2. Top-3 expiries by OI drive directional scoring (excluding DTE 0)
Scoring loops over `sorted(expiries, key=lambda e: e.total_oi, reverse=True)[:3]`, but excludes any expiry with DTE = 0 (expired instruments produce mechanical noise, not directional signal). If fewer than 3 expiries exist, all are used. Expiries with DTE 1-2 have all their scores clamped to ±1.0 (score clamping, not weight scaling — weight scaling cancels out in a weighted average when all elements share the same multiplier).

### 3. VRP stale-data correction (high case only)
When `cone_30d_pctile > 85`, a single extreme move has inflated 30d RV. The scorer uses `(rv_10d + rv_20d) / 2` as a forward estimate because the shorter windows may not contain the spike. When `cone_30d_pctile < 15` (abnormally quiet), the 10d/20d windows are nested inside the same quiet 30d period and are equally depressed — averaging them does not correct the baseline. The low case is flagged with a narrative warning only, without altering the score. The narrative reports the raw primary VRP for transparency and uses the same ±5 boundaries as the scorer for template selection.

### 4. TRANSITION regime override with minimum magnitude
Fires when near-term and far-term signals conflict AND `abs(near_value) + abs(far_value) >= 2`. This prevents noise-level disagreements from triggering defensive portfolio actions. One side must be at least STRONG, or both sides must be at least moderate, to represent a genuine structural divergence.

### 5. Vanna is IV-regime-conditional
Vanna's effect depends on the direction of IV change. In high IV (>60th pctile), IV is likely to drop, making positive vanna bullish. In low IV (<40th pctile), IV is likely to rise, making positive vanna bearish. In the middle, vanna signal is zeroed out. This prevents the scorer from producing wrong signals half the time.

### 6. Fragility detection reduces confidence, not score
When flow consensus is strong AND funding shows extreme same-side positioning, the fragility detector multiplies confidence by 0.5-0.7. It does NOT add another score to the weighted average (that would double-count funding). It does not flip direction — it reduces confidence. A BULLISH signal at 15% confidence communicates "probably right but fragile" differently from BULLISH at 45%.

### 7. RANGE_BOUND sub-types differentiate trade advice
Three variants capture materially different trading setups that v1 collapsed into one: directional-suppressed (lean the strikes), neutral-suppressed (symmetric IC), and neutral-elevated (widest wings, most aggressive premium). Each gets a distinct narrative and trade recommendation adjustment.

### 8. Score semantic axes are explicitly separate
Directional (+2 bullish / -2 bearish), vol richness (+2 sell / -2 buy), vol structure (+2 steep contango / -2 backwardation), and fear (+2 extreme / -2 complacent) are four distinct axes. They are never mixed in the same weighted average. Each feeds into its designated classifier as documented in Stage 3.

### 9. Normalized thresholds for GEX and DEX
GEX thresholds use `gex_total / spot` (e.g., threshold 20 = $2M at $100K, $1M at $50K). DEX thresholds use `total_dex / spot` (e.g., threshold 0.005 = 500 delta at $100K, 250 at $50K). Both scale linearly with price, requiring no manual updates as the market grows.

### 10. Term structure participates in vol regime
v1 computed `score_term_structure` but never used it. In v2, backwardation (term_structure_score <= -1) is an alternative confirmer for ELEVATED regime alongside VRP. Deep backwardation signals front-end stress even when VRP alone is ambiguous.

---

## Data Flow Dependency

```
OnChainAnalysisService.fetch_and_analyze()
    |
    |-- GexDexCalculator.calculate(instruments)
    |       -> stores result in analyzer.gex_dex_structured[expiry]
    |
    |-- VolatilitySurfaceCalculator.calculate(instruments)
    |       -> stores result in analyzer.volatility_surface_structured[expiry]
    |
    |-- BuySellFlowCalculator.calculate(instruments)
    |       -> stores result in analyzer.buy_sell_flow_structured[expiry]
    |
    '-- MarketWideCalculator.calculate(all_data)
            -> stores result in analyzer.market_wide_structured
                   |
                   v
        SynthesisMapper reads these four dicts
                   |
                   v
        SynthesisEngine.run(market, expiries)
```

The `return_analyzer=True` flag on `fetch_and_analyze()` is required for the synthesis path. Without it, the analyzer is discarded and `SynthesisMapper` has nothing to read.

---

## Appendix: Scorer Summary Table

| Scorer | Category | Axis | Weight Range | Feeds Into |
|--------|----------|------|-------------|------------|
| `score_pc_ratio` | Directional | +bull/-bear | 0.25–0.7 | Direction classifier |
| `score_dex` | Directional | +bull/-bear | 0.25–0.8 | Direction classifier |
| `score_max_pain_gravity` | Directional | +bull/-bear | 0.15–0.5 | Direction classifier |
| `score_funding` | Directional (contrarian) | +bull/-bear | 0.3–0.6 | Direction classifier |
| `score_flow` | Directional | +bull/-bear | 0.6 | Direction classifier |
| `score_vanna_charm` | Directional | +bull/-bear | 0.15–0.4 | Direction classifier |
| `score_futures_basis` | Directional | +bull/-bear | 0.3–0.6 | Direction classifier |
| `fragility_multiplier` | Post-scoring confidence | 0.5–1.0 multiplier | N/A (not a scorer) | Applied after directional weighted avg |
| `score_iv_percentile` | Vol richness | +sell/-buy | 0.5–0.8 | Vol regime classifier |
| `score_vrp` | Vol richness | +sell/-buy | 0.5–0.8 | Vol regime classifier, Narrative |
| `score_term_structure` | Vol structure | +contango/-backwardation | 0.3–0.6 | Vol regime classifier |
| `score_skew` | Fear indicator | +fear/-complacency | 0.4–0.6 | Vol regime, Risk factors, Trade recs |
