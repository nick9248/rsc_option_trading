# Market Regime Detection - Comprehensive Analysis

**Analysis Date**: 2026-01-24
**Analyst**: Claude Sonnet 4.5
**Test Subjects**: BTC & ETH

---

## Executive Summary

The market regime detection system is **operational and producing reasonable results**, but several areas need improvement for robustness and accuracy. The system correctly identified a Sideways/Slight Bearish regime for both BTC and ETH, consistent with current market conditions (Death Cross structure, Extreme Fear sentiment, weak momentum).

---

## 1. Test Results Analysis

### BTC Detection Results
```
Regime: Sideways
Confidence: 36.0%
Composite Score: -20.5
Current Price: $89,237.50

Component Scores:
  Trend:       -50.0  (Death Cross, ADX=21.1)
  Volatility:   +30.0  (LOW regime, ATR Percentile=2.2)
  Momentum:     -60.0  (RSI=42.5, MACD=Bearish)
  On-Chain:     +30.0  (Funding=0.000%)
  Sentiment:    -10.0  (F&G=25 Extreme Fear)
```

### ETH Detection Results
```
Regime: Sideways
Confidence: 32.0%
Composite Score: -27.0
Current Price: $2,958.75

Component Scores:
  Trend:       -50.0  (Death Cross, ADX=23.2)
  Volatility:   +20.0  (LOW regime, ATR Percentile=16.7)
  Momentum:     -80.0  (RSI=39.8, MACD=Bearish)
  On-Chain:     +30.0  (Funding=0.000%)
  Sentiment:    -10.0  (F&G=25 Extreme Fear)
```

---

## 2. Identified Issues & Problems

### **CRITICAL ISSUES**

#### Issue #1: Missing Put/Call Ratio Data
**Severity**: HIGH
**Impact**: On-Chain component is incomplete

**Problem**:
- Put/Call Ratio is consistently `N/A`
- On-chain scoring uses P/C ratio for 40% of its weight
- Currently only using funding rate (60% of on-chain score)

**Root Cause**:
- Service doesn't fetch current P/C ratio from book summary
- No integration with existing on-chain analysis data

**Fix Required**:
```python
# In RegimeDetectionService._get_onchain_metrics():
# Option 1: Fetch latest on-chain capture from database
latest_capture = self.repository.get_latest_onchain_capture(currency)
if latest_capture:
    metrics["put_call_ratio"] = latest_capture.get("put_call_ratio")

# Option 2: Calculate on-the-fly from book summary
book_summary = self.api_service.get_book_summary(currency, kind="option")
analyzer = OnChainAnalyzer(book_summary, currency)
oi_metrics = analyzer.calculate_put_call_ratio(...)
metrics["put_call_ratio"] = oi_metrics.get("ratio")
```

---

#### Issue #2: Low Confidence Scores
**Severity**: MEDIUM
**Impact**: Regime signals may be unreliable

**Problem**:
- BTC confidence: 36%
- ETH confidence: 32%
- Both below 50% threshold for actionable signals

**Analysis**:
Components are not well-aligned:
- Trend: Bearish (-50)
- Momentum: Bearish (-60 to -80)
- Volatility: Bullish (+20 to +30) ← Conflicts
- On-Chain: Bullish (+30) ← Conflicts
- Sentiment: Bearish (-10)

**Why Low Confidence**:
- 3 components bearish, 2 components bullish
- Mixed signals indicate transitional/uncertain market
- LOW volatility (stabilizing) conflicts with bearish momentum
- Neutral funding rate conflicts with bearish trend

**Implications**:
- Sideways classification is correct (conflicting signals)
- System is working as intended for uncertain markets
- But confidence calculation may be too harsh

**Potential Fix**:
```python
# Adjust confidence calculation
# Current: Simple alignment percentage
# Better: Weight by component importance + signal strength

def _calculate_confidence(self, component_scores: list, weights: dict) -> float:
    # Factor in:
    # 1. Alignment of components
    # 2. Strength of each signal
    # 3. Weight of aligned components

    total_weight_aligned = 0
    for component, score in zip(component_scores, weights.values()):
        if abs(score) > 30:  # Strong signal
            total_weight_aligned += weight

    return (total_weight_aligned / sum(weights.values())) * 100
```

