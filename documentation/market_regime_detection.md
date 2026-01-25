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
9. [Examples](#examples)
10. [Troubleshooting](#troubleshooting)

---

## Overview

The Market Regime Detection System is a multi-factor analysis framework that classifies cryptocurrency market conditions into five distinct regimes:

- **Strong Bullish**: Score ≥ 60
- **Weak Bullish**: Score ≥ 30
- **Sideways**: Score ≥ -30
- **Weak Bearish**: Score ≥ -60
- **Strong Bearish**: Score < -60

### Key Features

- Real-time regime detection for BTC and ETH
- Multi-factor scoring combining 5 components
- Confidence scoring based on component alignment
- Historical data storage for trend analysis
- GUI integration with detailed reasoning display
- Uses only free data sources (no paid APIs required)

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
│  - Deribit API (OHLCV, funding, options data)          │
│  - Alternative.me (Fear & Greed Index)                  │
│  - CoinGecko (BTC/ETH dominance)                        │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

1. **User initiates detection** via GUI or API call
2. **Service layer orchestrates**:
   - Fetch 200 days of OHLCV data from Deribit
   - Calculate technical indicators
   - Fetch on-chain metrics (funding rate, P/C ratio, DVOL)
   - Fetch external sentiment metrics
3. **Core algorithm processes**:
   - Score each component (-100 to +100)
   - Calculate weighted composite score
   - Classify regime based on thresholds
   - Calculate confidence based on alignment
4. **Results returned** to GUI or caller with detailed reasoning

---

## Components

### 1. Technical Indicator Calculator

**File**: `coding/core/analytics/technical_indicator_calculator.py`

**Purpose**: Calculate technical indicators from OHLCV data using pandas-ta library.

**Indicators Calculated**:
- **SMA (50, 200)**: Simple Moving Averages for trend identification
- **EMA (12, 26)**: Exponential Moving Averages for momentum
- **RSI (14)**: Relative Strength Index for overbought/oversold conditions
- **MACD (12, 26, 9)**: Moving Average Convergence Divergence for momentum
- **ADX (14)**: Average Directional Index for trend strength
- **ATR (14)**: Average True Range for volatility measurement

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

**ATR Percentile Calculation**:
Uses global percentile rank across the entire dataset (not rolling window):

```python
df["atr_percentile"] = df["atr"].rank(pct=True) * 100
```

This ensures volatility is measured relative to the asset's historical range.

---

### 2. Market Regime Detector

**File**: `coding/core/analytics/market_regime_detector.py`

**Purpose**: Core algorithm that combines multiple factors to classify market regime.

**Component Weights**:

| Component | Weight | Purpose |
|-----------|--------|---------|
| Trend | 30% | Price direction and MA alignment |
| Momentum | 25% | RSI and MACD signals |
| On-Chain | 20% | Funding rate and P/C ratio |
| Sentiment | 15% | Fear & Greed Index, BTC dominance |
| Volatility | 10% | ATR percentile (fear indicator) |

**Key Methods**:

```python
def detect_regime(
    self,
    technical_indicators: Dict,
    onchain_metrics: Dict,
    external_metrics: Dict,
    current_price: float
) -> Dict:
    """
    Detect current market regime.

    Returns:
        Dict with regime, scores, confidence, and reasoning
    """
```

**Component Scoring Methods**:
- `_score_trend_component()`: SMA/EMA positioning, golden/death cross, ADX strength
- `_score_volatility_component()`: ATR percentile interpretation
- `_score_momentum_component()`: RSI and MACD analysis
- `_score_onchain_component()`: Funding rate and P/C ratio
- `_score_sentiment_component()`: Fear & Greed Index (contrarian)

---

### 3. External Metrics Fetcher

**File**: `coding/core/api/external_apis.py`

**Purpose**: Fetch sentiment and market dominance data from free public APIs.

**APIs Used**:

1. **Fear & Greed Index** (Alternative.me)
   - URL: `https://api.alternative.me/fng/`
   - Returns: Value 0-100 and classification
   - Interpretation: Contrarian indicator (extreme fear = buy signal)

2. **BTC/ETH Dominance** (CoinGecko)
   - URL: `https://api.coingecko.com/api/v3/global`
   - Returns: Market cap percentages
   - Interpretation: Rising BTC dominance = flight to safety

**Classes**:
- `FearGreedAPI`: Fetches Fear & Greed Index
- `CoinGeckoAPI`: Fetches crypto market dominance
- `ExternalMetricsFetcher`: Combines both APIs

---

### 4. Regime Detection Service

**File**: `coding/service/regime/regime_detection_service.py`

**Purpose**: Orchestrates the entire detection process.

**Key Method**:

```python
def detect_regime(self, currency: str) -> Dict:
    """
    Main entry point for regime detection.

    Args:
        currency: 'BTC' or 'ETH'

    Returns:
        Complete detection result with all metrics
    """
```

**Process**:
1. Fetch 200 days of OHLCV data (1D resolution)
2. Calculate technical indicators
3. Fetch on-chain metrics:
   - Perpetual funding rate
   - DVOL (Deribit Volatility Index)
   - Put/Call ratio from options OI
4. Fetch external sentiment metrics
5. Run detection algorithm
6. Store results in database
7. Return complete result with reasoning

---

## Detection Algorithm

### Component Scoring Details

#### 1. Trend Component (30% weight)

**Scoring Logic**:

```python
# Price position relative to MAs (50% of trend score)
if price > SMA50 and price > SMA200:
    score += 30  # Bullish (above both MAs)
elif price < SMA50 and price < SMA200:
    score -= 30  # Bearish (below both MAs)
else:
    score += 0   # Mixed (neutral)

# MA alignment (50% of trend score)
if SMA50 > SMA200:
    score += 20  # Golden Cross structure
elif SMA50 < SMA200:
    score -= 20  # Death Cross structure

# ADX strength multiplier
if ADX > 40:
    multiplier = 1.5   # Very strong trend
elif ADX > 25:
    multiplier = 1.0   # Strong trend
elif ADX > 20:
    multiplier = 0.5   # Weak trend
else:
    multiplier = 0.2   # No trend

score = score * multiplier
```

**Range**: -100 to +100

**Interpretation**:
- Strong positive: Price above MAs, golden cross, strong ADX
- Strong negative: Price below MAs, death cross, strong ADX
- Weak signal: Low ADX dampens the score

---

#### 2. Volatility Component (10% weight)

**Scoring Logic**:

```python
# ATR Percentile interpretation
if atr_percentile < 25:
    score += 0    # LOW volatility = neutral (complacency)
elif atr_percentile < 75:
    score += 0    # NORMAL volatility = neutral
else:
    score -= 30   # EXTREME volatility = bearish (fear/uncertainty)

# DVOL consideration (if available)
if dvol > 80:
    score -= 10   # High implied vol = uncertainty
elif dvol < 40:
    score += 10   # Low implied vol = stability
```

**Range**: -40 to +10

**Interpretation**:
- Low/normal volatility is **neutral** (not bullish!)
- High volatility indicates fear and uncertainty (bearish)
- Volatility alone doesn't predict direction

**Important Note**: Low volatility indicates complacency or lack of conviction, not bullish sentiment. This differs from a "low volatility = safe = bullish" interpretation. Instead, we treat it as neutral since markets can be calm before either direction.

---

#### 3. Momentum Component (25% weight)

**Scoring Logic**:

```python
# RSI scoring (50% of momentum score)
if rsi > 70:
    score += 20    # Overbought (weak bullish, potential reversal)
elif rsi > 60:
    score += 40    # Strong bullish momentum
elif rsi > 50:
    score += 20    # Bullish
elif rsi > 40:
    score -= 10    # Neutral/slight bearish
elif rsi > 30:
    score -= 30    # Bearish
else:
    score -= 20    # Oversold (weak bearish, potential bounce)

# MACD scoring (50% of momentum score)
if macd > macd_signal:
    score += 30    # Bullish crossover
else:
    score -= 30    # Bearish crossover

# MACD histogram (confirmation)
if macd_histogram > 0:
    score += 20    # Positive momentum
else:
    score -= 20    # Negative momentum
```

**Range**: -70 to +90

**Interpretation**:
- RSI 60-70: Strong bullish momentum (best signal)
- RSI >70 or <30: Extreme values reduce score (reversal risk)
- MACD confirms trend direction

---

#### 4. On-Chain Component (20% weight)

**Scoring Logic**:

```python
# Funding Rate scoring (60% of on-chain score)
# Typical range: -0.03% to +0.03% daily
if funding_rate > 0.01:         # > 1%
    score += 40                 # Very bullish (longs paying shorts)
elif funding_rate > 0.005:      # > 0.5%
    score += 20                 # Bullish
elif funding_rate > -0.005:     # ±0.5%
    score += 0                  # NEUTRAL ZONE (normal conditions)
elif funding_rate > -0.01:      # < -0.5%
    score -= 20                 # Bearish (shorts paying longs)
else:
    score -= 40                 # Very bearish

# Put/Call Ratio scoring (40% of on-chain score)
if put_call_ratio > 1.2:
    score -= 30    # Heavy put bias (> 1.2 = fear)
elif put_call_ratio > 1.0:
    score -= 10    # Slight put bias
elif put_call_ratio > 0.8:
    score += 0     # Balanced (0.8-1.0 is neutral)
elif put_call_ratio > 0.6:
    score += 10    # Slight call bias (moderately bullish)
else:
    score += 30    # Heavy call bias (< 0.6 = greed)
```

**Range**: -70 to +70

**Interpretation**:
- **Funding rate**: Measures perpetual swap costs
  - Positive = longs dominant (bullish positioning)
  - Negative = shorts dominant (bearish positioning)
  - **±0.5% is neutral zone** (normal market fluctuations)
- **P/C ratio**: Measures options positioning
  - Ratio > 1.0 = more puts (fear)
  - Ratio < 1.0 = more calls (greed)
  - **0.8-1.0 is balanced** (no strong bias)

**Put/Call Ratio Calculation**:
Aggregates open interest across all strikes and expirations:

```python
total_call_oi = sum(call_oi for all strikes)
total_put_oi = sum(put_oi for all strikes)
put_call_ratio = total_put_oi / total_call_oi
```

---

#### 5. Sentiment Component (15% weight)

**Scoring Logic**:

```python
# Fear & Greed Index (70% of sentiment score)
# Contrarian interpretation
if value < 25:
    score += 30    # Extreme fear = buy signal (contrarian)
elif value < 45:
    score -= 20    # Fear = slight bearish
elif value < 55:
    score += 0     # Neutral
elif value < 75:
    score += 40    # Greed = bullish
else:
    score += 20    # Extreme greed = potential top (reduced bullish)

# BTC Dominance (30% of sentiment score)
if btc_dominance > 50:
    score += 10    # BTC strong (flight to safety)
else:
    score -= 10    # Alt season potential (risk-on)
```

**Range**: -30 to +50

**Interpretation**:
- **Fear & Greed**: Contrarian indicator
  - Extreme fear (0-25) = oversold, buy opportunity
  - Greed (55-75) = bullish momentum
  - Extreme greed (75-100) = potential top, reduce bullish signal
- **BTC Dominance**: Market risk appetite
  - Rising dominance = flight to safety (bearish for alts)
  - Falling dominance = risk-on (bullish for alts)

---

### Composite Score Calculation

```python
composite_score = (
    trend_score * 0.30 +
    momentum_score * 0.25 +
    onchain_score * 0.20 +
    sentiment_score * 0.15 +
    volatility_score * 0.10
)
```

**Range**: -100 to +100

**Classification**:
- composite_score ≥ 60 → **Strong Bullish**
- composite_score ≥ 30 → **Weak Bullish**
- composite_score ≥ -30 → **Sideways**
- composite_score ≥ -60 → **Weak Bearish**
- composite_score < -60 → **Strong Bearish**

---

### Confidence Calculation

Confidence measures how well the components agree on the market direction.

```python
def _calculate_confidence(component_scores: list) -> float:
    # Count components by direction
    bullish_count = sum(1 for score in component_scores if score > 20)
    bearish_count = sum(1 for score in component_scores if score < -20)
    neutral_count = len(component_scores) - bullish_count - bearish_count

    # Alignment percentage
    max_agreement = max(bullish_count, bearish_count)
    alignment = (max_agreement / total_components) * 100

    # Penalize for neutral components (uncertainty)
    neutral_penalty = (neutral_count / total_components) * 20

    confidence = alignment - neutral_penalty
    return max(0, min(100, confidence))
```

**Interpretation**:
- **High confidence (60-100%)**: Most components agree on direction
- **Medium confidence (30-60%)**: Mixed signals but some alignment
- **Low confidence (0-30%)**: Components are conflicting or neutral

**Example**:
- All 5 components bullish (>20): 100% confidence
- 4 bullish, 1 neutral: 80% - 20% penalty = 60% confidence
- 3 bullish, 2 bearish: 60% confidence
- All neutral: 0% confidence

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

# Initialize services
with DeribitApiService() as api_service:
    repository = DatabaseRepository()
    service = RegimeDetectionService(api_service, repository)

    # Detect regime
    result = service.detect_regime('BTC')

    # Access results
    print(f"Regime: {result['regime']}")
    print(f"Score: {result['composite_score']}")
    print(f"Confidence: {result['confidence']}%")
    print(f"Reasoning: {result['reasoning']}")

    # Access component scores
    comp_scores = result['component_scores']
    print(f"Trend: {comp_scores['trend']}")
    print(f"Momentum: {comp_scores['momentum']}")
    print(f"On-Chain: {comp_scores['onchain']}")
    print(f"Sentiment: {comp_scores['sentiment']}")
    print(f"Volatility: {comp_scores['volatility']}")
```

---

## API Reference

### RegimeDetectionService

#### `detect_regime(currency: str) -> Dict`

Main entry point for regime detection.

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
        'atr': 2500.0,
        'atr_percentile': 6.9
    },
    'onchain_metrics': {
        'funding_rate': 0.00000,
        'dvol': 54.70,
        'put_call_ratio': 0.71
    },
    'external_metrics': {
        'fear_greed': {
            'value': 25,
            'classification': 'Extreme Fear'
        },
        'btc_dominance': 57.59
    },
    'reasoning': 'Market Regime: Weak Bearish (Score: -34.5) | ...',
    'detection_time_seconds': 1.61
}
```

**Raises**:
- `ValueError`: If currency not supported
- `ConnectionError`: If API connection fails
- `Exception`: If detection fails

---

### MarketRegimeDetector

#### `detect_regime(technical_indicators, onchain_metrics, external_metrics, current_price) -> Dict`

Core detection algorithm.

**Parameters**:
- `technical_indicators` (Dict): Must contain sma_50, sma_200, adx, atr_percentile, rsi, macd, macd_signal, macd_histogram
- `onchain_metrics` (Dict): Must contain funding_rate, put_call_ratio (optional: dvol)
- `external_metrics` (Dict): Must contain fear_greed dict, btc_dominance
- `current_price` (float): Current asset price

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

### TechnicalIndicatorCalculator

#### `calculate_all_indicators(ohlcv_data: list) -> pd.DataFrame`

Calculate technical indicators from OHLCV data.

**Parameters**:
- `ohlcv_data` (list): List of [timestamp, open, high, low, close, volume]

**Returns** (DataFrame):
Columns include: timestamp, open, high, low, close, volume, sma_50, sma_200, ema_12, ema_26, rsi, macd, macd_signal, macd_histogram, adx, atr, atr_percentile

**Raises**:
- `ValueError`: If data format is invalid or insufficient data points

---

## Database Schema

### Tables Created (Migration 003)

#### `ohlcv_history`
Stores historical OHLCV data.

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

#### `technical_indicators`
Stores calculated technical indicators.

```sql
CREATE TABLE technical_indicators (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    sma_50 DECIMAL(20, 8),
    sma_200 DECIMAL(20, 8),
    ema_12 DECIMAL(20, 8),
    ema_26 DECIMAL(20, 8),
    rsi DECIMAL(10, 4),
    macd DECIMAL(20, 8),
    macd_signal DECIMAL(20, 8),
    macd_histogram DECIMAL(20, 8),
    adx DECIMAL(10, 4),
    atr DECIMAL(20, 8),
    atr_percentile DECIMAL(10, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(currency, timestamp)
);
```

#### `funding_rate_history`
Stores perpetual swap funding rates.

```sql
CREATE TABLE funding_rate_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    funding_rate DECIMAL(20, 10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(currency, timestamp)
);
```

#### `volatility_index_history`
Stores DVOL (Deribit Volatility Index) data.

```sql
CREATE TABLE volatility_index_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    timestamp BIGINT NOT NULL,
    dvol DECIMAL(10, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(currency, timestamp)
);
```

#### `external_metrics`
Stores Fear & Greed Index and dominance data.

```sql
CREATE TABLE external_metrics (
    id SERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    fear_greed_value INTEGER,
    fear_greed_classification VARCHAR(50),
    btc_dominance DECIMAL(10, 4),
    eth_dominance DECIMAL(10, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(timestamp)
);
```

#### `regime_detections`
Stores regime detection results.

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

---

## Configuration

### Component Weights

Located in `MarketRegimeDetector.WEIGHTS`:

```python
WEIGHTS = {
    "trend": 0.30,      # 30% - Most important (direction)
    "momentum": 0.25,   # 25% - Second most important (strength)
    "onchain": 0.20,    # 20% - Market positioning
    "sentiment": 0.15,  # 15% - Market psychology
    "volatility": 0.10, # 10% - Least important (risk indicator)
}
```

**Rationale**:
- **Trend** has highest weight because it determines the primary direction
- **Momentum** confirms trend with RSI/MACD
- **On-chain** provides unique insights into market positioning
- **Sentiment** is contrarian indicator (extremes signal reversals)
- **Volatility** is lowest because it indicates risk, not direction

### Regime Thresholds

Located in `MarketRegimeDetector.REGIME_THRESHOLDS`:

```python
REGIME_THRESHOLDS = {
    "Strong Bullish": 60,   # Very bullish conditions
    "Weak Bullish": 30,     # Moderately bullish
    "Sideways": -30,        # Neutral/consolidation
    "Weak Bearish": -60,    # Moderately bearish
    "Strong Bearish": -100, # Very bearish conditions
}
```

**Distribution**:
- Strong regimes: |score| ≥ 60 (strong conviction)
- Weak regimes: 30 ≤ |score| < 60 (moderate conviction)
- Sideways: |score| < 30 (no clear direction)

### API Configuration

#### Deribit API
- Base URL: `https://www.deribit.com/api/v2`
- Authentication: Not required for public endpoints
- Rate limits: Managed by API service layer

#### Alternative.me (Fear & Greed)
- URL: `https://api.alternative.me/fng/`
- Rate limits: Reasonable use (no official limit)
- Timeout: 10 seconds

#### CoinGecko
- URL: `https://api.coingecko.com/api/v3/global`
- Rate limits: 10-50 calls/minute (free tier)
- Timeout: 10 seconds

---

## Examples

### Example 1: Current Market (Weak Bearish)

**Input Data** (2026-01-25):
- Price: $95,432.50 (BTC)
- Death Cross: SMA50 < SMA200
- ADX: 20.7 (weak trend)
- RSI: 37.9 (bearish momentum)
- MACD: Bearish histogram
- ATR Percentile: 6.9 (very low volatility)
- Funding Rate: 0.000% (neutral)
- P/C Ratio: 0.71 (slight call bias)
- Fear & Greed: 25 (Extreme Fear)

**Component Scores**:
- Trend: -50.0 (death cross with weak ADX)
- Volatility: 0.0 (low vol = neutral)
- Momentum: -80.0 (bearish RSI and MACD)
- On-Chain: +10.0 (neutral funding, slight call bias)
- Sentiment: -10.0 (extreme fear = some contrarian bullish)

**Composite Score**: -34.5
- Calculation: (-50 × 0.30) + (0 × 0.10) + (-80 × 0.25) + (10 × 0.20) + (-10 × 0.15)
- = -15.0 + 0.0 - 20.0 + 2.0 - 1.5 = **-34.5**

**Classification**: Weak Bearish (-60 < score < -30)

**Confidence**: 28.0%
- 3 components bearish (trend, momentum, sentiment)
- 1 component bullish (onchain)
- 1 component neutral (volatility)
- Alignment: 60% (3/5)
- Neutral penalty: 20% (1/5)
- Confidence: 60% - 20% + adjustments = 28%

**Reasoning**:
"Market Regime: Weak Bearish (Score: -34.5) | Trend: Death Cross structure (50 SMA < 200 SMA), ADX=20.7 | Momentum: RSI=37.9, MACD=Bearish | Volatility: LOW regime (ATR Percentile=6.9) | On-Chain: Funding=0.000%, P/C Ratio=0.71 | Sentiment: Fear & Greed=25 (Extreme Fear)"

---

### Example 2: Sideways Market

**Input Data**:
- Price: Oscillating around both MAs
- ADX: 15 (no trend)
- RSI: 50 (neutral)
- MACD: Near zero, flat histogram
- ATR Percentile: 30 (normal volatility)
- Funding Rate: 0.002% (neutral zone)
- P/C Ratio: 0.9 (balanced)
- Fear & Greed: 50 (Neutral)

**Component Scores**:
- Trend: 0.0 (mixed MA position, low ADX)
- Volatility: 0.0 (normal vol)
- Momentum: 0.0 (neutral RSI, flat MACD)
- On-Chain: 0.0 (neutral funding, balanced P/C)
- Sentiment: 0.0 (neutral F&G)

**Composite Score**: 0.0

**Classification**: Sideways

**Confidence**: 0% (all components neutral = high uncertainty)

---

### Example 3: Strong Bullish Market

**Input Data**:
- Price: Above both MAs
- Golden Cross: SMA50 > SMA200
- ADX: 45 (very strong trend)
- RSI: 65 (strong momentum, not overbought)
- MACD: Bullish with positive histogram
- ATR Percentile: 40 (normal volatility)
- Funding Rate: +0.015% (longs dominant)
- P/C Ratio: 0.45 (heavy call buying)
- Fear & Greed: 70 (Greed)

**Component Scores**:
- Trend: +75.0 (golden cross with very strong ADX)
- Volatility: 0.0 (normal vol)
- Momentum: +90.0 (strong RSI + bullish MACD)
- On-Chain: +70.0 (strong funding + heavy call bias)
- Sentiment: +40.0 (greed)

**Composite Score**: +70.5
- Calculation: (75 × 0.30) + (0 × 0.10) + (90 × 0.25) + (70 × 0.20) + (40 × 0.15)
- = 22.5 + 0.0 + 22.5 + 14.0 + 6.0 = **+70.5**

**Classification**: Strong Bullish (score ≥ 60)

**Confidence**: 80% (all components bullish or neutral, high alignment)

---

## Troubleshooting

### Common Issues

#### 1. "No schema defined for endpoint"

**Error**:
```
ValueError: No schema defined for endpoint: /public/get_tradingview_chart_data
```

**Cause**: Missing schema definition for TradingView endpoint.

**Solution**: Ensure `coding/core/schemas/deribit_schemas.py` has `TRADINGVIEW_CHART_DATA` schema and is mapped in `get_schema_for_endpoint()`.

---

#### 2. "Shape of passed values is (201, 1), indices imply (201, 6)"

**Error**: OHLCV data format mismatch.

**Cause**: Deribit API returns columnar format `{ticks: [...], open: [...]}` but calculator expects row format `[[timestamp, open, high, low, close, volume], ...]`.

**Solution**: The service layer transforms columnar to row format. If error persists, check `RegimeDetectionService._fetch_ohlcv_data()` transformation logic.

---

#### 3. "TypeError: unsupported format string passed to NoneType"

**Error**: F-string formatting with None value.

**Cause**: Attempting conditional formatting inside f-string: `f"{value:.2f if value else 'N/A'}"`.

**Solution**: Separate string construction:
```python
value_str = f"{value:.2f}" if value is not None else "N/A"
text = f"Value: {value_str}"
```

---

#### 4. Low/Incorrect Confidence Scores

**Symptom**: Confidence always very low (0-20%).

**Cause**: Components are neutral or conflicting.

**Explanation**: This is expected behavior when:
- Market is in consolidation (all components near zero)
- Components strongly disagree (some bullish, some bearish)
- Data quality issues (missing metrics)

**Solution**:
- Check component scores individually
- Verify data fetching (all APIs returning valid data)
- Low confidence is a valid signal (uncertain market)

---

#### 5. Regime Detection Fails

**Error**: "Regime detection failed: [error message]"

**Troubleshooting Steps**:

1. **Check API connectivity**:
   ```python
   # Test Deribit connection
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

3. **Check data availability**:
   - Ensure 200+ days of OHLCV data available
   - Verify currency is supported (BTC or ETH only)

4. **Check database connection**:
   ```python
   from coding.core.database.repository import DatabaseRepository
   repo = DatabaseRepository()
   # Should not raise error
   ```

---

#### 6. ATR Percentile Always 0 or 100

**Symptom**: ATR percentile stuck at extreme values.

**Cause**: Insufficient data points or calculation error.

**Solution**:
- Ensure at least 50 data points for meaningful percentiles
- Check `TechnicalIndicatorCalculator` is using global rank:
  ```python
  df["atr_percentile"] = df["atr"].rank(pct=True) * 100
  ```

---

### Performance Issues

#### Slow Detection (>5 seconds)

**Causes**:
1. External API timeouts (Alternative.me or CoinGecko)
2. Large OHLCV dataset
3. Network latency

**Solutions**:
- Increase timeout values in external API classes
- Use cached data for repeated detections
- Run detection in background thread (GUI already does this)

---

### Data Quality Issues

#### Stale or Incorrect Data

**Symptoms**:
- Price doesn't match current market
- Funding rate is outdated
- P/C ratio seems wrong

**Solutions**:

1. **Check data freshness**:
   - OHLCV data should be within 1 day of current time
   - Funding rate should update every 8 hours
   - P/C ratio should use current book summary

2. **Verify data sources**:
   - Deribit API status: https://status.deribit.com/
   - Alternative.me API status: Check website
   - CoinGecko API status: Check website

3. **Clear cached data** (if caching is implemented):
   ```python
   # Force fresh data fetch
   result = service.detect_regime('BTC')
   ```

---

## Limitations and Considerations

### Current Limitations

1. **Supported Assets**: Only BTC and ETH
   - Other assets would need:
     - Sufficient OHLCV history
     - Perpetual swap for funding rate
     - Options market for P/C ratio

2. **Timeframe**: Daily resolution only
   - Designed for swing trading/position sizing
   - Not suitable for intraday trading

3. **External Dependencies**: Requires 3 external APIs
   - Deribit (critical)
   - Alternative.me (can function without)
   - CoinGecko (can function without)

4. **Historical Context**: No backtesting framework
   - Detections are point-in-time
   - No historical accuracy tracking (yet)

5. **Market Conditions**: Optimized for trending markets
   - May give false signals in choppy consolidation
   - Low confidence scores during uncertain periods

### Best Practices

1. **Don't rely on a single detection**:
   - Run multiple detections over days
   - Look for regime persistence
   - Consider confidence levels

2. **Combine with other analysis**:
   - Use regime as context, not sole decision factor
   - Consider fundamental analysis
   - Check on-chain metrics directly

3. **Understand the components**:
   - Know what each component measures
   - Identify which components matter most for your strategy
   - Check component scores individually, not just composite

4. **Monitor confidence**:
   - High confidence = clear market direction
   - Low confidence = wait for clarity
   - Don't trade on low-confidence signals

5. **Adapt to market evolution**:
   - Thresholds may need adjustment over time
   - Market regimes can change rapidly
   - Extreme events may break the model

---

## Future Enhancements

### Potential Improvements

1. **Volatility-Trend Interaction**:
   - High volatility in bull market ≠ high volatility in bear market
   - Context-aware volatility scoring

2. **Machine Learning Integration**:
   - Train model on historical regime transitions
   - Predict regime changes before they happen
   - Adaptive weight adjustment

3. **More Assets**:
   - Add SOL, ADA, DOT if data available
   - Unified "crypto market regime"
   - Individual asset regime vs market regime

4. **Backtesting Framework**:
   - Historical regime accuracy tracking
   - Performance metrics by regime
   - Regime transition analysis

5. **Alerts and Notifications**:
   - Regime change alerts
   - Confidence threshold alerts
   - Component divergence warnings

6. **Advanced Sentiment**:
   - Social media sentiment (Twitter, Reddit)
   - News sentiment analysis
   - Whale transaction monitoring

---

## Conclusion

The Market Regime Detection System provides a comprehensive, multi-factor approach to classifying cryptocurrency market conditions. By combining technical analysis, on-chain metrics, and sentiment indicators, it offers a robust framework for understanding the current market state.

**Key Takeaways**:
- 5 components with 30/25/20/15/10 weighting
- Composite score from -100 to +100
- 5 regime classifications (Strong Bullish → Strong Bearish)
- Confidence scoring based on component alignment
- Real-time detection with detailed reasoning

**Use Cases**:
- Position sizing (increase exposure in bullish regimes)
- Risk management (reduce exposure in bearish regimes)
- Strategy selection (trend-following vs mean-reversion)
- Market context for trading decisions

**Remember**: This is a tool for market analysis, not a trading signal. Always combine with fundamental analysis, risk management, and your own judgment.

---

## References

### Internal Documentation
- `GUI_DOCUMENTATION.md` - GUI usage guide
- `database_tab_on_chain_analysis.md` - On-chain analysis details

### External Resources
- Deribit API: https://docs.deribit.com/
- Alternative.me API: https://alternative.me/crypto/fear-and-greed-index/
- CoinGecko API: https://www.coingecko.com/en/api
- pandas-ta Documentation: https://github.com/twopirllc/pandas-ta

### Academic References
- Moving Average Crossovers: Classic technical analysis
- RSI/MACD: Wilder, J. Welles (1978). "New Concepts in Technical Trading Systems"
- ATR/ADX: Wilder, J. Welles (1978)
- Funding Rates: Perpetual swap mechanics (BitMEX whitepaper)
- Fear & Greed Index: CNN Fear & Greed Index adapted for crypto

---

**Version**: 1.0
**Last Updated**: 2026-01-25
**Maintained By**: Options Trading Platform Team
