# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

- **Purpose**: Options trading automation with Deribit
- **Hardware**: Intel 14900K CPU + NVIDIA 5090 Suprim SOC Liquid GPU
- **Python Version**: 3.13
- **Testing Framework**: pytest

## Permissions

Full permissions for read, write, execute, and file management. Only removal operations require user approval.

## Communication Style

- Always say the truth without sugar coating
- Mention potential problems and risks proactively
- Explain trade-offs clearly
- Don't use over-the-top validation or excessive praise
- Focus on technical accuracy over emotional validation
- If uncertain about something, investigate to find the truth first rather than confirming user's beliefs

## Documentation Rules

Only create detailed documentation summaries when:
1. The entire project phase is finished
2. The user explicitly confirms it's time to document
3. Requested by the user

For small task completions: write concise summaries in console output only. Do NOT create separate documentation files unless requested.

## Project Structure

```
option_trading/
├── coding/
│   ├── core/              # Definitions, models, base classes
│   │   ├── api/           # API connection, parsing, validation
│   │   ├── analytics/     # Analysis classes (OnChainAnalyzer, GexDexCalculator, ChartGenerator)
│   │   ├── database/      # Database config, repository
│   │   ├── endpoints/     # API endpoint definitions
│   │   ├── logging/       # Logging configuration
│   │   ├── schemas/       # Response schemas for validation
│   │   └── strategy/      # Strategy system (NEW)
│   │       ├── definitions/   # Strategy classes (BaseStrategy, LongCall, LongPut)
│   │       ├── models/        # Data models (StrategySignal, StrategyConfig)
│   │       └── scoring/       # Scoring logic (IntrinsicScorer, OnChainScorer, CompositeScorer)
│   ├── gui/               # GUI components
│   │   ├── components/    # Reusable UI components
│   │   ├── tabs/          # Tab widgets (thin layer, calls services)
│   │   │   ├── api_connection_tab.py
│   │   │   ├── snapshot_tab.py
│   │   │   ├── database_tab.py
│   │   │   ├── on_chain_analysis_tab.py
│   │   │   └── strategy_tab.py  # Strategy evaluation (NEW)
│   │   └── theme/         # Styling and colors
│   └── service/           # High-level orchestration services
│       ├── deribit/       # Deribit API service
│       ├── database/      # Database capture service (orchestrates capture operations)
│       └── strategy/      # Strategy evaluation services (NEW)
│           ├── strategy_evaluation_service.py
│           └── strategy_finder_service.py
├── tests/
│   ├── unit/              # Unit tests
│   │   └── strategy/      # Strategy system tests (NEW)
│   └── integration/       # Integration tests
│       └── strategy/      # Strategy integration tests (NEW)
├── output/
│   ├── charts/            # Generated charts by type and expiration
│   ├── data/              # CSV exports and data files
│   └── log/               # Log files with timestamps
├── migrations/            # Database migrations
│   └── add_strategy_signals.sql  # Strategy signals table (NEW)
```

**Structure Rule**: Code files must be inside related folders (e.g., `core/logging/logging_setup.py` not `core/logging_setup.py`).

## Git Workflow

- **Repository**: https://github.com/nick9248/rsc_option_trading.git
- **Main branch**: `main`

For each task:
1. Create a new branch from main
2. Implement the task
3. Wait for user confirmation
4. Push and merge to main

## Architecture Principles

Layered architecture with clear separation:

```
Core (definitions/models)
    ↓
Base Methods (connect, fetch, parse, check)
    ↓
Services (high-level orchestration using base methods)
```

Example: For API fetching, have core definitions, then base methods (connect, fetch, parse, check), then services that orchestrate these methods.

## Coding Preferences

- Simple and clear without complexity unless truly needed
- Scalable for future expansion (e.g., Asset class with expandable attributes)
- Completely modular
- Clear, understandable docstrings

## Code Quality Checklist (MANDATORY)

**Before completing ANY code task, verify:**

1. **Layered Architecture**: Does the code follow Core → Service → GUI/CLI flow?
   - GUI/CLI should NEVER contain business logic or direct API calls
   - Services orchestrate operations using core components
   - Core contains definitions, models, and base methods

2. **Modularity**: Is each class/function doing ONE thing?
   - No monolithic classes with multiple responsibilities
   - Use strategy pattern for variations of same operation
   - Each capture/analysis type should be separate class, not if/elif chains