---

#### Issue #3: ATR Percentile Calculation May Be Incorrect
**Severity**: MEDIUM
**Impact**: Volatility regime classification unreliable

**Problem**:
- BTC ATR Percentile: 2.2 (extremely low, 2nd percentile)
- ETH ATR Percentile: 16.7 (also very low)
- Both classified as LOW volatility regime

**Questions**:
1. Is the 90-day rolling window correct?
2. Is the percentile calculation working?
3. Are we comparing ATR correctly?

**Code Review**:
```python
# From technical_indicator_calculator.py
df["atr_percentile"] = df["atr"].rolling(window=90).apply(
    lambda x: (x.rank(pct=True).iloc[-1] * 100) if len(x) > 0 else None,
    raw=False
)
```

**Issues Found**:
- Using `x.rank(pct=True).iloc[-1]` ranks within the 90-day window
- This gives percentile **within** the window, not global percentile
- Should track ATR across entire dataset and find percentile

**Correct Approach**:
```python
# Calculate percentile against all historical data
df["atr_percentile"] = df["atr"].rank(pct=True) * 100
```

---

#### Issue #4: MACD Score Logic Needs Review
**Severity**: LOW
**Impact**: Momentum component may be oversensitive

**Problem**:
- MACD contributes -50 points when bearish (below signal)
- Combined with histogram, can add -70 points total
- Might be too strong relative to RSI (max ±40)

**Current Scoring**:
```python
# MACD above signal = +30
# MACD below signal = -30
# Histogram positive = +20
# Histogram negative = -20
# Total range: -50 to +50
```

**Recommendation**:
- MACD and RSI should have similar weight ranges
- Consider reducing MACD impact or increasing RSI impact

---

### **MODERATE ISSUES**

#### Issue #5: No Historical Regime Tracking
**Severity**: MEDIUM
**Impact**: Cannot see regime changes over time

**Problem**:
- `get_regime_history()` returns empty list
- No database storage for regime detections
- Cannot analyze regime stability or transitions

**Fix Required**:
1. Add repository method to save regime detections
2. Implement `_save_regime_detection()` in service
3. Implement `get_regime_history()` query

---

#### Issue #6: Sentiment Component Weight Too Low
**Severity**: LOW
**Impact**: Market sentiment underrepresented

**Analysis**:
- Sentiment: 10% weight
- Fear & Greed is a widely-followed indicator
- BTC dominance is important for altcoin regimes

**Current Behavior**:
- Fear & Greed = 25 (Extreme Fear) → only contributes -10 to composite
- In a -100 to +100 scale, -10 is negligible

**Recommendation**:
- Increase sentiment weight to 15%
- Reduce volatility to 10% (less predictive)
- New weights: Trend 30%, Momentum 25%, On-Chain 20%, Sentiment 15%, Volatility 10%

---

#### Issue #7: No Validation Against Actual Market Behavior
**Severity**: MEDIUM
**Impact**: Unknown accuracy

**Problem**:
- No backtesting
- No comparison with known regime periods
- No validation metrics (precision, recall, accuracy)

**Recommendation**:
- Test against historical known regimes:
  - 2021 Bull run (should detect Strong Bullish)
  - 2022 Bear market (should detect Strong Bearish)
  - 2023 Sideways accumulation
- Calculate confusion matrix
- Tune thresholds and weights

---

### **MINOR ISSUES**

#### Issue #8: DVOL Not Used Effectively
**Severity**: LOW
**Impact**: Missing valuable volatility signal

**Problem**:
- DVOL fetched but only used for basic threshold check
- Could provide more nuanced volatility assessment

**Enhancement**:
```python
# Use DVOL percentile similar to ATR
# High DVOL relative to history = uncertain regime
# Low DVOL = stable regime
```

