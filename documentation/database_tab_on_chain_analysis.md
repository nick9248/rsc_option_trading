# Database Tab & On-Chain Analysis Documentation

## Overview

The Database Tab provides a comprehensive system for capturing, storing, and visualizing on-chain options data from Deribit. It follows a layered architecture pattern with clear separation between GUI, Service, and Core layers.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         GUI Layer                                │
│                    (database_tab.py)                            │
│  - Thin wrapper, no business logic                              │
│  - Handles UI rendering and user interactions                   │
│  - Delegates all operations to Service layer                    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Service Layer                              │
│          (capture_service.py, capture_strategies.py)            │
│  - Orchestrates capture operations                              │
│  - Strategy pattern for different capture types                 │
│  - Coordinates between API, Analyzer, and Repository            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Core Layer                                │
│  - repository.py: Database operations                           │
│  - chart_generator.py: Plotly chart generation                  │
│  - on_chain_analyzer.py: Data parsing and analysis              │
│  - gex_dex_calculator.py: GEX/DEX calculations                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
coding/
├── service/
│   └── database/
│       ├── __init__.py              # Module exports
│       ├── capture_service.py       # Main orchestration service
│       └── capture_strategies.py    # Strategy pattern implementations
├── gui/
│   └── tabs/
│       └── database_tab.py          # GUI layer (thin wrapper)
└── core/
    ├── database/
    │   └── repository.py            # Database CRUD operations
    └── analytics/
        ├── chart_generator.py       # Plotly chart generation
        ├── on_chain_analyzer.py     # Data parsing and analysis
        └── gex_dex_calculator.py    # GEX/DEX calculations
```

---

## Database Tables

### 1. snapshots
Stores raw book summary data for each capture.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| captured_at | TIMESTAMP | Capture timestamp |
| currency | VARCHAR(10) | Currency symbol (ETH, BTC) |
| instrument_name | VARCHAR(50) | Full instrument name |
| expiration | VARCHAR(20) | Expiration date string |
| strike | DECIMAL | Strike price |
| option_type | CHAR(1) | C (call) or P (put) |
| open_interest | DECIMAL | Open interest |
| volume | DECIMAL | Trading volume |
| volume_usd | DECIMAL | Volume in USD |
| underlying_price | DECIMAL | Underlying asset price |
| mark_price | DECIMAL | Mark price |
| bid_price | DECIMAL | Best bid |
| ask_price | DECIMAL | Best ask |

### 2. max_pain
Tracks max pain strike over time.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| captured_at | TIMESTAMP | Capture timestamp |
| currency | VARCHAR(10) | Currency symbol |
| expiration | VARCHAR(20) | Expiration date |
| max_pain_strike | DECIMAL | Max pain strike price |
| underlying_price | DECIMAL | Current underlying price |
| distance_from_price | DECIMAL | Price - Max Pain |
| distance_percent | DECIMAL | Distance as percentage |

### 3. open_interest
Tracks open interest by type.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| captured_at | TIMESTAMP | Capture timestamp |
| currency | VARCHAR(10) | Currency symbol |
| expiration | VARCHAR(20) | Expiration date |
| total_call_oi | DECIMAL | Total call open interest |
| total_put_oi | DECIMAL | Total put open interest |
| total_oi | DECIMAL | Combined open interest |
| put_call_ratio | DECIMAL | Put/Call ratio |
| underlying_price | DECIMAL | Current underlying price |

### 4. volume
Tracks trading volume by type.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| captured_at | TIMESTAMP | Capture timestamp |
| currency | VARCHAR(10) | Currency symbol |
| expiration | VARCHAR(20) | Expiration date |
| total_call_volume | DECIMAL | Total call volume |
| total_put_volume | DECIMAL | Total put volume |
| total_volume | DECIMAL | Combined volume |
| volume_put_call_ratio | DECIMAL | Volume P/C ratio |
| underlying_price | DECIMAL | Current underlying price |

### 5. levels
Tracks support/resistance levels.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| captured_at | TIMESTAMP | Capture timestamp |
| currency | VARCHAR(10) | Currency symbol |
| expiration | VARCHAR(20) | Expiration date |
| level_type | VARCHAR(30) | Type (resistance_1, support_1, etc.) |
| strike | DECIMAL | Strike price |
| oi_or_gex_value | DECIMAL | OI or GEX value at level |
| underlying_price | DECIMAL | Current underlying price |

### 6. gex_dex
Tracks gamma and delta exposure.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| captured_at | TIMESTAMP | Capture timestamp |
| currency | VARCHAR(10) | Currency symbol |
| expiration | VARCHAR(20) | Expiration date |
| total_net_gex | DECIMAL | Total net gamma exposure |
| total_net_dex | DECIMAL | Total net delta exposure |
| call_resistance_strike | DECIMAL | Call resistance strike |
| call_resistance_gex | DECIMAL | GEX at call resistance |
| put_support_strike | DECIMAL | Put support strike |
| put_support_gex | DECIMAL | GEX at put support |
| hvl_strike | DECIMAL | High Volume Level (zero gamma) |
| underlying_price | DECIMAL | Current underlying price |

---

## Service Layer Components

### DatabaseCaptureService

The main orchestration service that coordinates capture operations.

```python
class DatabaseCaptureService:
    def __init__(self, repository=None, progress_callback=None):
        """Initialize with optional repository and progress callback."""

    def capture(self, capture_type: str, currency: str, generate_charts: bool = True) -> CaptureResult:
        """
        Perform a single capture operation.

        Args:
            capture_type: Type of capture (snapshot, max_pain, etc.)
            currency: Currency symbol (ETH, BTC)
            generate_charts: Whether to generate charts after capture

        Returns:
            CaptureResult with operation details
        """

    def capture_all(self, currency: str, generate_charts: bool = True) -> List[CaptureResult]:
        """Capture all data types sequentially."""
