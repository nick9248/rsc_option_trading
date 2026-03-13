# Market Regime Detection — Redesign Spec

**Date**: 2026-03-13
**Branch**: `quality-check`
**Scope**: Targeted redesign of scoring functions, bug fixes, and new signals. Architecture, GUI, database schema, and service layer structure are unchanged.

---

## 1. Background and Motivation

The current market regime detection system (`market_regime_detector.py`) has three critical bugs that silently break the core logic, plus seven design flaws that reduce signal quality. The net effect is that regime output is unreliable even when the underlying data is good.

### Critical Bugs

| Bug | Location | Effect |
|---|---|---|
| ADX multiplier defined but never applied for ADX > 20 | `_score_trend_component` L183–198 | Trend scores ignore trend strength entirely |
| Funding rate divided by 100 twice (unit mismatch) | `_get_onchain_metrics` L230 + detector thresholds | Funding rate always in neutral zone |
| MACD histogram 100% correlated with MACD line | `_score_momentum_component` L271–281 | MACD contributes ±50 binary with zero nuance |

### Design Flaws

1. Volatility component can only be 0 or negative (structural downward bias on composite)
2. P/C ratio interpretation borrowed from equity markets — direction is backwards for crypto
3. Confidence formula ignores disagreement between components
4. BTC dominance applied with same sign to both BTC and ETH analysis
5. Plus DI / Minus DI are calculated but completely ignored (only ADX strength used, not direction)
6. Regime thresholds asymmetric — Sideways spans -15 to +30, not centered on 0
7. No rate-of-change signals anywhere — only current state measured

---

## 2. Architecture

### What Changes

| File | Change |
|---|---|
| `coding/core/analytics/market_regime_detector.py` | Rewrite all 5 scoring functions, confidence formula, regime thresholds, ADX override |
| `coding/core/analytics/technical_indicator_calculator.py` | Add `get_velocity_indicators(df, lookback=5)` method |
| `coding/service/regime/regime_detection_service.py` | Expand `_get_onchain_metrics()` to compute wings skew, DVOL percentile, DVOL term structure ratio, VRP signal, total OI, OI direction, fix funding units; update external metrics call to include 7-day F&G history |

### What Does Not Change

- Service class structure and public API (`detect_regime()`)
- Database schema and persistence methods
- GUI layer (`regime_tab.py`)
- All other tabs and their services
- `external_apis.py` class structure (only call the existing `get_historical` method)

### New Weights

```
Trend:      30%  (unchanged — necessary lagging anchor)
On-Chain:   25%  (+5 — crypto's informational edge over pure technical)
Momentum:   20%  (-5 — was overcounted due to RSI/MACD daily correlation)
Volatility: 15%  (+5 — DVOL-based vol regime is genuinely leading)
Sentiment:  10%  (-10 — too noisy and lagging to justify higher weight)
```

### Sub-signal Weight Labels

Throughout this spec, sub-signals within each component are labelled with a percentage (e.g., "50% of momentum score"). These are **documentary labels** indicating intended relative importance. They are NOT enforced by a formula. All sub-signal bucket scores are added directly. The combined score is clamped to [−100, +100]. The labels help reason about relative contribution and guide future calibration.

---

## 3. Component Designs

### 3.1 Trend Component (30%)

**Inputs**: `sma_50`, `sma_200`, `adx`, `plus_di`, `minus_di`, `current_price`, `ema_50_velocity` (from `get_velocity_indicators`)

