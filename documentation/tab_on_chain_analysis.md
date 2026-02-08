# On Chain Analysis Tab Documentation

## Overview

The On Chain Analysis tab provides a comprehensive text-based analysis report for options market data. It analyzes max pain, open interest, support/resistance levels, and optionally GEX/DEX metrics.

## Purpose

- Generate formatted analysis reports for selected currency
- View max pain strike and distance from current price
- Analyze open interest distribution and put/call ratios
- Identify key support and resistance levels
- Optional: Calculate Gamma Exposure (GEX) and Delta Exposure (DEX)
- Export reports to text files for record-keeping

## Features

### 1. Currency Selection
- Select currency (BTC or ETH)
- Analysis runs for all available expirations for that currency

### 2. GEX/DEX Analysis (Optional)
- Checkbox to enable Greek fetching
- Calculates gamma and delta exposure across strikes
- Identifies call resistance and put support levels
- Note: Slower due to individual API calls for Greeks

### 3. Analysis Report
- Formatted text report with sections:
  - **Summary**: Currency, timestamp, number of expirations
  - **Max Pain**: Strike price and distance from current price
  - **Open Interest**: Total call/put OI and put/call ratio
  - **Support/Resistance**: Top 3 levels by OI or GEX
  - **GEX/DEX** (if enabled): Net exposure, call resistance, put support, HVL
- Monospaced font for easy reading
- Color-coded headers

### 4. Export Functionality
- Save report to text file
- Default filename: `on_chain_analysis_{currency}_{timestamp}.txt`
- Saved to `output/data/` directory

### 5. Progress Logging
- Real-time progress updates during fetch
- Shows which expirations are being processed
- Error messages if fetch fails

## Architecture

```
On Chain Analysis Tab (GUI)
    ↓
OnChainAnalysisService (Service Layer)
    ↓
DeribitApiService + OnChainAnalyzer (Core Layer)
```

**Service**: `coding/service/on_chain/on_chain_analysis_service.py`
**GUI**: `coding/gui/tabs/on_chain_analysis_tab.py`
**Core**: `coding/core/analytics/on_chain_analyzer.py`

## Usage

1. Select currency (ETH or BTC)
2. Optionally check "Fetch Greeks for GEX/DEX" (slower but more detailed)
3. Click "Load Analysis"
4. View report in the text area
5. Click "Export Report" to save to file

## Sample Report

```
================================================================================
ON-CHAIN OPTIONS ANALYSIS - ETH
================================================================================
Timestamp: 2026-02-08 17:30:15
Total Expirations: 5

--------------------------------------------------------------------------------
EXPIRATION: 14FEB26
--------------------------------------------------------------------------------

MAX PAIN: $2,800.00
Current Price: $2,906.50
Distance from Max Pain: $106.50 (+3.67%)

OPEN INTEREST:
  Total Call OI: 15,234.5
  Total Put OI: 12,876.3
  Put/Call Ratio: 0.845 (Bullish)

RESISTANCE LEVELS (by OI):
  1. $3,000.00 - OI: 2,456.8
  2. $3,200.00 - OI: 1,987.4
  3. $3,400.00 - OI: 1,654.2

SUPPORT LEVELS (by OI):
  1. $2,600.00 - OI: 2,123.6
  2. $2,400.00 - OI: 1,765.9
  3. $2,200.00 - OI: 1,432.1

[Additional expirations follow...]
```

## Important Notes

### Performance
- Without GEX/DEX: Fast (single API call per expiration)
- With GEX/DEX: Slower (individual ticker calls for Greeks on all strikes)
- Consider disabling GEX/DEX for quick overview analysis

### Data Source
- Uses live Deribit API data
- No database storage (analysis is ephemeral)
- For persistent tracking, use Database tab instead

### Use Cases
- Quick market overview for decision-making
- Identify potential price magnets (max pain)
- Find high-volume strikes for spread strategies
- Export reports for historical reference

## Comparison: On Chain Analysis vs Database Tab

| Feature | On Chain Analysis Tab | Database Tab |
|---------|----------------------|--------------|
| **Purpose** | Quick text report | Historical data capture |
| **Storage** | None (ephemeral) | PostgreSQL database |
| **Output** | Text report | Database + Charts |
| **Speed** | Fast | Slower (database writes) |
| **History** | Single snapshot | Full historical tracking |
| **Export** | Text file | Charts (HTML/PNG) |
| **Use Case** | Quick analysis | Long-term trend analysis |

## Future Enhancements

- Compare multiple currencies side-by-side
- Historical report comparison
- Automated report scheduling
- Alert system for max pain shifts
- Integration with strategy evaluation
