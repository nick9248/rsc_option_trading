# Database Tab Improvements — Design Spec

**Date**: 2026-03-12
**Branch**: quality-check

---

## Overview

Quality improvements to the Database tab in the PySide6 GUI. Two features requested by user, four quality fixes identified during review.

---

## Changes

### 1. Default Currency: BTC

Change the currency combo box default from ETH to BTC.

**File**: `coding/gui/tabs/database_tab.py`
- Change `addItems(["ETH", "BTC"])` to `addItems(["BTC", "ETH"])` so BTC is index 0 (default).

---

### 2. "Capture All (BTC/ETH)" Button

New button that runs all 6 captures for BTC sequentially, then all 6 for ETH.

**Approach A — typed queue items**

`_capture_queue` changes type from `List[str]` to `List[tuple[str, str]]` where each item is `(capture_type, currency)`.

- `_start_next_capture` reads `currency` from the tuple instead of `currency_combo.currentText()`
- Existing "Capture All" button enqueues 6 tuples using `currency_combo.currentText()` — behaviour unchanged
- New "Capture All (BTC/ETH)" button enqueues 12 tuples: 6 × BTC then 6 × ETH
- Single tile captures bypass the queue entirely — no change to `_on_tile_capture`
- "Capture All (BTC/ETH)" uses the same `worker.isRunning()` guard as "Capture All": if a capture is already in progress (including a single tile capture), the button does nothing and logs a warning
- Button placement: add "Capture All (BTC/ETH)" next to "Capture All" in the controls row

**Rate limit note**: GEX/DEX already makes 150–400 ticker calls per run with no throttle. Running it twice (BTC then ETH) with natural spacing from the 5 other captures in between is not meaningfully more risky. Rate limiting is a separate concern.

---

### 3. Progress Counter on Capture All Buttons

During a Capture All run, the initiating button's label updates to show progress.

- "Capture All" → "Capture All (2/6)" as captures complete
- "Capture All (BTC/ETH)" → "Capture All BTC/ETH (8/12)"
- The button that was clicked has its label updated. The other Capture All button is disabled but its label is not changed.
- Restores original label when complete or cancelled

**Tracking mechanism**: Add two instance variables to `DatabaseTab`:
- `_capture_all_total: int` — set to queue length (6 or 12) when a Capture All starts
- `_capture_all_completed: int` — starts at 0, incremented by 1 in both `_on_capture_finished` and `_on_capture_error` when `_capture_all_in_progress` is True

Also add `_active_capture_all_btn: Optional[QPushButton]` — set to the button that initiated the run, used to update its label and restore it on completion.

---

### 4. Cancel Button

A "Cancel" button is added to the controls row. It is hidden when no Capture All is running and shown during a Capture All or Capture All (BTC/ETH) operation.

- When clicked: sets `_capture_all_in_progress = False` and clears `_capture_queue` immediately, before the in-flight worker finishes
- Button label changes to "Cancelling..." until the current in-flight capture finishes
- When the in-flight worker fires `_on_capture_finished` or `_on_capture_error`, those handlers check `_capture_all_in_progress` — since it is now False, they do not call `_start_next_capture`. The handlers then: (1) restore `_active_capture_all_btn` label to its original text, (2) re-enable both Capture All buttons, (3) hide the Cancel button.
- This ordering ensures the error path and the cancel path do not conflict.

---

### 5. Last Captured Timestamps

Each tile shows when its data was last captured for the selected currency.

**Service layer** (`coding/service/database/capture_service.py`):
- New method: `get_last_captured(currency: str) -> Dict[str, Optional[datetime]]`
- Uses `self.repository` (the existing instance variable) — does not create a new repository instantiation
- Makes 6 queries, one per capture table. SQL table names vs dict key mapping:
  - `snapshots` table → dict key `"snapshot"`
  - `max_pain` table → dict key `"max_pain"`
  - `open_interest` table → dict key `"open_interest"`
  - `volume` table → dict key `"volume"`
  - `levels` table → dict key `"levels"`
  - `gex_dex` table → dict key `"gex_dex"`
- Each query: `SELECT MAX(captured_at) FROM <table> WHERE currency = %s` (psycopg2 `%s` parameterization, consistent with existing repository code)
- For tables that store per-expiration rows (max_pain, open_interest, volume, levels, gex_dex), `MAX(captured_at)` is taken across all expirations — this gives the most recent capture event for that type, which is the correct intent
- Returns dict keyed by capture type: `{"snapshot": datetime | None, "max_pain": None, ...}`

**GUI layer** (`coding/gui/tabs/database_tab.py`):
- `CaptureTile` gains a `last_captured_label` (QLabel) placed below the description, above the stretch
- Shows "Last: HH:MM" if a datetime is present, "Never" if None
- New method `set_last_captured(dt: Optional[datetime])` on `CaptureTile`
- Timestamp loading is intentionally synchronous — `get_last_captured` makes 6 lightweight `MAX` queries expected to complete in milliseconds. This is acceptable for read-only metadata queries and avoids the complexity of a separate worker thread.

**When timestamps are loaded**:
- On tab first shown: override `showEvent(self, event)` in `DatabaseTab` to call `_refresh_timestamps()` on first show (use a `_timestamps_loaded: bool` flag to avoid re-loading on every show)
- On currency combo change — calls `_refresh_timestamps()` for the new currency
- After each successful single-tile or Capture All capture — calls `get_last_captured(currency)` (all 6 queries) and then calls `set_last_captured` only on the tile that just completed, using the relevant value from the result dict

---

### 6. Error Truncation Fix

`CaptureTile.set_error` currently truncates error messages to 30 characters (`message[:30]...`), hiding useful information.

**Fix**: Cap at the first 3 lines of the error message rather than 30 characters. Use `"\n".join(message.splitlines()[:3])`. The tile height policy changes from `QSizePolicy.Policy.Fixed` to `QSizePolicy.Policy.Minimum` so tiles can expand vertically to fit error content without overflow.

---

## Files Changed

| File | Change |
|------|--------|
| `coding/gui/tabs/database_tab.py` | All GUI changes (queue type, buttons, cancel, timestamps, error fix) |
| `coding/service/database/capture_service.py` | Add `get_last_captured()` method |

No new files. No changes to other tabs or services.

---

## Architecture Compliance

- GUI layer only handles presentation — `get_last_captured()` lives in the service layer
- No business logic added to the GUI
- Queue change is internal GUI state management — correct layer
- All DB queries go through the service layer