```
Step 1 — Directional Signal (uses DI+/DI- for direction)
  DI+ − DI- spread:
  > +15     →  +35
  +5 to +15 →  +20
  -5 to +5  →    0  (no directional conviction)
  -15 to -5 →  -20
  < -15     →  -35
  Missing   →   0, skip to Step 2

Step 2 — MA Structure (4 states capturing trend + pullback context)
  price > SMA50 > SMA200             →  +20  (clean uptrend)
  SMA50 > SMA200 AND price < SMA50   →  +10  (pullback in uptrend)
  price < SMA50 < SMA200             →  -20  (clean downtrend)
  SMA50 < SMA200 AND price > SMA50   →  -10  (bounce in downtrend)
  Missing MAs                        →   0

Step 3 — ADX Strength Multiplier (applied to Steps 1+2 sum)
  ADX > 40:    × 1.4
  ADX > 25:    × 1.0
  ADX 20–25:   × 0.6
  ADX < 20:    × 0.3
  ADX missing: × 0.7

Step 4 — EMA50 Velocity (added after multiplier — not scaled by ADX, intentional)
  Defined as: (EMA50_today − EMA50_yesterday) / EMA50_yesterday × 100
  i.e., 1-day percentage change of EMA50 value itself.
  > +0.2%  →  +10  (EMA50 accelerating upward)
  < -0.2%  →  -10  (EMA50 accelerating downward)
  Otherwise →   0
  Missing   →   0

  Note: EMA velocity is intentionally not scaled by the ADX multiplier.
  It captures momentum irrespective of trend strength. In a choppy market
  (ADX < 20), EMA velocity provides a weak additional signal the multiplier
  would otherwise suppress entirely.

Final: clamp to [−100, +100]
```

---

### 3.2 Volatility Component (15%)

**Inputs**: `dvol` (current, already fetched), `dvol_history` (30-day hourly, from existing DVOL fetch), `vrp_signal` (computed in service using `VRPCalculator` + OHLCV)

**Key notes**:
- Deribit's `mark_iv` is a percentage value (e.g., 80.0 means 80% IV). All IV thresholds in this spec use percentage units.
- DVOL is the Deribit Volatility Index — already fetched by `_get_onchain_metrics()` at 1h resolution.

```
Sub-signal 1 — DVOL Percentile, 30-day rolling (approx. 50% of vol score)
  Rank: where does current DVOL sit among the last 720 hourly DVOL values (30 days)?
  Calculation: len([v for v in dvol_history_720 if v < current_dvol]) / len(dvol_history_720) * 100

  < 20th pct:  +40  (compressed vol → regime precursor, historically bullish in crypto)
  20–40th:     +20  (below avg, calm environment)
  40–60th:       0  (average)
  60–80th:     -20  (elevated, uncertainty rising)
  > 80th:      -40  (extreme vol → fear premium being paid)

Sub-signal 2 — DVOL Term Structure Ratio (approx. 30% of vol score)
  current_DVOL  = latest single DVOL value (most recent hourly close)
  dvol_30d_avg  = mean of all available hourly DVOL values in the fetched history
  ratio         = current_DVOL / dvol_30d_avg

  < 0.80:       +20  (contango — near vol cheap relative to recent avg, calm)
  0.80–0.95:    +10
  0.95–1.10:      0  (flat)
  1.10–1.25:    -15  (mild backwardation — near-term fear premium)
  > 1.25:       -25  (steep backwardation — crisis premium being priced)

Sub-signal 3 — VRP Signal (approx. 20% of vol score)
  Use: `VRPCalculator.calculate_vrp(implied_vol=dvol/100, realized_vol=rv_30d)`
  The field `vrp_percentage` = (IV − RV) / RV × 100 from the existing calculator.
  Do NOT use `_interpret_vrp()` output — apply thresholds below directly.

  VRP > +20%:   -20  (options very expensive → hedgers paying up → fear)
  +5 to +20%:   -10
  -5 to +5%:      0  (fair pricing)
  -20 to -5%:   +10  (cheap options → complacency → bullish regime)
  < -20%:       +20  (extreme complacency → pre-run environment)

Final: clamp to [−100, +100]
```

---

### 3.3 Momentum Component (20%)

**Inputs**: `rsi`, `macd`, `macd_signal`, `macd_histogram`, `rsi_velocity`, `macd_histogram_velocity` (last two from `get_velocity_indicators`)