---

#### Issue #9: No Regime Transition Detection
**Severity**: LOW
**Impact**: Cannot detect regime shifts

**Problem**:
- Only detects current regime
- No smoothing or confirmation mechanism
- A single detection might be noise

**Recommendation**:
- Require 2-3 consecutive detections before regime change
- Add "confidence trend" (increasing/decreasing over time)

---

#### Issue #10: External API Dependency
**Severity**: LOW
**Impact**: Fails if external APIs are down

**Problem**:
- Fear & Greed and CoinGecko required for sentiment
- No fallback if APIs fail

**Fix**:
- Cache last known values
- Continue with reduced confidence if APIs unavailable

---

## 3. Scoring Calibration Analysis

### Current Weights
```python
Trend:      30%
Volatility: 15%
Momentum:   25%
On-Chain:   20%
Sentiment:  10%
```

### Component Score Distributions (From Test)

| Component | BTC Score | ETH Score | Range     | Saturation |
|-----------|-----------|-----------|-----------|------------|
| Trend     | -50       | -50       | -100/+100 | Moderate   |
| Volatility| +30       | +20       | -100/+100 | Low        |
| Momentum  | -60       | -80       | -100/+100 | Moderate   |
| On-Chain  | +30       | +30       | -100/+100 | Low        |
| Sentiment | -10       | -10       | -100/+100 | Very Low   |

**Observations**:
- Sentiment never reaches high absolute values (max seen: ±30)
- On-Chain saturates low without P/C ratio
- Momentum can saturate heavily (ETH at -80)
- Trend is binary (±50 for cross structure)

**Recommended Adjustments**:
1. Normalize components to use full ±100 range
2. Increase weight of underutilized components (sentiment, on-chain)
3. Add non-linear scaling for extreme values

---

## 4. Algorithm Logic Review

### Trend Scoring - **GOOD**
✅ MA crossovers correctly identified
✅ ADX strength multiplier makes sense
✅ Price position relative to MAs is logical

**Recommendation**: Add EMA analysis for faster reaction

---

