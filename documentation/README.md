# Documentation Organization

This directory contains comprehensive documentation for the Options Trading Platform.

**Last Updated**: February 13, 2026 - Reorganized into logical subdirectories

## 📁 Directory Structure

```
documentation/
├── README.md                          # This file
├── system_overview.md                 # High-level system architecture
├── strategy_scoring_calculation_example.md
│
├── ml/                                # ML & Analytics Documentation
│   ├── ml_for_decision_making_complete.md     # Complete ML system guide
│   └── ml_data_collection_system.md           # Data collection technical reference
│
├── gui_tabs/                          # GUI Tab Documentation
│   ├── tab_api_connection.md
│   ├── tab_database.md
│   ├── tab_snapshot.md
│   ├── tab_on_chain_analysis.md
│   ├── tab_market_regime.md
│   ├── tab_strategies.md
│   └── tab_system_validation.md
│
├── features/                          # Feature Implementation Guides
│   ├── feature_market_regime_detection.md
│   └── feature_strategy_system.md
│
└── archive/                           # Historical/Deprecated Documentation
    └── ... (old docs and research)
```

## 📚 Documentation Categories

### 🔬 ML & Analytics (`ml/`)

Machine learning and analytics systems:

- **`ml_for_decision_making_complete.md`** ⭐ - **COMPREHENSIVE ML GUIDE**
  - Complete project goals, architecture, and status
  - What was achieved (data collection infrastructure)
  - What remains (ML model training)
  - Feature engineering (Flow-based GEX, VRP, 80+ features)
  - Database verification results
  - Timeline and roadmap

- **`ml_data_collection_system.md`** - Technical data collection reference
  - TradeCollector, ProspectiveCollector, UnifiedScheduler
  - Architecture diagrams
  - Database schemas
  - API endpoints
  - Troubleshooting guide

### 🎨 GUI Tabs (`gui_tabs/`)

Documentation for each PySide6 GUI tab:

- **`tab_api_connection.md`** - API connection testing
- **`tab_database.md`** - Database operations and capture controls
- **`tab_snapshot.md`** - Real-time snapshot viewer
- **`tab_on_chain_analysis.md`** - On-chain analytics (max pain, GEX/DEX)
- **`tab_market_regime.md`** - Market regime detection
- **`tab_strategies.md`** - Strategy evaluation and scoring
- **`tab_system_validation.md`** - System health checks

### 🚀 Features (`features/`)

Detailed feature implementation guides:

- **`feature_market_regime_detection.md`** - Regime detection methodology
- **`feature_strategy_system.md`** - Strategy evaluation framework
- **`feature_otm_contract_finder.md`** - OTM contract finder (4-gate pipeline, Kelly sizing, GUI)

### 📊 Top-Level

Core system documentation:

- **`system_overview.md`** - High-level system architecture
- **`strategy_scoring_calculation_example.md`** - Scoring methodology

## Documentation Structure

### Tab Documentation
Each tab document includes:
- **Overview**: Purpose and functionality
- **Features**: List of all features with descriptions
- **Architecture**: Component diagram and file locations
- **Usage**: Step-by-step usage instructions
- **Sample Output**: Example data/reports
- **Use Cases**: Common usage scenarios
- **Important Notes**: Performance, limitations, best practices
- **Future Enhancements**: Planned improvements

### Feature Documentation
Each feature document includes:
- **System Architecture**: Component relationships
- **Implementation Details**: How it works internally
- **Configuration Options**: Customization and tuning
- **Integration Points**: How it connects with other systems
- **Examples**: Code examples and usage patterns
- **Reference Information**: Formulas, algorithms, data structures

### System Documentation
System-level documentation includes:
- **Setup Instructions**: Installation and configuration
- **Database Schema**: Table structures and relationships
- **Data Collection**: Daemon setup and monitoring
- **ML Pipeline**: Model training and inference
- **Maintenance**: Backup, monitoring, troubleshooting
- **Production Readiness**: Requirements and checklists

## Quick Reference

### For GUI Users
Start with tab documentation:
1. `tab_api_connection.md` - Test connectivity first
2. `tab_snapshot.md` - Quick data export
3. `tab_on_chain_analysis.md` - Market overview
4. `tab_database.md` - Historical tracking
5. `tab_strategies.md` - Strategy evaluation
6. `tab_market_regime.md` - Market regime detection
7. `tab_system_validation.md` - Health monitoring

### For Developers
Start with system and feature documentation:
1. `system_overview.md` - Understand overall architecture
2. `feature_strategy_system.md` - Strategy system deep dive
3. `feature_market_regime_detection.md` - Regime detection internals

### For Strategy Development
Follow this sequence:
1. `feature_strategy_system.md` - Understand scoring system
2. `strategy_scoring_calculation_example.md` - See calculation details
3. `tab_strategies.md` - Use GUI for evaluation

## Archive

The `archive/` directory contains deprecated documentation from previous versions:
- `archive/GUI_DOCUMENTATION.md` - Old GUI documentation (superseded by tab_*.md files)

## Updating Documentation

When creating new documentation:
1. Follow the naming pattern: `{type}_{name}.md`
2. Use consistent section structure
3. Include practical examples
4. Update this README to reference new files
5. Move old/deprecated docs to archive/

When updating major features:
1. Update relevant tab documentation
2. Update feature documentation if architecture changes
3. Update system documentation if integration points change
4. Keep examples current with latest API/features
