# Options Trading Platform - GUI Documentation

## Overview

The Options Trading Platform GUI provides a modern, minimal luxury interface for interacting with the Deribit cryptocurrency options exchange. Built with PySide6, it features a dark theme with gold accents for a premium visual experience.

## Architecture

```
coding/gui/
├── __init__.py
├── main_window.py          # Main application window with tab bar
├── app.py                  # Application entry point
├── theme/
│   ├── colors.py           # Color palette definitions
│   └── styles.py           # QSS stylesheet generation
├── components/
│   └── log_viewer.py       # Reusable log display component
└── tabs/
    ├── api_connection_tab.py   # API endpoint testing tab
    └── snapshot_tab.py         # Option chain snapshot tab
```

## Running the GUI

```bash
# Activate virtual environment
.venv\Scripts\activate

# Run the application
python -m coding.gui.app
```

## Tabs

### 1. API Connection Tab

The API Connection tab allows testing individual Deribit API endpoints with configurable parameters.

**Features:**
- Dropdown to select API endpoint
- Dynamic parameter fields based on selected endpoint
- Run button to execute API call
- Log viewer displaying results and errors
- Save to CSV option

**Supported Endpoints:**
| Endpoint | Description |
|----------|-------------|
| Test | Connection test |
| Get Expirations | Available expiration dates |
| Get Instruments | List of tradeable instruments |
| Book Summary | Market summary by currency |
| Ticker | Real-time ticker data |
| Order Book | Order book depth data |
| Funding Chart Data | Perpetual funding history |
| Historical Volatility | Historical volatility data |
| Volatility Index Data | VIX-like index data |

**Dynamic Instrument Loading:**
For Order Book and Ticker endpoints, instruments can be loaded dynamically:
1. Select currency (BTC/ETH)
2. Select kind (future/option/perpetual)
3. Click "Load" to populate available instruments
4. Select specific instrument from dropdown

### 2. Snapshot Tab

The Snapshot tab captures option chain data for selected expirations.

**Features:**
- Load expirations by currency (BTC/ETH/USDC/USDT)
- Multi-select expiration dates with "Select All" option
- Volume filter to exclude low-volume options
- Fetch Greeks option (delta, gamma, vega, theta, rho)
- Modified format CSV export with USD price calculations

**Modified CSV Format:**
When "Modified Format" is enabled, the CSV includes:
- `instrument_name` - Option identifier
- `bid_price`, `ask_price`, `mid_price`, `mark_price` - Prices in base currency
- `bid_price_usd`, `ask_price_usd`, `mid_price_usd`, `mark_price_usd` - USD equivalent prices
- `underlying_price`, `underlying_index` - Underlying asset data
- `volume`, `volume_usd`, `open_interest` - Trading activity
- `mark_iv`, `interest_rate` - Implied volatility and rates
- Greeks (if enabled): `delta`, `gamma`, `vega`, `theta`, `rho`
- `timestamp` - Human-readable format (YYYY-MM-DD HH:MM:SS)

**SYN ETH vs ETH:**
- **ETH options**: Settled in ETH, premium paid/received in ETH
- **SYN ETH options**: Synthetically settled in USDC, prices shown in ETH but settled in stablecoin

## Theme

The GUI uses a luxury dark theme with the following color palette:

| Element | Color |
|---------|-------|
| Background Primary | #0D0D0F |
| Background Secondary | #141418 |
| Text Primary | #E8E6E3 |
| Text Secondary | #A0A0A0 |
| Accent (Gold) | #B8860B |
| Success | #2E7D32 |
| Error | #C62828 |
| Warning | #F9A825 |

## Responsive Design

The GUI adapts to window sizes:
- Minimum size: 600x400 pixels
- Default size: 1200x800 pixels
- All layouts use flexible sizing policies

## Threading

API calls run in background threads (QThread) to prevent UI freezing:
- Worker classes emit signals on completion
- Results are processed in the main thread
- Progress feedback via log viewer

## CSV Output

CSV files are saved to: `output/data/`

Naming convention:
- Snapshot: `snapshot_{currency}_{timestamp}.csv`
- API results: `{endpoint}_{timestamp}.csv`

## Dependencies

- PySide6 >= 6.6.0 (GUI framework)
- All other dependencies from main project requirements.txt
