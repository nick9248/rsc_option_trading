# OTM Contract Finder — Design Spec
**Date:** 2026-03-14
**Branch:** strategies/otm_contracts
**Status:** Draft v4 — multi-persona audit fixes applied

---

## 1. Overview

The OTMContractFinder is a regime-gated, signal-stacked system for identifying and sizing OTM crypto options contracts (BTC and ETH on Deribit) with the highest probability of maximum gain when bought outright.

**Scope:**
- Assets: BTC and ETH only
- Venue: Deribit (primary), with cross-venue signals from Binance (funding), CoinGlass (max pain, P/C), CBOE (IBIT flow, BTC only)
- Holding periods: Short (1–7d), Medium (7–30d), Long (30–90d) — medium and long preferred
- Direction: Long OTM calls and long OTM puts (no spreads, no selling)
- Output: Ranked `OTMSignal` list with full score breakdown, position size, and exit thresholds
- Execution: Staged — scoring/alerts first → backtest/forward test → VPS automation

---

## 2. Architecture

```
strategies/otm_contracts/
├── research/                       # Parameter research docs
├── backtest_results/               # Backtest output CSVs
└── forward_test_log/               # Forward test trade log

coding/core/strategy/otm/
├── signals/
│   ├── liquidity_gate.py           # Gate 1: hard pass/fail filters
│   ├── volatility_regime_gate.py   # Gate 2: is vol worth buying?
│   ├── directional_scorer.py       # Gate 3: which direction, how strong?
│   └── strike_expiry_optimizer.py  # Gate 4: which specific contract?
├── models/
│   ├── otm_signal.py               # OTMSignal Pydantic output model (fields defined in §10)
│   └── otm_config.py               # OTMConfig Pydantic config model (fields defined in §11)
└── scoring/
    └── kelly_sizer.py              # Fractional Kelly within fixed budget

coding/service/strategy/otm/
├── otm_finder_service.py           # Orchestrates all 4 gates, produces ranked signals
└── otm_backtest_service.py         # Backtests signals vs. historical data (interface in §9)
```

**Layer rules (mandatory):**
- Core layer: signal computation, scoring, models — no API calls
- Service layer: orchestration, API calls, data fetching
- No business logic in GUI

**Existing analytics reused (no rebuild needed):**
- GEX/DEX: `coding/core/analytics/gex_dex_calculator.py`
- Funding rate trend: `coding/core/analytics/market_wide_calculator.py` → `calculate_perpetual_funding_trend()`
- 25Δ skew / P/C OI / Vanna / second-order Greeks: `coding/core/analytics/volatility_surface_calculator.py`
- VRP / IV percentile: `coding/core/analytics/vrp_calculator.py`
- IV term structure: `coding/core/analytics/market_wide_calculator.py` → `calculate_iv_term_structure()`
- Block trades: `coding/core/analytics/market_wide_calculator.py` → `detect_block_trades(notional_threshold=500_000)`
- OHLCV (daily candles): existing DB table, collected by `ProspectiveCollector`

**New components needed:**
- GARCH volatility forecast: GJR-GARCH on **daily** OHLCV data (existing DB). Requires `arch` library (add to `requirements.txt`). Minimum 180 daily candles for stability.
- Realized vs. Implied Skew (RIS): simplified implementation — 30d rolling mean of daily 25Δ RR (from existing `volatility_surface_calculator`) vs. current 25Δ RR mark. No trade feed parsing required.
- Stablecoin inflow fetcher: CryptoQuant free API `/v1/stablecoins/exchange-inflow` — if endpoint unavailable on free tier, fallback = neutral score (zero weight, log warning)
- IBIT options P/C fetcher: CBOE delayed data at `https://cdn.cboe.com/api/global/delayed_quotes/options/_IBIT.json` — parse `put_call_ratio` field. BTC only. If unavailable, fallback = neutral score.
- Max pain fetcher: CoinGlass `/api/public/option/max_pain` (free tier). Used only in Gate 4 (tiebreaker), not Gate 3.
- Macro regime overlay (MAC): **deferred to phase 2** — requires Fed stance, DXY, and Nasdaq data pipelines not yet in system. Gate 2 weight redistributed among V1/V2+V4/V3.
- DVOL history: fetch from Deribit `/public/get_index_price_history` for `btc_dvol` and `eth_dvol` indices. Store in new DB table — schema defined in §16. Minimum 36 months for BTC; use all available for ETH. Degraded mode: if available history < 36 months, use all available data and set `dvol_percentile_v1_score = 50` (neutral) if < 90 days available; otherwise compute on reduced window and log a `WARNING: DVOL history degraded ({n} days available, 36m preferred)`.

---

## 3. Data Flow

