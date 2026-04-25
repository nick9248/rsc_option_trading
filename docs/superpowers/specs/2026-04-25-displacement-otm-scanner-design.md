# Displacement OTM Scanner — Design Spec
**Date**: 2026-04-25  
**Branch**: `understanding/on_chain_analysis`  
**Status**: Approved, ready for implementation planning

---

## Background

The user's proven trading edge: buy long-dated OTM calls after a heavy BTC/ETH price displacement (20%+ drop), hold until 50–400% profit. This was done intuitively. The goal is to make it systematic, scientifically rigorous, and automated.

The existing 4-gate OTM Finder system is being replaced entirely. It was designed for a "buy cheap vol" thesis (enter when DVOL < 30th percentile) — the opposite of the user's actual edge (enter during elevated vol after a crash). It also has two broken signals (rr25_history, pc_ratio_history unpopulated) and has never been run live.

---

## What Gets Deleted

All of the following are removed:
- `coding/core/strategy/otm/` — entire directory
- `coding/service/strategy/otm/` — entire directory
- `coding/gui/tabs/otm_contracts_view.py`
- `coding/gui/components/otm_signal_card.py`
- `coding/gui/components/gate_score_bar.py`
- OTM tile from `coding/gui/tabs/special_strategies_tab.py`

DB tables `dvol_history`, `ohlcv_history`, `funding_rate_history` are kept — they feed the new system.

---

## System Overview

A 24/7 displacement scanner:
- Monitors BTC and ETH price every 5 minutes
- Detects significant price drops against configurable thresholds
- Scores the setup using 6 market signals via a trained logistic regression model
- Selects the optimal OTM call contract to buy
- Sends a Telegram alert when conviction ≥ threshold
- Runs headless on VPS; also accessible via a simple GUI tab locally

---

## Architecture

Layered architecture — Core → Service → GUI/VPS daemon.

```
coding/
├── core/displacement/
│   ├── displacement_detector.py      — detects drop events by timeframe
│   ├── conviction_scorer.py          — 6 signals → probability (0–100%)
│   ├── strike_selector.py            — picks optimal OTM call from options chain
│   └── models/
│       ├── displacement_event.py     — Pydantic model for a detected event
│       ├── displacement_signal.py    — Pydantic model for a scored + recommended signal
│       └── displacement_config.py   — Pydantic config (all tunable thresholds)
│
├── service/displacement/
│   ├── displacement_scanner_service.py   — orchestrates detection → scoring → selection
│   ├── backtest_service.py               — historical simulation + model training
│   ├── telegram_alert_service.py         — formats and sends Telegram messages
│   └── historical_options_fetcher.py     — fetches Deribit historical options data
│
└── gui/tabs/
    └── displacement_tab.py               — simple replacement for OTM Contracts tab

scripts/
└── displacement_daemon.py                — headless VPS runner (systemd service)

tests/
└── unit/displacement/
    ├── test_displacement_detector.py
    ├── test_conviction_scorer.py
    ├── test_strike_selector.py
    └── test_telegram_alert_service.py
```

---

## Phase 1: Displacement Detection

### Trigger Logic (`displacement_detector.py`)

Checks price drop against multiple timeframes simultaneously. A displacement event fires when the drop exceeds any threshold:

| Timeframe | Default threshold | Config key |
|-----------|-------------------|------------|
| 1 hour    | −8%               | `drop_1h_threshold` |
| 4 hours   | −12%              | `drop_4h_threshold` |
| 24 hours  | −20%              | `drop_24h_threshold` |
| 7 days    | −30%              | `drop_7d_threshold` |

**Cooldown**: 24 hours per asset after an event fires (`cooldown_hours = 24`). Prevents re-triggering during extended multi-day crashes.

**Data source**: Deribit index price API (same source as `ohlcv_history`). Fetched live every 5 minutes by the daemon.

**Output**: `DisplacementEvent` Pydantic model containing asset, timestamp, drop magnitudes across all timeframes, current price, triggering timeframe.

---

## Phase 2: Conviction Scoring

### The 6 Signals (`conviction_scorer.py`)

Each signal is computed at the moment the displacement event is detected:

| # | Signal | What it measures | Data source | Range |
|---|--------|-----------------|-------------|-------|
| 1 | **Drop magnitude percentile** | How rare is this drop vs last 3 years of history? | `ohlcv_history` | 0–100 |
| 2 | **Drop speed** | 1h drop / 24h drop ratio. Flash crashes bounce faster than slow bleeds | `ohlcv_history` (live + stored) | 0–100 |
| 3 | **Funding rate** | Deeply negative = crowded shorts = squeeze fuel. −1% → 100, 0% → 50, positive → 0 | Deribit perpetual funding API | 0–100 |
| 4 | **DVOL spike magnitude** | How many σ above historical mean is current DVOL? Sweet spot: 1.5–2.5σ. Too high (>3σ) penalized — parabolic IV crushes long vol returns | `dvol_history` + live DVOL | 0–100 |
| 5 | **Max pain distance** | How far below max pain is current spot? Market makers have incentive to push price up. >10% below = 100 | Deribit options chain (live) | 0–100 |
| 6 | **Term structure inversion** | Front-month IV > back-month IV = panic. Inversion depth correlates with bounce size | Deribit options chain (live) | 0–100 |