```
Sub-signal 1 — RSI (approx. 50% of momentum score)

  Level scoring:
  RSI > 70:    +25  (overbought — bullish but marginally lower than peak momentum range;
                      asymmetric: overbought can persist in trending markets, but at
                      lower score than 60–70 to reflect elevated reversal risk)
  RSI 60–70:   +35  (strongest bullish momentum range)
  RSI 50–60:   +15  (mild bullish)
  RSI 40–50:   -10  (mild bearish)
  RSI 30–40:   -30  (bearish)
  RSI < 30:    -15  (oversold — intentionally less bearish than 30–40;
                      oversold = potential bounce = contrarian signal;
                      asymmetric to the bullish side by design)

  RSI Velocity (from get_velocity_indicators(df, lookback=5)):
  Defined as: RSI value at df.iloc[-1] minus RSI value at df.iloc[-6] (5-bar lookback).
  i.e., rsi_velocity = rsi_today − rsi_5days_ago  (absolute RSI point change)
  Data source: same RSI series in the indicators DataFrame, row [-1] vs row [-6].
  > +8 pts:   +10  (momentum accelerating bullish)
  < -8 pts:   -10  (momentum decelerating)
  Otherwise:    0

Sub-signal 2 — MACD (approx. 50% of momentum score — fully independent sub-signals)

  Crossover signal:
  MACD > macd_signal: +25
  MACD ≤ macd_signal: -25

  Histogram Magnitude Velocity (genuinely independent of crossover direction):
  Defined as: abs(macd_histogram_today) vs abs(macd_histogram_yesterday)
  abs(hist_today) > abs(hist_yesterday): +15  (momentum building in current direction)
  abs(hist_today) < abs(hist_yesterday): -15  (momentum fading)
  abs(hist_today) = abs(hist_yesterday):   0  (flat)
  Missing yesterday's histogram:           0

  These two MACD sub-signals are genuinely independent: you can have
  MACD above signal (+25) with histogram magnitude shrinking (-15) = net +10.
  Previously, these were 100% correlated (both fired identically).

Final: clamp to [−100, +100]
```

---

### 3.4 On-Chain Component (25%)

**Inputs**: `wings_skew` (new, from book_summary), `funding_rate` (fixed units), `oi_direction` (new, from book_summary + DB)

**Funding rate unit fix**: Remove the `/100` division in `_get_onchain_metrics()`. Store `funding_rate = funding_8h` (raw percentage from Deribit, e.g., 0.01 means 0.01% per 8h). Deribit's `funding_8h` field is already in percentage terms.

```
Sub-signal 1 — Wings Skew (approx. 40% of on-chain score) — NEW
  Computed from book_summary (already fetched).
  Filter: nearest expiry with DTE 7–45 days (same filter as edge case table).
  mark_iv values are Deribit percentages (e.g., 80.0 = 80% IV).

  otm_put_iv  = mean mark_iv of ALL puts with strike in range [spot × 0.90, spot × 0.97]
                for the selected expiry only
  otm_call_iv = mean mark_iv of ALL calls with strike in range [spot × 1.03, spot × 1.10]
                for the selected expiry only
  wings_skew  = otm_put_iv − otm_call_iv   (percentage points)

  Positive wings_skew = puts more expensive = institutional hedging/fear = bearish signal
  Negative wings_skew = calls more expensive = upside positioning = bullish signal

  > +10 pp:       -40  (strong fear premium; puts bid hard)
  +5 to +10 pp:   -20
  -5 to +5 pp:      0  (balanced skew — normal market)
  -10 to -5 pp:   +20
  < -10 pp:       +40  (calls expensive; market positioned for upside)

Sub-signal 2 — Funding Rate (approx. 40% of on-chain score) — UNITS FIXED
  funding_rate stored as raw percentage (0.01 = 0.01% per 8h).
  Typical Deribit range: -0.05% to +0.05% per 8h.

  > +0.05%:         +30  (very bullish — longs paying heavy premium)
  +0.02 to +0.05%:  +20
  -0.02 to +0.02%:    0  (neutral zone — normal market conditions)
  -0.05 to -0.02%:  -20
  < -0.05%:         -30

Sub-signal 3 — OI Direction (approx. 20% of on-chain score) — NEW
  total_oi_current = sum of open_interest across all instruments in book_summary
  total_oi_previous = from last regime detection record in DB where detected_at
                      is >= 4 hours before current detection.
  If no qualifying previous record exists: sub-signal = 0 (skip).

  price_up   = current_price > price_at_last_detection
  oi_rising  = total_oi_current > total_oi_previous × 1.02  (+2% threshold)
  oi_falling = total_oi_current < total_oi_previous × 0.98  (-2% threshold)

  price_up   AND oi_rising:   +20  (confirmed new long entries)
  price_up   AND oi_falling:  +10  (short covering — weaker confirmation)
  price_down AND oi_rising:   -20  (confirmed new short entries)
  price_down AND oi_falling:  -10  (long liquidation — weaker signal)
  Otherwise (flat OI, or <4h since last detection): 0

Final: clamp to [−100, +100]
```

