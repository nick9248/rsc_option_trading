# Morning Note Synthesis System

Generates an institutional-grade executive summary from on-chain analysis data.
Runs automatically after every "Load Analysis" click in the GUI and saves both
the full report and synthesis to disk.

## Architecture

```
Core Layer (coding/core/analytics/)
├── synthesis.py
│   ├── ExpiryMetrics          # Typed dataclass — per-expiry structured data
│   ├── MarketWideMetrics      # Typed dataclass — market-wide structured data
│   ├── ScoringEngine          # Converts raw metrics → directional/vol scores
│   ├── RegimeClassifier       # Scores → MarketRegime + VolRegime enums
│   ├── NarrativeGenerator     # Regimes + scores → human-readable text
│   ├── SynthesisEngine        # Master pipeline: run(market, expiries) → str
│   └── SynthesisMapper        # OnChainAnalyzer structured dicts → dataclasses
└── market_wide_calculator.py  # Returns (str, dict) tuples; dict feeds mapper

Service Layer (coding/service/)
├── on_chain/on_chain_analysis_service.py
│   └── fetch_and_analyze(..., return_analyzer=True) → (report, analyzer)
└── morning_note/morning_note_service.py
    ├── generate(currency)              # Full fetch + synthesis
    ├── generate_from_analyzer(analyzer) # Synthesis only (no re-fetch)
    └── save_report_bundle(currency, report, synthesis) → Path

GUI Layer (coding/gui/tabs/on_chain_analysis_tab.py)
└── OnChainAnalysisWorker.run()  # Calls service → saves bundle → emits report
```

## Data Flow

```
DeribitApiService
    ↓
OnChainAnalysisService.fetch_and_analyze(return_analyzer=True)
    ↓ populates 4 structured dicts on OnChainAnalyzer:
    ├── analyzer.gex_dex_structured        {expiry: {total_net_gex, key_levels, ...}}
    ├── analyzer.buy_sell_flow_structured  {expiry: {bias_interpretation, flow_trend, ...}}
    ├── analyzer.volatility_surface_structured  {expiry: {atm_iv, skew_25d, ...}}
    └── analyzer.market_wide_structured    {rv_10d, rv_20d, rv_30d, vrp, funding_rate, ...}
    ↓
SynthesisMapper.build_all(analyzer)
    ↓ returns (MarketWideMetrics, List[ExpiryMetrics])
    ↓
SynthesisEngine.run(market, expiries)
    ↓
Executive summary string
```

## Output Files

Every analysis saves to:
```
output/data/onchain_analysis/{BTC|ETH}/report/{YYYYMMDD_HHMMSS}/
├── report.txt     # Full on-chain analysis (per-expiry + market-wide sections)
└── synthesis.txt  # Executive summary
```

## Scoring Pipeline

### Directional Scores (all return `(score, weight, reason)`)

| Scorer | Range | Key thresholds |
|---|---|---|
| `score_pc_ratio` | ±2 | <0.60 strong bull, >1.30 strong bear |
| `score_gex` | 0 only | Negative GEX → breakout risk flag |
| `score_dex` | ±2 | >500 strong bull, <-500 strong bear |
| `score_max_pain_gravity` | ±2 | >±10% from spot = ±2 |
| `score_funding` | ±2 | Uses `funding_8h × 3 × 365`; >20% ann = ±2 |
| `score_flow` | ±2 | "Heavy Buying" = +2, trend adjustment ±0.5 |
| `score_vanna_charm` | ±1 | Sign of net vanna + charm |
| `score_futures_basis` | ±2 | >10% ann contango = +2 |

### Volatility Scores

| Scorer | Range | Key thresholds |
|---|---|---|
| `score_iv_percentile` | ±2 | >90th = +2 (expensive), <10th = -2 (cheap) |
| `score_vrp` | ±2 | Uses `market.vrp` (primary); forward VRP is informational only |
| `score_skew` | ±2 | >12% extreme put demand, <0% inverted |
| `score_term_structure` | ±2 | Shape + abs spread; kink detection on front 3 expiries |