3. **Right Layer**: Is the code in the correct layer?
   - API calls → Service layer
   - Data models → Core layer
   - UI rendering → GUI layer
   - Business logic → Service layer (NOT GUI)

4. **No Shortcuts**: Even if it works, is it architecturally correct?
   - Quick solutions that violate architecture must be refactored
   - "It works" is not sufficient - it must be clean

**Example - WRONG (business logic in GUI):**
```python
# In GUI worker - BAD
for inst in instruments:
    ticker = service.get_ticker(instrument_name)  # API call in GUI!
    # ... process data
```

**Example - CORRECT (GUI calls service):**
```python
# In GUI worker - GOOD
result = capture_service.capture_gex_dex(currency, expiration)

# In service layer - business logic here
class DatabaseCaptureService:
    def capture_gex_dex(self, currency, expiration):
        # API calls and processing here
```

## Problem-Solving Approach

When fixing bugs or issues, follow structural thinking - not quick patches:

1. **Understand the flow first**: Before fixing, trace the data flow and understand WHY the problem exists
2. **Find the root cause**: Don't patch symptoms. If data is wrong, find where it becomes wrong in the pipeline
3. **Fix at the right layer**: The fix should be in the component responsible for that logic
4. **Maintain clean architecture**: Don't add external calls or workarounds that bypass the established flow

**Example - Wrong approach:**
```python
# Problem: OnChainAnalyzer has stale underlying_price
# Bad fix: Add separate API call in worker to fetch fresh price
perpetual_ticker = service.get_ticker(f"{currency}-PERPETUAL")
analyzer.underlying_price = perpetual_ticker.get("index_price")
```

**Example - Correct approach:**
```python
# Good fix: Fix the extraction logic inside OnChainAnalyzer
# The class receives the data, so it should extract the price correctly
def _extract_underlying_price(self, data):
    """Use mode (most common value) since stale instruments have old prices."""
    prices = [item.get("underlying_price") for item in data if item.get("underlying_price")]
    return Counter(prices).most_common(1)[0][0] if prices else 0.0
```

**Key principle**: If the same data source works correctly elsewhere (e.g., Snapshot tab), the problem is in how this component processes the data, not in the data itself.

**Data investigation example:**
When extracting a value from aggregated data (like `underlying_price` from book_summary), investigate the actual data distribution first:
```python
# Don't assume - investigate
from collections import Counter
prices = [item.get('underlying_price') for item in data]
print(Counter(prices).most_common(5))  # See what values exist

# Then find the pattern
# e.g., high-volume instruments have more recent data
active = [i for i in data if i.get('volume', 0) > 0]
highest_volume = max(active, key=lambda x: x.get('volume'))
# Use price from most active instrument
```

## Logging System

Use `logging` module. Never use `print()`.

```python
# At the top of every Python file:
import logging

# For standalone scripts/pipelines:
from coding.core.logging.logging_setup import init_logging
init_logging(level="INFO")
logger = logging.getLogger(__name__)

# For services/modules (logging already initialized):
logger = logging.getLogger(__name__)

# Usage:
logger.info("Starting capture...")
logger.warning("Connection timeout")
logger.error(f"Failed: {error}")
logger.debug("Detailed debug info")
```

## Naming Conventions

- Descriptive names related to the method's purpose
- No abbreviations
- No leading underscores

## Quality Control Workflow

After the first major task is completed, it becomes the **reference example**. All future code must be validated against this reference using agents:

1. **Code Quality Agent** - Checks adherence to coding preferences
2. **Naming Agent** - Validates naming conventions are followed
3. **Flow Correctness Agent** - Ensures architecture patterns match reference

## Strategy System

The strategy evaluation system scores and ranks option strategies based on intrinsic and on-chain metrics.

### Architecture

```
Core Layer (coding/core/strategy/)
├── definitions/           # Strategy classes (BaseStrategy, LongCall, LongPut)
├── models/               # Data models (StrategySignal, StrategyConfig)
└── scoring/              # Scoring logic (IntrinsicScorer, OnChainScorer, CompositeScorer)

Service Layer (coding/service/strategy/)
├── strategy_evaluation_service.py    # Evaluates strategies for single expiration
└── strategy_finder_service.py        # Scans multiple currencies/expirations

GUI Layer (coding/gui/tabs/)
└── strategy_tab.py                   # Strategy evaluation interface

Database
└── strategy_signals table            # Persisted scored signals
```

