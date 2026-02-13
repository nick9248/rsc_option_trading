# Market Regime Tab Documentation

## Overview

The Market Regime tab provides real-time market regime detection and visualization. It analyzes market conditions and classifies the current regime as Bullish, Bearish, or Sideways with varying strength levels.

## Purpose

- Detect current market regime for selected currency
- Display confidence scores and contributing factors
- Show historical regime transitions
- Export regime detection results
- Save regime data to database for strategy evaluation

## Features

### 1. Currency Selection
- Select currency (BTC or ETH)
- Real-time regime detection for selected asset

### 2. Regime Detection
- Click "Detect Regime" to analyze current market
- Classifies regime into 9 categories:
  - **Strong Bullish**
  - **Moderate Bullish**
  - **Weak Bullish**
  - **Strong Bearish**
  - **Moderate Bearish**
  - **Weak Bearish**
  - **Sideways**
  - **Choppy** (high volatility sideways)
  - **Ranging** (low volatility sideways)

### 3. Confidence Score
- 0-100% confidence in detected regime
- Based on alignment of multiple indicators
- Higher confidence = stronger signal

### 4. Contributing Factors Display
- **Trend Score**: Price trend direction and strength
- **Volatility Score**: ATR percentile and volatility regime
- **Momentum Score**: RSI, ADX, directional movement
- **On-Chain Score**: Put/call ratio, GEX/DEX positioning
- **Sentiment Score**: Fear/greed index, funding rate

### 5. Market Indicators
- Current Price
- SMA 50 / SMA 200
- ADX (trend strength)
- ATR Percentile
- RSI
- Funding Rate
- Put/Call Ratio
- Fear & Greed Index

### 6. Reasoning Display
- Natural language explanation of regime classification
- Lists key factors contributing to the regime
- Highlights divergences or conflicting signals

### 7. Export Functionality
- Save detection results to text file
- Default filename: `regime_detection_{currency}_{timestamp}.txt`
- Saved to `output/data/` directory

### 8. Database Save
- Save regime detection to `regime_detections` table
- Enables historical regime tracking
- Used by strategy evaluation for regime-aware scoring

## Architecture

```
Market Regime Tab (GUI)
    ↓
RegimeDetectionService (Service Layer)
    ↓
DeribitApiService + DatabaseRepository (Core Layer)
```

**Service**: `coding/service/regime/regime_detection_service.py`
**GUI**: `coding/gui/tabs/regime_tab.py`
**Repository**: `coding/core/database/repository.py`

For detailed regime detection methodology, see: `documentation/feature_market_regime_detection.md`

## Usage

1. Select currency (ETH or BTC)
2. Click "Detect Regime"
3. View detected regime and confidence score
4. Review contributing factors and market indicators
5. Read reasoning for classification
6. Click "Export" to save report
7. Regime is automatically saved to database

## Sample Output

```
================================================================================
MARKET REGIME DETECTION - ETH
================================================================================
Detected At: 2026-02-08 17:30:15

REGIME: Moderate Bullish
CONFIDENCE: 78.5%

CONTRIBUTING FACTORS:
  Trend Score: 72.3% (Bullish)
  Volatility Score: 65.8% (Elevated)
  Momentum Score: 81.2% (Strong Bullish)
  On-Chain Score: 68.4% (Neutral-Bullish)
  Sentiment Score: 75.6% (Greedy)

MARKET INDICATORS:
  Current Price: $2,906.50
  SMA 50: $2,765.30
  SMA 200: $2,634.80
  ADX: 28.4 (Trending)
  ATR Percentile: 65.8%
  RSI: 62.3 (Mild Overbought)
  Funding Rate: 0.012% (Positive)
  Put/Call Ratio: 0.845 (Bullish)
  Fear & Greed: 68 (Greed)

REASONING:
Price is trending above both SMA 50 and SMA 200, indicating bullish trend.
ADX shows moderate trend strength. RSI is mildly overbought but not extreme.
On-chain metrics show balanced positioning with slight bullish bias.
Sentiment indicators show greed but not excessive.
Overall: Moderate bullish regime with room for continuation.
```

## Regime Classification Logic

### Bullish Regimes
- **Strong**: Trend > 80%, Momentum > 75%, RSI < 70, all indicators aligned
- **Moderate**: Trend > 60%, Momentum > 60%, most indicators aligned
- **Weak**: Trend > 40%, some bullish signals but mixed indicators

### Bearish Regimes
- **Strong**: Trend < 20%, Momentum < 25%, RSI > 30, all indicators aligned
- **Moderate**: Trend < 40%, Momentum < 40%, most indicators aligned
- **Weak**: Trend < 60%, some bearish signals but mixed indicators

### Sideways Regimes
- **Sideways**: ADX < 25, price between SMA 50 and 200
- **Choppy**: High volatility (ATR > 70th percentile) + no clear trend
- **Ranging**: Low volatility (ATR < 30th percentile) + no clear trend

## Use Cases

### 1. Strategy Selection
- Detect current regime before evaluating strategies
- Use bullish strategies in bullish regimes
- Use bearish strategies in bearish regimes
- Avoid directional strategies in sideways regimes

### 2. Risk Management
- Reduce position size in choppy markets
- Increase size in strong trending regimes
- Use tighter stops in weak regimes

### 3. Historical Analysis
- Export regime detections over time
- Correlate regime changes with PnL
- Backtest strategy performance by regime

### 4. Alert System (Future)
- Set alerts for regime transitions
- Get notified when confidence drops below threshold
- Monitor regime stability

## Important Notes

### Data Requirements
- Requires historical price data for SMA calculations
- On-chain metrics from Deribit API
- External sentiment data (Fear & Greed index)

### Update Frequency
- Regime detection is on-demand (not real-time)
- Click "Detect Regime" to get latest classification
- Recommended: Check regime before major trading decisions

### Confidence Interpretation
- **80-100%**: High confidence, strong signal
- **60-80%**: Moderate confidence, generally reliable
- **40-60%**: Low confidence, mixed signals (use caution)
- **< 40%**: Very low confidence, wait for clarity

### Database Integration
- Each detection is saved to `regime_detections` table
- Strategy evaluation queries latest regime for scoring
- Historical regime data enables backtesting

## Future Enhancements

- Real-time regime monitoring with alerts
- Multi-timeframe regime analysis
- Regime transition probability forecasts
- ML-based regime classification
- Backtesting regime-based strategies
- Regime stability metrics