```

### CaptureResult

Data class holding capture operation results.

```python
class CaptureResult:
    capture_type: str       # Type of capture performed
    record_count: int       # Number of records saved
    chart_paths: List[str]  # List of generated chart paths
    success: bool           # Whether capture was successful
    error: Optional[str]    # Error message if failed
```

### Capture Strategies

Strategy pattern implementation for different capture types. Each strategy implements:

```python
class CaptureStrategy(ABC):
    @abstractmethod
    def capture(self, analyzer, raw_data, captured_at) -> int:
        """Capture data to database. Returns record count."""

    @abstractmethod
    def generate_charts(self, analyzer) -> List[str]:
        """Generate charts. Returns list of chart paths."""
```

#### Available Strategies

| Strategy | Description | Charts Generated |
|----------|-------------|------------------|
| SnapshotCaptureStrategy | Raw book summary data | OI Distribution, Volume Distribution |
| MaxPainCaptureStrategy | Max pain calculation | Max Pain vs Price Trend |
| OpenInterestCaptureStrategy | Open interest tracking | OI Trend, P/C Ratio Trend |
| VolumeCaptureStrategy | Volume tracking | Volume Trend |
| LevelsCaptureStrategy | Support/Resistance levels | Levels Trend |
| GexDexCaptureStrategy | Gamma/Delta exposure | GEX/DEX Trend (3-panel) |

---

## Chart Generation

### Output Structure

Charts are organized in subfolders by type and expiration:

```
output/charts/
├── snapshot/
│   ├── 17JAN26/
│   │   ├── oi_snapshot_ETH_20260116_110205.html
│   │   ├── oi_snapshot_ETH_20260116_110205.png
│   │   ├── volume_snapshot_ETH_20260116_110206.html
│   │   └── volume_snapshot_ETH_20260116_110206.png
│   └── 18JAN26/
│       └── ...
├── max_pain/
│   └── {expiration}/
├── open_interest/
│   └── {expiration}/
├── pc_ratio/
│   └── {expiration}/
├── volume/
│   └── {expiration}/
├── levels/
│   └── {expiration}/
└── gex_dex/
    └── {expiration}/