### Model

**Type**: Logistic regression with L2 regularization (Ridge)  
**Target**: Binary — did buying an OTM call at this event produce >50% profit within 90 days?  
**Features**: The 6 signal values above  
**Output**: Calibrated probability 0–100% (not an arbitrary weighted score)  
**Validation**: Walk-forward time-series split (no lookahead bias)  
**Initial weights**: Equal (1/6 each) until backtest trains the model  
**Retraining**: Monthly on VPS as more events accumulate  
**Model storage**: Serialized with `joblib`, stored in `models/displacement_scorer_v1/`

### Alert Thresholds

| Conviction | Action | Label |
|-----------|--------|-------|
| ≥ 70% | Telegram alert sent | HIGH |
| 50–69% | Telegram alert sent | MEDIUM |
| < 50% | No alert | — |

---

## Phase 3: Strike Selection (`strike_selector.py`)

Filters the live options chain for the optimal contract to buy.

**Filters** (all must pass):

| Criterion | Value |
|-----------|-------|
| Direction | Calls only |
| DTE | 90–270 days |
| Delta | 0.10–0.20 |
| Bid/ask spread | < 8% relative |
| Min open interest | BTC ≥ 50, ETH ≥ 200 contracts |

**Ranking** (among contracts passing filters):
1. Closest delta to 0.15 (center of sweet spot)
2. Tiebreaker: lowest bid/ask spread
3. DTE preference: favor 120–180 days (avoids extreme theta decay and illiquidity)

**Output per contract** (included in `DisplacementSignal`):
- Instrument name, strike, expiry date, DTE
- Delta, IV, mark price (USD)
- Profit targets: price needed for 50% / 100% / 200% gain
- Suggested position size: 2% of configured risk budget (fixed until backtest calibrates this)

---

## Phase 4: Backtest Engine (`backtest_service.py`)

### Process

1. **Find historical events**: Scan `ohlcv_history` for all dates where BTC/ETH triggered any drop threshold. Apply 24h cooldown deduplication.

2. **Reconstruct signals**: For each event date, compute all 6 signal values from stored data (`ohlcv_history`, `funding_rate_history`, `dvol_history`) plus historically fetched options chain data for that date (signals 5 and 6 require this — handled by `historical_options_fetcher.py`).

3. **Fetch historical options** (`historical_options_fetcher.py`):
   - Deribit endpoint: `/public/get_tradingview_chart_data` for historical mark prices
   - For each event: fetch available OTM calls (90–270 DTE, delta 0.10–0.20) at event timestamp
   - Fetch mark price at 30 / 60 / 90 / 180 days post-entry
   - One-time local run (1–3 hours). Results cached to avoid re-fetching.

4. **Label outcomes**: `1` if max gain at any checkpoint ≥ 50%, else `0`. Also record actual max gain, max loss, days to 50% if reached.

5. **Train model**: Logistic regression on (6 signals → label). Walk-forward split. Save model to `models/displacement_scorer_v1/`.

6. **Print backtest report** (no file saved):
```
Events found:          N
Profitable (>50%):     N  (X%)
Avg gain (winners):    X%
Avg loss (losers):     X%
High conviction (≥70): N events, X% win rate
Medium conviction:     N events, X% win rate
Low conviction (<50):  N events — correctly filtered
Top signal by weight:  [signal name]
```

---

## Phase 5: Telegram Alerts (`telegram_alert_service.py`)

**Config**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` loaded from `.env`.

**Alert format**:
```
🚨 DISPLACEMENT ALERT — {ASSET}

Drop: -{X}% in 24h | -{Y}% in 1h
Conviction: {Z}% ({HIGH/MEDIUM})

Signals:
  Drop magnitude   {bar}  {score}
  Funding rate     {bar}  {score}  ({funding_rate}% funding)
  DVOL spike       {bar}  {score}  ({dvol_sigma}σ above mean)
  Max pain dist    {bar}  {score}  ({pct}% below pain)
  Term structure   {bar}  {score}  ({inverted/normal})
  Drop speed       {bar}  {score}  ({timeframe} move)

Recommended contract:
  {INSTRUMENT_NAME}
  Delta: {d} | IV: {iv}% | Premium: ${premium}
  DTE: {dte} days

Profit targets:
  50%  → {ASSET} at ${price}
  100% → {ASSET} at ${price}
  200% → {ASSET} at ${price}

⚠️ Paper trade — verify before acting
```

**Error handling**: If Telegram send fails, log error and continue — never crash the daemon over an alert failure.

---

## Phase 6: Simple GUI Tab (`displacement_tab.py`)

Replaces `otm_contracts_view.py`. Single-panel layout, no collapsible sections.

**Layout**:
```
DISPLACEMENT SCANNER