---

### 3.5 Sentiment Component (10%)

**Inputs**: `fear_greed_7d` (7-day mean from `get_historical(limit=7)`), `btc_dominance`, `market_cap_change_24h` (already in `external_metrics`, currently unused), `currency`, `trend_score` (passed from trend component)

```
Sub-signal 1 — Fear & Greed 7-day Mean (approx. 60% of sentiment score)
  fear_greed_avg = mean of [entry["value"] for entry in get_historical(limit=7)]
  (Use available days if API returns fewer than 7.)

  "Strong downtrend" condition for context-aware scoring:
  in_strong_trend = adx is not None and adx > 25
  trend_is_bearish = trend_score < -30    ← explicit threshold

  Scoring (half-open intervals: lower bound inclusive, upper bound exclusive):
  fear_greed_avg <  25  → extreme fear:
      if in_strong_trend and trend_is_bearish: score += -15
      else:                                    score += +25
  25 ≤ fear_greed_avg <  45  → score += -20  (fear)
  45 ≤ fear_greed_avg <  55  → score +=   0  (neutral)
  55 ≤ fear_greed_avg <  75  → score += +35  (greed — bullish)
  fear_greed_avg ≥  75       → score += +15  (extreme greed — potential top warning)

Sub-signal 2 — BTC Dominance, Currency-Aware (approx. 25% of sentiment score)
  For BTC:
    btc_dom > 55%: +10  (BTC strength = flight to BTC = bullish for BTC)
    btc_dom < 45%: -10  (alt season = capital leaving BTC)
    45–55%:          0

  For ETH (sign inverted — alt season is bullish for ETH):
    btc_dom > 55%: -10  (capital in BTC, not alts)
    btc_dom < 45%: +10  (alt season = bullish for ETH)
    45–55%:          0

Sub-signal 3 — Broad Market 24h Change (approx. 15% of sentiment score)
  market_cap_change_24h from CoinGecko (already fetched, was previously unused):
  > +3%:  +10  (risk-on, broad crypto market rising)
  < -3%: -10  (risk-off, broad crypto market falling)
  Otherwise: 0

Final: clamp to [−100, +100]
```

---

## 4. Confidence Formula (Fixed)

**Problem with current formula**: Measures "majority side alignment" but doesn't subtract disagreement. A 2-bullish / 2-bearish split produces 36% confidence when it should be near 0%. Additionally the previous formula could produce 100% from a single fringe component agreeing with itself.

**New formula** — net weighted agreement, divided by sum of all weights (1.0):

```python
# Component weights in fixed order: trend, volatility, momentum, onchain, sentiment
WEIGHTS_LIST = [0.30, 0.15, 0.20, 0.25, 0.10]  # must sum to 1.0
# scores: [trend_score, volatility_score, momentum_score, onchain_score, sentiment_score]
# same order as WEIGHTS_LIST

bullish_weight = sum(w for s, w in zip(scores, WEIGHTS_LIST) if s > 20)
bearish_weight = sum(w for s, w in zip(scores, WEIGHTS_LIST) if s < -20)

dominant    = max(bullish_weight, bearish_weight)
conflicting = min(bullish_weight, bearish_weight)

# Divide by 1.0 (sum of all component weights) — not by total_active.
# This ensures a single fringe component (weight 0.10) produces at most 10%
# confidence, not 100%. All neutral components reduce confidence proportionally.
confidence = (dominant - conflicting) * 100  # equivalent to / 1.0 * 100

# Clamp
confidence = max(0.0, min(100.0, confidence))
```