```
Every 30 minutes (or on-demand trigger):

1. Fetch Deribit options chain (BTC + ETH)
   └── All strikes, all expirations with DTE 1–90 days
   └── Fields per contract: instrument_name, strike, expiry, delta, gamma, vega,
       theta, vanna, mark_iv, bid_iv, ask_iv, bid, ask, open_interest, volume_24h

2. For each OTM contract candidate where abs(delta) is in [0.05, 0.45]:

   ─────────────────────────────────────────────────────
   GATE 1 — Liquidity (hard pass/fail — cheap to compute, run first)
   ─────────────────────────────────────────────────────
   ├── Bid-ask spread:   DUAL threshold — BOTH conditions must pass:
   │     Condition A (relative): (ask_iv − bid_iv) / mid_iv < 0.08
   │       mid_iv = (ask_iv + bid_iv) / 2
   │       Catches wide spreads in high-vol regimes where absolute spreads naturally widen
   │     Condition B (absolute cap): (ask_iv − bid_iv) < 4.0 vol pts
   │       Hard ceiling; prevents entry on illiquid far-wing strikes in any regime
   │     If ask_iv or bid_iv is null → FAIL
   │     Rationale: absolute-only threshold boxes out valid OTM trades in vol expansion;
   │       relative-only threshold allows too-wide spreads on low-IV options
   ├── Volume/OI ratio:  volume_24h / open_interest > 0.05
   ├── Min OI:           open_interest > 50 contracts (BTC) | > 200 contracts (ETH)
   ├── Tx cost floor:    round_trip_fee = 2 × 0.0003 × underlying_price × contract_qty
   │     required: (2 × entry_premium) > round_trip_fee × 5
   │     (uses 2× premium as minimum expected return proxy for Gate 1 filter)
   └── FAIL → discard; log reason

   If no candidates survive Gate 1 for a given asset-direction pair:
   → return empty OTMSignal list for that pair; log warning "No liquid OTM candidates"

   ─────────────────────────────────────────────────────
   GATE 2 — Volatility Regime (score 0–100; asset-agnostic)
   ─────────────────────────────────────────────────────
   ├── V1 (30%):  DVOL Percentile
   │     Formula: percentile_rank(current_dvol, dvol_history_36m)
   │     Primary signal: full score if < 30th percentile of 36m history
   │     Dynamic absolute floor (replaces hardcoded 60/70):
   │       dvol_floor = dvol_36m_median + dvol_36m_std
   │       Full score only if current_dvol < dvol_floor AND < 30th percentile
   │       Rationale: hardcoded floors break across macro vol regimes; dynamic floor
   │         self-calibrates to each regime's level
   │       Fallback if < 36m history available:
   │         ≥ 12m: use available history for percentile; set dvol_floor = dvol_available_median + 1σ
   │         < 12m but ≥ 90d: compute on available data; log WARNING
   │         < 90d: V1 score = 50 (neutral); log WARNING "DVOL history insufficient"
   │
   ├── V2+V4 (40%): Vol Fair Value (merged signal)
   │     Sub-signal A (50%): VRP component
   │       VRP = atm_iv_30d − rv_30d_parkinson
   │       Score: VRP < +5 pts → full weight; VRP < 0 → max score
   │     Sub-signal B (50%): GARCH component
   │       Fit GJR-GARCH(1,1) on last 180 daily log-returns (from OHLCV DB)
   │       GARCH_fcast = 1-day-ahead conditional vol annualized
   │       Score: GARCH_fcast / atm_iv > 1.10 → full weight
   │     V2+V4 combined = (A_score + B_score) / 2
   │
   └── V3 (30%):  IV Term Structure Slope
         Use existing calculate_iv_term_structure() on available expirations
         Slope = back_iv − front_iv (shortest vs. longest available within 90d DTE)
         If only one expiration available: V3 score = 50 (neutral)

         SCORING (for outright naked OTM buying only — this is NOT a spread system):
         Contango (slope > +5 pts): FULL SCORE (100)
           → Back-month vol is cheap relative to front; buy back-month OTM
         Flat (|slope| ≤ 5 pts): score = 50 (neutral)
         Shallow backwardation (slope −5 to −15 pts): score = 25 (suppressed)
           → Front-month vol elevated; entry discouraged but not blocked
         Deep backwardation (slope < −15 pts): score = 0 (block)
           → Front-month vol is extremely expensive; market pricing imminent tail event
           → Buying naked OTM here is negative EV — premium too high to break even
           → Gate 2 total score is materially suppressed; new entries will be blocked

         CRITICAL RATIONALE: Deep backwardation ≠ buy signal for naked OTM.
         It means the market is already pricing a large move — you are buying
         after the premium has spiked. This is the opposite of cheap vol.
         Phase 2 (spreads): backwardation becomes a BUY signal for debit spreads
         (IV elevation on short leg reduces net debit), but that is out of scope for v1.

   Gate 2 output: 0–100 score (integer boundaries are inclusive)
   ├── Score ≥ 40  → new entries allowed; no exit action
   ├── Score 30–39 → no new entries; no exit action on existing positions
   └── Score ≤ 29  → no new entries; mandatory 50% partial exit on ALL open positions for this asset

   GARCH fallback (V2+V4 sub-signal B):
   ├── If ≥ 180 daily candles available: fit GJR-GARCH normally
   ├── If 90–179 candles available: fit GJR-GARCH on available data; log WARNING
   ├── If < 90 candles available: GARCH sub-signal B score = 50 (neutral); log WARNING
   └── V2+V4 still uses 50/50 blend — a neutral GARCH score of 50 leaves VRP sub-signal
       as the sole contributor at full weight for that asset until history accumulates

   ─────────────────────────────────────────────────────
   GATE 3 — Directional Conviction (Call Score + Put Score, each 0–100)
   ─────────────────────────────────────────────────────
   Compute separate call_score and put_score using the signals below.
   Each signal returns a value in [−1.0, +1.0]:
     +1.0 = maximum bullish (call) signal
     −1.0 = maximum bearish (put) signal
      0.0 = neutral
   Weighted sum → normalize to [0, 100] where 100 = maximum conviction.

   BTC weights (sum = 100%):
   ├── D1+D7  Dealer Positioning          22%
   ├── D2     Funding Rate Percentile     15%
   ├── D3     25Δ RR Z-Score              14%
   ├── D4     P/C OI Ratio                11%
   ├── D6+D9  Institutional Flow          14%
   ├── D8     Stablecoin Inflow            8%
   ├── D10    IBIT P/C Flow                9%
   └── RIS    Realized vs. Implied Skew    7%
   Total BTC: 100%

   ETH weights (D10 = 0%; remaining renormalized to sum 100%):
   ├── D1+D7  Dealer Positioning          24%
   ├── D2     Funding Rate Percentile     17%
   ├── D3     25Δ RR Z-Score              15%
   ├── D4     P/C OI Ratio                12%
   ├── D6+D9  Institutional Flow          15%
   ├── D8     Stablecoin Inflow            9%
   ├── D10    IBIT P/C Flow                0%
   └── RIS    Realized vs. Implied Skew    8%
   Total ETH: 100%

   Special rules (applied in this order):
   1. D3+D4 conflict: if D3 and D4 return opposite signs → multiply combined
      D3+D4 contribution by 0.70 (reduce combined weight 30%)
   2. ETH call penalty: if asset=ETH AND direction=call AND delta in [0.25, 0.35]
      → multiply call_score by 0.85 (structural covered-call sell pressure)
   3. Regime adjustment:
      bull_flag = (ema_10d > ema_20d) AND (spot_close > sma_50d) on daily closes from OHLCV DB
      Rationale: 50/200 SMA crossover is too lagging for options with 1–90 DTE —
        by the time it signals, a 50%+ move has happened and vol is expensive.
        Dual filter: fast EMA cross (10/20) for momentum + spot > 50 SMA for trend support.
      Directional signal mapping for regime scaling:
        CALL-directional signals (scaled ×1.30 in bull, ×0.70 in bear):
          D1+D7 (Dealer Positioning), D2 (Funding), D3 (RR Z-Score), D4 (P/C OI),
          D6+D9 (Institutional Flow), D10 (IBIT), RIS (Realized vs Implied Skew)
        NEUTRAL signals (not scaled — provide same value regardless of regime):
          D8 (Stablecoin Inflow) — macro liquidity signal, direction-agnostic
      In bull regime: scale call-directional weights ×1.30 for call_score computation;
                      scale call-directional weights ×0.70 for put_score computation
      In bear regime: reverse (×0.70 call, ×1.30 put)
      After scaling: renormalize all weights to sum to 100% before scoring

   ─────────────────────────────────────────────────────
   GATE 4 — Strike & Expiry Optimizer (applied to survivors of Gates 1–3)
   ─────────────────────────────────────────────────────
   Filter by Gate 3 direction (buy calls if call_score > put_score, else buy puts)
   Then score remaining candidates 0–100 per rule below; select top ranked:

   ├── Delta range filter (hard):
   │     Directional: abs(delta) in [0.20, 0.35]
   │     Event/tail:  abs(delta) in [0.10, 0.20]
   │     ETH calls:   avoid [0.25, 0.35] unless call_score > 80 (structural sell pressure)
   │
   ├── Expiry selection (preference score):
   │     Signal type assignment from Gate 3 context:
   │       Short (DTE 1–7):   use when a known binary event is within 7 days
   │       Medium (DTE 7–30): default for regime trades
   │       Long (DTE 30–90):  use when Gate 2 score > 70 (cheap vol + cycle setup)
   │     Multiple expiry candidates: score by Vega/Theta ratio, select highest
   │
   ├── Vega/Theta ratio: abs(vega) / abs(theta) using raw Deribit greek values ($/1% vol / $/day)
   │     Targets are DTE-dependent (ratio collapses near expiry — flat threshold is wrong):
   │       Short  (DTE 1–7):   prefer > 0.05  (theta dominates; vega/theta naturally low)
   │       Medium (DTE 7–30):  prefer > 0.30  (balanced decay vs. vol sensitivity)
   │       Long   (DTE 30–90): prefer > 0.80  (vega should dominate; cheap carry cost)
   │
   ├── Gamma/Premium ratio: gamma / entry_premium (per contract)
   │     For short-dated (DTE ≤ 7): select highest Gamma/Premium among delta-filtered candidates
   │
   ├── Breakeven distance: strike ± premium ≤ spot × (1 + 2 × GARCH_fcast_30d)
   │     If breakeven exceeds 2× expected move → score penalized by 50%
   │
   └── Max Pain tiebreaker: when two candidates score within 5 points of each other
         prefer the strike closer to max_pain_strike (from CoinGlass)

   ─────────────────────────────────────────────────────
   KELLY SIZER
   ─────────────────────────────────────────────────────
   conviction_score = (gate2_score × 0.50) + (gate3_directional_score × 0.50)
   gate3_directional_score = call_score if buying calls; put_score if buying puts

   P_win lookup (pre-calibration priors; replaced post-backtesting via OTMConfig):
     conviction 40–60 → P_win = 0.35, avg_return_multiple = 1.5
     conviction 60–75 → P_win = 0.40, avg_return_multiple = 2.0
     conviction 75–90 → P_win = 0.45, avg_return_multiple = 2.5
     conviction > 90  → P_win = 0.50, avg_return_multiple = 3.0

   CRITICAL: avg_return_multiple capped at 3.0× pre-calibration.
   The previous 8× prior would catastrophically oversize positions — options rarely
   sustain 8× average returns; assuming it causes Kelly to allocate as if you have
   a near-certain 8:1 bet. Cap at 3.0× until backtesting proves actual return distribution.
   These conservative priors will undersize positions — that is intentional.

   kelly_fraction = (P_win × avg_return_multiple − (1 − P_win)) / avg_return_multiple
   fractional_kelly = kelly_fraction × 0.25        # 1/4 Kelly — prevents ruin on binary outcomes
   position_usd = min(fractional_kelly × risk_budget_usd, risk_budget_usd × 0.10)

   Portfolio correlation cap (enforced PRE-TRADE, before order placement):
     If any open position already exists in the same direction:
       combined_allocated = sum(position_usd for all open same-direction positions)
       remaining_cap = (risk_budget_usd × 0.10) − combined_allocated
       new_position_usd = min(new_position_usd, max(remaining_cap, 0))
       If remaining_cap ≤ 0: skip this trade; log "portfolio correlation cap reached"

4. Output: list of OTMSignal (see §10), sorted descending by conviction_score
```