```

### Chart Types

#### Snapshot Charts
- **OI Distribution**: Bar chart showing call/put open interest by strike
- **Volume Distribution**: Bar chart showing call/put volume by strike

#### Trend Charts (require >= 2 data points)
- **Max Pain Trend**: Line chart with max pain vs underlying price
- **OI Trend**: 2-panel chart (OI by type + Total OI)
- **P/C Ratio Trend**: Line chart with sentiment zones
- **Volume Trend**: Grouped bar chart
- **Levels Trend**: Multi-line chart for S/R levels
- **GEX/DEX Trend**: 3-panel chart (Net GEX, Net DEX, Key Levels vs Price)

### Theme

All charts use a consistent dark theme:
- Background: #1a1a1a
- Grid: #333333
- Text: #e0e0e0
- Accent colors:
  - Calls: #4ecdc4 (teal)
  - Puts: #ff6b6b (red)
  - Price: #ffd93d (yellow)
  - GEX: #f39c12 (orange)
  - DEX: #9b59b6 (purple)

---

## GUI Components

### DatabaseTab

Main tab widget containing:
- Currency selector (ETH/BTC)
- "Open Charts" button to open output folder
- "Capture All" button to run all captures sequentially
- Grid of 6 capture tiles
- Log viewer for progress output

### CaptureTile

Individual tile for each capture type showing:
- Title and description
- Status label (capturing/success/error)
- Chart count label
- "Capture & Chart" button

### CaptureWorker

QThread worker that:
- Runs capture in background thread
- Emits progress signals to GUI
- Reports success/error through signals

---

## Data Flow

### Single Capture Flow

```
1. User clicks "Capture & Chart" on tile
2. GUI creates CaptureWorker thread
3. CaptureWorker calls DatabaseCaptureService.capture()
4. Service fetches data via DeribitApiService
5. Service creates OnChainAnalyzer and parses data
6. Service gets appropriate CaptureStrategy
7. Strategy captures data to database via Repository
8. Strategy generates charts via chart_generator
9. Service returns CaptureResult
10. GUI updates tile status and logs
```

### Capture All Flow

```
1. User clicks "Capture All"
2. GUI queues all 6 capture types
3. For each type in queue:
   a. Start CaptureWorker
   b. Wait for completion
   c. Update tile status
   d. Start next capture
4. Log "Capture All completed!"
```

---

## GEX/DEX Calculation

### Gamma Exposure (GEX)
```
For calls: GEX = Gamma * Open Interest * 100
For puts:  GEX = Gamma * Open Interest * 100 * (-1)
Net GEX = Call GEX + Put GEX (at each strike)
```

### Delta Exposure (DEX)
```
For calls: DEX = Delta * Open Interest
For puts:  DEX = Delta * Open Interest
Net DEX = Call DEX + Put DEX (at each strike)
```

### Key Levels
- **Call Resistance**: Strike with highest positive net GEX
- **Put Support**: Strike with lowest negative net GEX
- **HVL (High Volume Level)**: Strike closest to zero gamma (flip point)

---

## Usage Examples

### Programmatic Capture

```python
from coding.service.database import DatabaseCaptureService

# Create service with progress callback
def on_progress(message):
    print(f"Progress: {message}")

service = DatabaseCaptureService(progress_callback=on_progress)

# Capture single type
result = service.capture("max_pain", "ETH")
print(f"Captured {result.record_count} records")
print(f"Generated {len(result.chart_paths)} charts")

# Capture all types
results = service.capture_all("ETH")
for r in results:
    print(f"{r.capture_type}: {r.record_count} records, {len(r.chart_paths)} charts")
```

### Direct Repository Access

```python
from coding.core.database import DatabaseRepository

repo = DatabaseRepository()

# Get max pain history
history = repo.get_max_pain_history("ETH", "17JAN26", limit=50)
for record in history:
    print(f"{record['captured_at']}: {record['max_pain_strike']}")

# Get available expirations
expirations = repo.get_available_expirations("ETH", "gex_dex")
print(f"Expirations with GEX/DEX data: {expirations}")
```

---

## Important Notes

### Trend Chart Requirements
- Trend charts require **minimum 2 data points** to generate
- First capture of an expiration will not produce trend charts
- Snapshot distribution charts are generated immediately (no history needed)

### Data Persistence
- All captured data is stored in PostgreSQL database
- Charts are regenerated each capture using full historical data
- Old charts are NOT deleted; new charts have timestamp in filename

### Performance Considerations
- GEX/DEX capture fetches Greeks via individual ticker API calls
- This can be slow for expirations with many instruments
- Consider running captures during low-activity periods

### Architecture Compliance
- GUI layer must NEVER contain business logic
- All API calls go through Service layer
- All database operations go through Repository
- Use Strategy pattern for variations of same operation

---

## Commit History

- `3e79b72`: Add on-chain analysis features with Database and GEX/DEX tracking
- `b6c993d`: Refactor Database tab to follow layered architecture

---

## Future Enhancements (Potential)

1. Scheduled automatic captures
2. Chart comparison view
3. Alert system for significant changes
4. Export to CSV functionality
5. Historical data cleanup/archival
