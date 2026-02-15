# Buy/Sell Flow Charts - Database-Driven Interface

**Status**: ✅ Implemented
**Date**: 2026-02-15
**Version**: 1.0

## Overview

Redesigned the buy/sell flow analysis system with a database-driven, tabbed interface for better UX and data persistence. The system analyzes trade direction (buy vs sell aggressor side) to identify market conviction and directional pressure.

## What Changed

### Before
- ❌ Cluttered UI (export button, 2 checkboxes, embedded charts)
- ❌ Static HTML charts saved to disk
- ❌ No easy way to compare expirations
- ❌ GEX/DEX and flow analysis were optional
- ❌ No automatic report saving

### After
- ✅ Clean UI (single "View Flow Charts" button)
- ✅ Dynamic charts from database
- ✅ Tabbed interface for easy navigation
- ✅ GEX/DEX and flow always enabled
- ✅ Auto-save reports per expiration
- ✅ Interactive charts with hover highlighting

## Architecture

### Database Layer
**File**: `coding/core/database/repository.py`

**New Methods**:
```python
save_flow_metrics(currency, expiration, flow_data, underlying_price, window_hours)
# Saves per-strike buy/sell metrics to database for fast queries

get_flow_metrics(currency, expiration, limit=1)
# Retrieves latest flow data, reconstructs flow_data structure

get_active_expirations_with_flow(currency)
# Gets expirations with flow data, sorted by OI (highest first)
```

**Database Schema**:
```sql
CREATE TABLE buy_sell_flow_metrics (
    id SERIAL PRIMARY KEY,
    captured_at TIMESTAMP NOT NULL,
    window_hours INTEGER NOT NULL DEFAULT 24,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,
    strike DECIMAL(12,2) NOT NULL,
    option_type CHAR(1) NOT NULL,  -- 'C' or 'P'
    buy_count INTEGER,
    buy_volume DECIMAL(18,8),
    buy_notional DECIMAL(20,4),
    sell_count INTEGER,
    sell_volume DECIMAL(18,8),
    sell_notional DECIMAL(20,4),
    net_flow DECIMAL(18,8),
    buy_sell_ratio DECIMAL(10,4),
    underlying_price DECIMAL(16,4)
);
```

### Service Layer
**File**: `coding/service/on_chain/on_chain_analysis_service.py`

**Changes**:
1. **Always-on analysis**: Removed optional parameters, GEX/DEX and flow always run
2. **Database storage**: Saves flow metrics to DB after each analysis
3. **Auto-save reports**: Parses and saves per-expiration sections

**Key Methods**:
```python
def fetch_and_analyze(currency, progress_callback) -> str:
    # Always runs GEX/DEX and buy/sell flow
    # Saves flow metrics to database
    # Auto-saves reports per expiration

def _save_reports_per_expiration(full_report, currency, analyzer):
    # Parses report by "EXPIRATION:" delimiter
    # Saves header + section per expiration
    # Location: output/data/onchain_analysis/{currency}/{expiration}/
```

### GUI Layer
**File**: `coding/gui/dialogs/flow_charts_window.py` (NEW)

**FlowChartsWindow Dialog**:
- Fullscreen modal window
- Expiration selector dropdown (sorted by OI)
- Three tabs: Distribution, Net Flow, Trend
- Charts load from database on-demand
- Info button with detailed explanations

**Removed from OnChainAnalysisTab**:
- GEX/DEX checkbox
- Buy/Sell Flow checkbox
- Export button
- Embedded FlowChartsWidget

**Added to OnChainAnalysisTab**:
- "View Flow Charts" button
- Simplified 2-section layout (report + log)

## Chart Features

### 1. Distribution by Strike
**Purpose**: Show buy/sell activity distribution across strikes