---

## 4. Signal Definitions — Gate 2

| # | Parameter | Formula | Source | Buy Signal | Weight | Confidence |
|---|---|---|---|---|---|---|
| V1 | DVOL Percentile | `percentile_rank(dvol_now, dvol_36m_history)` + dynamic floor = (36m median + 1σ) | New: Deribit `/public/get_index_price_history`, `dvol_history` table | < 30th pctile AND dvol < dynamic floor | 30% | High |
| V2+V4 | Vol Fair Value | 50% × VRP_score + 50% × GARCH_score | VRP: `vrp_calculator.py`; GARCH: GJR-GARCH on daily OHLCV | VRP < +5 pts AND GARCH/IV > 1.10 | 40% | High |
| V3 | Term Structure | `calculate_iv_term_structure()` back_iv − front_iv | `market_wide_calculator.py` | **Contango > +5 pts ONLY** (full score). Flat = 50. Shallow backwardation = 25. **Deep backwardation < −15 pts = 0 (block)** — front-month vol too expensive for naked OTM | 30% | High |
| MAC | Macro Overlay | Fed + DXY + BTC/Nasdaq | External — not yet available | — | **Deferred to phase 2** | Medium-High |

*Gate 2 total: 100% (V1 + V2+V4 + V3)*

---

## 5. Signal Definitions — Gate 3