**Behaviour table**:
| Scenario | Result |
|---|---|
| All 5 components bullish | (1.0 − 0.0) × 100 = **100%** |
| 2 large components bullish (0.55 weight), rest neutral | (0.55 − 0.0) × 100 = **55%** |
| 2 bullish (0.55), 2 bearish (0.35), 1 neutral | (0.55 − 0.35) × 100 = **20%** |
| Equal split (0.50 vs 0.50) | (0.50 − 0.50) × 100 = **0%** |
| Only sentiment bullish (0.10) | (0.10 − 0.0) × 100 = **10%** |

---

## 5. Regime Thresholds and Classification (Fixed)

### Thresholds (symmetric around 0)

The composite score is NOT explicitly clamped before classification. With all component weights summing to 1.0 and each component clamped to [−100, +100], the composite naturally stays in [−100, +100].

```
Strong Bullish:  composite ≥  55
Weak Bullish:    20 ≤ composite <  55
Sideways:       -20 ≤ composite <  20   ← symmetric, was -15 to +30
Weak Bearish:   -55 ≤ composite < -20
Strong Bearish:  composite < -55
```

### ADX Override (moved here — applies to final composite score)

"DI spread" in this section refers to the **raw indicator values** `plus_di` and `minus_di` as returned by `technical_indicator_calculator`, not to the Step 1 bucketed scores in Section 3.1.

After computing composite score, if ADX > 25:
- If composite is in the Sideways range (−20 to +20):
  - `plus_di − minus_di > 5`: override → **Weak Bullish**
  - `minus_di − plus_di > 5`: override → **Weak Bearish**
  - `abs(plus_di − minus_di) ≤ 5`: keep **Sideways** (trend strong but direction mixed)
- If composite is already outside Sideways range: apply thresholds normally, no override
- If DI+/DI- missing: no override (fall back to threshold classification only)

---

## 6. New Data Requirements

### `_get_onchain_metrics()` additions

| New Signal | Data Source | New API Call? | Notes |
|---|---|---|---|
| Wings skew | `book_summary` (already fetched) + `mark_iv` field | No | DTE filter 7–45 days; use existing fetch |
| DVOL percentile (30-day rolling) | 1h DVOL data (already fetched) | No | Use all 720 hourly values returned |
| DVOL term structure ratio | 1h DVOL data (already fetched) | No | ratio = latest / mean(all fetched) |
| VRP signal | `VRPCalculator` (already built) + OHLCV (already fetched by service, pass into method) | No | Uses `vrp_percentage` field, not `_interpret_vrp` |
| OI direction | Sum OI from `book_summary` vs DB record ≥ 4h ago | No | DB query for previous detection |
| Funding rate fix | Remove `/100` in `_get_onchain_metrics` line 230 | No | Adjust thresholds in detector accordingly |

### External metrics addition

| New Signal | Source | New API Call? | Notes |
|---|---|---|---|
| F&G 7-day mean | `FearGreedAPI.get_historical(limit=7)` | Yes — 1 extra call | Method already exists in `FearGreedAPI` |

### `TechnicalIndicatorCalculator` new method

```python
def get_velocity_indicators(self, df: pd.DataFrame, lookback: int = 5) -> Dict[str, Optional[float]]:
    """
    Return rate-of-change indicators.
    - ema_50_velocity: always 1-day delta (lookback not used)
    - rsi_velocity: lookback-day delta (controlled by lookback param, default 5)
    - macd_histogram_velocity: always 1-day magnitude delta (lookback not used)
    Returns dict with keys: ema_50_velocity, rsi_velocity, macd_histogram_velocity
    """
```

- `ema_50_velocity`: `(df["ema_50"].iloc[-1] - df["ema_50"].iloc[-2]) / df["ema_50"].iloc[-2] * 100`
  Always 1-day. `lookback` does NOT apply.
- `rsi_velocity`: `df["rsi"].iloc[-1] - df["rsi"].iloc[-(lookback + 1)]`
  Lookback-day RSI delta. `lookback` applies here only. Default: 5-day.
- `macd_histogram_velocity`: `abs(df["macd_histogram"].iloc[-1]) - abs(df["macd_histogram"].iloc[-2])`
  Always 1-day magnitude change. `lookback` does NOT apply.

