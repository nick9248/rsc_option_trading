# Project Structure

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
├── claude_resources/      # Claude reference documentation
│   ├── project_structure.md    # This file
│   ├── strategy_system.md      # Strategy system details
│   └── testing_guide.md        # Testing methodology
```

**Structure Rule**: Code files must be inside related folders (e.g., `core/logging/logging_setup.py` not `core/logging_setup.py`).