| # | Parameter | Formula | Source | Bullish (+1) | Bearish (−1) | BTC Wt | ETH Wt | Conf |
|---|---|---|---|---|---|---|---|---|
| D1+D7 | Dealer Positioning | GEX sign near spot + vanna sign post-IV change | `gex_dex_calculator.py` + `_calculate_second_order_greeks()` | Net GEX negative below spot + vanna unwind → up | Net GEX positive above spot | 22% | 24% | High |
| D2 | Funding Rate Pctile | `percentile_rank(funding_now, funding_12m_history)` + trend direction | `market_wide_calculator.calculate_perpetual_funding_trend()` | **Bullish (calls):** < 10th pctile AND spot not making new 30d low (peak short crowding = squeeze setup). **Also bullish:** > 90th pctile AND spot making higher highs AND no bearish divergence (trend continuation in bull run — do NOT fade high funding in momentum regime). **Bearish (puts):** > 90th pctile AND spot making lower highs on same timeframe (bearish divergence only). | 15% | 17% | High |
| D3 | 25Δ RR Z-Score | z = (rr25_now − rr25_30d_mean) / rr25_30d_std | `volatility_surface_calculator._calculate_25_delta_skew()` | z < −1.5 = calls cheap vs. puts | z > +1.5 = puts cheap | 14% | 15% | High |
| D4 | P/C OI Ratio Pctile | `percentile_rank(pc_ratio_now, pc_ratio_12m_history)` — computed per asset | `volatility_surface_calculator._calculate_pc_by_moneyness()` | > 70th pctile (12m, asset-specific) | < 30th pctile | 11% | 12% | High |
| D6+D9 | Institutional Flow | Block trades (≥ $500K OTM premium) + DEX sign change (sign flip between current and prior run) | `detect_block_trades(notional_threshold=500_000)` + `gex_dex_calculator.py` | OTM call blocks + positive DEX flip | OTM put blocks + negative DEX flip | 14% | 15% | Med-High |
| D8 | Stablecoin Inflow | (inflow_3d / total_stablecoin_supply) × 100 | CryptoQuant free API; fallback = neutral (0) | > 0.5% of supply in 3d | < −0.5% (outflow) | 8% | 9% | Medium |
| D10 | IBIT P/C Flow | `put_call_ratio` from CBOE IBIT JSON; compare vs. 30d avg | `https://cdn.cboe.com/api/global/delayed_quotes/options/_IBIT.json` — BTC only; fallback = neutral (0) | Below 30d avg = call demand | Above 30d avg = put demand | 9% | 0% | High (BTC) |
| RIS | Realized vs. Implied Skew | `rr25_30d_rolling_mean − rr25_current_mark` (positive = calls structurally cheap vs. recent history) | Derived from `_calculate_25_delta_skew()` stored 30d history in DB | Divergence > +2 vol pts = calls cheap | Divergence < −2 vol pts = puts cheap | 7% | 8% | Med-High |