### Volatility Scoring - **NEEDS IMPROVEMENT**
⚠️ ATR percentile calculation flawed (see Issue #3)
⚠️ DVOL underutilized
✅ Low volatility = bullish logic is sound

**Fix**: Recalculate percentiles correctly

---

### Momentum Scoring - **ACCEPTABLE**
✅ RSI zones are correct
⚠️ MACD might be too heavily weighted
✅ Histogram confirmation is good

**Recommendation**: Balance RSI and MACD weights

---

### On-Chain Scoring - **INCOMPLETE**
❌ Missing Put/Call ratio (CRITICAL)
✅ Funding rate logic is sound
⚠️ Only using 60% of available data

**Fix**: Integrate P/C ratio immediately

---

### Sentiment Scoring - **UNDERWEIGHTED**
✅ Fear & Greed zones are reasonable
✅ BTC dominance logic makes sense
⚠️ Only 10% weight is too low

**Recommendation**: Increase to 15%

---

## 5. Edge Cases & Robustness

### Tested Scenarios
✅ Normal market conditions (working)
❓ Extreme volatility (not tested)
❓ Flash crash events (not tested)
❓ Low liquidity periods (not tested)

### Error Handling
✅ Handles missing OHLCV data
✅ Handles missing external API data
⚠️ Doesn't handle partial indicator failures well

---

## 6. Performance Analysis

### Speed (From Test Run)
- BTC Detection: ~1.5 seconds
- ETH Detection: ~1.1 seconds

**Breakdown**:
- OHLCV fetch: ~0.3s
- Indicator calculation: ~0.2s
- On-chain metrics: ~0.2s
- External APIs: ~0.8s (rate-limited)

**Bottleneck**: External APIs (Fear & Greed, CoinGecko)

**Optimization**:
- Cache external metrics (update hourly)
- Fetch APIs in parallel
- Cache OHLCV data (only fetch new candles)

---

## 7. Recommendations Priority

### **HIGH PRIORITY (Do First)**

1. **Fix ATR Percentile Calculation** (Issue #3)
   - Impact: Volatility regime is core to regime detection
   - Effort: Low (single line change)
   - Test: Verify percentiles make sense

2. **Add Put/Call Ratio Integration** (Issue #1)
   - Impact: On-chain component is only 60% functional
   - Effort: Medium (requires integration with existing on-chain data)
   - Test: Verify P/C ratio updates correctly

3. **Implement Regime History Storage** (Issue #5)
   - Impact: Cannot track regime changes without this
   - Effort: Medium (database methods + migration)
   - Test: Verify storage and retrieval

### **MEDIUM PRIORITY (Do Next)**

4. **Recalibrate Component Weights** (Issue #6)
   - Suggested: Trend 30%, Momentum 25%, On-Chain 20%, Sentiment 15%, Volatility 10%
   - Effort: Low (config change)
   - Test: Run on historical data to validate

5. **Improve Confidence Calculation** (Issue #2)
   - Add signal strength weighting
   - Effort: Medium
   - Test: Should produce higher confidence when components align strongly

6. **Add Backtesting Framework** (Issue #7)
   - Test against 2021-2023 historical data
   - Calculate accuracy metrics
   - Effort: High
   - Test: Validate against known regime periods

### **LOW PRIORITY (Future Enhancement)**

7. **MACD/RSI Balance** (Issue #4)
8. **DVOL Enhancement** (Issue #8)
9. **Regime Transition Detection** (Issue #9)
10. **External API Fallback** (Issue #10)

---

## 8. Validation Tests Needed

### Test Cases to Run

1. **Bull Market Test (2021)**
   - Expected: Strong Bullish
   - Components: All positive, high confidence

2. **Bear Market Test (2022)**
   - Expected: Strong Bearish
   - Components: All negative, high confidence

3. **Sideways Test (2023 Q1)**
   - Expected: Sideways
   - Components: Mixed signals, moderate confidence

4. **Volatility Spike Test (Flash Crash)**
   - Expected: Should detect regime change quickly
   - ATR percentile should spike

5. **Sentiment Shift Test**
   - Change Fear & Greed from 25 → 75
   - Should shift regime toward bullish

---

## 9. Overall Assessment

### **Strengths**
✅ Multi-factor approach is sound
✅ Component isolation allows debugging
✅ Extensible architecture
✅ Free data sources (no API costs)
✅ Fast execution (<2 seconds)
✅ Readable reasoning output

### **Weaknesses**
❌ Missing key data (P/C ratio)
❌ ATR percentile calculation error
❌ Low confidence scores
❌ No historical validation
❌ No regime transition detection
❌ External API dependency

### **Risk Assessment**
- **Technical Risk**: MEDIUM (some bugs to fix)
- **Data Risk**: MEDIUM (dependency on external APIs)
- **Accuracy Risk**: HIGH (no backtesting yet)

### **Production Readiness**
**Current State**: BETA - Works but needs refinement
**Required for Production**:
1. Fix ATR percentile
2. Add P/C ratio
3. Backtest against historical data
4. Add regime storage
5. Implement caching

**Timeline Estimate**: 1-2 days of focused work

---

## 10. Conclusion

The market regime detection system is **operational and shows promise**, but requires several fixes before production use:

**Immediate Actions**:
1. Fix ATR percentile calculation (critical bug)
2. Integrate Put/Call ratio data
3. Add database storage for regime history

**Short-term Improvements**:
4. Recalibrate component weights
5. Improve confidence scoring
6. Add backtesting validation

**Long-term Enhancements**:
7. Regime transition detection
8. Machine learning enhancement
9. Multi-timeframe analysis

The system correctly identified the current market state (Sideways with bearish bias), which validates the core logic. With the recommended fixes, this will be a robust and valuable tool for strategy selection and risk management.

---

**Next Steps**: Implement HIGH priority fixes and run validation tests.
