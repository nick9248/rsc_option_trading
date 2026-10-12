# Database Tab

## Overview

The Database tab pulls the data collected on the VPS into the local database. It does no collection of its own: the VPS daemon (`ProspectiveCollector`) is the only data collector.

Earlier versions of this tab also had "Capture" tiles and a "Capture All" button that wrote to the `max_pain`, `open_interest`, `volume`, `levels` and `gex_dex` tables. That pipeline (`capture_service.py`, `capture_strategies.py`) and those tables are gone.

## Controls

- **Sync from VPS**: runs `VpsSyncService.sync()` in a background thread and logs the progress and a summary. While it runs the button reads "Syncing...".
- **Open Charts Folder**: opens `output/charts/` in the file browser.
- A log area with the output.

## Architecture

```
DatabaseTab (coding/gui/tabs/database_tab.py)
    -> VpsSyncService.sync()        (coding/service/database/vps_sync_service.py)
        -> same mechanics as scripts/sync_from_vps.py (SSH tunnel, SYNC_TABLES loop)
```

The synced tables, their watermark columns and conflict handling are the `SYNC_TABLES` list in `scripts/sync_from_vps.py` (20 tables). Sync needs the SSH tunnel to the VPS; see `documentation/vps/vps_setup_and_operations.md`.

## Tests

`tests/unit/gui/test_database_tab.py`, `tests/unit/test_vps_sync_service.py`, `tests/integration/test_sync_from_vps_real_postgres.py`.