*BTC total: 22+15+14+11+14+8+9+7 = 100%. ETH total: 24+17+15+12+15+9+0+8 = 100%.*

---

## 6. Kelly Sizer — P_win Priors

Pre-calibration priors (replaced after backtesting by updating `OTMConfig`):

| Conviction Score | P_win | Avg Return Multiple (capped 3×) | Max Position (% of budget) |
|---|---|---|---|
| 40–60 | 0.35 | 1.5× | ~1.0% |
| 60–75 | 0.40 | 2.0× | ~2.5% |
| 75–90 | 0.45 | 2.5× | ~3.5% |
| > 90  | 0.50 | 3.0× | ~4.5% |

**All priors deliberately conservative pre-backtesting. Capped at 3.0× to prevent Kelly oversizing.**
Replaced with empirical values from backtesting. All thresholds in `OTMConfig`.

---

## 7. Exit Logic

All exit parameters are stored in `OTMConfig` (configurable without code changes).

```
TAKE PROFIT (set at entry, based on conviction_score at time of trade):
  conviction 40–60  → exit at 2× entry_premium
  conviction 60–75  → exit at 3× (medium-dated DTE 7–30) | 4× (long-dated DTE 30–90)
  conviction 75+    → exit at 5× (medium-dated) | 8× (long-dated)
  Short-dated (DTE 1–7): always exit at 2× regardless of conviction

VEGA WINDFALL RULE:
  If position value ≥ 2× AND IV_current − IV_entry > 15 vol pts
  AND |spot_pct_change_since_entry| < 0.01 (spot moved < 1%)
  → take 75% profit immediately; hold 25% for directional follow-through

SIGNAL REVERSAL STOP (real-time only — D2, D3, D6+D9 recalculated each 30-min cycle):
  Compute real_time_gate3_subscore using D2, D3, D6+D9 weights only, renormalized
  If (entry_real_time_subscore − current_real_time_subscore) > 30 → full exit
  Note: D4, D8, D10 are daily signals — excluded from intraday reversal computation

TIME / LOSS STOP (theta-adjusted — rigid % stops fire from normal decay):
  At entry: compute expected_theta_loss_at_50pct_dte = sum of daily theta × (DTE / 2)
  At any point:
    thesis_stop_price = min(entry_spot × 0.85 for calls, entry_spot × 1.15 for puts)
    If spot breaches thesis_stop_price → full exit (thesis invalidated, not just decay)
    If real_time_gate3_subscore flips fully negative → full exit (signal reversal)
  At 50% of DTE elapsed:
    excess_loss = unrealized_loss − expected_theta_loss_at_50pct_dte
    If excess_loss > 20% of entry_premium → reduce to 50% size (loss beyond theta = thesis failing)
    If excess_loss > 40% of entry_premium → full exit
  Hard floor (prevent total bleed):
    If unrealized_loss > 70% of entry_premium at any point → full exit regardless of DTE
  Rationale: A rigid 50% premium stop fires routinely from normal theta decay on 14-DTE
    options; theta-adjusted stop only triggers when losses exceed the expected decay path.

EXPIRY RULE (checked every 30-min cycle):
  Never hold through expiry unless ALL of:
    - (strike − spot) / spot < −0.10 (option > 10% ITM for calls; adjust for puts)
    - DTE ≤ 5 days
    - real_time_gate3_subscore > 0

LIQUIDITY EXIT (limit-chase — NEVER use market orders on OTM crypto options):
  If (exit_spread > 3 × entry_spread):
    Activate limit-chase algorithm:
      Step 1: Place limit sell at bid_iv (current best bid)
      Step 2: If no fill within 10 seconds: reprice to (bid_iv − 0.5 vol pts); re-submit
      Step 3: Repeat every 10 seconds, conceding 0.5 vol pts per cycle
      Step 4: After 8 repricing cycles (80 seconds): hold position and trigger admin alert
        → Log "LIQUIDITY_ALERT: unable to exit {instrument_name} after 8 reprice attempts"
        → Do NOT send market order under any circumstances
        → Position remains open; alert operator for manual decision
  Rationale: OTM crypto option order books can have near-zero bids. A market order
    sweeps the book and market makers fill you at 90% discount to fair value.
    Holding the position with an alert is always preferable to a catastrophic market fill.

GATE 2 POSITION MONITOR (checked every 30-min cycle, for all open positions):
  Gate 2 score 40+:  no action
  Gate 2 score 30–40: no new entries; no exit action on existing positions
  Gate 2 score < 30:  mandatory 50% partial exit on all open positions for this asset
```

