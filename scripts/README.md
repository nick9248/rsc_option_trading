# Scripts Directory

Utility scripts for system maintenance, testing, and validation.

## Production Scripts (Keep)

### Data Collection
- **check_collection_status.py** - Verify daemon is collecting data and database is up-to-date
- **start_collection_daemon.bat** - Windows batch file to start collection daemon

### Database Management
- **check_database.py** - Check database for collected trades (basic)
- **verify_tables.py** - Verify all required tables exist
- **apply_migration.py** - Apply database migrations (one-time use)

### Historical Data
- **backfill_historical_trades.py** - Download historical trades from Deribit API
- **aggregate_hourly_snapshots.py** - Aggregate trades into hourly snapshots for ML
- **run_full_backfill.py** - Master script for full backfill pipeline
- **check_backfill_status.py** - Monitor backfill progress

### System Validation
- **validate_system.py** - **[TO CREATE]** Comprehensive system health check

## Test Scripts (Development/Reference)

These were used during development. Can be removed after system is stable:

- **test_collection_manual.py** - Manual collection test
- **test_prospective_collection.py** - Test prospective collector
- **test_api_lookback_window.py** - Test API lookback limits
- **test_historical_trades_api.py** - Test historical trades API
- **test_recent_trades.py** - Test recent trades fetch
- **test_black_scholes.py** - Test Greeks calculation
- **test_label_generation.py** - Test label generation
- **test_end_to_end_pipeline.py** - End-to-end pipeline test
- **test_final_validation.py** - Final validation checklist

## Usage

### Daily Operations
```bash
# Check if collection daemon is working
python scripts/check_collection_status.py

# Validate entire system
python scripts/validate_system.py

# Check database state
python scripts/check_database.py
```

### Backfill Operations
```bash
# Run full backfill (6 months of BTC data)
python -m scripts.run_full_backfill --currency BTC --months 6

# Check backfill progress
python scripts/check_backfill_status.py
```

### Maintenance
```bash
# Aggregate new trades into hourly snapshots
python -m scripts.aggregate_hourly_snapshots --currency BTC
```
