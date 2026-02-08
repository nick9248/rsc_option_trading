# Strategy System - Complete Documentation

**Last Updated**: January 18, 2026
**Version**: 1.0
**System**: Options Strategy Evaluation and Scoring

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Scoring System](#scoring-system)
4. [GUI Usage Guide](#gui-usage-guide)
5. [Programmatic Usage](#programmatic-usage)
6. [Interpreting Results](#interpreting-results)
7. [Best Practices](#best-practices)
8. [Troubleshooting](#troubleshooting)
9. [Calculation Examples](#calculation-examples)

---

## Overview

The strategy evaluation system automatically scores and ranks option strategies based on **intrinsic metrics** (strategy structure and risk profile) and **on-chain metrics** (market positioning and sentiment).

### Key Features

- **Automated Scoring**: 0-10 scale for objective strategy comparison
- **Multi-Factor Analysis**: Combines 11 different metrics across intrinsic and on-chain categories
- **Real-Time Data**: Fetches live market data from Deribit API
- **GEX/DEX Analysis**: Automatic gamma and delta exposure calculation
- **Trend Detection**: Historical pattern analysis for max pain and volume
- **Market Regime Awareness**: Adjusts scores based on bullish/bearish environment
- **Multi-Strategy Evaluation**: Evaluate multiple strategies across multiple expirations simultaneously
- **Comprehensive Reports**: Detailed text files with all metrics and score breakdowns

### Supported Strategies

Currently implemented:
- **Long Call** (Directional Bullish)
- **Long Put** (Directional Bearish)

Easily extensible - add new strategies by inheriting from `BaseStrategy` class.

---

## System Architecture

The system follows a strict **layered architecture**:

### Layer Structure

```
┌─────────────────────────────────────────┐
│         GUI Layer                       │
│  - Strategy Tab (UI only)               │
│  - No business logic                    │
│  - Calls services via workers           │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│         Service Layer                   │
│  - StrategyEvaluationService            │
│  - StrategyFinderService                │
│  - Orchestrates API calls               │
│  - Coordinates analysis                 │
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│         Core Layer                      │
│  - BaseStrategy, LongCall, LongPut      │
│  - IntrinsicScorer, OnChainScorer       │
│  - StrategySignal, StrategyConfig       │
│  - Pure definitions and calculations    │
└─────────────────────────────────────────┘
```

### Directory Structure

```
coding/
├── core/
│   └── strategy/
│       ├── definitions/          # Strategy classes
│       │   ├── base_strategy.py
│       │   ├── long_call.py
│       │   └── long_put.py
│       ├── models/               # Data models
│       │   ├── strategy_signal.py
│       │   └── strategy_config.py
│       ├── scoring/              # Scoring logic
│       │   ├── intrinsic_scorer.py
│       │   ├── on_chain_scorer.py
│       │   └── composite_scorer.py
│       └── report_generator.py   # Text report generation
│
├── service/
│   └── strategy/
│       ├── strategy_evaluation_service.py
│       └── strategy_finder_service.py
│
└── gui/
    └── tabs/
        └── strategy_tab.py       # UI only, no business logic

output/
└── strategies/
    └── {expiration}/
        └── {currency}_{strategy}_{timestamp}.txt
```

### Data Flow

1. **User Input** → GUI collects configuration (strategy, expiration, strike method)
2. **Worker Thread** → Background thread calls service to avoid UI blocking
3. **Service Layer** → Fetches ticker data and market context from API
4. **Core Layer** → Builds strategy, calculates greeks, runs scoring
5. **Results** → Returns `EvaluationResult` with ranked `StrategySignal` objects
6. **Display** → GUI shows table, service generates detailed text report

---

## Scoring System

All scores are on a **0-10 scale**:
- **0-4**: Poor/Unfavorable - Avoid these strategies
- **4-6**: Neutral - Marginal opportunities, proceed with caution
- **6-8**: Good - Solid opportunities worth considering
- **8-10**: Excellent - Strong opportunities with favorable conditions

### Composite Score Formula

```
Composite Score = (Intrinsic Score × 50%) + (On-Chain Score × 50%)
```

Default weights are 50/50, but can be customized in `StrategyConfig`.

### Intrinsic Scorer (50% weight)

Evaluates the strategy's internal characteristics independent of market conditions.

**Component Weights (sum to 100%):**

| Component | Weight | Purpose |
|-----------|--------|---------|
| Risk/Reward Ratio | 30% | Profit potential vs risk |
| Cost Efficiency | 20% | Premium cost relative to underlying |
| Greek Profile | 20% | Greeks alignment with strategy type |
| Breakeven Distance | 15% | Distance to profitability |
| **Strike Moneyness** | **15%** | **Optimal OTM positioning** |

---

#### 1. Risk/Reward Ratio (30%)

**Formula for Unlimited Profit (Long Call):**
```python
risk_pct = (max_risk / underlying_price) × 100
score = normalize(20.0 - risk_pct, min=0.0, max=20.0)
```

**Formula for Limited Profit (spreads):**
```python
ratio = max_profit / max_risk
score = normalize(ratio, min=0.5, max=3.0)
```

**Interpretation:**
- **Score 10**: Risk ≤ 1% of underlying (very efficient) OR 3:1+ reward/risk
- **Score 8**: Risk 1-2% of underlying OR 2:1 reward/risk
- **Score 6**: Risk 2-5% OR 1.5:1 reward/risk
- **Score 4**: Risk 5-10% OR 1:1 reward/risk
- **Score 0**: Risk > 10% OR < 0.5:1 reward/risk

**Example:**
```
Long Call: Premium $58.35, Underlying $3,334.42
risk_pct = ($58.35 / $3,334.42) × 100 = 1.75%
score = normalize(20.0 - 1.75, min=0.0, max=20.0) = 9.12/10
```

---

#### 2. Cost Efficiency (20%)

**Formula:**
```python
max_loss_pct = (max_risk / underlying_price) × 100
score = normalize(10.0 - max_loss_pct, min=0.0, max=10.0)
```

**Interpretation:**
- **Score 10**: Premium ≤ 1% of underlying (very cheap)
- **Score 8**: Premium 1-2% (reasonable cost)
- **Score 6**: Premium 3-5% (moderate cost)
- **Score 4**: Premium 5-7% (expensive)
- **Score 0**: Premium > 10% (very expensive)

**Example:**
```
Premium $58.35, Underlying $3,334.42
max_loss_pct = 1.75%
score = normalize(10.0 - 1.75, min=0.0, max=10.0) = 8.25/10
```

---

#### 3. Greek Profile (20%)

**Formula for Directional Bullish (Long Call):**
```python
delta = net_delta
score = normalize(delta, min=0.0, max=1.0)
```

**Formula for Directional Bearish (Long Put):**
```python
delta = net_delta  # Negative value
score = normalize(-delta, min=0.0, max=1.0)
```

**Interpretation:**
- **Score 10**: |Delta| = 1.0 (deep ITM, acts like stock)
- **Score 7**: |Delta| = 0.70 (ITM, strong directional exposure)
- **Score 5**: |Delta| = 0.50 (ATM, balanced)
- **Score 3**: |Delta| = 0.30 (OTM, moderate exposure)
- **Score 1**: |Delta| = 0.10 (far OTM, lottery ticket)

**Example:**
```
Long Call: Delta = 0.3148
score = normalize(0.3148, min=0.0, max=1.0) = 3.15/10
```

**Trade-off**: Higher delta = higher score but also higher premium (lower cost efficiency).

---

#### 4. Breakeven Distance (15%)

**Formula:**
```python
# For single breakeven (long call/put)
distance_pct = |breakeven - underlying| / underlying × 100
score = normalize(20.0 - distance_pct, min=0.0, max=20.0)
```

**For Long Call:**
```
Breakeven = Strike + Premium
```

**For Long Put:**
```
Breakeven = Strike - Premium
```

**Interpretation:**
- **Score 10**: Breakeven < 5% away (very achievable)
- **Score 8**: Breakeven 5-7% away (reasonable)
- **Score 6**: Breakeven 7-10% away (moderate move needed)
- **Score 4**: Breakeven 10-15% away (significant move)
- **Score 0**: Breakeven > 20% away (unlikely)

**Example:**
```
Long Call: Strike $3,500, Premium $58.35, Underlying $3,334.42
Breakeven = $3,500 + $58.35 = $3,558.35
distance_pct = |$3,558.35 - $3,334.42| / $3,334.42 × 100 = 6.72%
score = normalize(20.0 - 6.72, min=0.0, max=20.0) = 6.64/10
```

---

#### 5. Strike Moneyness (15%) **NEW**

**Formula for Directional Strategies:**
```python
# For calls: OTM when strike > underlying
moneyness_pct = (strike - underlying) / underlying × 100

# For puts: OTM when strike < underlying
moneyness_pct = (underlying - strike) / underlying × 100
```

**Scoring Bands for Directional Strategies:**
```
Moneyness %   | Score | Description
--------------+-------+----------------------------------
< -5%         | 4.0   | Deep ITM - expensive, low leverage
-5% to 0%     | 6.0   | Slightly ITM - good delta, expensive
0% to 3%      | 8.0   | Near ATM - balanced
3% to 10%     | 10.0  | OPTIMAL - best cost/probability
10% to 20%    | 7.0   | Moderately OTM - cheaper, lower prob
20% to 30%    | 4.0   | Far OTM - very cheap, low prob
> 30%         | 2.0   | Very far OTM - lottery ticket
```

**Interpretation:**
- **Optimal Zone (3-10% OTM)**: Best balance of:
  - Cost efficiency (cheaper than ATM)
  - Probability (not too far out)
  - Leverage (meaningful move captures value)
- **Near ATM (0-3%)**: Higher cost but safer
- **Far OTM (>10%)**: Cheap but requires large move

**Example:**
```
Long Call: Strike $3,500, Underlying $3,334.42
moneyness_pct = ($3,500 - $3,334.42) / $3,334.42 × 100 = 4.97%

Since 3% ≤ 4.97% < 10%:
    score = 10.0/10  ✓ OPTIMAL positioning
```

**Why This Matters:**
- Previous system indirectly considered strike distance through delta and breakeven
- **NEW**: Explicit scoring rewards the statistically optimal 3-10% OTM range
- This is where most profitable directional trades occur (per market research)

---

### On-Chain Scorer (50% weight)

Evaluates market conditions and positioning to predict directional bias.

**Component Weights (sum to 100%):**

| Component | Weight | Purpose |
|-----------|--------|---------|
| Max Pain Alignment | 20% | Dealer hedging dynamics |
| GEX/DEX Support | 20% | Gamma/delta exposure levels |
| OI Levels | 15% | Open interest at strikes |
| Put/Call Ratio | 15% | Market sentiment |
| Volume Profile | 15% | Trading volume patterns |
| Trend Analysis | 15% | Historical trends |

---

#### 1. Max Pain Alignment (20%)

**Concept**: Max pain is the strike where option sellers lose the least money. Price tends to gravitate toward max pain at expiration due to dealer hedging.

**Formula for Directional Bullish:**
```python
distance_pct = (underlying - max_pain) / underlying × 100
score = normalize(distance_pct, min=-5.0, max=5.0)
```

**Formula for Directional Bearish:**
```python
distance_pct = (underlying - max_pain) / underlying × 100
score = normalize(-distance_pct, min=-5.0, max=5.0)
```

**Interpretation for Bullish:**
- **Score 10**: Price 5%+ above max pain (strong bullish positioning)
- **Score 8**: Price 3-5% above max pain
- **Score 5**: Price at max pain (neutral)
- **Score 2**: Price 3-5% below max pain (bearish positioning)
- **Score 0**: Price 5%+ below max pain (very bearish)

**Example:**
```
Long Call (Bullish Strategy)
Underlying: $3,334.42, Max Pain: $3,200.00
distance_pct = ($3,334.42 - $3,200.00) / $3,334.42 × 100 = 4.03%
score = normalize(4.03, min=-5.0, max=5.0) = 9.03/10
```

**Logic**: Being 4.03% above max pain means dealers are net short calls or long puts, so they'll hedge by buying on dips, supporting upward moves.

---

#### 2. GEX/DEX Support (20%)

**Concept**:
- **GEX** (Gamma Exposure): Measures dealer gamma hedging needs
  - Positive GEX = dealers provide stability (sell rallies, buy dips)
  - Negative GEX = dealers amplify moves (buy rallies, sell dips)
- **DEX** (Delta Exposure): Measures dealer delta positioning
  - Positive DEX = dealers long delta, hedge by selling on rallies
  - Negative DEX = dealers short delta, hedge by buying on dips

**Formula for Directional Bullish:**
```python
# DEX component (70% weight)
if dex_total > 0:
    dex_score = 10.0  # Positive DEX supports upside
elif dex_total < 0:
    dex_score = 0.0
else:
    dex_score = 5.0

# GEX component (30% weight)
if gex_total < 0:
    gex_score = 8.0  # Negative GEX bonus (volatility)
else:
    gex_score = 5.0

score = (dex_score × 0.7) + (gex_score × 0.3)
```

**Interpretation:**
- **Score 10**: Positive DEX + negative GEX (ideal for big bullish moves)
- **Score 8**: Positive DEX + positive GEX (supports upside, stable)
- **Score 5**: Neutral positioning
- **Score 3**: Mixed signals
- **Score 0**: Negative DEX (dealers hedging against upside)

**Example:**
```
Long Call: DEX = +38,767, GEX = +254,129
dex_score = 10.0  (positive DEX)
gex_score = 5.0   (positive GEX, neutral for volatility)
score = (10.0 × 0.7) + (5.0 × 0.3) = 8.5/10
```

**NEW**: GEX/DEX now calculated **automatically** from ticker data during evaluation. No manual capture required!

---

#### 3. OI Levels (15%)

**Concept**: High open interest indicates significant market positioning and liquidity.

**Formula (currency-specific):**
```python
# For BTC
score = normalize(total_oi, min=1,000, max=20,000)

# For ETH
score = normalize(total_oi, min=5,000, max=100,000)
```

**Interpretation:**
- **Score 10**: Very high OI (liquid market, reliable pricing)
- **Score 7**: Moderate OI (decent liquidity)
- **Score 5**: Low OI (thin market)
- **Score 0**: Minimal OI (avoid, poor execution)

**Example:**
```
Total OI > 100,000 for ETH
score = 10.0/10  (highly liquid expiration)
```

---

#### 4. Put/Call Ratio (15%)

**Concept**: P/C ratio shows market sentiment (put volume vs call volume).

**Formula for Directional Bullish:**
```python
pc_ratio = put_oi / call_oi
score = normalize(1.5 - pc_ratio, min=0.0, max=1.0)
```

**Interpretation:**
- **P/C < 0.7**: Strong bullish sentiment → Score 9-10 for calls
- **P/C = 1.0**: Neutral sentiment → Score 5
- **P/C > 1.3**: Strong bearish sentiment → Score 0-2 for calls

**Example:**
```
Long Call: P/C Ratio = 0.581
score = normalize(1.5 - 0.581, min=0.0, max=1.0)
score = normalize(0.919, min=0.0, max=1.0) = 9.19/10
```

**Logic**: Low P/C means more calls than puts, confirming bullish bias.

---

#### 5. Volume Profile (15%)

**Concept**: Trading volume indicates market activity and conviction.

**Formula (currency-specific):**
```python
# For BTC
score = normalize(total_volume, min=500, max=10,000)

# For ETH
score = normalize(total_volume, min=2,000, max=50,000)
```

**Interpretation:**
- **Score 10**: Very high volume (strong conviction)
- **Score 7**: Moderate volume (decent activity)
- **Score 5**: Low volume (light trading)
- **Score 0**: Minimal volume (avoid, low liquidity)

**Example:**
```
Total Volume = 3,344 for ETH
score = normalize(3,344, min=2,000, max=50,000) = 0.28/10
(Low volume warning)
```

---

#### 6. Trend Analysis (15%)

**Concept**: Historical trends predict future direction.

**Max Pain Trend (60% of trend score):**
```
Query last 5 max pain captures
Compare oldest vs newest

If change < -2%: "decreasing" (bullish for calls)
If change > +2%: "increasing" (bearish for calls)
Else: "neutral"
```

**Volume Trend (40% of trend score):**
```
Query last 5 volume captures
Compare oldest vs newest

If change < -20%: "decreasing" (weakening conviction)
If change > +20%: "increasing" (strong conviction)
Else: "neutral"
```

**Formula for Directional Bullish:**
```python
if max_pain_trend == "decreasing":
    max_pain_score = 10.0  # Bullish signal
elif max_pain_trend == "neutral":
    max_pain_score = 5.0
else:  # increasing
    max_pain_score = 0.0   # Bearish signal

if volume_trend == "increasing":
    volume_score = 10.0
elif volume_trend == "neutral":
    volume_score = 5.0
else:  # decreasing
    volume_score = 3.0

score = (max_pain_score × 0.6) + (volume_score × 0.4)
```

**Example:**
```
Long Call:
max_pain_trend = "increasing" → max_pain_score = 0.0
volume_trend = "decreasing" → volume_score = 3.0

score = (0.0 × 0.6) + (3.0 × 0.4) = 1.2/10
(Negative trends warning)
```

---

### Market Regime Adjustment

**Optional**: User can specify market regime to apply directional penalties.

**Formula:**
```python
if market_regime and (strategy_type != regime):
    composite_score = composite_score × 0.5  # 50% penalty
```

**Examples:**
- Bullish regime + Long Put (bearish) = 50% penalty
- Bearish regime + Long Call (bullish) = 50% penalty
- Neutral regime or no regime = no penalty

**Use Case**: If you have strong conviction the market is bearish, setting regime="bearish" will penalize bullish strategies, helping filter for regime-aligned trades.

---

## GUI Usage Guide

### Strategy Tab Interface

#### 1. General Settings Section

**Currency Selection:**
- Choose BTC or ETH
- Determines OI/volume normalization thresholds

**Market Regime:**
- **Neutral** (default): No directional bias, all strategies scored equally
- **Bullish**: Favors call strategies, penalizes puts
- **Bearish**: Favors put strategies, penalizes calls
- Click **Info** button for detailed explanation

**Load Expiry Dates:**
- Fetches all available expirations from Deribit
- Sorted by open interest (descending)
- Format: "30JAN26 (OI: 12345)"

**Select Expiries:**
- **Multi-selection enabled** (hold Ctrl/Cmd to select multiple)
- Can evaluate multiple expirations in one run
- Results sorted by composite score across all

#### 2. Strategy Selection Section

**Available Strategies:**
- Long Call (bullish)
- Long Put (bearish)

**Selection:**
- **Multi-selection enabled** - can select multiple strategies
- Click multiple buttons to evaluate both calls and puts
- Results will include all selected strategies

#### 3. Strategy Configuration Section

**Strike Selection Methods:**

**By Delta** (Recommended):
- **Input**: Target delta (0.1 to 1.0)
- **Important**: Always enter **positive** values (e.g., 0.30)
  - For calls: System uses +0.30
  - For puts: System automatically converts to -0.30
- **Info Button**: Click for detailed delta explanation
- **Examples**:
  - 0.30 = OTM (3-10% out), cheap, lower probability
  - 0.50 = ATM, balanced cost/probability
  - 0.70 = ITM, expensive, higher probability

**By Moneyness**:
- **Input**: Percentage distance from current price
- 5% = Strike 5% above (calls) or below (puts) current price
- **Examples**:
  - ETH at $3,300, 5% moneyness
    - Call: Strike $3,465
    - Put: Strike $3,135

**By Specific Strike**:
- **Input**: Exact strike price
- Use when you have specific price target
- **Example**: Enter 3500 for $3,500 strike

**Max Loss %:**
- Filters out strategies risking more than X% of underlying
- **Conservative**: 1-2%
- **Moderate**: 3-5%
- **Aggressive**: 5-10%

**Take Profit %:**
- Optional exit target (checkbox to enable)
- **Recommended**:
  - Short-dated (<7 DTE): 30-50%
  - Medium (7-30 DTE): 50-100%
  - Long (>30 DTE): 100-200%

#### 4. Evaluation Process

1. **Configure** all settings
2. **Select** strategies (can select multiple)
3. **Select** expiries (can select multiple)
4. **Click "Evaluate Strategy"**
5. **Wait** for evaluation (progress shown in logs)
6. **Review** results in table (sorted by composite score)

**Multi-Evaluation:**
- If selecting 2 strategies × 3 expiries = 6 evaluations
- Processed sequentially
- Results accumulated and sorted by composite score
- Table shows ALL results, not just last one

#### 5. Results Table

**Columns:**
- **Rank**: Position by composite score
- **Strategy**: Long Call or Long Put
- **Expiry**: Expiration date
- **Composite**: Overall score (0-10)
- **Intrinsic**: Intrinsic score (0-10)
- **On-Chain**: On-chain score (0-10)
- **Max Loss %**: Risk as % of underlying
- **Breakeven**: Breakeven price(s)

**Sorting:**
- Automatically sorted by Composite score (descending)
- Highest scoring strategies at top

**Output Files:**
- Each evaluation generates detailed text report
- Location: `output/strategies/{expiration}/{currency}_{strategy}_{timestamp}.txt`
- Contains: Full market data, score breakdowns, risk metrics, greeks

---

## Programmatic Usage

### Basic Example

```python
from coding.service.strategy import StrategyEvaluationService
from coding.core.strategy.models import StrategyConfig, StrikeConfig
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.database.repository import DatabaseRepository

# Initialize services
api_service = DeribitApiService()
repository = DatabaseRepository()

# Create configuration
config = StrategyConfig(
    strategy_names=["Long Call", "Long Put"],
    expirations=["30JAN26"],
    strike_configs={
        "Long Call": StrikeConfig(
            method="by_delta",
            target_delta=0.30,  # Positive for both calls and puts
            quantity=1
        ),
        "Long Put": StrikeConfig(
            method="by_delta",
            target_delta=-0.30,  # Or let GUI auto-adjust
            quantity=1
        )
    },
    max_loss_filter=5.0,  # Max 5% loss
    take_profit_percentage=50.0,  # Exit at 50% gain
    market_regime="bullish",  # Optional
    intrinsic_weight=0.5,  # 50% intrinsic
    on_chain_weight=0.5,   # 50% on-chain
    top_n=10  # Return top 10 results
)

# Evaluate
service = StrategyEvaluationService(api_service, repository)
result = service.evaluate_strategies(
    currency="ETH",
    expiration="30JAN26",
    config=config
)

# Access results
if result.success:
    for signal in result.signals:
        print(f"{signal.strategy_name}: {signal.composite_score:.2f}/10")
        print(f"  Intrinsic: {signal.intrinsic_score:.2f}")
        print(f"  On-Chain: {signal.on_chain_score:.2f}")
        print(f"  Max Risk: ${signal.max_risk:.2f}")
        print(f"  Breakeven: ${signal.breakeven_points[0]:.2f}")
else:
    print(f"Errors: {result.errors}")
```

### Advanced: Multi-Expiration Scanning

```python
from coding.service.strategy import StrategyFinderService

# Initialize
finder = StrategyFinderService(api_service, repository)

# Scan all expirations for BTC and ETH
results = finder.find_best_strategies(
    currencies=["BTC", "ETH"],
    strategy_names=["Long Call"],
    strike_config=StrikeConfig(method="by_delta", target_delta=0.40),
    max_loss_filter=3.0,
    top_n=5  # Top 5 per expiration
)

# Results sorted by composite score across all currencies/expirations
for signal in results[:10]:  # Top 10 overall
    print(f"{signal.currency} {signal.expiration} {signal.strategy_name}: {signal.composite_score:.2f}")
```

### Accessing Score Breakdowns

```python
signal = result.signals[0]

# Intrinsic breakdown
print(signal.intrinsic_breakdown)
# {
#   'risk_reward_ratio': 9.12,
#   'cost_efficiency': 8.25,
#   'greek_profile': 3.15,
#   'breakeven_distance': 6.64,
#   'strike_moneyness': 10.00
# }

# On-chain breakdown
print(signal.on_chain_breakdown)
# {
#   'max_pain_alignment': 9.03,
#   'gex_dex_support': 8.50,
#   'oi_levels': 10.00,
#   'put_call_ratio': 9.19,
#   'volume_profile': 0.28,
#   'trend_analysis': 1.20
# }
```

---

## Interpreting Results

### Score Ranges

| Composite Score | Interpretation | Action |
|----------------|----------------|--------|
| 8.0 - 10.0 | **EXCELLENT** | High confidence, strong edge |
| 7.0 - 8.0 | **GOOD** | Solid opportunity, consider entry |
| 6.0 - 7.0 | **NEUTRAL** | Marginal, requires conviction |
| 4.0 - 6.0 | **POOR** | Unfavorable, likely pass |
| 0.0 - 4.0 | **VERY POOR** | Strong avoid signal |

### Reading Score Breakdowns

**High Composite (>7.0) Examples:**

**Example 1: Aligned Setup**
```
Composite: 8.5/10
├─ Intrinsic: 8.2/10
│  ├─ Risk/Reward: 9.5 ✓ (low risk, high leverage)
│  ├─ Cost Efficiency: 9.0 ✓ (cheap premium)
│  ├─ Greek Profile: 5.0 (moderate delta)
│  ├─ Breakeven Distance: 8.0 ✓ (close to current)
│  └─ Strike Moneyness: 10.0 ✓ (optimal 3-10% OTM)
└─ On-Chain: 8.8/10
   ├─ Max Pain Alignment: 9.5 ✓ (above max pain)
   ├─ GEX/DEX Support: 9.0 ✓ (positive DEX)
   ├─ OI Levels: 10.0 ✓ (high liquidity)
   ├─ Put/Call Ratio: 9.0 ✓ (bullish sentiment)
   ├─ Volume Profile: 7.0 (decent volume)
   └─ Trend Analysis: 8.0 ✓ (favorable trends)

Action: STRONG BUY - All signals aligned
```

**Example 2: Good Structure, Weak Market**
```
Composite: 6.5/10
├─ Intrinsic: 8.5/10 ✓
└─ On-Chain: 4.5/10 ⚠

Action: WAIT - Market not ready for this trade
Strategy is well-constructed but market positioning doesn't support the move
```

**Low Composite (<6.0) Examples:**

**Example 3: Mixed Signals**
```
Composite: 5.5/10
├─ Intrinsic: 6.0
│  ├─ Cost Efficiency: 3.0 ⚠ (expensive)
│  └─ Breakeven: 4.0 ⚠ (far away)
└─ On-Chain: 5.0
   └─ Volume Profile: 2.0 ⚠ (low volume)

Action: PASS - No clear edge, too many weaknesses
```

### Understanding Component Scores

**Strike Moneyness Impact:**

```
Strike at 4.97% OTM (calls):
├─ Moneyness Score: 10.0/10 ✓ (OPTIMAL zone)
├─ Why: 3-10% OTM provides:
│  ├─ Lower cost than ATM
│  ├─ Higher leverage than deep OTM
│  └─ Reasonable probability (30-40%)
└─ This is where most profitable trades occur
```

**GEX/DEX Interpretation:**

```
Positive DEX (+38,767):
├─ Dealers are net long delta
├─ They hedge by selling on rallies
├─ Provides support for upward moves
└─ Score: 10.0/10 for bullish strategies

Positive GEX (+254,129):
├─ Dealers provide stability
├─ Sell rallies, buy dips
├─ Dampens volatility
└─ Score: 5.0/10 (neutral for volatility seekers)
```

**Trend Analysis:**

```
Max Pain Trend: Increasing ⚠
├─ Dealers increasing put hedging
├─ Bearish signal for calls
└─ Score: 0.0/10

Volume Trend: Decreasing ⚠
├─ Weakening conviction
├─ Less market participation
└─ Score: 3.0/10

Combined Trend Score: 1.2/10
Action: Strong negative signal, consider waiting
```

---

## Best Practices

### Pre-Evaluation Checklist

**1. Data Quality**
- ✅ API connection active (check Database tab)
- ✅ Recent market data (<30 minutes old)
- ✅ Historical captures available (for trend analysis)

**2. Market Analysis**
- Review current price action
- Check major support/resistance levels
- Consider upcoming events (earnings, Fed meetings)

**3. Configuration**
- Set realistic max loss % based on account size
- Use delta 0.40-0.50 for balanced approach
- Set take profit targets (don't be greedy)

### Optimal Workflow

**Step 1: Select Currency & Regime**
1. Choose BTC or ETH based on conviction
2. Set market regime if you have directional bias
3. Click "Load Expiry Dates"

**Step 2: Choose Expirations**
1. Select 2-3 expirations with high OI
2. Prefer 7-30 DTE for active trading
3. Use Ctrl/Cmd for multi-selection

**Step 3: Configure Strategy**
1. **For balanced approach**: By Delta, target=0.40-0.50
2. **For cheap lottery**: By Delta, target=0.20-0.30
3. **For high probability**: By Delta, target=0.60-0.70
4. Set max loss 3-5% (moderate risk)
5. Set take profit 50-100%

**Step 4: Select Strategies**
1. Select Long Call if bullish bias
2. Select Long Put if bearish bias
3. Can select both for comparison

**Step 5: Evaluate & Analyze**
1. Click "Evaluate Strategy"
2. Wait for completion (check logs)
3. Review results table
4. Open text reports for top 3 strategies

**Step 6: Decision**
- **Composite >7.5**: Strong signal, high confidence
- **Composite 6.5-7.5**: Moderate signal, requires conviction
- **Composite <6.5**: Pass, wait for better setup

### Risk Management

**Position Sizing:**
```
Account Size: $10,000
Max Loss per Trade: 2% = $200

If premium = $100:
  Max contracts = $200 / $100 = 2 contracts

If premium = $50:
  Max contracts = $200 / $50 = 4 contracts
```

**Stop Loss:**
- Set mental stop at -50% of premium (cut losses)
- Or set stop at support level breakdown

**Take Profit:**
- First target: +50% (take half off)
- Second target: +100% (exit remaining)
- Don't be greedy - theta decay accelerates near expiration

### Common Mistakes to Avoid

❌ **Don't**: Select far OTM strikes (delta < 0.20) hoping for lottery win
✅ **Do**: Use delta 0.30-0.50 for consistent profitability

❌ **Don't**: Ignore on-chain score if intrinsic is high
✅ **Do**: Require both scores >6.0 for high-conviction trades

❌ **Don't**: Hold long options through expiration week
✅ **Do**: Exit by 7 DTE or roll to next expiration

❌ **Don't**: Evaluate only one expiration
✅ **Do**: Compare 2-3 expirations to find best setup

❌ **Don't**: Skip setting take profit targets
✅ **Do**: Always have profit exit plan (50-100%)

---

## Troubleshooting

### Common Issues

**Issue 1: "No instruments found for expiration"**
- **Cause**: Invalid expiration format or expired contract
- **Solution**: Use "Load Expiry Dates" button
- **Format**: Must be "30JAN26", not "2026-01-30"

**Issue 2: Evaluation is very slow (>60 seconds)**
- **Cause**: Fetching ticker data for many instruments
- **Normal**: 10-20 seconds for single expiration
- **Solution**: Wait for completion, or reduce number of expirations

**Issue 3: All scores are low (<5.0)**
- **Cause**: Market is directionless, no clear edge
- **Solution**: Don't force trades, wait for clearer setup
- **Alternative**: Try different expirations or currencies

**Issue 4: Delta selection returns unexpected strike**
- **Cause**: Requested delta not available (limited strikes)
- **Example**: Request delta 0.15, get 0.20 (closest available)
- **Solution**: Check actual delta in results, adjust if needed

**Issue 5: Results table only shows last evaluation**
- **Cause**: Bug in older version
- **Solution**: **FIXED** - Now accumulates all results across evaluations
- **Verify**: Table should show all strategies from all selected expirations

**Issue 6: Info buttons show nothing**
- **Cause**: Button too small or text not rendering
- **Solution**: **FIXED** - Now properly sized with "Info" label
- **Alternative**: Refer to this documentation

**Issue 7: Long Put shows wrong strike (far OTM)**
- **Cause**: Delta sign issue (using +0.30 for puts instead of -0.30)
- **Solution**: **FIXED** - System auto-converts to negative delta for puts
- **Usage**: Always enter positive delta values (e.g., 0.30) for both calls and puts

**Issue 8: GEX/DEX always shows "Not available"**
- **Old Behavior**: Required manual capture from Database tab
- **Solution**: **FIXED** - Now calculated automatically from ticker data
- **Verify**: Check report file for "GEX/DEX Analysis" section with values

**Issue 9: High score but trade lost money**
- **Reality**: Scores predict PROBABILITY, not certainty
- **Factors not predicted**:
  - Black swan events
  - Sudden volatility spikes
  - Regulatory announcements
  - Exchange issues
- **Mitigation**: Always use stop losses and position sizing

**Issue 10: Can't select multiple strategies/expiries**
- **Old Behavior**: Only single selection allowed
- **Solution**: **FIXED** - Multi-selection now enabled
- **Usage**:
  - Expiries: Ctrl/Cmd + Click to select multiple
  - Strategies: Click multiple buttons (stay highlighted)

### Performance Optimization

**Expected Timings:**
- Single strategy, single expiration: 5-10 seconds
- 2 strategies, 3 expirations: 30-45 seconds
- Full multi-currency scan: 1-3 minutes

**If Slower:**
1. Check API rate limits (Deribit throttling)
2. Verify database connection pool
3. Check system resources (CPU/memory)
4. Review logs for retry/timeout warnings

### Data Quality Indicators

**Good Quality** (trust the scores):
- ✅ Total OI > 10,000 (BTC) or > 50,000 (ETH)
- ✅ Volume > 500 (BTC) or > 2,000 (ETH)
- ✅ GEX/DEX data available (automatic)
- ✅ 3+ historical captures for trends
- ✅ Bid-ask spread < 5% of mid price

**Poor Quality** (scores unreliable):
- ❌ Total OI < 1,000
- ❌ Volume < 100
- ❌ Only 1 historical capture (no trends)
- ❌ Wide bid-ask spreads (>10%)
- ❌ Stale data (>2 hours old)

---

## Calculation Examples

For a **detailed, step-by-step calculation walkthrough** showing exactly how scores are computed, see:

**📄 `documentation/strategy_scoring_calculation_example.md`**

This document provides:
- Real example from ETH Long Call evaluation
- Every formula with actual numbers
- All 11 component scores calculated in detail
- Weighted averaging for intrinsic, on-chain, and composite scores
- Interpretation of each metric
- Complete breakdown matching actual report output

**Example covered:**
```
Strategy: Long Call
Currency: ETH at $3,334.42
Strike: $3,500 (4.97% OTM)
Premium: $58.35
Composite Score: 7.06/10
  ├─ Intrinsic: 7.51/10
  └─ On-Chain: 6.61/10
```

**All formulas with actual values:**
- Risk/Reward: 9.12/10 (1.75% risk)
- Cost Efficiency: 8.25/10 (1.75% of underlying)
- Greek Profile: 3.15/10 (delta 0.3148)
- Breakeven Distance: 6.64/10 (6.72% move needed)
- **Strike Moneyness: 10.00/10** (4.97% OTM = OPTIMAL)
- Max Pain Alignment: 9.03/10 (4.03% above max pain)
- GEX/DEX Support: 8.50/10 (positive DEX)
- OI Levels: 10.00/10 (very liquid)
- Put/Call Ratio: 9.19/10 (0.581 = bullish)
- Volume Profile: 0.28/10 (low volume warning)
- Trend Analysis: 1.20/10 (negative trends)

Use this document to understand exactly how your strategies are scored!

---

## Database Schema

Strategies are automatically saved to the database for historical analysis.

**Table: `strategy_signals`**

```sql
CREATE TABLE strategy_signals (
    id SERIAL PRIMARY KEY,
    generated_at TIMESTAMP NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,

    -- Scores (0-10 scale)
    intrinsic_score DECIMAL(4,2) NOT NULL,
    on_chain_score DECIMAL(4,2) NOT NULL,
    composite_score DECIMAL(4,2) NOT NULL,
    rank INTEGER,

    -- Structure and breakdowns (JSON)
    legs JSONB NOT NULL,
    intrinsic_breakdown JSONB,
    on_chain_breakdown JSONB,

    -- Risk metrics
    max_risk DECIMAL(12,2) NOT NULL,
    max_profit DECIMAL(12,2),
    total_cost DECIMAL(12,2) NOT NULL,
    max_loss_percentage DECIMAL(6,2) NOT NULL,
    take_profit_percentage DECIMAL(6,2),

    -- Greeks
    net_delta DECIMAL(8,6),
    net_gamma DECIMAL(10,8),
    net_theta DECIMAL(8,6),
    net_vega DECIMAL(8,6),

    -- Market context
    underlying_price DECIMAL(12,2),
    max_pain_strike DECIMAL(12,2),
    market_regime VARCHAR(20)
);

CREATE INDEX idx_strategy_signals_composite ON strategy_signals(composite_score DESC);
CREATE INDEX idx_strategy_signals_currency_exp ON strategy_signals(currency, expiration);
```

**Query Examples:**

```sql
-- Top 10 strategies overall
SELECT strategy_name, currency, expiration, composite_score, rank
FROM strategy_signals
ORDER BY composite_score DESC
LIMIT 10;

-- Best Long Calls for BTC
SELECT expiration, composite_score, intrinsic_score, on_chain_score
FROM strategy_signals
WHERE strategy_name = 'Long Call' AND currency = 'BTC'
ORDER BY composite_score DESC
LIMIT 5;

-- Historical performance tracking
SELECT DATE(generated_at) as date,
       AVG(composite_score) as avg_score,
       COUNT(*) as count
FROM strategy_signals
WHERE strategy_name = 'Long Call'
GROUP BY DATE(generated_at)
ORDER BY date DESC;
```

---

## Future Enhancements

**Not Yet Implemented:**

1. **Complex Strategies**:
   - Bull Call Spread
   - Bear Put Spread
   - Iron Condor
   - Butterfly

2. **Advanced Features**:
   - ML-based weight optimization
   - Automated market regime detection
   - Backtesting framework
   - Real-time execution integration
   - IV rank/percentile scoring

3. **Performance**:
   - Ticker data caching
   - Parallel evaluation
   - Incremental updates

4. **UI Enhancements**:
   - Interactive charts
   - Score history graphs
   - Comparison mode

---

## Appendix: Glossary

**ATM (At-The-Money)**: Strike price equals current underlying price

**Breakeven**: Price where strategy neither profits nor loses

**Composite Score**: Weighted average of intrinsic and on-chain scores

**Delta**: Rate of option price change per $1 move in underlying (0-1 for calls, 0 to -1 for puts)

**DEX (Delta Exposure)**: Net dealer delta position across all strikes

**Directional Strategy**: Profits from specific price direction (calls = up, puts = down)

**Gamma**: Rate of delta change per $1 move in underlying

**GEX (Gamma Exposure)**: Net dealer gamma position across all strikes

**Intrinsic Score**: Score based on strategy structure (risk/reward, cost, greeks)

**ITM (In-The-Money)**: Strike where option has intrinsic value

**Max Pain**: Strike where option sellers lose least money

**Moneyness**: Percentage distance from strike to current price

**On-Chain Score**: Score based on market positioning (max pain, GEX, OI, sentiment)

**OI (Open Interest)**: Number of outstanding option contracts

**OTM (Out-of-The-Money)**: Strike where option has no intrinsic value (only time value)

**P/C Ratio (Put/Call Ratio)**: Put volume or OI divided by call volume or OI

**Theta**: Rate of option value decay per day (time decay)

**Vega**: Rate of option price change per 1% change in implied volatility

---

## Version History

**v1.0 (January 18, 2026)**
- ✅ Initial release with Long Call and Long Put strategies
- ✅ 11-component scoring system (5 intrinsic + 6 on-chain)
- ✅ **NEW**: Strike Moneyness component (15% weight) added to intrinsic scorer
- ✅ **NEW**: Automatic GEX/DEX calculation from ticker data (no manual capture)
- ✅ **NEW**: Multi-selection for strategies and expirations
- ✅ **NEW**: Info buttons with detailed tooltips
- ✅ **NEW**: Auto delta sign adjustment for puts (always enter positive values)
- ✅ GUI integration with Strategy Tab
- ✅ Comprehensive text report generation
- ✅ Database persistence with JSONB breakdowns
- ✅ Market regime awareness with penalty system
- ✅ Trend analysis with historical captures
- ✅ Complete documentation with calculation examples

---

## Support & Feedback

For issues, questions, or feature requests:
- Check this documentation first
- Review `documentation/strategy_scoring_calculation_example.md` for detailed examples
- Check logs in GUI Log Viewer for debugging
- Review generated report files in `output/strategies/{expiration}/`

**File Locations:**
- This Guide: `documentation/strategy_system_guide.md`
- Calculation Examples: `documentation/strategy_scoring_calculation_example.md`
- Code: `coding/core/strategy/`, `coding/service/strategy/`, `coding/gui/tabs/strategy_tab.py`
- Reports: `output/strategies/{expiration}/{currency}_{strategy}_{timestamp}.txt`

---

**End of Documentation**
