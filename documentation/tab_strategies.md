# Strategies Tab Documentation

## Overview

The Strategies tab provides a GUI interface for evaluating and ranking option strategies. It scores strategies based on intrinsic metrics (risk/reward, greeks) and on-chain metrics (max pain, GEX/DEX, volume), with optional market regime awareness.

For detailed strategy system architecture and scoring methodology, see: `documentation/feature_strategy_system.md`

## Purpose

- Evaluate option strategies for selected currency and expiration
- Rank strategies by composite score (intrinsic + on-chain)
- Configure strike selection methods (delta, moneyness, specific)
- Filter strategies by risk criteria
- Export evaluation results to text files

## Features

### 1. Currency Selection
- Select currency (BTC or ETH)
- Load available expiration dates

### 2. Expiration Selection
- Grid view or dropdown for expiration selection
- Shows all available expirations for selected currency
- Supports single expiration evaluation

### 3. Market Regime (Optional)
- Select current market regime: Bullish, Bearish, Sideways, or None
- Applies regime penalties:
  - Bullish strategy in bearish regime: -50% score
  - Bearish strategy in bullish regime: -50% score
- If "None", no regime penalties applied

### 4. Strategy Selection
- **Available Strategies**:
  - Long Call (bullish directional)
  - Long Put (bearish directional)
  - Bull Call Spread (bullish, capital-efficient) - Programmatic only
  - Bear Put Spread (bearish, capital-efficient) - Programmatic only

### 5. Strike Selection Configuration

#### By Delta (Default)
- Specify target delta (e.g., 0.30 = 30 delta)
- Finds option with delta closest to target
- Best for: Probability-based selection

#### By Moneyness
- Specify % out-of-the-money (e.g., 5% OTM)
- Finds strike relative to underlying price
- Best for: Price-based selection

#### By Specific Strike
- Enter exact strike price (e.g., 3000)
- Uses specified strike exactly
- Best for: Manual strike selection

### 6. Filters

#### Max Loss % Filter
- Maximum loss as % of underlying price
- Example: 5% = reject strategies with max loss > 5% of price
- Useful for risk control

#### Take Profit % Filter (Optional)
- Minimum profit as % of underlying price
- Example: 10% = only show strategies with ≥10% profit potential
- Useful for reward targeting

### 7. Evaluation Results Table
- Columns:
  - **Rank**: 1, 2, 3... (sorted by composite score)
  - **Strategy**: Strategy name
  - **Composite Score**: 0-10 combined score
  - **Intrinsic Score**: 0-10 risk/reward/greeks score
  - **On-Chain Score**: 0-10 market positioning score
  - **Max Risk**: Maximum loss (dollars)
  - **Max Profit**: Maximum profit (dollars or "Unlimited")
  - **Total Cost**: Net debit/credit

### 8. Export Functionality
- Saves detailed evaluation report to text file
- Filename: `{Currency}_{Strategy}_{Timestamp}.txt`
- Location: `output/strategies/{expiration}/`
- Includes:
  - All market data (max pain, OI, GEX/DEX)
  - Strategy details (legs, strikes, costs)
  - Complete score breakdowns
  - Recommendation and reasoning

### 9. Progress Logging
- Real-time progress updates during evaluation
- Shows which strikes are being analyzed
- Error messages if evaluation fails

## Architecture

```
Strategies Tab (GUI)
    ↓
StrategyEvaluationService (Service Layer)
    ↓
BaseStrategy + Scorers (Core Layer)
```

**GUI**: `coding/gui/tabs/strategy_tab.py`
**Service**: `coding/service/strategy/strategy_evaluation_service.py`
**Core**: `coding/core/strategy/`

## Usage

1. Select currency (ETH or BTC)
2. Click "Load Expirations" to fetch available dates
3. Select expiration from grid/dropdown
4. Optionally select market regime
5. Select strategy (Long Call or Long Put)
6. Configure strike selection:
   - Choose method (delta/moneyness/strike)
   - Enter target value
7. Set filters (max loss %, take profit %)
8. Click "Evaluate Strategies"
9. View ranked results in table
10. Click row to see details
11. Click "Export" to save full report

## Scoring System

### Composite Score (0-10)
Weighted combination of intrinsic and on-chain scores:
- **Intrinsic Weight**: 50% (default)
- **On-Chain Weight**: 50% (default)
- **Formula**: (Intrinsic × 0.5) + (On-Chain × 0.5)

### Intrinsic Score Components
- **Risk/Reward Ratio** (30%): Max profit ÷ max loss
- **Cost Efficiency** (25%): Premium vs strike width
- **Greek Profile** (25%): Delta, gamma, theta balance
- **Breakeven Distance** (20%): Distance from current price

