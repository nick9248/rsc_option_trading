# Snapshot Tab Documentation

## Overview

The Snapshot tab provides a quick view of option chain data filtered by expiration dates and volume. It displays a table of all options with their prices, greeks, and market data, with the ability to export to CSV.

## Purpose

- View option chain data for selected expirations
- Filter instruments by minimum volume threshold
- Optionally enrich data with Greeks (delta, gamma, theta, vega)
- Export filtered data to CSV for analysis

## Features

### 1. Currency Selection
- Select currency (BTC or ETH)
- Load available expiration dates for that currency

### 2. Expiration Selection
- Multi-select list of available expirations
- Select one or multiple expirations to include
- Visual selection with checkboxes

### 3. Volume Filter
- Minimum volume threshold (default: 0 = no filter)
- Filter out low-volume/illiquid options
- Useful for focusing on actively traded strikes

### 4. Greek Enrichment (Optional)
- Checkbox to fetch Greeks via ticker endpoint
- Adds delta, gamma, theta, vega columns
- Note: Slower due to individual API calls per instrument

### 5. Data Table
- Interactive table with sortable columns
- Columns displayed:
  - Instrument Name
  - Expiration
  - Strike
  - Type (Call/Put)
  - Mark Price
  - Bid Price
  - Ask Price
  - Volume
  - Open Interest
  - IV (Implied Volatility)
  - Greeks (if enabled): Delta, Gamma, Theta, Vega
- Monospaced font for numbers
- Sortable by clicking column headers

### 6. Export to CSV
- Save filtered data to CSV file
- Default filename: `snapshot_{currency}_{timestamp}.csv`
- Saved to `output/data/` directory
- Includes all displayed columns

### 7. Progress Logging
- Real-time progress updates during fetch
- Shows number of instruments loaded
- Error messages if fetch fails

## Architecture

```
Snapshot Tab (GUI)
    ↓
SnapshotService (Service Layer)
    ↓
DeribitApiService (Core Layer)
```

**Service**: `coding/service/snapshot/snapshot_service.py`
**GUI**: `coding/gui/tabs/snapshot_tab.py`
**API**: `coding/service/deribit/deribit_api_service.py`

## Usage

1. Select currency (ETH or BTC)
2. Click "Load Expirations" to fetch available dates
3. Select one or more expirations from the list
4. Set minimum volume filter (e.g., 1.0 to filter out illiquid options)
5. Optionally check "Fetch Greeks" for delta/gamma/theta/vega
6. Click "Load Snapshot"
7. View data in the table
8. Click "Export to CSV" to save data

## Sample Data Table

| Instrument Name | Expiration | Strike | Type | Mark Price | Volume | OI | IV |
|----------------|------------|--------|------|------------|--------|----|----|
| ETH-14FEB26-2800-C | 14FEB26 | 2800 | C | 145.50 | 12.5 | 234.8 | 65.2% |
| ETH-14FEB26-2800-P | 14FEB26 | 2800 | P | 38.20 | 8.3 | 187.6 | 63.8% |
| ETH-14FEB26-3000-C | 14FEB26 | 3000 | C | 62.30 | 24.7 | 456.2 | 68.1% |
| ... | ... | ... | ... | ... | ... | ... | ... |

## Performance Considerations

### Without Greeks
- **Speed**: Fast (single API call per currency)
- **Data**: Basic market data (prices, volume, OI, IV)
- **Use Case**: Quick overview, export for external analysis

### With Greeks
- **Speed**: Slower (individual ticker calls for each instrument)
- **Data**: Complete market data + Greeks (delta, gamma, theta, vega)
- **Use Case**: Strategy analysis, delta-neutral positioning

**Recommendation**: Use Greeks only when needed for strategy analysis. For quick exports, disable Greeks.

## Use Cases

### 1. Quick Market Overview
- Select near-term expirations
- Set volume filter to 1.0+ to see active strikes
- Sort by volume to find most traded options

### 2. Strategy Analysis
- Select specific expiration
- Enable Greeks
- Export to CSV
- Analyze in Excel/Python for spread opportunities

### 3. Liquidity Screening
- Select all expirations
- Set high volume filter (10.0+)
- Identify which strikes have sufficient liquidity

### 4. IV Analysis
- Export data with IV column
- Compare implied volatility across strikes
- Identify volatility skew

## CSV Export Format

```csv
instrument_name,expiration,strike,type,mark_price,bid_price,ask_price,volume,open_interest,iv,delta,gamma,theta,vega
ETH-14FEB26-2800-C,14FEB26,2800,C,145.50,144.20,146.80,12.5,234.8,65.2,0.6234,0.0012,-0.34,1.23
ETH-14FEB26-2800-P,14FEB26,2800,P,38.20,37.50,38.90,8.3,187.6,63.8,-0.3766,0.0012,-0.32,1.21
...
```

## Important Notes

### Data Freshness
- Data is fetched live from Deribit API
- No caching (always fresh data)
- Prices update only when "Load Snapshot" is clicked

### Volume Filter
- Volume = 0: Include all instruments (even zero volume)
- Volume > 0: Filter out instruments with volume below threshold
- Useful for removing stale/illiquid options from view

### Multiple Expirations
- Can select and view multiple expirations in single table
- Table shows expiration date for each row
- Useful for comparing strikes across different expiries

### Greeks Accuracy
- Greeks are fetched from Deribit's ticker endpoint
- Values are Deribit's calculated greeks (not custom calculated)
- Delta/gamma/theta/vega are per-contract values

## Comparison: Snapshot vs Database Tab

| Feature | Snapshot Tab | Database Tab |
|---------|--------------|--------------|
| **Purpose** | Quick view & export | Historical tracking |
| **Storage** | None (ephemeral) | PostgreSQL database |
| **Output** | CSV file | Database + Charts |
| **History** | Single snapshot | Full historical data |
| **Filtering** | Volume threshold | N/A |
| **Use Case** | Data export for analysis | Long-term trend tracking |

## Future Enhancements

- Save/load filter presets
- Live updating table (auto-refresh)
- Advanced filters (IV range, moneyness)
- Chart view (price distribution by strike)
- Delta-neutral spread suggestions