### Regime Classification

```
Direction scores → Signal enum (STRONG_BULLISH/BULLISH/NEUTRAL/BEARISH/STRONG_BEARISH)
Vol scores → VolRegime enum (SUPPRESSED/NORMAL/ELEVATED/EXPLOSIVE)
Signal + VolRegime → MarketRegime enum (TRENDING_UP/RANGE_BOUND/TRANSITION/...)
```

## Unit Conventions

Critical: all vol/funding values in `MarketWideMetrics` are in **percentage points**,
not decimals. The mapper applies `× 100` when reading from structured dicts:

| Field | Source (decimal) | MarketWideMetrics (pct) |
|---|---|---|
| `rv_10d/20d/30d` | `calculate_realized_volatility_multi_window` → 0.585 | 58.5 |
| `funding_rate` | API `current_funding` → -0.000201 | -0.0201 |
| `funding_8h` | API `funding_8h` → -0.000135 | -0.0135 |
| `dvol` | Already in pct | 58.7 |
| `vrp` | VRPCalculator pts | -7.6 |

## Term Structure Display

`structured["spread"] = abs(diff)` — kept unsigned for `score_term_structure` comparisons.
`structured["spread_signed"] = diff` — signed value used in synthesis header display.
Header format: `Term Structure: FLAT (-1.5pts)` — one decimal place, sign preserved.

## Funding Sources

Two different `current_funding` values appear in the report from different API call timings:
- **MARKET METRICS section**: first API call (`_fetch_market_metrics`)
- **PERPETUAL FUNDING & OI section**: second API call inside `calculate_perpetual_funding_trend`)

The synthesis **always uses the second call** (same source as the "PERPETUAL FUNDING & OI" section),
labelled `Perp Funding:` in the header to avoid ambiguity.

## Forward VRP — Design Decision

When `cone_30d_pctile > 85`, the 30d RV may be stale (inflated by a prior extreme move).
The synthesis computes a forward VRP proxy using `dvol − avg(rv_10d, rv_20d)`.

**Rule**: forward VRP is always informational only — it never overrides the primary VRP
(`dvol − rv_30d`) for the trade recommendation. Overriding would create an internal
contradiction between the header (which shows primary VRP) and the VOL ASSESSMENT text.

If the two signals conflict, the synthesis explicitly labels this:
`"NOTE [model]: ... Conflicts with primary VRP (−8.2pts) — treat as uncertain."`

## GEX Transition Signal

`_estimate_transition_window` finds the first expiry where per-expiry GEX changes sign.
**OI filter**: only expiries with `total_oi ≥ 500` are considered — low-OI expiries
produce near-zero GEX whose sign is floating-point noise, not a regime signal.
Output includes OI of the flipping expiry so the reader can judge signal strength.

## Known Model Outputs (not Deribit data)

These fields are derived by the synthesis engine, not raw API fields:
- `MarketRegime` / `VolRegime` classification
- Direction confidence percentage
- Forward VRP proxy
- GEX sign-change transition estimate
- Risk reversal skew threshold (>10% = elevated)

All are labelled in output with `(model:` prefix or `NOTE [model]:` when appropriate.

## Adding a New Scorer

1. Add `@staticmethod score_xxx(...)` to `ScoringEngine` — returns `(score, weight, reason)`
2. Call it in `SynthesisEngine.run()` Step 1 (directional) or Step 2 (vol)
3. Add to `all_direction_scores` or pass to `classify_vol_regime`
4. Add unit test in `tests/unit/analytics/test_synthesis.py`

## Tests

```
tests/unit/analytics/test_synthesis.py  — 18 tests
    TestScoreFundingBugFix      (3)  — funding_8h used for annualized rate
    TestBuildMarketWide         (2)  — mapper unit conversion
    TestBuildExpiryMetrics      (5)  — missing data handling
    TestBuildAll                (2)  — full pipeline assembly
    TestSynthesisEngineRun      (6)  — no-crash, regime labels, trade recs
```

Run: `pytest tests/unit/analytics/test_synthesis.py -v`