---

## 8. Data Sources

| Source | Data Provided | Endpoint / Method | Cost | Update Freq | Fallback |
|---|---|---|---|---|---|
| Deribit API | Options chain, greeks, OI, IV, OHLCV, trade feed | Existing service | Free | Real-time | N/A |
| Deribit `/get_index_price_history` | DVOL historical index | New fetcher, store in `dvol_history` table | Free | Daily backfill | Use available history |
| Binance REST | Perpetual funding rate | Existing via `market_wide_calculator` | Free | 8h | Skip D2, neutral score |
| CoinGlass API | Max pain by asset/expiry | `/api/public/option/max_pain` | Free tier | Daily | Skip Gate 4 tiebreaker |
| CryptoQuant | Stablecoin exchange inflows | `/v1/stablecoins/exchange-inflow` | Free tier | Daily | D8 = neutral (0), log warning |
| CBOE | IBIT options P/C ratio (BTC only) | `https://cdn.cboe.com/api/global/delayed_quotes/options/_IBIT.json` | Free (public) | Daily | D10 = neutral (0), log warning |
| Internal DB | OHLCV daily candles | `ohlcv_history` table (ProspectiveCollector) | — | 30-min updates | — |
| Computed | GARCH forecast, RIS, SMA regime, VRP | Derived from above | — | Each cycle | Degrade gracefully |

---

## 9. Backtest Service Interface

```python
class OTMBacktestService:
    def run_backtest(
        self,
        asset: str,                  # "BTC" or "ETH"
        start_date: datetime,
        end_date: datetime,
        config: OTMConfig,
    ) -> BacktestResult:
        """
        Reconstruct all signals historically, label trade outcomes,
        and return calibrated P_win values per conviction band.
        """

    def label_outcomes(
        self,
        signals: List[OTMSignal],
        price_history: Dict[str, List[Candle]],
    ) -> List[LabeledTrade]:
        """
        For each historical signal, determine:
        - outcome: "take_profit" | "stop_loss" | "time_stop" | "expired_worthless"
        - return_multiple: float (e.g., 3.0 = exited at 3× premium)
        - holding_period_days: int
        """

    def calibrate_conviction_bands(
        self,
        labeled_trades: List[LabeledTrade],
    ) -> Dict[str, float]:
        """
        Return empirical P_win per conviction band.
        Output replaces prior values in OTMConfig.
        """
```

---

## 10. OTMSignal Model (Pydantic)

