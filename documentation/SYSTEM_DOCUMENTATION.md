# Options Trading Platform - System Documentation

**Version:** 1.0.0
**Last Updated:** February 8, 2026
**Python Version:** 3.13
**Database:** PostgreSQL 18.0

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [Setup Instructions](#setup-instructions)
4. [Data Collection Pipeline](#data-collection-pipeline)
5. [ML Pipeline](#ml-pipeline)
6. [Strategy Evaluation](#strategy-evaluation)
7. [System Health Monitoring](#system-health-monitoring)
8. [Maintenance](#maintenance)
9. [Troubleshooting](#troubleshooting)

---

## System Overview

### Purpose
Automated options trading system for Deribit cryptocurrency options (BTC, ETH) featuring:
- Real-time data collection and aggregation
- Machine learning-based market regime detection
- Multi-strategy evaluation and ranking
- On-chain analysis (GEX/DEX, max pain, OI)
- Comprehensive GUI for analysis and monitoring

### Key Features
- **Data Collection**: Autonomous daemon collecting trades every 30 minutes
- **ML Models**: Market regime detection and realized volatility prediction
- **Strategy System**: Long Call, Long Put, Bull Call Spread, Bear Put Spread
- **On-Chain Analytics**: GEX/DEX analysis, max pain calculation, IV surface analysis
- **Health Monitoring**: Built-in system validation and health checks

### Hardware Requirements
- **CPU**: Intel 14900K (or equivalent high-performance CPU)
- **GPU**: NVIDIA 5090 Suprim SOC Liquid (for ML training)
- **RAM**: 32GB+ recommended
- **Storage**: 100GB+ for historical data

---

## Architecture

### Layered Design

```
┌─────────────────────────────────────────────────────────────┐
│                         GUI Layer                           │
│  (PySide6 - Thin wrappers, no business logic)              │
├─────────────────────────────────────────────────────────────┤
│                       Service Layer                         │
│  (Orchestration, high-level operations)                    │
│  - DeribitApiService                                       │
│  - StrategyEvaluationService                               │
│  - RegimeDetectionService                                  │
│  - DatabaseCaptureService                                  │
├─────────────────────────────────────────────────────────────┤
│                        Core Layer                           │
│  (Business logic, models, base classes)                    │
│  - Analytics (OnChainAnalyzer, GexDexCalculator)          │
│  - Strategy Definitions (BaseStrategy, spreads)           │
│  - ML (FeatureEngineer, LabelGenerator)                   │
│  - Database (Repository, ConnectionPool)                  │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
option_trading/
├── coding/
│   ├── core/              # Core business logic
│   │   ├── analytics/     # On-chain analysis, GEX/DEX
│   │   ├── database/      # Database config, repository
│   │   ├── ml/            # ML feature engineering, training
│   │   ├── strategy/      # Strategy system
│   │   │   ├── definitions/  # Strategy classes
│   │   │   ├── models/       # Data models
│   │   │   └── scoring/      # Scoring logic
│   │   └── schemas/       # API response schemas
│   │
│   ├── service/           # Service orchestration layer
│   │   ├── data_collection/  # Collection daemon
│   │   ├── deribit/          # API service
│   │   ├── regime/           # Regime detection
│   │   └── strategy/         # Strategy evaluation
│   │
│   └── gui/               # GUI components
│       ├── tabs/          # Tab implementations
│       └── theme/         # Styling, colors
│
├── migrations/            # Database migrations
├── scripts/              # Utility scripts
├── tests/                # Unit and integration tests
├── models/               # Trained ML models
├── output/               # Generated files
│   ├── log/              # Log files
│   └── strategies/       # Strategy evaluation reports
└── documentation/        # Documentation files
```

---

## Setup Instructions

### 1. Environment Setup

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Database Configuration

```bash
# Create .env file in project root
echo "DB_PASSWORD=your_secure_password" > .env

# Ensure PostgreSQL is running on localhost:5433
# Database: option_trading
# User: postgres
```

### 3. Run Migrations

```sql
-- Connect to PostgreSQL and run migrations in order
\i migrations/001_add_strategy_signals.sql
\i migrations/002_add_regime_detection.sql
\i migrations/003_add_regime_detection_tables.sql
-- ... (run all migrations sequentially)
```

### 4. Launch Application

```bash
# Start GUI
python coding/gui/app.py

# Or run system validation
python -m scripts.validate_system
```

---

## Data Collection Pipeline

### Collection Daemon

**Purpose**: Continuously collect options trades and market data every 30 minutes.

**Location**: `coding/service/data_collection/collection_daemon.py`

**How It Works**:
1. Starts automatically on system boot (via Task Scheduler)
2. Collects data every 30 minutes while system is running
3. Fetches:
   - Historical trades (last 1.5 hours)
   - Option chain snapshots
   - Greeks, IVs, prices
4. Aggregates into hourly snapshots
5. Logs all operations to `output/log/collection_daemon_*.log`

**Data Flow**:
```
Deribit API → ProspectiveCollector → historical_trades table
                                    ↓
                             Aggregation (hourly)
                                    ↓
                          hourly_snapshots table
                                    ↓
                            ML Feature Engineering
```

**Monitoring**:
```bash
# Check daemon status via GUI
# Navigate to: System Health tab → Run Validation

# Or check logs directly
tail -f output/log/collection_daemon_*.log
```

### Data Quality

**Current Status** (as of Feb 8, 2026):
- **62,491 trades** collected (BTC: 44,899 | ETH: 17,592)
- **60 hours** of hourly snapshots per currency
- **100% IV completeness** (all trades have implied volatility)
- **255+ trades/hour** active collection rate

**Requirements for ML Training**:
- **Minimum**: 48 hours (for inference)
- **Production**: 720 hours (30 days) for robust training
- **Current Progress**: 60/720 hours (8.3%)
- **ETA**: March 7, 2026 (27.5 days remaining)

---

## ML Pipeline

### Overview

The ML pipeline predicts:
1. **Market Regime** (Strong Bullish, Weak Bullish, Sideways, Weak Bearish, Strong Bearish)
2. **Realized Volatility** (24h forward prediction)

### Components

#### 1. Feature Engineering
**Location**: `coding/core/ml/feature_engineering.py`

**Features Generated**:
- Technical indicators (SMA, RSI, ADX, ATR)
- Options metrics (IV rank, PCR, skew)
- On-chain signals (GEX, DEX, max pain distance)
- Funding rate, volume profile
- Momentum, volatility metrics

**Input**: `hourly_snapshots` table
**Output**: Feature matrix for training/inference

#### 2. Label Generation
**Location**: `coding/core/ml/label_generator.py`

**Generates**:
- Market regime labels (5 classes)
- Realized volatility targets
- Binary direction labels

#### 3. Model Training
**Location**: `coding/core/ml/training/`

**Models Used**:
- LightGBM for regime classification
- XGBoost for volatility regression
- Ensemble methods for robustness

**Training Requirements**:
- **Minimum**: 720 hours (30 days) of data
- **Recommended**: 1440+ hours (60 days) for production

#### 4. Inference
**Location**: `coding/service/regime/regime_detection_service.py`

**Methods**:
- ML-based prediction (when models trained)
- Heuristic fallback (trend + volatility + on-chain)

**Current Status**: Using heuristic fallback (insufficient training data)

### Model Management

**Location**: `models/` directory

**Structure**:
```
models/
├── BTC_market_regime_20260207_213226_v1/
│   ├── model.pkl
│   ├── metadata.json
│   └── feature_importance.png
├── BTC_realized_vol_24h_20260207_221729_v1/
│   └── ...
└── ...
```

**Current Models**:
- 6 market regime models
- 4 realized volatility models
- **Status**: Trained but need retraining with 30+ days data

---

## Strategy Evaluation

### Available Strategies

#### 1. Long Call
- **Type**: Directional bullish
- **Max Risk**: Premium paid (limited)
- **Max Profit**: Unlimited
- **Use Case**: Strong bullish outlook

#### 2. Long Put
- **Type**: Directional bearish
- **Max Risk**: Premium paid (limited)
- **Max Profit**: Strike - premium
- **Use Case**: Strong bearish outlook

#### 3. Bull Call Spread
- **Type**: Vertical spread (directional bullish)
- **Max Risk**: Net debit paid (limited)
- **Max Profit**: Strike width - net debit (limited)
- **Use Case**: Moderately bullish, capital-efficient
- **Configuration**: Pydantic-based with skew-aware optimization

#### 4. Bear Put Spread
- **Type**: Vertical spread (directional bearish)
- **Max Risk**: Net debit paid (limited)
- **Max Profit**: Strike width - net debit (limited)
- **Use Case**: Moderately bearish, capital-efficient

### Scoring System

**Composite Score** = (Intrinsic Score × 50%) + (On-Chain Score × 50%)

#### Intrinsic Scorer (50% weight)
- Risk/Reward Ratio (30%)
- Cost Efficiency (25%)
- Greek Profile (25%)
- Breakeven Distance (20%)

#### On-Chain Scorer (50% weight)
- Max Pain Alignment (20%)
- GEX/DEX Support (20%)
- OI Levels (15%)
- Put/Call Ratio (15%)
- Volume Profile (15%)
- Trend Analysis (15%)

**Market Regime Penalties**:
- Bullish strategy in bearish regime: -50%
- Bearish strategy in bullish regime: -50%

### Usage

**Via GUI**:
1. Navigate to "Strategies" tab
2. Select currency and expiration
3. Choose strategies to evaluate
4. Configure filters (max loss %, take profit %)
5. Click "Evaluate Strategies"
6. View ranked results with detailed breakdowns

**Programmatic**:
```python
from coding.service.strategy import StrategyEvaluationService
from coding.core.strategy.models import StrategyConfig, StrikeConfig

config = StrategyConfig(
    strategy_names=["Long Call", "Bull Call Spread"],
    expirations=["31JAN25"],
    max_loss_filter=5.0,  # Max 5% loss
    market_regime="bullish",
    top_n=10
)

service = StrategyEvaluationService(api_service, repository)
result = service.evaluate_strategies("BTC", "31JAN25", config)
```

**Output**:
- Ranked strategy signals with scores
- Detailed text reports in `output/strategies/{expiration}/`
- Interactive P&L charts in `output/strategies/{expiration}/charts/`

---

## System Health Monitoring

### System Health Tab (GUI)

**Location**: GUI → "System Health" tab

**Checks Performed**:
1. API Connectivity (Deribit)
2. Database Connection
3. Required Tables Existence
4. Collection Daemon Status
5. Data Freshness (< 1 hour)
6. Historical Trades Count
7. Hourly Snapshots Coverage
8. Backfill Status
9. Data Quality (IV completeness)
10. Prospective Collection (trades/hour)
11. ML Pipeline Readiness

**Usage**:
1. Click "Run Validation" button
2. View real-time progress
3. Check summary for passed/warnings/failed
4. Address any warnings or failures

### Validation Script

```bash
# Run from command line
python -m scripts.validate_system

# Exit code 0 if passed, 1 if failed
```

### Key Metrics

**Healthy System**:
- Daemon: ACTIVE (log modified < 15 min ago)
- Data: FRESH (collected < 1 hour ago)
- Trades: 200+ per hour actively collecting
- IV Completeness: > 95%
- Hourly Snapshots: Growing continuously

**Warning Indicators**:
- Daemon: Last activity > 1 hour
- Data: Stale (> 24 hours old)
- Trades: < 100 per hour (possible market hours)
- ML: < 720 hours data (insufficient for training)

---

## Maintenance

### Daily

**Check System Health** (2 minutes):
```bash
# Launch GUI → System Health tab → Run Validation
# Verify: Daemon ACTIVE, Data FRESH, No failed checks
```

### Weekly

**Review Collection Logs** (5 minutes):
```bash
# Check for errors or gaps
tail -100 output/log/collection_daemon_*.log | grep ERROR
```

**Database Backup** (10 minutes):
```bash
# Backup PostgreSQL database
pg_dump -U postgres -d option_trading > backup_YYYYMMDD.sql
```

### Monthly

**ML Model Retraining** (once 720+ hours accumulated):
```bash
# After accumulating 30+ days of data:
# 1. Train new models
python coding/core/ml/training/train_regime_model.py

# 2. Evaluate performance
python coding/core/ml/training/evaluate_model.py

# 3. Deploy if metrics improved
```

**Data Quality Audit**:
```sql
-- Check for anomalies
SELECT
    DATE(snapshot_hour) as date,
    currency,
    COUNT(*) as snapshots
FROM hourly_snapshots
GROUP BY DATE(snapshot_hour), currency
ORDER BY date DESC;

-- Verify IV completeness
SELECT
    currency,
    COUNT(*) as total,
    COUNT(iv) as with_iv,
    ROUND(COUNT(iv)::NUMERIC / COUNT(*) * 100, 2) as completeness_pct
FROM historical_trades
GROUP BY currency;
```

### Quarterly

**Performance Review**:
- Review strategy evaluation accuracy
- Analyze ML model drift
- Optimize collection intervals if needed
- Archive old logs and reports

---

## Troubleshooting

### Daemon Not Running

**Symptoms**: System Health shows "Daemon: STOPPED" or data not fresh

**Solutions**:
1. Check if process is running:
   ```bash
   # Windows Task Manager → Look for Python process
   # Or check log modification time
   ```

2. Restart daemon manually:
   ```bash
   python coding/service/data_collection/collection_daemon.py
   ```

3. Check Task Scheduler (if auto-start configured)

### Database Connection Errors

**Symptoms**: "connection to server failed" or "password authentication failed"

**Solutions**:
1. Verify PostgreSQL is running:
   ```bash
   # Check port 5433
   netstat -an | findstr 5433
   ```

2. Check `.env` file exists with correct password:
   ```bash
   # Should contain: DB_PASSWORD=your_password
   cat .env
   ```

3. Test connection manually:
   ```bash
   psql -h localhost -p 5433 -U postgres -d option_trading
   ```

### API Rate Limiting

**Symptoms**: "Rate limit exceeded" in logs

**Solutions**:
1. Reduce collection frequency (increase interval)
2. Add exponential backoff in API calls
3. Verify API key limits (if using authenticated endpoints)

### Strategy Evaluation Errors

**Symptoms**: "No valid strategies found" or constraint violations

**Solutions**:
1. Check market regime normalization:
   ```python
   # Should be: 'bullish', 'bearish', 'neutral' (lowercase)
   ```

2. Verify ticker data has greeks:
   ```sql
   SELECT instrument_name, mark_iv, delta
   FROM snapshots
   WHERE greeks IS NOT NULL
   LIMIT 10;
   ```

3. Check strike selection config matches strategy type

### ML Prediction Failures

**Symptoms**: "Model not found" or heuristic fallback always used

**Solutions**:
1. Verify models exist:
   ```bash
   ls -la models/
   ```

2. Check data coverage:
   ```sql
   SELECT currency, COUNT(DISTINCT snapshot_hour)
   FROM hourly_snapshots
   GROUP BY currency;
   ```

3. Retrain models if data sufficient (720+ hours)

### GUI Crashes

**Symptoms**: Application freezes or closes unexpectedly

**Solutions**:
1. Check logs:
   ```bash
   tail -50 output/log/gui_*.log
   ```

2. Update dependencies:
   ```bash
   pip install --upgrade -r requirements.txt
   ```

3. Clear cached bytecode:
   ```bash
   find . -type d -name __pycache__ -exec rm -rf {} +
   python -m compileall coding/
   ```

---

## Testing

### Unit Tests

```bash
# Run all tests
pytest

# Run specific module
pytest tests/unit/strategy/

# Run with coverage
pytest --cov=coding tests/
```

### Integration Tests

```bash
# Test API connectivity
pytest tests/integration/test_deribit_api_service.py

# Test strategy evaluation end-to-end
pytest tests/integration/strategy/
```

### System Validation

```bash
# Comprehensive system health check
python -m scripts.validate_system

# Expected: 11+ passed, 0 failed
# Warnings OK if data < 30 days
```

---

## Performance Benchmarks

### Data Collection
- Collection cycle: ~7-10 seconds
- Memory usage: ~200-300 MB
- Database writes: ~50-100 rows/cycle

### Strategy Evaluation
- Single strategy: ~1-2 seconds
- Multiple strategies (4): ~5-7 seconds
- With chart generation: +2-3 seconds

### ML Inference
- Feature engineering: ~0.5 seconds
- Regime prediction: ~0.1 seconds
- Volatility prediction: ~0.1 seconds

---

## Roadmap

### Current Status (v1.0.0)
- ✅ Data collection pipeline operational
- ✅ Strategy evaluation system complete
- ✅ ML infrastructure ready
- ⏳ Accumulating training data (60/720 hours)

### Next 30 Days
- ⏳ Accumulate 720+ hours of data
- ⏳ ML model retraining
- 🔜 Production ML deployment

### Future Enhancements
- [ ] Additional strategies (Iron Condor, Straddle, Strangle)
- [ ] Real-time execution integration
- [ ] Portfolio management
- [ ] Risk management dashboard
- [ ] Backtesting framework
- [ ] Multi-exchange support

---

## Support

For issues or questions:
1. Check this documentation
2. Review CLAUDE.md for development guidelines
3. Run system validation to diagnose issues
4. Check logs in `output/log/`

---

**End of Documentation**

*Last Updated: February 8, 2026*
*System Version: 1.0.0*
*Documentation Version: 1.0*