Asset: [BTC] [ETH] [BOTH]

── CURRENT CONDITIONS ──────────────────────────
BTC   $82,400   -3.2% 24h   Monitoring
ETH   $1,580    -4.1% 24h   Monitoring

── LAST ALERT ──────────────────────────────────
BTC  |  2026-04-10 03:22  |  Conviction: 76%
Contract: BTC-25SEP26-70000-C
Premium: $1,240  |  Delta: 0.14  |  DTE: 153
[VIEW BREAKDOWN]

── CONFIGURATION ───────────────────────────────
Drop threshold 24h:  [ 20 ]%
Min conviction:      [ 60 ]%
Telegram alerts:     [✓] Enabled

[RUN SCAN NOW]             Last scan: 2 min ago
```

**VIEW BREAKDOWN** opens a popup showing the 6 signal scores and the full contract details — same content as the Telegram alert.

**Auto-refresh**: Pulls latest scan result every 5 minutes (matches daemon interval). Does not re-run the full scan — reads last result from DB or cache.

---

## VPS Daemon (`scripts/displacement_daemon.py`)

- Headless, no GUI imports
- Loop: every 5 minutes, run `DisplacementScannerService.scan(assets=["BTC", "ETH"])`
- If event detected and conviction ≥ threshold: call `TelegramAlertService.send(signal)`
- Logs to `/var/log/option_trading/displacement.log`
- Deployed as `systemd` service (same pattern as existing VPS daemons)
- Loads config from `.env`

---

## Configuration (`displacement_config.py`)

All tunable values in one Pydantic frozen model:

```python
drop_1h_threshold: float = 0.08       # 8%
drop_4h_threshold: float = 0.12       # 12%
drop_24h_threshold: float = 0.20      # 20%
drop_7d_threshold: float = 0.30       # 30%
cooldown_hours: int = 24
min_delta: float = 0.10
max_delta: float = 0.20
preferred_delta: float = 0.15
min_dte: int = 90
max_dte: int = 270
preferred_dte_min: int = 120
preferred_dte_max: int = 180
min_oi_btc: int = 50
min_oi_eth: int = 200
max_bid_ask_spread_relative: float = 0.08
alert_high_threshold: float = 0.70    # 70%
alert_medium_threshold: float = 0.50  # 50%
risk_budget_usd: float = 10000.0
position_size_pct: float = 0.02       # 2% per trade
dvol_sweet_spot_low: float = 1.5      # σ above mean
dvol_sweet_spot_high: float = 2.5     # σ above mean
max_pain_distance_full_score: float = 0.10   # 10% below pain = 100
```

---

## Data Flow

```
Every 5 min (daemon / GUI refresh)
  ↓
DisplacementDetector.check(asset)
  ├── No displacement → log "monitoring", sleep
  └── Displacement detected → DisplacementEvent
        ↓
ConvictionScorer.score(event)
  ├── Fetch: funding rate, DVOL, options chain (max pain, term structure)
  ├── Compute 6 signals
  └── Run logistic regression → probability
        ↓
If probability ≥ 50%:
  StrikeSelector.select(asset, options_chain)
  └── DisplacementSignal (event + score + contract)
        ↓
TelegramAlertService.send(signal)   [if enabled]
DB: save signal to displacement_signals table
GUI: update last alert display
```

---

## Database

New table: `displacement_signals`

```sql
CREATE TABLE displacement_signals (
    id              SERIAL PRIMARY KEY,
    asset           VARCHAR(10) NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL,
    drop_24h_pct    NUMERIC(6,4),
    drop_1h_pct     NUMERIC(6,4),
    conviction_pct  NUMERIC(5,2),
    conviction_label VARCHAR(10),
    instrument_name VARCHAR(50),
    strike          NUMERIC(12,2),
    expiry_date     DATE,
    dte             INTEGER,
    delta           NUMERIC(5,4),
    mark_iv         NUMERIC(6,4),
    premium_usd     NUMERIC(10,2),
    signal_breakdown JSONB,
    telegram_sent   BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Implementation Phases (ordered)

1. **Delete** old OTM system (files listed above)
2. **Core models**: `DisplacementConfig`, `DisplacementEvent`, `DisplacementSignal`
3. **Core logic**: `DisplacementDetector`, `ConvictionScorer` (equal weights initially), `StrikeSelector`
4. **Service**: `DisplacementScannerService`, `TelegramAlertService`
5. **GUI tab**: `displacement_tab.py` (simple, replaces old tab)
6. **Tests**: unit tests for detector, scorer, selector, telegram service
7. **Backtest**: `HistoricalOptionsFetcher`, `BacktestService`, model training
8. **DB migration**: `displacement_signals` table
9. **VPS daemon**: `displacement_daemon.py`
10. **VPS deployment**: sync, systemd service, verify alerts end-to-end

---

## Out of Scope

- Puts / short-side entries (calls only for now)
- Automated order execution (alert only, human executes)
- Portfolio-level position tracking
- Exit signal generation (user decides when to exit)