```python
class OTMSignal(BaseModel):
    # Identity
    signal_id: str                    # UUID
    generated_at: datetime
    asset: str                        # "BTC" | "ETH"
    instrument_name: str              # e.g., "BTC-28MAR25-95000-C"
    direction: str                    # "call" | "put"
    strike: float
    expiry: str                       # "28MAR25"
    dte: int                          # days to expiry at signal time

    # Contract metrics at signal time
    delta: float
    gamma: float
    vega: float
    theta: float
    mark_iv: float
    entry_premium: float              # USD per contract
    underlying_price: float

    # Gate scores
    gate1_passed: bool
    gate2_score: float                # 0–100
    gate3_call_score: float           # 0–100
    gate3_put_score: float            # 0–100
    gate3_directional_score: float    # whichever direction is selected
    conviction_score: float           # 0–100

    # Gate 3 signal breakdown
    d1_d7_score: float                # Dealer Positioning
    d2_score: float                   # Funding Rate
    d3_score: float                   # RR Z-Score
    d4_score: float                   # P/C OI
    d6_d9_score: float                # Institutional Flow
    d8_score: float                   # Stablecoin Inflow
    d10_score: float                  # IBIT P/C (BTC only; 0.0 for ETH)
    ris_score: float                  # Realized vs Implied Skew

    # Sizing
    position_usd: float               # USD size from Kelly sizer
    p_win_prior: float                # P_win used for sizing
    kelly_fraction: float

    # Exit thresholds (set at entry)
    take_profit_multiple: float       # e.g., 3.0 = exit at 3× premium
    stop_loss_pct: float              # 0.50 = exit if loss > 50% of premium
    time_stop_dte: int                # DTE at which time stop activates

    # Gate 4 selection rationale
    vega_theta_ratio: float
    gamma_premium_ratio: float
    breakeven_price: float
    expiry_category: str              # "short" | "medium" | "long"

    # Regime context
    regime_flag: str                  # "bull" | "bear" | "neutral"
    gate2_suppressed: bool            # True if Gate 2 < 40 at time of signal
```

---

## 11. OTMConfig Model (Pydantic)

```python
class OTMConfig(BaseModel):
    # Budget
    risk_budget_usd: float            # Total USD budget allocated to OTM trading
    max_single_trade_pct: float = 0.10  # Max 10% of budget per trade
    max_correlated_pct: float = 0.10  # BTC + ETH same direction = treated as one; max 10%

    # Gate 1 thresholds (dual spread: relative AND absolute)
    max_bid_ask_spread_relative: float = 0.08   # (ask_iv - bid_iv) / mid_iv < 8%
    max_bid_ask_spread_absolute: float = 4.0    # hard cap in vol pts regardless of regime
    min_volume_oi_ratio: float = 0.05
    min_oi_btc: int = 50
    min_oi_eth: int = 200
    tx_cost_floor_multiplier: float = 5.0

    # Gate 2 thresholds
    gate2_suppress_threshold: float = 40.0
    gate2_position_exit_threshold: float = 30.0
    dvol_percentile_threshold: float = 30.0
    dvol_lookback_months: int = 36
    # Dynamic floor: dvol_floor = rolling_median + rolling_std (no hardcoded absolute)
    dvol_floor_std_multiplier: float = 1.0       # floor = median + (1 × std)
    vrp_cheap_threshold: float = 5.0             # VRP < this = cheap vol
    garch_iv_ratio_threshold: float = 1.10       # GARCH/IV > this = model says more vol
    term_structure_contango_threshold: float = 5.0    # slope > this = contango (buy back-month)
    term_structure_shallow_back_threshold: float = -5.0   # slope -5 to -15 = suppressed
    term_structure_deep_back_threshold: float = -15.0     # slope < this = block (deep backwardation)

    # Gate 3 thresholds
    rr_z_score_threshold: float = 1.5
    pc_ratio_percentile_bull: float = 70.0
    pc_ratio_percentile_bear: float = 30.0
    funding_percentile_bull: float = 10.0
    funding_percentile_bear: float = 90.0
    block_trade_min_premium: float = 500_000.0
    stablecoin_inflow_threshold_pct: float = 0.5
    ris_divergence_threshold: float = 2.0        # vol pts

    # Gate 3 regime (fast EMA dual filter — replaces slow 50/200 SMA)
    ema_fast: int = 10
    ema_slow: int = 20
    trend_sma: int = 50                           # spot > this = trend support
    regime_call_multiplier: float = 1.30
    regime_put_multiplier: float = 0.70

    # Gate 4 thresholds
    min_delta_directional: float = 0.20
    max_delta_directional: float = 0.35
    min_delta_event: float = 0.10
    max_delta_event: float = 0.20
    # Vega/Theta targets per DTE category (ratio collapses near expiry)
    vega_theta_short: float = 0.05               # DTE 1–7
    vega_theta_medium: float = 0.30              # DTE 7–30
    vega_theta_long: float = 0.80                # DTE 30–90
    max_breakeven_move_multiplier: float = 2.0

    # Kelly / sizing (conservative priors — capped at 3.0× until backtesting)
    kelly_divisor: float = 4.0                   # 1/4 Kelly
    p_win_priors: Dict[str, float] = {
        "40_60": 0.35,
        "60_75": 0.40,
        "75_90": 0.45,
        "90_100": 0.50,
    }
    avg_return_priors: Dict[str, float] = {      # CAPPED at 3.0× pre-calibration
        "40_60": 1.5,
        "60_75": 2.0,
        "75_90": 2.5,
        "90_100": 3.0,
    }

    # Exit thresholds (theta-adjusted stop)
    stop_loss_hard_floor_pct: float = 0.70       # never let loss exceed 70% of premium
    theta_excess_loss_reduce_pct: float = 0.20   # at 50% DTE: reduce if excess > 20%
    theta_excess_loss_full_exit_pct: float = 0.40 # at 50% DTE: full exit if excess > 40%
    thesis_stop_call_pct: float = 0.85           # full exit if spot drops to 85% of entry
    thesis_stop_put_pct: float = 1.15            # full exit if spot rises to 115% of entry
    vega_windfall_iv_spike_threshold: float = 15.0
    vega_windfall_spot_move_max: float = 0.01
    vega_windfall_profit_threshold: float = 2.0
    # Liquidity exit — limit-chase only (no market orders)
    liquidity_exit_spread_multiplier: float = 3.0
    liquidity_exit_reprice_interval_sec: int = 10
    liquidity_exit_reprice_concession_vol_pts: float = 0.5
    liquidity_exit_max_reprice_cycles: int = 8   # 80 seconds then alert; NO market order
    itm_threshold_for_hold: float = 0.10
    hold_through_expiry_max_dte: int = 5
```