### On-Chain Score Components
- **Max Pain Alignment** (20%): Strike proximity to max pain
- **GEX/DEX Support** (20%): Gamma/delta exposure levels
- **OI Levels** (15%): Open interest at strike
- **Put/Call Ratio** (15%): Market sentiment
- **Volume Profile** (15%): Trading activity
- **Trend Analysis** (15%): Max pain and volume trends

### Regime Penalties
- Bullish strategy (Long Call, Bull Call Spread) in bearish regime: **-50%**
- Bearish strategy (Long Put, Bear Put Spread) in bullish regime: **-50%**
- No penalty for sideways regime or if regime not specified

## Sample Results

```
RANK | STRATEGY  | COMPOSITE | INTRINSIC | ON-CHAIN | MAX RISK | MAX PROFIT | COST
-----|-----------|-----------|-----------|----------|----------|------------|------
  1  | Long Call |   8.42    |   8.75    |   8.10   | $145.50  | Unlimited  | -$145.50
  2  | Long Call |   7.89    |   7.95    |   7.83   | $168.20  | Unlimited  | -$168.20
  3  | Long Call |   7.23    |   7.50    |   6.96   | $123.40  | Unlimited  | -$123.40
```

## Sample Export File

```
================================================================================
STRATEGY EVALUATION REPORT
================================================================================
Currency: ETH
Expiration: 14FEB26
Evaluation Time: 2026-02-08 17:30:15
Market Regime: Moderate Bullish

MARKET DATA SNAPSHOT:
  Underlying Price: $2,906.50
  Max Pain Strike: $2,800.00
  Total Call OI: 15,234.5
  Total Put OI: 12,876.3
  Put/Call Ratio: 0.845 (Bullish)

STRATEGY: Long Call
  Strike: $3,000.00
  Quantity: 1
  Total Cost: $145.50 (debit)
  Max Risk: $145.50
  Max Profit: Unlimited
  Breakeven: $3,145.50

SCORING BREAKDOWN:
  Composite Score: 8.42 / 10

  Intrinsic Score: 8.75 / 10
    - Risk/Reward: 9.2
    - Cost Efficiency: 8.5
    - Greek Profile: 8.8
    - Breakeven Distance: 8.5

  On-Chain Score: 8.10 / 10
    - Max Pain Alignment: 7.5
    - GEX/DEX Support: 8.8
    - OI Levels: 8.2
    - Put/Call Ratio: 9.1
    - Volume Profile: 7.8
    - Trend Analysis: 7.2

RECOMMENDATION: STRONG BUY
Reasoning: Excellent intrinsic score with strong risk/reward ratio.
On-chain metrics show bullish positioning with GEX support. Strike
aligns well with market expectations. Regime-aligned strategy.
```

## Use Cases

### 1. Quick Strategy Screening
- Select expiration
- Evaluate all strategies
- See top-ranked options immediately

### 2. Delta-Based Selection
- Choose target delta (e.g., 0.30 for ~30% ITM probability)
- Evaluate across multiple strikes
- Compare risk/reward at different deltas

### 3. Regime-Aware Trading
- Detect market regime first (Market Regime tab)
- Select matching strategy type
- Evaluate with regime filtering enabled

### 4. Risk-Limited Trading
- Set max loss filter (e.g., 5%)
- Only see strategies within risk tolerance
- Focus on capital preservation

## Important Notes

### Evaluation Speed
- Evaluates one expiration at a time
- Fetches full book summary + Greeks
- Typical evaluation time: 5-30 seconds depending on strikes

### Data Requirements
- Requires live market data (prices, IV, greeks)
- Requires historical on-chain data (max pain trends, volume trends)
- If insufficient historical data, some score components may be limited

### Multi-Leg Strategies
- Bull Call Spread and Bear Put Spread available programmatically only
- GUI support coming in future release
- Use Python scripts for spread evaluation currently

### Score Interpretation
- **8.0-10.0**: Excellent - Strong recommendation
- **6.0-8.0**: Good - Favorable conditions
- **4.0-6.0**: Fair - Mixed signals
- **< 4.0**: Poor - Avoid or wait for better setup

## Comparison to Other Tabs

| Feature | Strategies Tab | On Chain Analysis Tab |
|---------|---------------|----------------------|
| **Purpose** | Strategy ranking | Market overview |
| **Output** | Scored strategies | Text report |
| **Filtering** | Risk-based | None |
| **Recommendation** | Yes (buy/avoid) | No |
| **Storage** | Database (signals) | None |

## Future Enhancements

- Multi-expiration comparison
- Spread strategy GUI support
- Custom weight configuration
- Backtesting integration
- Strategy performance tracking
- Real-time strategy monitoring
- Automated strategy execution
- Portfolio-level strategy analysis