**Features**:
- Population pyramid style (puts left, calls right)
- 4 distinct colors:
  - Bright Green (#00ff88): Call Buy
  - Bright Red (#ff4444): Call Sell
  - Cyan (#00d4ff): Put Buy
  - Orange (#ff9500): Put Sell
- Grouped bars (side-by-side, not stacked)
- Toggle metrics: Notional / Volume / Trade Count
- Spot price marker

### 2. Net Flow by Strike
**Purpose**: Show net buying/selling pressure per strike

**Features**:
- Green bars: Net buying (buy > sell)
- Red bars: Net selling (sell > buy)
- Separate traces for calls and puts
- Bar height = magnitude of net flow

### 3. Flow Trend Over Time
**Purpose**: Show historical flow trends (7-day lookback)

**Features**:
- Time series of 4 flows
- Hourly aggregation
- Identifies acceleration/deceleration
- Detects regime shifts

### Interactive Features
1. **Click Legend**: Isolate that trace (hide others)
2. **Double-Click Legend**: Toggle trace on/off
3. **Hover Legend**: Highlight trace (dims others to 15%)
4. **Info Button**: Shows detailed chart explanations

## File Structure

```
coding/
├── core/
│   └── database/
│       └── repository.py           (+ 3 methods, 180 LOC)
├── service/
│   └── on_chain/
│       └── on_chain_analysis_service.py  (modified, +90 LOC)
└── gui/
    ├── dialogs/
    │   ├── __init__.py            (NEW)
    │   └── flow_charts_window.py  (NEW, 350 LOC)
    └── tabs/
        └── on_chain_analysis_tab.py     (simplified, -110 net LOC)

migrations/
└── 009_add_buy_sell_flow_metrics.sql    (NEW)

output/data/onchain_analysis/
└── {currency}/
    └── {expiration}/
        └── report_{timestamp}.txt       (auto-saved)
```

## Usage

### GUI Workflow
1. Open GUI: `python -m coding.gui.main`
2. Go to "On-Chain Analysis" tab
3. Select currency (BTC/ETH)
4. Click "Load Analysis"
   - GEX/DEX analysis runs automatically
   - Buy/Sell flow analysis runs automatically
   - Flow metrics saved to database
   - Reports auto-saved per expiration
5. Click "View Flow Charts"
   - Select expiration from dropdown
   - Switch between tabs (Distribution / Net Flow / Trend)
   - Click "ℹ️ Chart Info" for explanations

### Report Structure
**Full report in GUI**: Contains all expirations

**Per-expiration files**:
```
output/data/onchain_analysis/BTC/27MAR26/report_20260215_223553.txt

Contents:
[HEADER - Market Metrics]
[EXPIRATION: 27MAR26 - Only this expiration's data]
```

## Data Verification

All calculations verified against raw API data:
- ✅ Total OI: Exact match (within floating point precision)
- ✅ Volume: Exact match
- ✅ Moneyness: Perfect percentage match (4.37%, 95.63%, etc.)
- ✅ Buy/Sell Flow: Perfect match (84.7, 156.0, 125.6, 101.3)

**Verification Script**: `scripts/verify_report_27MAR26.py`

## Performance

- **Database queries**: < 1 second (indexed)
- **Chart generation**: < 1 second per chart
- **Chart switching**: Instant (no lag between tabs)
- **Memory**: Efficient (charts generated on-demand)

## Key Design Decisions

### 1. Database-Driven vs Static Files
**Decision**: Store flow metrics in database, generate charts on-demand

**Rationale**:
- Enables historical analysis
- Fast queries with indexes
- No disk space issues with static files
- Flexible filtering by expiration

### 2. Tabs vs Stacked Charts
**Decision**: Use tabbed interface

**Rationale**:
- One chart visible at a time
- Better readability
- Less scrolling
- Cleaner UI

### 3. Always-On Analysis
**Decision**: Remove optional checkboxes, always run GEX/DEX and flow

**Rationale**:
- These metrics are core to on-chain analysis
- Optional features add UI clutter
- Users always want complete analysis

### 4. Grouped vs Stacked Bars
**Decision**: Grouped bars (side-by-side)

**Rationale**:
- Easier to compare buy vs sell at each strike
- All 4 colors clearly visible
- No obscured data behind stacked bars

### 5. Data Structure (C/P vs calls/puts)
**Decision**: Use "C" and "P" as option_type keys

**Rationale**:
- Matches BuySellFlowAnalyzer output
- Consistent with chart generator expectations
- Single-character keys are more compact

## Testing Checklist

- [x] Migration creates table successfully
- [x] Flow metrics save to database
- [x] Flow metrics retrieve correctly
- [x] Active expirations query works
- [x] Data types correct (float, not Decimal)
- [x] Reports save per expiration
- [x] Reports contain only relevant section
- [x] GUI charts load without errors
- [x] Tabs switch smoothly
- [x] Charts are full screen
- [x] Colors are distinct (4 different)
- [x] Bars are grouped (not stacked)
- [x] Legend interactions work
- [x] Info button shows explanations
- [x] Manual verification of calculations

## Common Issues & Solutions

### Charts show white screen
**Cause**: Invalid HTML or JavaScript errors
**Solution**: Simplified write_html() with minimal config, added error handling

### Decimal vs float arithmetic errors
**Cause**: Database returns Decimal types
**Solution**: Convert all Decimals to float in repository methods

### Reports in wrong location
**Cause**: Relative paths depend on CWD
**Solution**: Use absolute paths from project root

### Stacked bars hard to read
**Cause**: barmode="relative"
**Solution**: Changed to barmode="group"

### Charts not full screen
**Cause**: Fixed height constraints
**Solution**: Use autosize=True, responsive config

## Future Enhancements

### Potential Improvements
- [ ] Multi-expiration comparison (side-by-side charts)
- [ ] Export individual charts to PNG/PDF
- [ ] Configurable lookback windows (4h, 12h, 24h, 7d)
- [ ] Flow alerts (detect unusual buying/selling)
- [ ] Historical flow playback (animate over time)

### Integration Opportunities
- Could reuse DatabaseCaptureService strategies (avoid duplication)
- Could add flow metrics to Database tab for manual capture
- Could integrate with strategy scoring system

## Related Documentation

- **Testing Guide**: `claude_resources/testing_guide.md`
- **Project Structure**: `claude_resources/project_structure.md`
- **System Overview**: `documentation/system_overview.md`
- **Database Schema**: `migrations/009_add_buy_sell_flow_metrics.sql`

## Conclusion

The redesigned buy/sell flow charts system provides:
- ✅ **Cleaner UX**: Simplified interface, tabbed charts
- ✅ **Better Data**: Database-driven, persistent storage
- ✅ **Richer Analysis**: Interactive charts, detailed explanations
- ✅ **Quality Code**: Follows CLAUDE.md standards, verified calculations

The system successfully balances functionality, performance, and code quality while maintaining clean architecture and excellent user experience.