---

## 7. Edge Cases

| Scenario | Handling |
|---|---|
| DVOL not available | Skip all 3 vol sub-signals, vol component = 0 |
| DVOL history < 30 days (< 720 hourly points) | Use available points; compute percentile and ratio from partial data |
| No near-expiry options (DTE 7–45) | Wings skew = None → skip wings sub-signal, on-chain score from remaining 2 sub-signals only |
| Wings skew one-sided (puts or calls missing in range) | Skip wings sub-signal entirely → on-chain score from remaining 2 sub-signals |
| First detection (no previous OI in DB) | OI direction = 0 |
| Last detection < 4 hours ago | OI direction = 0 (too recent to be meaningful) |
| All components neutral (all scores in −20 to +20) | Confidence = 0, regime determined by composite score alone |
| ADX missing | Use × 0.7 multiplier, skip ADX override in classifier |
| DI+/DI- missing | Skip Step 1 of trend (DI directional signal = 0), use MA structure only |
| VRPCalculator returns 0 / insufficient OHLCV | VRP sub-signal = 0 |
| Currency = ETH | BTC dominance scoring uses the ETH row in Section 3.5 Sub-signal 2 (opposite sign to BTC row — already defined in the table) |
| F&G API returns < 7 days | Average whatever is returned (minimum 1 day) |
| F&G API call fails entirely | Skip sentiment sub-signal 1 (= 0) |
| SMA50 / SMA200 missing (insufficient OHLCV) | Skip MA structure sub-signal, trend score from DI + velocity only |

---

## 8. Files Changed (Implementation Scope)

**3 files modified, 0 files created, 0 schema changes**:

### `coding/core/analytics/market_regime_detector.py`
- Update `WEIGHTS` constant (onchain: 0.25, volatility: 0.15, momentum: 0.20, sentiment: 0.10)
- Update `REGIME_THRESHOLDS` constant (symmetric ±20 for Sideways, ±55 for strong)
- Rewrite `_score_trend_component()` — DI+/DI-, 4-state MA structure, fixed multiplier, EMA velocity
- Rewrite `_score_volatility_component()` — DVOL percentile, term structure ratio, VRP signal
- Rewrite `_score_momentum_component()` — RSI levels + velocity, MACD crossover + histogram magnitude velocity
- Rewrite `_score_onchain_component()` — wings skew, fixed funding thresholds, OI direction
- Rewrite `_score_sentiment_component()` — 7-day F&G mean, currency-aware dominance, market cap change
- Rewrite `_classify_regime()` — symmetric thresholds, DI-based ADX override (moved here from trend)
- Rewrite `_calculate_confidence()` — weighted net agreement, divide by 1.0
- `detect_regime()` signature: add `velocity_indicators: Dict` parameter (passed from service)

### `coding/core/analytics/technical_indicator_calculator.py`
- Add `get_velocity_indicators(df, lookback=5)` method

### `coding/service/regime/regime_detection_service.py`
- `_get_onchain_metrics(currency, ohlcv_data)`: add `ohlcv_data` parameter for VRP calculation
- Add wings skew computation (book_summary already fetched, apply DTE filter 7–45, compute OTM IV means)
- Add DVOL percentile and term structure ratio computation (from already-fetched DVOL history)
- Add VRP computation via `VRPCalculator`
- Add total OI computation and OI direction (compare to DB record ≥ 4h ago)
- Remove `/100` from funding rate (line 230)
- Add F&G 7-day history fetch via `FearGreedAPI.get_historical(limit=7)` in `fetch_all_metrics()`
- Call `get_velocity_indicators()` after indicators calculated, pass to `detect_regime()`

---

## 9. Out of Scope

- No changes to the 5-component weighted architecture
- No new database tables
- No changes to GUI rendering or color scheme
- No changes to any other tab (Snapshot, Strategy, On-Chain, Database)
- No backtesting infrastructure
- No funding rate momentum (requires storing full historical funding in DB — future enhancement)
- No per-strike OI heatmap or GEX-based signals (belong in On-Chain tab, not regime)
