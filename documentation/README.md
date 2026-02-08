# Documentation Organization

This directory contains comprehensive documentation for the Options Trading Platform.

## Naming Pattern

All documentation files follow a consistent naming pattern: `{type}_{name}.md`

### Documentation Types

#### Tab Documentation (`tab_*.md`)
GUI tab-specific documentation covering features, usage, and workflows.

- `tab_api_connection.md` - API Connection tab
- `tab_snapshot.md` - Snapshot tab (option chain data export)
- `tab_on_chain_analysis.md` - On Chain Analysis tab (market analysis reports)
- `tab_database.md` - Database tab (historical data capture and charts)
- `tab_strategies.md` - Strategies tab (strategy evaluation and ranking)
- `tab_market_regime.md` - Market Regime tab (regime detection)
- `tab_system_validation.md` - System Validation tab (health checks)

#### Feature Documentation (`feature_*.md`)
Detailed guides for major system features and subsystems.

- `feature_strategy_system.md` - Strategy evaluation system architecture
- `feature_market_regime_detection.md` - Market regime detection methodology

#### System Documentation (`system_*.md`)
System-level documentation covering architecture, setup, and maintenance.

- `system_overview.md` - Comprehensive system documentation

#### Examples and References
- `strategy_scoring_calculation_example.md` - Detailed scoring calculation walkthrough

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
