# Market Regime Detection System

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Components](#components)
4. [Detection Algorithm](#detection-algorithm)
5. [Usage Guide](#usage-guide)
6. [API Reference](#api-reference)
7. [Database Schema](#database-schema)
8. [Configuration](#configuration)
9. [Weight Optimizer](#weight-optimizer)
10. [Examples](#examples)
11. [Troubleshooting](#troubleshooting)

---

## Overview

The Market Regime Detection System is a multi-factor analysis framework that classifies cryptocurrency market conditions into five distinct regimes:

- **Strong Bullish**: Score ≥ 55
- **Weak Bullish**: 20 ≤ Score < 55
- **Sideways**: -20 ≤ Score < 20
- **Weak Bearish**: -55 ≤ Score < -20
- **Strong Bearish**: Score < -55

### Key Features

- Real-time regime detection for BTC and ETH
- Multi-factor scoring combining 5 components
- Confidence scoring based on weighted component alignment
- Historical data storage for trend analysis
- GUI integration with detailed reasoning display
- Uses only free data sources (no paid APIs required)
- Offline weight optimizer for data-driven weight calibration

### Supported Assets

Currently supports:
- Bitcoin (BTC)
- Ethereum (ETH)

---

## Architecture

### Layered Design

```
┌─────────────────────────────────────────────────────────┐
│                    GUI Layer (RegimeTab)                │
│              - User interface and visualization         │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│           Service Layer (RegimeDetectionService)        │
│         - Orchestrates data fetching and detection      │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    Core Components                      │
│  ┌──────────────────────────────────────────────────┐  │
│  │  TechnicalIndicatorCalculator                    │  │
│  │  - SMA, EMA, RSI, MACD, ADX, ATR                 │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │  MarketRegimeDetector                            │  │
│  │  - Multi-factor scoring algorithm                │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │  ExternalMetricsFetcher                          │  │
│  │  - Fear & Greed Index, BTC Dominance             │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    Data Sources                         │
│  - Deribit API (OHLCV, funding, options data, DVOL)    │
│  - Alternative.me (Fear & Greed Index)                  │
│  - CoinGecko (BTC/ETH dominance, market cap change)     │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

1. **User initiates detection** via GUI or API call
2. **Service layer orchestrates**:
   - Fetch 200 days of OHLCV data from Deribit
   - Calculate technical indicators and velocity indicators
   - Fetch on-chain metrics (funding rate, wings skew, OI direction, DVOL metrics)
   - Fetch external sentiment metrics (F&G 7d average, BTC dominance, market cap change)
3. **Core algorithm processes**:
   - Score each component (-100 to +100)
   - Calculate weighted composite score
   - Classify regime based on thresholds (with ADX override for Sideways)
   - Calculate confidence based on weighted component alignment
4. **Results returned** to GUI or caller with detailed reasoning

---

## Components

### 1. Technical Indicator Calculator

**File**: `coding/core/analytics/technical_indicator_calculator.py`

**Purpose**: Calculate technical indicators from OHLCV data using pandas-ta library.

**Indicators Calculated**:
- **SMA (50, 200)**: Simple Moving Averages for trend identification
- **EMA (12, 26, 50)**: Exponential Moving Averages for momentum and velocity
- **RSI (14)**: Relative Strength Index for overbought/oversold conditions
- **MACD (12, 26, 9)**: Moving Average Convergence Divergence for momentum
- **ADX (14)** + **DI+/DI-**: Average Directional Index with directional indicators
- **ATR (14)**: Average True Range for volatility measurement

**Velocity Indicators** (5-day change):
- `ema_50_velocity`: Rate of change of EMA-50 (trend acceleration)
- `rsi_velocity`: 5-day change in RSI (momentum acceleration)
- `macd_histogram_velocity`: Change in MACD histogram magnitude (momentum building/fading)

**Key Methods**:

```python
def calculate_all_indicators(self, ohlcv_data: list) -> pd.DataFrame:
    """
    Calculate all technical indicators from OHLCV data.

    Args:
        ohlcv_data: List of [timestamp, open, high, low, close, volume]

    Returns:
        DataFrame with all indicators and ATR percentile (global rank)
    """
```

---

### 2. Market Regime Detector

**File**: `coding/core/analytics/market_regime_detector.py`

**Purpose**: Core algorithm that combines multiple factors to classify market regime.

**Component Weights**:

| Component | Weight | Purpose |
|-----------|--------|---------|
| Trend | 30% | DI+/DI- spread, MA structure, ADX-scaled, EMA velocity |
| On-Chain | 25% | Wings skew, funding rate (non-monotonic), OI direction |
| Momentum | 20% | RSI level + velocity, MACD crossover + histogram velocity |
| Volatility | 15% | DVOL percentile, term structure ratio, VRP signal |
| Sentiment | 10% | F&G 7d avg (context-aware), BTC dominance (currency-aware), market cap change |

**Key Methods**:

```python
def detect_regime(
    self,
    technical_indicators: Dict,
    onchain_metrics: Dict,
    external_metrics: Dict,
    current_price: float,
    velocity_indicators: Optional[Dict] = None,
    currency: str = "BTC",
) -> Dict:
    """
    Detect current market regime.

    Returns:
        Dict with regime, scores, confidence, and reasoning
    """
```

---

### 3. External Metrics Fetcher

**File**: `coding/core/api/external_apis.py`

**Purpose**: Fetch sentiment and market dominance data from free public APIs.

**APIs Used**:

1. **Fear & Greed Index** (Alternative.me)
   - Returns: Value 0-100, classification, and 7-day average
   - Interpretation: Context-aware (contrarian only when trend is weak/ranging)

2. **BTC/ETH Dominance + Market Cap Change** (CoinGecko)
   - Returns: Market cap percentages + 24h change
   - Interpretation: Currency-aware — sign inverts for ETH vs BTC

---

### 4. Regime Detection Service

**File**: `coding/service/regime/regime_detection_service.py`

**Purpose**: Orchestrates the entire detection process.

**Process**:
1. Fetch 200 days of OHLCV data (1D resolution)
2. Calculate technical indicators and velocity indicators
3. Fetch on-chain metrics:
   - Perpetual funding rate (raw %, with non-monotonic interpretation)
   - DVOL percentile, term structure ratio, VRP percentage
   - Wings skew (OTM put IV - OTM call IV)
   - OI direction score (pre-computed in service, range [-20, +20])
4. Fetch external sentiment metrics (F&G 7d avg, BTC dominance, market cap 24h change)
5. Run detection algorithm
6. Store results in database
7. Return complete result with reasoning

---

## Detection Algorithm

### Component Scoring Details

#### 1. Trend Component (30% weight)

Scoring proceeds in 4 steps:

**Step 1: DI+/DI- directional signal**
```python
di_spread = plus_di - minus_di
if di_spread > 15:   score += 35   # Strong bullish
elif di_spread > 5:  score += 20
elif di_spread > -5: score += 0    # Neutral
elif di_spread > -15: score -= 20
else:                score -= 35   # Strong bearish
```

**Step 2: MA structure (4 states)**
```python
if price > sma_50 and sma_50 > sma_200:  score += 20  # Clean uptrend
elif sma_50 > sma_200 and price < sma_50: score += 10  # Pullback in uptrend
elif price < sma_50 and sma_50 < sma_200: score -= 20  # Clean downtrend
elif sma_50 < sma_200 and price > sma_50: score -= 10  # Bounce in downtrend
```

**Step 3: ADX strength multiplier (applied to Steps 1+2 sum)**
```python
if adx > 40:   multiplier = 1.4   # Very strong trend
elif adx > 25: multiplier = 1.0   # Confirmed trend
elif adx > 20: multiplier = 0.6   # Weak trend
else:          multiplier = 0.3   # No trend (dampens signal)
score = score * multiplier
```

**Step 4: EMA-50 velocity (added after multiplier — not ADX-scaled)**
```python
if ema_50_velocity > 0.2:  score += 10   # Accelerating
elif ema_50_velocity < -0.2: score -= 10  # Decelerating
```

**ADX override in classifier**: If ADX > 25 and composite score falls in Sideways range (-20 to +20), the classifier uses raw DI+ vs DI- to force a directional classification (Weak Bullish or Weak Bearish) when the spread exceeds 5.

**Range**: -100 to +100

---

#### 2. Volatility Component (15% weight)

Driven by DVOL (Deribit Volatility Index) signals rather than ATR percentile alone. Low DVOL + cheap options = bullish regime precursor. High DVOL + backwardation = fear/crisis = bearish.

**Sub-signal 1: DVOL 30-day rolling percentile (primary)**
```python
if dvol_percentile < 20:   score += 40   # Very low vol — calm/complacent
elif dvol_percentile < 40: score += 20
elif dvol_percentile < 60: score += 0    # Normal
elif dvol_percentile < 80: score -= 20
else:                      score -= 40   # Extreme vol — fear/crisis
```

**Sub-signal 2: DVOL term structure ratio (current / 30d_avg)**
```python
if ratio < 0.80:   score += 20   # Contango — near-term vol cheap
elif ratio < 0.95: score += 10
elif ratio < 1.10: score += 0    # Flat
elif ratio < 1.25: score -= 15   # Mild backwardation
else:              score -= 25   # Steep backwardation — crisis premium
```

**Sub-signal 3: VRP signal (IV - RV) / RV × 100**
```python
if vrp > 20:    score -= 20   # Options very expensive — hedgers paying up
elif vrp > 5:   score -= 10
elif vrp > -5:  score += 0    # Fair pricing
elif vrp > -20: score += 10   # Cheap options — complacency
else:           score += 20   # Extreme complacency — pre-run environment
```

**Range**: -100 to +100

---

#### 3. Momentum Component (20% weight)

**Sub-signal 1: RSI level + velocity**
```python
if rsi > 70:   score += 25   # Overbought — bullish but less than 60-70 range
elif rsi > 60: score += 35   # Strongest bullish range
elif rsi > 50: score += 15
elif rsi > 40: score -= 10
elif rsi > 30: score -= 30
else:          score -= 15   # Oversold — contrarian bounce reduces bearish signal

# RSI 5-day velocity
if rsi_velocity > 8:  score += 10   # Accelerating bullish
elif rsi_velocity < -8: score -= 10  # Decelerating
```

**Sub-signal 2: MACD crossover + histogram velocity**
```python
# Crossover
if macd > macd_signal: score += 25
else:                  score -= 25

# Histogram magnitude velocity (independent of crossover direction)
if hist_velocity > 0:  score += 15   # Momentum building
elif hist_velocity < 0: score -= 15  # Momentum fading
```

**Range**: -100 to +100

---

#### 4. On-Chain Component (25% weight)

**Sub-signal 1: Wings skew (OTM put IV − OTM call IV, in percentage points)**
```python
if wings_skew > 10:   score -= 40   # Strong fear — puts very expensive
elif wings_skew > 5:  score -= 20
elif wings_skew > -5: score += 0    # Balanced skew
elif wings_skew > -10: score += 20
else:                 score += 40   # Strong call premium — upside positioned
```

**Sub-signal 2: Funding rate — non-monotonic by design**

Extreme funding signals *crowded* positioning (liquidation/squeeze risk), not conviction. The scoring is intentionally non-monotonic: moderate positive funding is bullish, but extreme positive flips to bearish.

```python
# Typical Deribit 8h funding range: -0.05% to +0.05%
if funding > 0.10:   score -= 20   # Extreme longs — overcrowded, liquidation risk
elif funding > 0.05: score += 10   # Elevated — bullish but getting crowded
elif funding > 0.02: score += 30   # Healthy bullish — longs paying, genuine demand
elif funding > -0.02: score += 0   # Neutral zone
elif funding > -0.05: score -= 30  # Healthy bearish
elif funding > -0.10: score -= 10  # Elevated shorts — squeeze risk rising
else:                score += 20   # Extreme shorts — overcrowded, squeeze risk
```

**Sub-signal 3: OI direction (pre-computed in service, range [-20, +20])**

Measures whether open interest is expanding in the direction of price movement. Passed directly as a score.

**Range**: -100 to +100

---

#### 5. Sentiment Component (10% weight)

**Sub-signal 1: F&G 7-day average — context-aware**

In a confirmed bearish trend (ADX > 25 and trend score < -30), extreme fear is *not* treated as a contrarian buy signal — it confirms the bearish regime.

```python
if fg_avg < 25:
    if in_strong_bearish_trend: score -= 15   # Confirms bear
    else:                       score += 25   # Contrarian buy
elif fg_avg < 45: score -= 20    # Fear
elif fg_avg < 55: score += 0     # Neutral
elif fg_avg < 75: score += 35    # Greed — bullish
else:             score += 15    # Extreme greed — potential top warning
```

**Sub-signal 2: BTC dominance — currency-aware**

Sign inverts depending on which asset is being analyzed:
```python
# BTC:
if btc_dom > 55: score += 10   # Capital in BTC — bullish for BTC
elif btc_dom < 45: score -= 10  # Alt season — capital leaving BTC

# ETH (and other alts):
if btc_dom > 55: score -= 10   # Capital in BTC, not alts
elif btc_dom < 45: score += 10  # Alt season — bullish for ETH
```

**Sub-signal 3: Market cap 24h change**
```python
if mc_change > 3:  score += 10   # Risk-on
elif mc_change < -3: score -= 10  # Risk-off
```

**Range**: -100 to +100

---

### Composite Score Calculation

```python
composite_score = (
    trend_score      * 0.30 +
    onchain_score    * 0.25 +
    momentum_score   * 0.20 +
    volatility_score * 0.15 +
    sentiment_score  * 0.10
)
```

**Range**: -100 to +100

**Classification**:
- composite_score ≥ 55 → **Strong Bullish**
- composite_score ≥ 20 → **Weak Bullish**
- composite_score ≥ -20 → **Sideways**
- composite_score ≥ -55 → **Weak Bearish**
- composite_score < -55 → **Strong Bearish**

Note: An ADX override can reclassify Sideways → Weak Bullish or Weak Bearish when ADX > 25 and DI+/DI- spread > 5. See `_classify_regime()`.

---

### Confidence Calculation

Confidence measures weighted net agreement between components. Each component's vote is weighted by its WEIGHTS entry (so trend at 30% has 3× the voting power of sentiment at 10%).

```python
bullish_weight = sum(w for s, w in zip(scores, WEIGHTS_LIST) if s > 20)
bearish_weight = sum(w for s, w in zip(scores, WEIGHTS_LIST) if s < -20)
confidence = (dominant - conflicting) * 100
```

**Examples**:
- All 5 bullish → 100%
- Only sentiment bullish (weight 0.10) → 10%
- Trend bullish (0.30) vs onchain bearish (0.25), rest neutral → 5%

---

## Usage Guide

### GUI Usage

1. **Launch the application**:
   ```bash
   .venv\Scripts\activate
   python main.py
   ```

2. **Navigate to Market Regime tab**

3. **Select currency** (BTC or ETH) from dropdown

4. **Click "Detect Regime"** button

5. **View results**:
   - Regime classification (color-coded)
   - Confidence percentage
   - Composite score
   - Component scores table
   - Detailed reasoning text
   - Detection timestamp

**Results Display**:
- **Strong Bullish**: Green
- **Weak Bullish**: Light green
- **Sideways**: Gray
- **Weak Bearish**: Light orange
- **Strong Bearish**: Red

---

### Programmatic Usage

```python
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.regime.regime_detection_service import RegimeDetectionService
from coding.core.database.repository import DatabaseRepository

with DeribitApiService() as api_service:
    repository = DatabaseRepository()
    service = RegimeDetectionService(api_service, repository)

    result = service.detect_regime('BTC')

    print(f"Regime: {result['regime']}")
    print(f"Score: {result['composite_score']}")
    print(f"Confidence: {result['confidence']}%")
    print(f"Reasoning: {result['reasoning']}")

    comp_scores = result['component_scores']
    print(f"Trend:      {comp_scores['trend']}")
    print(f"On-Chain:   {comp_scores['onchain']}")
    print(f"Momentum:   {comp_scores['momentum']}")
    print(f"Volatility: {comp_scores['volatility']}")
    print(f"Sentiment:  {comp_scores['sentiment']}")
```

---

## API Reference

### RegimeDetectionService

#### `detect_regime(currency: str) -> Dict`

**Parameters**:
- `currency` (str): Asset symbol ('BTC' or 'ETH')

**Returns** (Dict):
```python
{
    'currency': 'BTC',
    'detected_at': datetime,
    'current_price': 95432.50,
    'regime': 'Weak Bearish',
    'confidence': 28.0,
    'composite_score': -34.5,
    'component_scores': {
        'trend': -50.0,
        'volatility': 0.0,
        'momentum': -80.0,
        'onchain': 10.0,
        'sentiment': -10.0
    },
    'technical_indicators': {
        'sma_50': 96500.0,
        'sma_200': 98000.0,
        'rsi': 38.6,
        'macd': -500.0,
        'macd_signal': -300.0,
        'macd_histogram': -200.0,
        'adx': 23.4,
        'plus_di': 18.2,
        'minus_di': 24.6,
        'atr': 2500.0,
        'atr_percentile': 6.9
    },
    'onchain_metrics': {
        'funding_rate': 0.00000,
        'wings_skew': 3.5,
        'oi_direction': -10,
        'dvol_percentile': 45.0,
        'dvol_term_structure_ratio': 1.05,
        'vrp_percentage': 8.0,
    },
    'external_metrics': {
        'fear_greed': {
            'value': 25,
            'classification': 'Extreme Fear'
        },
        'fear_greed_7d_avg': 28.0,
        'btc_dominance': 57.59,
        'market_cap_change_24h': -1.2,
    },
    'reasoning': 'Market Regime: Weak Bearish (Score: -34.5) | ...',
    'detection_time_seconds': 1.61
}
```

---

### MarketRegimeDetector

#### `detect_regime(...) -> Dict`

**Parameters**:
- `technical_indicators` (Dict): sma_50, sma_200, adx, plus_di, minus_di, atr_percentile, rsi, macd, macd_signal, macd_histogram
- `onchain_metrics` (Dict): funding_rate, wings_skew, oi_direction, dvol_percentile, dvol_term_structure_ratio, vrp_percentage
- `external_metrics` (Dict): fear_greed dict, fear_greed_7d_avg, btc_dominance, market_cap_change_24h
- `current_price` (float): Current asset price
- `velocity_indicators` (Optional Dict): ema_50_velocity, rsi_velocity, macd_histogram_velocity
- `currency` (str): 'BTC' or 'ETH' — affects sentiment scoring

**Returns** (Dict):
```python
{
    'regime': 'Weak Bearish',
    'composite_score': -34.5,
    'confidence': 28.0,
    'trend_score': -50.0,
    'volatility_score': 0.0,
    'momentum_score': -80.0,
    'onchain_score': 10.0,
    'sentiment_score': -10.0,
    'reasoning': 'Market Regime: Weak Bearish (Score: -34.5) | ...'
}
```

---

## Database Schema

### Tables Used by the Optimizer

#### `regime_detections`
Stores regime detection results. Read by the weight optimizer.

```sql
CREATE TABLE regime_detections (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    detected_at TIMESTAMP NOT NULL,
    regime VARCHAR(50) NOT NULL,
    composite_score DECIMAL(10, 4) NOT NULL,
    confidence DECIMAL(10, 4) NOT NULL,
    trend_score DECIMAL(10, 4),
    volatility_score DECIMAL(10, 4),
    momentum_score DECIMAL(10, 4),
    onchain_score DECIMAL(10, 4),
    sentiment_score DECIMAL(10, 4),
    current_price DECIMAL(20, 8),
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### `ohlcv_history`
Used by the optimizer for long-horizon (7d, 30d) forward return lookups.

```sql
CREATE TABLE ohlcv_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    open DECIMAL(20, 8),
    high DECIMAL(20, 8),
    low DECIMAL(20, 8),
    close DECIMAL(20, 8),
    volume DECIMAL(20, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(currency, timestamp)
);
```

---

## Configuration

### Component Weights

Located in `MarketRegimeDetector.WEIGHTS`:

```python
WEIGHTS = {
    "trend":      0.30,   # 30% — Primary direction signal
    "onchain":    0.25,   # 25% — Market positioning (raised from 20%)
    "momentum":   0.20,   # 20% — Momentum strength (lowered from 25%)
    "volatility": 0.15,   # 15% — Vol regime (raised from 10%)
    "sentiment":  0.10,   # 10% — Market psychology (lowered from 15%)
}
```

**Rationale** (hand-tuned, pending data validation):
- **Trend** highest because it determines primary direction
- **On-chain** raised — wings skew and funding capture positioning directly
- **Momentum** lowered — velocity indicators complement RSI/MACD without needing overweight
- **Volatility** raised — DVOL regime provides regime-context signal, not just noise
- **Sentiment** lowest — F&G is useful but lags and can be misleading in strong trends

**Note**: These weights are hand-tuned based on judgment. Use the weight optimizer (see [Weight Optimizer](#weight-optimizer) section) to validate and potentially update these once sufficient historical data has accumulated.

### Regime Thresholds

Located in `MarketRegimeDetector.REGIME_THRESHOLDS`:

```python
REGIME_THRESHOLDS = {
    "Strong Bullish": 55,    # composite >= 55
    "Weak Bullish":   20,    # 20 <= composite < 55
    "Sideways":      -20,    # -20 <= composite < 20
    "Weak Bearish":  -55,    # -55 <= composite < -20
    "Strong Bearish": -100,  # composite < -55
}
```

**Design**: Symmetric around 0. Sideways band narrowed (±20 vs old ±30) to classify more detections as directional. Strong threshold lowered (±55 vs old ±60) to be achievable given the new component scoring ranges.

---

## Weight Optimizer

### Overview

The regime weight optimizer is an offline tool that reads historical regime detections from the database, computes forward returns, and uses numerical optimization to find component weights that would have produced the best directional accuracy and/or risk-adjusted returns.

**It does not write to the database or modify any code.** It is purely a reporting tool. You must manually apply any suggested weights by updating `WEIGHTS` and `REGIME_THRESHOLDS` in `market_regime_detector.py`.

### Files

- **`coding/core/database/regime_dataset_builder.py`** — Queries DB and builds the optimization dataset
- **`coding/core/analytics/regime_weight_optimizer.py`** — SLSQP optimizer with Dirichlet warm-start
- **`scripts/optimize_regime_weights.py`** — CLI entry point

### How It Works

1. **Dataset building** (`RegimeDatasetBuilder`):
   - Fetches all `regime_detections` records from `DATASET_START_DATE` (2020-01-01) onward
   - Resolves 8 forward-return horizons per detection:
     - Short (4h, 8h, 12h, 24h, 48h, 72h): matched from the `regime_detections` price pool (±10% window)
     - Long (7d, 30d): matched from `ohlcv_history` (±10% window)
   - Drops rows with missing `current_price` or all-horizons-null

2. **Optimization** (`RegimeWeightOptimizer`):
   - Runs SLSQP with 500 Dirichlet-sampled warm starts
   - Optimizes for 3 objectives separately: accuracy, per-horizon Sharpe, blended (50/50)
   - Bounds: weights [0.05, 0.60], sideways threshold [10, 30], strong threshold [40, 70]
   - Constraint: weights sum to 1.0, strong ≥ sideways + 1

3. **Output**: Reports 4 parameter sets: current (hand-tuned), accuracy-optimal, Sharpe-optimal, blended

### Running the Optimizer

```bash
# BTC (default)
python -m scripts.optimize_regime_weights --currency BTC

# ETH
python -m scripts.optimize_regime_weights --currency ETH

# Custom directional threshold (default 1.5%)
python -m scripts.optimize_regime_weights --currency BTC --directional-threshold 2.0
```

### Example Output

```
=== REGIME WEIGHT OPTIMIZER ===
Currency: BTC
Dataset: 87 detections (2025-06-01 → 2026-03-13)

Horizon coverage:
    4h:   82/87 (94%)
    8h:   81/87 (93%)
   12h:   80/87 (92%)
   24h:   78/87 (90%)
   48h:   75/87 (86%)
   72h:   72/87 (83%)
    7d:   65/87 (75%)
   30d:   41/87 (47%)

────────────────────────────────────────────────────────────
CURRENT (hand-tuned):
  trend=0.30  vol=0.15  momentum=0.20  onchain=0.25  sentiment=0.10
  sideways=±20  strong=±55
  Accuracy: 62.0%   Sharpe: 0.38

ACCURACY-OPTIMAL:
  trend=0.35  vol=0.12  momentum=0.18  onchain=0.28  sentiment=0.07
  sideways=±18  strong=±52
  Accuracy: 67.3% (+5.3 pp)   Sharpe: 0.41

SHARPE-OPTIMAL:
  trend=0.28  vol=0.20  momentum=0.22  onchain=0.23  sentiment=0.07
  sideways=±22  strong=±58
  Accuracy: 61.1%   Sharpe: 0.45 (+0.07)

BLENDED (50/50):
  trend=0.32  vol=0.16  momentum=0.20  onchain=0.26  sentiment=0.06
  sideways=±20  strong=±55
  Accuracy: 64.8%   Sharpe: 0.43
  (no delta — blended has no single natural baseline)
────────────────────────────────────────────────────────────
To apply: update WEIGHTS and REGIME_THRESHOLDS in market_regime_detector.py
```

### When to Re-Run

**The optimizer requires at least 30 regime detections with good horizon coverage to produce meaningful results.**

Current status: as of March 2026, only ~13 detections are stored, meaning optimization results are noise-fitting and should not be applied. The optimizer will warn when the dataset is too small.

**Re-run the optimizer before applying any weight changes**, once:
- 30+ regime detections exist in the database
- Short horizons (4h–24h) have ≥ 20 matched rows each
- Ideally, the dataset spans multiple distinct market regimes (bull, bear, sideways)

After re-running, if the optimized weights differ meaningfully from the current hand-tuned values, update `WEIGHTS` and `REGIME_THRESHOLDS` in `market_regime_detector.py` and commit the change.

---

## Examples

### Example 1: Weak Bearish Market

**Input Data**:
- Price: $95,432.50 (BTC), below both MAs (Death Cross)
- ADX: 20.7 (weak trend), DI-: 24.6, DI+: 18.2
- RSI: 37.9 (bearish momentum)
- MACD: Bearish histogram
- DVOL Percentile: 35 (below average)
- Funding Rate: 0.000% (neutral)
- Wings Skew: +3.5 (slight fear premium)
- Fear & Greed 7d avg: 25 (Extreme Fear)

**Component Scores**:
- Trend: ~-30.0 (DI spread -6.4, death cross, low ADX dampens)
- Volatility: +20.0 (below-average DVOL percentile)
- Momentum: -50.0 (bearish RSI and MACD)
- On-Chain: 0.0 (neutral funding, modest wings skew, OI neutral)
- Sentiment: +10.0 (extreme fear in ranging market = contrarian)

**Approximate Composite Score**: -22.5

**Classification**: Weak Bearish or Sideways (depending on velocity inputs)

---

### Example 2: Strong Bullish Market

**Input Data**:
- Price: Above both MAs (Golden Cross), DI+ > DI- by 18
- ADX: 42 (very strong trend)
- RSI: 65 (strong momentum)
- MACD: Bullish, expanding histogram
- DVOL Percentile: 18 (very low — calm environment)
- Funding Rate: +0.035% (healthy bullish — not overcrowded)
- Wings Skew: -8 (calls premium — upside positioned)
- Fear & Greed 7d avg: 70 (Greed)

**Component Scores**:
- Trend: ~+70.0 (strong DI spread, golden cross, high ADX multiplier)
- Volatility: +50.0 (low DVOL percentile + contango)
- Momentum: ~+65.0 (RSI 60-70 range + bullish MACD)
- On-Chain: ~+60.0 (healthy funding + calls premium)
- Sentiment: +45.0 (Greed + BTC dominance context)

**Approximate Composite Score**: +60+

**Classification**: Strong Bullish

---

## Troubleshooting

### Common Issues

#### 1. Optimizer reports "Dataset too small"

**Symptom**: Warning: "N detections available, minimum recommended is 30."

**Cause**: The detection daemon hasn't been running long enough to accumulate sufficient historical data.

**Solution**: Wait until 30+ detections are stored, then re-run. The detection runs each time a user triggers regime detection in the GUI, plus any automated schedule.

---

#### 2. Unicode encoding error on Windows

**Error**:
```
UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'
```

**Cause**: Windows console uses cp1252 encoding by default.

**Solution**: Already fixed in the CLI script — `sys.stdout.reconfigure(encoding='utf-8')` is applied at startup.

---

#### 3. "No schema defined for endpoint"

**Error**:
```
ValueError: No schema defined for endpoint: /public/get_tradingview_chart_data
```

**Solution**: Ensure `coding/core/schemas/deribit_schemas.py` has `TRADINGVIEW_CHART_DATA` schema and is mapped in `get_schema_for_endpoint()`.

---

#### 4. Low/Incorrect Confidence Scores

**Symptom**: Confidence always very low (0-20%).

**Explanation**: Expected when market is in consolidation (all components near zero) or components strongly disagree. Low confidence is a valid signal.

Confidence is weighted net agreement — if only sentiment (10% weight) is bullish, confidence is 10% even if all other components are neutral.

---

#### 5. Regime Detection Fails

**Troubleshooting Steps**:

1. **Check API connectivity**:
   ```python
   with DeribitApiService() as api:
       ticker = api.get_ticker("BTC-PERPETUAL")
       print(ticker)
   ```

2. **Check external APIs**:
   ```python
   from coding.core.api.external_apis import ExternalMetricsFetcher
   fetcher = ExternalMetricsFetcher()
   metrics = fetcher.fetch_all()
   print(metrics)
   ```

3. **Check data availability**: Ensure 200+ days of OHLCV data available.

---

### Performance Issues

#### Slow Detection (>5 seconds)

**Causes**: External API timeouts (Alternative.me or CoinGecko), large OHLCV dataset, network latency.

**Solutions**: Increase timeout values in external API classes. The GUI already runs detection in a background thread.

---

## Limitations and Considerations

### Current Limitations

1. **Supported Assets**: Only BTC and ETH

2. **Timeframe**: Daily resolution only — designed for swing trading/position sizing, not intraday

3. **External Dependencies**: Requires 3 external APIs (Deribit critical; others degrade gracefully)

4. **Hand-tuned weights**: Weights are based on judgment, not backtested. Re-run the optimizer once 30+ detections are available.

5. **DVOL sub-signals**: `dvol_term_structure_ratio` and `vrp_percentage` require additional data fetching in the service layer. If unavailable, those sub-signals score 0 and the volatility component relies only on `dvol_percentile`.

### Best Practices

1. **Don't rely on a single detection** — run multiple detections over days, look for regime persistence

2. **Check confidence levels** — high confidence means clear alignment, low confidence means uncertain market

3. **Combine with other analysis** — use regime as context, not sole decision factor

4. **Re-run optimizer periodically** — as more detections accumulate, re-run to validate weights

5. **Adapt to market evolution** — extreme events may break the model; monitor and recalibrate

---

## Future Enhancements

1. **Data-validated weights**: Once 30+ detections exist, apply optimizer results if they show meaningful improvement
2. **Automated optimizer scheduling**: Run optimizer on a schedule (e.g., monthly) as DB fills up
3. **More assets**: Add SOL if sufficient Deribit options/perp data available
4. **Alerts**: Regime change notifications with confidence threshold filtering
5. **Advanced sentiment**: Social media / news sentiment integration

---

## References

### Internal Documentation
- `GUI_DOCUMENTATION.md` — GUI usage guide
- `database_tab_on_chain_analysis.md` — On-chain analysis details

### External Resources
- Deribit API: https://docs.deribit.com/
- Alternative.me API: https://alternative.me/crypto/fear-and-greed-index/
- CoinGecko API: https://www.coingecko.com/en/api
- pandas-ta Documentation: https://github.com/twopirllc/pandas-ta

### Academic References
- ADX/DI+/DI-: Wilder, J. Welles (1978). "New Concepts in Technical Trading Systems"
- RSI/MACD: Wilder (1978)
- Funding Rates: Perpetual swap mechanics (BitMEX whitepaper)
- Fear & Greed Index: CNN Fear & Greed Index adapted for crypto
- Volatility Risk Premium: Standard options pricing literature

---

**Version**: 2.0
**Last Updated**: 2026-03-13
**Maintained By**: Options Trading Platform Team