### How to Add New Strategies

1. Create new strategy class in `coding/core/strategy/definitions/`:
   ```python
   from .base_strategy import BaseStrategy, StrategyLeg

   class MyStrategy(BaseStrategy):
       @property
       def name(self) -> str:
           return "My Strategy"

       @property
       def strategy_type(self) -> str:
           return "directional_bullish"  # or directional_bearish, neutral, etc.

       def build_legs(self, ticker_data: Dict, **kwargs) -> None:
           # Implement leg construction logic
           pass
   ```

2. Register in `strategy_factory.py`:
   ```python
   STRATEGY_REGISTRY["My Strategy"] = MyStrategy
   ```

3. Strategy is now available in GUI and evaluation services.

### How to Add New Scorers

1. Create new scorer in `coding/core/strategy/scoring/`:
   ```python
   from .base_scorer import BaseScorer

   class MyScorer(BaseScorer):
       def calculate_score(self, strategy, market_context: Dict) -> float:
           # Return 0-10 score
           pass

       def get_breakdown(self, strategy, market_context: Dict) -> Dict[str, float]:
           # Return component scores
           pass
   ```

2. Integrate into `CompositeScorer` or use standalone.

### Scoring Components and Weights

**Intrinsic Scorer (default 50% weight)**:
- Risk/Reward Ratio (30%)
- Cost Efficiency (25%)
- Greek Profile (25%)
- Breakeven Distance (20%)

**On-Chain Scorer (default 50% weight)**:
- Max Pain Alignment (20%)
- GEX/DEX Support (20%)
- OI Levels (15%)
- Put/Call Ratio (15%)
- Volume Profile (15%)
- Trend Analysis (15%)

**Composite Score** = (Intrinsic × weight) + (On-Chain × weight)

Market regime penalties:
- Bullish strategy in bearish regime: 50% penalty
- Bearish strategy in bullish regime: 50% penalty

**Detailed scoring formulas and interpretation guide**: See `documentation/strategy_system_guide.md`

### Database Schema

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
    net_vega DECIMAL(8,6)
);
```

### GUI Usage (Strategies Tab)

1. Select currency and load expiry dates
2. Choose market regime (optional)
3. Select strategy (Long Call/Put)
4. Configure strike selection (delta/moneyness/specific)
5. Set filters (max loss %, take profit %)
6. Evaluate and view ranked results

**Detailed usage guide and score interpretation**: See `documentation/strategy_system_guide.md`

### Service Layer Usage (Programmatic)

```python
from coding.service.strategy import StrategyEvaluationService
from coding.core.strategy.models import StrategyConfig, StrikeConfig

# Create config
config = StrategyConfig(
    strategy_names=["Long Call", "Long Put"],
    expirations=["31JAN25"],
    strike_configs={
        "Long Call": StrikeConfig(method="by_delta", target_delta=0.30, quantity=1)
    },
    max_loss_filter=5.0,  # Max 5% loss
    market_regime="bullish",
    top_n=10
)

# Evaluate
service = StrategyEvaluationService(api_service, repository)
result = service.evaluate_strategies(
    currency="BTC",
    expiration="31JAN25",
    config=config
)

# Access signals
for signal in result.signals:
    print(f"{signal.strategy_name}: {signal.composite_score:.2f}")
```

### Key Design Decisions

1. **Trend Analysis**: Uses last 5 historical captures to detect max pain and volume trends
2. **Graceful Error Handling**: One strategy failure doesn't stop entire evaluation
3. **Regime Awareness**: Optional market regime parameter with basic penalty logic
4. **Extensibility**: Easy to add new strategies by inheriting from BaseStrategy
5. **Transparency**: Full score breakdowns stored for analysis
6. **Detailed Output**: Each evaluation generates comprehensive text file in `output/strategies/{expiration}/` with all market data, scores, and analysis

### Future Enhancements (Not Yet Implemented)

- ML-based weight optimization
- Advanced market regime detection
- Backtesting framework
- Complex multi-leg strategies (spreads, condors, etc.)
- Real-time execution integration
## Commands

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run a specific test
pytest tests/unit/test_file.py::test_function_name

# Run with verbose output
pytest -v
```
