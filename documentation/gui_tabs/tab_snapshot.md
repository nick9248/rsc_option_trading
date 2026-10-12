# Snapshot Tab

## Overview

Fetches a live snapshot of the option chain for chosen expirations from Deribit, filters it by volume, optionally adds Greeks, and optionally saves it to a CSV file. The tab shows progress and a count in its log; it does not display the chain as a table. The CSV is the output.

## Controls

- **Currency**: dropdown (ETH, BTC). **Load Expirations** fills the expiration list for that currency.
- **Expiration list**: multi-select, with a **Select All** checkbox.
- **Min Volume**: whole number, 0 means no filter.
- **Fetch Greeks**: also fetches delta, gamma, vega, theta and rho from the ticker endpoint. Slower, because it makes one call per instrument.
- **Save to CSV**: writes the result to a CSV file after loading.
- **Modified Format** (on by default): reorders the columns and adds USD prices. Untick it for the raw fields.
- **Load Snapshot** and **Clear** buttons, a status label ("Loaded N instruments") and a log area.

## CSV output

`SnapshotService.save_snapshot_to_csv()` writes to `output/data/snapshots/` with the currency in the file name.

The modified format has these columns, in this order: `instrument_name`, `bid_price`, `bid_price_usd`, `mark_price`, `mark_price_usd`, `mid_price`, `mid_price_usd`, `ask_price`, `ask_price_usd`, `open_interest`, `underlying_price`, `volume`, `volume_usd`, `delta`, `gamma`, `vega`, `theta`, `rho`, `timestamp`.

USD prices are the coin price multiplied by the Deribit INDEX price (the basis Deribit's own website uses), not by the per-expiry future price (`underlying_price`). If an item has no index price the service falls back to `underlying_price` and logs a warning.

## Architecture

```
SnapshotTab (coding/gui/tabs/snapshot_tab.py)
    -> SnapshotService                     (coding/service/snapshot/snapshot_service.py)
        -> DeribitApiService               (coding/service/deribit/deribit_api_service.py)
```

Data is fetched live on every "Load Snapshot"; nothing is stored in the database.

## Tests

`tests/unit/test_snapshot_service.py`.
