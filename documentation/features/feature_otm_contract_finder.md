# OTM Contract Finder — Complete Documentation

**Last Updated**: March 15, 2026
**Version**: 1.0
**Branch**: `strategies/otm_contracts` (merged to `main`)
**Status**: Implementation complete. Logic calibration pending VPS data (see [Pending Calibration](#pending-calibration)).

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [The Four-Gate Pipeline](#the-four-gate-pipeline)
   - [Gate 1 — Liquidity Filter](#gate-1--liquidity-filter)
   - [Gate 2 — Volatility Regime](#gate-2--volatility-regime)
   - [Gate 3 — Directional Scorer](#gate-3--directional-scorer)
   - [Gate 4 — Strike & Expiry Optimizer](#gate-4--strike--expiry-optimizer)
4. [Kelly Sizing](#kelly-sizing)
5. [OTMSignal Model](#otmsignal-model)
6. [OTMConfig — Tunable Parameters](#otmconfig--tunable-parameters)
7. [GUI: Special Strategies Tab](#gui-special-strategies-tab)
8. [GUI: OTM Contracts View](#gui-otm-contracts-view)
9. [Data Requirements](#data-requirements)
10. [File Structure](#file-structure)
11. [Pending Calibration](#pending-calibration)

---

## Overview

The OTM Contract Finder is a systematic option-buying framework for BTC and ETH on Deribit. It scans the full options chain, applies four sequential filtering and scoring gates, and surfaces ranked trade signals with Kelly-sized position recommendations.

The core philosophy is **capital preservation first**: contracts are eliminated early and cheaply (Gate 1), then allowed into the pipeline only when the volatility environment is favourable (Gate 2). Directional bias and optimal strike selection follow from market data, not intuition.

### Key Properties

- **Assets**: BTC, ETH, or both simultaneously
- **Direction**: Calls, puts, or auto-detected from Gate 3
- **Expiry preference**: Short (≤7 DTE), medium (8–21 DTE), long (>21 DTE), or auto-selected by Gate 4
- **Risk model**: Fractional Kelly with hard floor of 70% stop-loss
- **Paper trading guard**: Gate 2 suppression flag prevents live execution when regime is unfavourable; signals are still produced for review
- **Auto-refresh**: Special Strategies tile refreshes Gate 2 score every 30 minutes without running the full pipeline

---

## System Architecture

Follows the project's layered architecture:

```
┌──────────────────────────────────────────────────────┐
│  GUI Layer                                           │
│  SpecialStrategiesTab → OTMContractsView             │
│  OTMSignalCard (one card per signal)                 │
│  GateScoreBar (reusable score widget)                │
└───────────────────┬──────────────────────────────────┘
                    │ QThread worker (OTMFinderWorker)
┌───────────────────▼──────────────────────────────────┐
│  Service Layer                                       │
│  OTMFinderService                                    │
│  ├── DVOLFetcher (fetches Deribit DVOL index)        │
│  ├── StablecoinFetcher (stablecoin inflow %)         │
│  └── IBITFetcher (IBIT put/call ratio for BTC)       │
└───────────────────┬──────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────┐
│  Core Layer                                          │
│  LiquidityGate     (Gate 1 — pure computation)       │
│  VolatilityRegimeGate  (Gate 2 — pure computation)   │
│  DirectionalScorer     (Gate 3 — pure computation)   │
│  StrikeExpiryOptimizer (Gate 4 — pure computation)   │
│  KellySizer            (position sizing)             │
│  OTMSignal (Pydantic, frozen)                        │
│  OTMConfig (Pydantic, frozen)                        │
└──────────────────────────────────────────────────────┘
```

All four gates are stateless, pure-computation classes. External I/O (API, DB) is handled exclusively in the service layer.

---

## The Four-Gate Pipeline

Data flows through all four gates sequentially per asset. A contract that fails any gate is eliminated. The gates are **intentionally ordered cheapest-first** to minimise API calls.

```
Options chain (Deribit)
        │
        ▼
  Delta pre-filter: |delta| ∈ [0.05, 0.45]
        │
        ▼
  GATE 1 — Liquidity (per contract)
  Pass: bid/ask spread + OI + volume checks
        │ surviving contracts
        ▼
  Fetch: DVOL, on-chain data, OHLCV, funding rates
        │
        ▼
  GATE 2 — Volatility Regime (asset-level)
  Pass: DVOL percentile, VRP, GARCH, term structure
  If fail and gate2_override=False → return [] for this asset
        │
        ▼
  GATE 3 — Directional Scorer (asset-level)
  Output: call_score, put_score ∈ [0, 100]
  Selects call/put direction; computes regime flag
        │
        ▼
  GATE 4 — Strike & Expiry Optimizer (per contract)
  Pass: delta range, vega/theta ratio, breakeven filter
  Ranks surviving contracts
        │
        ▼
  Kelly Sizer → OTMSignal objects
```

### Gate 1 — Liquidity Filter

**Location**: `coding/core/strategy/otm/signals/liquidity_gate.py`

Eliminates contracts that are too illiquid to enter or exit cleanly.

| Check | Threshold (default) | Notes |
|-------|--------------------|----|
| Relative bid/ask spread | ≤ 8% of mid | `max_bid_ask_spread_relative` |
| Absolute bid/ask spread | ≤ 4.0 volatility points | `max_bid_ask_spread_absolute` |
| Open interest (BTC) | ≥ 50 contracts | `min_oi_btc` |
| Open interest (ETH) | ≥ 200 contracts | `min_oi_eth` |
| Volume/OI ratio | ≥ 5% | `min_volume_oi_ratio` |
| Entry cost vs spread | Premium ≥ 5× bid/ask spread | `tx_cost_floor_multiplier` |

All checks must pass. Any single failure eliminates the contract.

**Implementation note**: Deribit's `get_book_summary` response does not always include greeks. When `delta = 0.0` and mark_iv > 0, the service layer falls back to a Black-Scholes delta approximation using a logistic N(d₁) function (no scipy dependency).

### Gate 2 — Volatility Regime

**Location**: `coding/core/strategy/otm/signals/volatility_regime_gate.py`

Asset-level gate. Determines whether the current vol environment is favourable for buying premium.

**Inputs**:
- DVOL history (up to 36 months, fetched from DB)
- Current DVOL index value (fetched live from Deribit)
- ATM IV 30-day
- 30-day Parkinson realized volatility
- Daily OHLCV (for GARCH forecast)
- IV term structure

**Sub-scores and weights**:

| Sub-score | What it measures | Weight |
|-----------|-----------------|--------|
| V1 — DVOL percentile | Current DVOL vs 36-month history | High |
| V2/V4 — VRP | IV − RV spread (cheap vol) | Medium |
| V3 — GARCH | GARCH forecast > current IV | Medium |
| Term structure | Contango vs backwardation | Medium |

**Actions**:

| Total score | Action | Meaning |
|-------------|--------|---------|
| ≥ 40 | `new_entries_allowed` | Full green light |
| 30–39 | `partial_exit` | No new entries; manage existing |
| < 30 | `full_exit` | Exit all positions |

When `action != new_entries_allowed` and `gate2_override=False`, the pipeline returns no signals for this asset.

**Gate 2 Override**: Available in Advanced Filters. Bypasses the gate2 action check, producing signals labelled `gate2_suppressed=True`. These are paper-trade only — the signal card disables the PAPER TRADE button unless override was active.

**Current behavior with sparse data**: GARCH requires ≥ 90 daily OHLCV candles for a reliable forecast. With fewer candles (e.g., 60), sub-signal B defaults to 50 (neutral). This is correct behavior, not a bug. Gate 2 calibration will improve once the VPS has collected 90+ days of OHLCV data.

### Gate 3 — Directional Scorer

**Location**: `coding/core/strategy/otm/signals/directional_scorer.py`

Produces `call_score` and `put_score` in [0, 100] and a `regime_flag` (bull/bear/neutral). The pipeline trades in the direction of whichever score is higher (when `direction="auto"`).

**Sub-signals**:

| ID | Signal | Data source |
|----|--------|------------|
| D1/D7 | GEX/DEX positioning | On-chain analyzer |
| D2 | Funding rate percentile | Funding rate history (DB) |
| D3 | 25-delta risk reversal (RR25) | RR25 history (DB) |
| D4 | Put/call ratio percentile | PC ratio history (DB) |
| D6/D9 | Stablecoin inflow | StablecoinFetcher |
| D8 | IBIT P/C ratio (BTC only) | IBITFetcher |
| D10 | Block trade flow | On-chain analyzer |
| RIS | Regime indicator score | OHLCV (EMA fast/slow/trend SMA) |

Each sub-signal is in [−1, +1]. The regime multiplier (`1.30` for calls in bull, `0.70` in bear) adjusts the final directional score.

**Current behavior with sparse data**: D2 (funding), D3 (RR25), and D4 (PC ratio) require populated DB tables (`funding_rate_history`, `rr25_history`, `pc_ratio_history`). With empty or sparse tables these sub-signals default to neutral (0.0). Calibration pending VPS data.

### Gate 4 — Strike & Expiry Optimizer

**Location**: `coding/core/strategy/otm/signals/strike_expiry_optimizer.py`

Filters and ranks the surviving contracts from Gate 1 based on strike quality for the chosen direction and expiry.

**Checks**:

| Check | Notes |
|-------|-------|
| Delta range | Directional: 0.20–0.35; Event: 0.10–0.20 |
| Vega/theta ratio | Short expiry ≥ 0.05, medium ≥ 0.30, long ≥ 0.80 |
| Breakeven filter | Breakeven move ≤ `max_breakeven_move_multiplier × GARCH 30d forecast` |
| Expiry category match | Filters to selected expiry preference (short/medium/long/auto) |

Contracts are ranked by a composite of Gate 2 score, Gate 3 directional score, vega/theta ratio, and gamma/premium ratio.

---

## Kelly Sizing

**Location**: `coding/core/strategy/otm/scoring/kelly_sizer.py`

Translates conviction into a USD position size using fractional Kelly criterion.

**Conviction score** = average of Gate 2 score (vol regime quality) and Gate 3 directional score.

**Position sizing**:

| Conviction band | p_win prior | avg_return prior | Kelly divisor |
|----------------|-------------|-----------------|---------------|
| 40–60 | 0.35 | 1.5× | 4.0 |
| 60–75 | 0.40 | 2.0× | 4.0 |
| 75–90 | 0.45 | 2.5× | 4.0 |
| 90–100 | 0.50 | 3.0× | 4.0 |

Position USD = `kelly_fraction × risk_budget_usd × conviction_factor`, capped at `max_single_trade_pct` of budget and `max_correlated_pct` for correlated assets.

**Take profit multiple** scales with conviction and DTE. Higher conviction + shorter DTE = tighter take-profit.

**Stop losses** (set at signal generation time, not yet automated in execution):
- Hard floor: 70% premium loss
- Thesis stop (call): spot drops below 85% of entry underlying
- Thesis stop (put): spot rises above 115% of entry underlying
- Time stop: DTE ÷ 2 remaining

---

## OTMSignal Model

**Location**: `coding/core/strategy/otm/models/otm_signal.py`

Pydantic model, frozen (immutable once created). Persisted to `otm_signals` table via `DatabaseRepository.save_otm_signals()`.

**Key fields**:

| Field | Type | Description |
|-------|------|-------------|
| `signal_id` | str (UUID4) | Unique identifier |
| `generated_at` | datetime (UTC) | Signal creation time |
| `asset` | "BTC" \| "ETH" | Underlying asset |
| `instrument_name` | str | Deribit instrument (e.g. `BTC-28MAR26-100000-C`) |
| `direction` | "call" \| "put" | Trade direction |
| `strike` | float | Option strike price |
| `expiry` | str | Expiry date string |
| `dte` | int | Days to expiry at signal time |
| `expiry_category` | "short"\|"medium"\|"long" | ≤7 / 8–21 / >21 DTE |
| `delta` | float | Contract delta (BS fallback if greeks absent) |
| `mark_iv` | float | Mark implied volatility (decimal, e.g. 0.68) |
| `entry_premium` | float | Mark price in USD at signal time |
| `gate2_score` | float [0,100] | Vol regime score |
| `conviction_score` | float [0,100] | Kelly conviction (avg Gate 2 + Gate 3) |
| `position_usd` | float | Recommended USD allocation |
| `take_profit_multiple` | float | Exit at this × entry premium |
| `stop_loss_pct` | float | Hard floor % loss before exit |
| `time_stop_dte` | int | Exit if DTE ≤ this value |
| `gate2_suppressed` | bool | True = regime unfavourable, paper trade only |
| `regime_flag` | "bull"\|"bear"\|"neutral" | Gate 3 regime |

---

## OTMConfig — Tunable Parameters

**Location**: `coding/core/strategy/otm/models/otm_config.py`

Pydantic model, frozen. All thresholds live here so calibration is a config change, not a code change. Default values are theory-derived starting points pending backtesting.

**Categories**:

| Category | Key params |
|----------|-----------|
| Budget | `risk_budget_usd`, `max_single_trade_pct=10%`, `max_correlated_pct=10%` |
| Gate 1 | `max_bid_ask_spread_relative=8%`, `min_oi_btc=50`, `min_volume_oi_ratio=5%` |
| Gate 2 | `gate2_suppress_threshold=40`, `dvol_percentile_threshold=30%`, `garch_iv_ratio_threshold=1.10` |
| Gate 3 | `rr_z_score_threshold=1.5`, `funding_percentile_bull/bear=10/90%` |
| Gate 4 | `min_delta_directional=0.20`, `max_delta_directional=0.35`, `vega_theta_medium=0.30` |
| Kelly | `kelly_divisor=4.0`, p_win/avg_return priors per conviction band |
| Exits | `stop_loss_hard_floor_pct=0.70`, `thesis_stop_call_pct=0.85` |

**Usage**:
```python
from coding.core.strategy.otm.models.otm_config import OTMConfig
config = OTMConfig(risk_budget_usd=10_000.0)  # all other fields use defaults
```

---

## GUI: Special Strategies Tab

**Location**: `coding/gui/tabs/special_strategies_tab.py`
**Access**: Main window → "Special Strategies" tab

The entry point to the OTM Contract Finder from the GUI. Displays a tile grid of available special strategies.

### OTM Contract Finder Tile

The tile shows:
- **Gate 2 score bar** (color-coded: green ≥60, amber 40–59, red <40)
- **Regime badge** (BULL / BEAR / NEUTRAL)
- **Last scan time**
- **Signal count** from last run
- **Status dot** (green = active, amber = suppressed, grey = not yet scanned)

**Auto-refresh**: A QTimer fires every 30 minutes and calls `score_gate2()` — the lightweight Gate 2-only fetch — to update the tile without running the full 4-gate pipeline.

**Opening the view**: Click the tile to navigate to the full OTM Contracts View (page 1 of the QStackedWidget). The Back button returns to the tile grid.

---

## GUI: OTM Contracts View

**Location**: `coding/gui/tabs/otm_contracts_view.py`
**Components**: `OTMContractsView` (main view), `OTMFinderWorker` (QThread)

### Layout

Two-panel layout via QSplitter (300px left / 900px right):

```
┌─────────────────────────┬──────────────────────────────────────┐
│  LEFT PANEL (300px)     │  RIGHT PANEL                         │
│                         │                                      │
│  LIVE CONDITIONS        │  [Header: N signals | Gate2 | Regime]│
│  ├ Gate 2 score bar     │  [Warning banner if suppressed]      │
│  ├ Regime badge         │                                      │
│  └ Last scanned         │  OTMSignalCard #1                    │
│  ─────────────────────  │  OTMSignalCard #2                    │
│  QUICK SETUP            │  OTMSignalCard #3                    │
│  ├ Asset: BTC│ETH│BOTH  │  ...                                 │
│  ├ Direction: CALLS│    │                                      │
│  │   PUTS│AUTO          │                                      │
│  └ Expiry: SHORT│       │                                      │
│    MEDIUM│LONG│AUTO     │                                      │
│  ─────────────────────  │                                      │
│  ▶ ADVANCED FILTERS     │                                      │
│  [collapsible]          │                                      │
│                         │                                      │
│  [FIND OTM CONTRACTS]   │                                      │
│  N signals | HH:MM:SS   │                                      │
└─────────────────────────┴──────────────────────────────────────┘
```

### Advanced Filters (collapsible)

| Filter | Default | Range |
|--------|---------|-------|
| Min Conviction | 60 | 40–100 |
| Min Delta | 0.20 | 0.05–0.45 |
| Max Delta | 0.35 | 0.05–0.45 |
| Kelly Multiplier | 0.25 | 0.05–0.50 |
| Gate 2 Override | Off | checkbox |
| Show Suppressed | Off | checkbox |

Delta and conviction filters are applied client-side after the service returns results.

### Scan Workflow

1. User clicks **FIND OTM CONTRACTS**
2. `OTMFinderWorker` starts in a background QThread
3. Worker calls `score_gate2(asset)` first — emits `gate2_updated` signal mid-run to update the left panel score bar without waiting for the full pipeline
4. Worker calls `find_signals()` to run all four gates
5. Client-side filters applied (conviction, delta range, suppressed)
6. `finished` signal emits the filtered signal list
7. One `OTMSignalCard` is created per signal, inserted into the right panel scroll area

### OTMSignalCard

**Location**: `coding/gui/components/otm_signal_card.py`

Expandable card for one signal. Collapsed view shows instrument, direction, strike, expiry, delta, conviction, and position size. Expanded view adds all gate scores and sub-signal breakdown.

**Buttons**:
- **PAPER TRADE**: Logs the signal as JSON to `forward_test_dir`. Disabled when `gate2_suppressed=True` (unless Gate 2 Override was active during the scan).
- **BREAKDOWN**: Toggles the Gate 3 sub-signal breakdown panel (D1–D10, RIS scores).
- **COPY**: Copies a summary string to clipboard.

### GateScoreBar

**Location**: `coding/gui/components/gate_score_bar.py`

Reusable 0–100 score bar widget. Color coding:
- **Green** (≥ 60): `Colors.SUCCESS`
- **Amber** (40–59): `Colors.WARNING`
- **Red** (< 40): `Colors.ERROR`

Used in both the Special Strategies tile and the OTM Contracts View live conditions panel.

---

## Data Requirements

The pipeline draws from the following sources:

| Data | Source | DB Table | Status |
|------|--------|----------|--------|
| Options chain (greeks, IV, OI, volume) | Deribit API (live) | — | Live |
| DVOL index (current) | Deribit API (live) | — | Live |
| DVOL history | DB | `dvol_history` | Collected by VPS daemon |
| OHLCV daily candles | DB | `ohlcv_history` | Collected by VPS daemon |
| Funding rate history | DB | `funding_rate_history` | Collected by VPS daemon |
| On-chain analytics (GEX, VRP, term structure) | OnChainAnalysisService | — | Live computation |
| Stablecoin inflow % | StablecoinFetcher | — | External API |
| IBIT P/C ratio (BTC only) | IBITFetcher | — | External API |
| Put/call ratio history | DB | `pc_ratio_history` | **Not yet collected** |
| 25-delta risk reversal history | DB | `rr25_history` | **Not yet collected** |

---

## File Structure

```
coding/
├── core/strategy/otm/
│   ├── models/
│   │   ├── otm_config.py       # All tunable thresholds (Pydantic, frozen)
│   │   └── otm_signal.py       # Output signal model (Pydantic, frozen)
│   ├── signals/
│   │   ├── liquidity_gate.py           # Gate 1
│   │   ├── volatility_regime_gate.py   # Gate 2
│   │   ├── directional_scorer.py       # Gate 3
│   │   └── strike_expiry_optimizer.py  # Gate 4
│   └── scoring/
│       └── kelly_sizer.py      # Position sizing
│
├── service/strategy/otm/
│   ├── otm_finder_service.py   # Pipeline orchestrator
│   └── fetchers/
│       ├── dvol_fetcher.py     # Deribit DVOL index
│       ├── stablecoin_fetcher.py
│       └── ibit_fetcher.py
│
├── gui/
│   ├── tabs/
│   │   ├── special_strategies_tab.py   # Tile grid entry point
│   │   └── otm_contracts_view.py       # Full two-panel view
│   └── components/
│       ├── gate_score_bar.py   # Reusable score bar widget
│       └── otm_signal_card.py  # Expandable signal card
│
└── core/database/
    └── repository.py           # get_dvol_history, get_ohlcv_daily, save_otm_signals, ...
```

---

## Pending Calibration

> **The implementation is complete. Logic calibration is blocked pending sufficient historical data from the VPS.**

The following items require backtesting once the VPS has collected enough data:

### 1. Gate 1 — Spread Thresholds
The current `max_bid_ask_spread_relative=8%` and `min_oi_btc=50` are conservative starting points. After 30+ days of data, analyse the distribution of spreads and OI across the full BTC/ETH options chain to calibrate thresholds that reject illiquid contracts without over-filtering.

### 2. Gate 2 — DVOL Percentile and GARCH
- **DVOL percentile** (`dvol_percentile_threshold=30%`) requires ≥ 90 days of DVOL history for a meaningful 36-month lookback. Current DB has partial history.
- **GARCH sub-signal B** defaults to neutral (50) until ≥ 90 daily OHLCV candles are available. This is expected behavior. Once the VPS delivers ≥ 90 candles, the GARCH forecast will activate automatically.

### 3. Gate 3 — Sub-signal Calibration
D2 (funding rate), D3 (RR25), and D4 (PC ratio) currently contribute 0.0 to directional scores because their history tables (`funding_rate_history`, `rr25_history`, `pc_ratio_history`) are sparse or empty. Calibration steps:
- Ensure `funding_rate_history` is populated by the VPS daemon (currently collected)
- Add data collection for `rr25_history` (25-delta risk reversal) and `pc_ratio_history`
- After 60+ days of history, validate that z-score/percentile thresholds produce sensible directional signals

### 4. Kelly Priors
The p_win priors (0.35–0.50) and avg_return priors (1.5×–3.0×) are theory-derived starting points. After a 3–6 month forward-testing period using the PAPER TRADE log, update these priors with empirical win rates from `forward_test_dir` JSON files.

### 5. Gate 4 — Breakeven Filter
The `max_breakeven_move_multiplier=2.0` (breakeven must be reachable within 2× the GARCH 30-day forecast move) is a conservative default. Once GARCH is active, review how many contracts are being eliminated by this filter and adjust if needed.

### Timeline
- **Data ready estimate**: ~2–4 weeks post-VPS deployment (VPS collecting since March 15, 2026)
- **Calibration gate**: minimum 60 OHLCV candles for GARCH, 90+ days DVOL history for percentile
- **Forward test gate**: 3–6 months of paper trading before live sizing is trusted

---

*For the original design specification, see: `docs/superpowers/specs/2026-03-14-otm-contract-finder-design.md`*
