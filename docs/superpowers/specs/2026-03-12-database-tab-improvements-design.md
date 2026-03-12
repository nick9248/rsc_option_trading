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
- Single tile captures bypass the queue entirely — no change
- Button placement: add "Capture All (BTC/ETH)" next to "Capture All" in the controls row

**Rate limit note**: GEX/DEX already makes 150–400 ticker calls per run with no throttle. Running it twice (BTC then ETH) with natural spacing from the 5 other captures in between is not meaningfully more risky. Rate limiting is a separate concern.

---

### 3. Progress Counter on Capture All Buttons

During a Capture All run, the active button label updates to show progress.

- "Capture All" → "Capture All (2/6)" as captures complete
- "Capture All (BTC/ETH)" → "Capture All BTC/ETH (8/12)"
- Restores original label when complete or cancelled

---

### 4. Cancel Button

A "Cancel" button appears in the controls row during any Capture All operation.

- Clears `_capture_queue`, sets `_capture_all_in_progress = False`
- Current in-progress capture finishes naturally (cannot interrupt mid-HTTP)
- Button shows "Cancelling..." until current capture finishes, then disappears
- Hidden when no Capture All is running

---

### 5. Last Captured Timestamps

Each tile shows when its data was last captured for the selected currency.

**Service layer** (`coding/service/database/capture_service.py`):
- New method: `get_last_captured(currency: str) -> Dict[str, Optional[datetime]]`
- Queries each capture table for `MAX(captured_at)` where `currency = ?`
- Returns dict keyed by capture type: `{"snapshot": datetime, "max_pain": None, ...}`

**GUI layer** (`coding/gui/tabs/database_tab.py`):
- `CaptureTile` gains a `last_captured_label` (QLabel) below the description
- Shows "Last: HH:MM" or "Never" depending on result
- New method `set_last_captured(dt: Optional[datetime])` on `CaptureTile`

**When timestamps are loaded**:
- On tab first shown (`showEvent`) — loads for current currency
- On currency combo change — reloads all tiles for new currency
- After each successful capture — refreshes that specific tile

---

### 6. Error Truncation Fix

`CaptureTile.set_error` currently truncates error messages to 30 characters (`message[:30]...`), hiding useful information.

**Fix**: Remove the truncation. Show full error message with word wrap already enabled on the label.

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