---

## 12. Multi-Persona Audit Plan (post-spec-fix, pre-implementation)

Dispatch three parallel targeted audits on the revised spec:

| Persona | Domain | Scope |
|---|---|---|
| Quant/Structurer (JP Morgan style) | Statistical validity | Signal independence, Kelly math correctness, threshold overfitting, GARCH specification |
| Crypto-Native Market Maker (Wintermute/GSR style) | Execution realities | Deribit spread dynamics, GEX assumptions in crypto, liquidity exit feasibility |
| Risk Manager (Bank CRO style) | Tail risk and drawdown | Exit logic completeness, correlated position scenarios, budget edge cases |

Each persona scoped to their domain only. Findings synthesized into a final revision before implementation begins.

---

## 13. Quality Standards

All implementation follows Bull Call Spread reference (99/100):
- Pydantic for all config and output models (`OTMConfig`, `OTMSignal`)
- Minimum 20 unit tests per component; 30+ edge case tests
- All signal formulas manually verified against real Deribit data before commit
- Complete docstrings on all public methods
- `logging` module only — no `print()` statements
- Layered architecture enforced: no API calls in core, no business logic in GUI

---

## 14. Dependencies to Add

```
arch>=6.0.0           # GJR-GARCH implementation
requests>=2.31.0      # already present, used for CryptoQuant/CBOE fetchers
```

---

## 15. Future Extensions (out of scope for v1)

- Macro regime overlay (MAC) — deferred: Fed stance, DXY trend, BTC/Nasdaq correlation
- ML signal ranker — phase 2: XGBoost trained on labeled backtest outcomes
- Additional assets: SOL, XRP (after BTC/ETH calibration proven)
- GUI tab: OTM finder integrated into strategy tab
- Spread strategies: OTM call + spread combinations

---

## 16. New DB Schema

### Table: `dvol_history`

```sql
CREATE TABLE dvol_history (
    id          SERIAL PRIMARY KEY,
    asset       VARCHAR(10)   NOT NULL,          -- 'BTC' | 'ETH'
    timestamp   TIMESTAMPTZ   NOT NULL,
    dvol_value  DECIMAL(8,4)  NOT NULL,           -- e.g., 52.3400
    UNIQUE (asset, timestamp)
);
CREATE INDEX idx_dvol_history_asset_ts ON dvol_history (asset, timestamp DESC);
```

Populated by a new backfill script (`scripts/backfill_dvol_history.py`) and then by ProspectiveCollector on each cycle.

### Table: `otm_signals`

```sql
CREATE TABLE otm_signals (
    id                      SERIAL PRIMARY KEY,
    signal_id               UUID          NOT NULL UNIQUE,
    generated_at            TIMESTAMPTZ   NOT NULL,
    asset                   VARCHAR(10)   NOT NULL,
    instrument_name         VARCHAR(50)   NOT NULL,
    direction               VARCHAR(4)    NOT NULL,   -- 'call' | 'put'
    strike                  DECIMAL(14,2) NOT NULL,
    expiry                  VARCHAR(10)   NOT NULL,
    dte                     INTEGER       NOT NULL,
    delta                   DECIMAL(8,6)  NOT NULL,
    mark_iv                 DECIMAL(8,4),
    entry_premium           DECIMAL(12,4),
    underlying_price        DECIMAL(14,2),
    gate2_score             DECIMAL(6,2),
    gate3_call_score        DECIMAL(6,2),
    gate3_put_score         DECIMAL(6,2),
    conviction_score        DECIMAL(6,2),
    position_usd            DECIMAL(12,2),
    take_profit_multiple    DECIMAL(6,2),
    expiry_category         VARCHAR(10),            -- 'short'|'medium'|'long'
    regime_flag             VARCHAR(10),            -- 'bull'|'bear'|'neutral'
    signal_breakdown        JSONB,                   -- all sub-signal scores
    exit_params             JSONB                    -- all exit thresholds at entry
);
CREATE INDEX idx_otm_signals_asset_ts ON otm_signals (asset, generated_at DESC);
```
