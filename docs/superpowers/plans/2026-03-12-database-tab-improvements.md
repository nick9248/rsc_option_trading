# Database Tab Improvements Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the Database tab with BTC default, Capture All (BTC/ETH) button, last-captured timestamps, cancel support, progress counter, and error truncation fix.

**Architecture:** Repository layer gets `get_last_captured_times()`. Service layer gets `get_last_captured()` delegating to the repository. GUI layer gets all visual/interaction changes — typed queue items, new buttons, tile timestamp labels, cancel logic.

**Tech Stack:** Python 3.13, PySide6, psycopg2, pytest + unittest.mock

**Spec:** `docs/superpowers/specs/2026-03-12-database-tab-improvements-design.md`

---

## Chunk 1: Service and Repository Layer

### Task 1: Add `get_last_captured_times` to `DatabaseRepository`

**Files:**
- Modify: `coding/core/database/repository.py` (append after `get_available_expirations`)

No unit tests for this method — it requires a live DB connection (consistent with codebase: no existing repository unit tests). It will be exercised via the service-layer test in Task 2.

- [ ] **Step 1: Add the method**

Open `coding/core/database/repository.py`. Append this method inside `DatabaseRepository`, after `get_available_expirations`:

```python
def get_last_captured_times(self, currency: str) -> Dict[str, Optional[datetime]]:
    """
    Get the most recent captured_at timestamp per capture type for a currency.

    Args:
        currency: Currency symbol (BTC, ETH).

    Returns:
        Dict keyed by capture type. Value is datetime if data exists, None if never captured.
        Keys: "snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"
    """
    table_map = {
        "snapshot": "snapshots",
        "max_pain": "max_pain",
        "open_interest": "open_interest",
        "volume": "volume",
        "levels": "levels",
        "gex_dex": "gex_dex",
    }
    result: Dict[str, Optional[datetime]] = {}

    with self._db_cursor() as cursor:
        for capture_type, table in table_map.items():
            cursor.execute(
                f"SELECT MAX(captured_at) FROM {table} WHERE currency = %s",
                (currency,)
            )
            row = cursor.fetchone()
            result[capture_type] = row[0] if row and row[0] is not None else None

    return result
```

- [ ] **Step 2: Verify import — `Optional` and `Dict` are already imported**

Check line 1-20 of `repository.py`. If `Optional` or `Dict` are missing from `typing` imports, add them.

- [ ] **Step 3: Commit**

```bash
git add coding/core/database/repository.py
git commit -m "feat: add get_last_captured_times to DatabaseRepository"
```

---

### Task 2: Add `get_last_captured` to `DatabaseCaptureService` (TDD)

**Files:**
- Modify: `coding/service/database/capture_service.py`
- Create: `tests/unit/test_database_capture_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_database_capture_service.py`:

```python
"""
Unit tests for DatabaseCaptureService.get_last_captured.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from coding.service.database.capture_service import DatabaseCaptureService


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return DatabaseCaptureService(repository=mock_repo)


def test_get_last_captured_returns_all_six_keys(service, mock_repo):
    """All 6 capture types are always present in the result."""
    mock_repo.get_last_captured_times.return_value = {
        "snapshot": datetime(2026, 3, 12, 10, 0),
        "max_pain": datetime(2026, 3, 12, 10, 1),
        "open_interest": datetime(2026, 3, 12, 10, 2),
        "volume": datetime(2026, 3, 12, 10, 3),
        "levels": datetime(2026, 3, 12, 10, 4),
        "gex_dex": datetime(2026, 3, 12, 10, 5),
    }
    result = service.get_last_captured("BTC")
    assert set(result.keys()) == {"snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"}


def test_get_last_captured_delegates_to_repository(service, mock_repo):
    """Service delegates to repository with correct currency."""
    mock_repo.get_last_captured_times.return_value = {
        k: None for k in ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
    }
    service.get_last_captured("ETH")
    mock_repo.get_last_captured_times.assert_called_once_with("ETH")


def test_get_last_captured_returns_none_for_never_captured(service, mock_repo):
    """None values are passed through for capture types with no data."""
    mock_repo.get_last_captured_times.return_value = {
        "snapshot": None,
        "max_pain": datetime(2026, 3, 12, 9, 0),
        "open_interest": None,
        "volume": None,
        "levels": None,
        "gex_dex": None,
    }
    result = service.get_last_captured("BTC")
    assert result["snapshot"] is None
    assert result["max_pain"] == datetime(2026, 3, 12, 9, 0)


def test_get_last_captured_returns_datetime_values(service, mock_repo):
    """Datetime values are returned unchanged."""
    ts = datetime(2026, 3, 12, 14, 32, 55)
    mock_repo.get_last_captured_times.return_value = {
        k: ts for k in ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
    }
    result = service.get_last_captured("BTC")
    for v in result.values():
        assert v == ts
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/unit/test_database_capture_service.py -v
```

Expected: `AttributeError: 'DatabaseCaptureService' object has no attribute 'get_last_captured'`

- [ ] **Step 3: Implement the method**

In `coding/service/database/capture_service.py`, add after `get_available_expirations`:

```python
def get_last_captured(self, currency: str) -> Dict[str, Optional[datetime]]:
    """
    Get the most recent capture timestamp per capture type for a currency.

    Args:
        currency: Currency symbol (BTC, ETH).

    Returns:
        Dict keyed by capture type. Value is datetime if data exists, None if never captured.
    """
    return self.repository.get_last_captured_times(currency)
```

- [ ] **Step 4: Check imports at top of `capture_service.py`**

Ensure `Optional` and `Dict` are imported from `typing`. Add if missing.

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/unit/test_database_capture_service.py -v
```

Expected: 4 tests PASS

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
pytest tests/unit/ -v
```

Expected: all existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add coding/service/database/capture_service.py tests/unit/test_database_capture_service.py
git commit -m "feat: add get_last_captured to DatabaseCaptureService"
```

---

## Chunk 2: GUI Layer

### Task 3: Simple fixes — BTC default and error truncation

**Files:**
- Modify: `coding/gui/tabs/database_tab.py`

- [ ] **Step 1: Change default currency to BTC**

In `database_tab.py` line 287, change:
```python
self.currency_combo.addItems(["ETH", "BTC"])
```
to:
```python
self.currency_combo.addItems(["BTC", "ETH"])
```

- [ ] **Step 2: Fix error truncation in `CaptureTile.set_error`**

At line 218, change:
```python
self.status_label.setText(f"Error: {message[:30]}...")
```
to:
```python
truncated = "\n".join(message.splitlines()[:3])
self.status_label.setText(f"Error: {truncated}")
```

- [ ] **Step 3: Change tile height policy to allow expansion**

In `CaptureTile._setup_ui` at line 133, change:
```python
self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
```
to:
```python
self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
```

- [ ] **Step 4: Commit**

```bash
git add coding/gui/tabs/database_tab.py
git commit -m "fix: BTC default currency and error message truncation in database tab"
```

---

### Task 4: Typed queue + Capture All (BTC/ETH) + progress counter

**Files:**
- Modify: `coding/gui/tabs/database_tab.py`

- [ ] **Step 1: Add new instance variables to `DatabaseTab.__init__`**

In `__init__`, replace:
```python
self._capture_queue: List[str] = []
self._capture_all_in_progress: bool = False
```
with:
```python
self._capture_queue: List[tuple[str, str]] = []
self._capture_all_in_progress: bool = False
self._capture_all_total: int = 0
self._capture_all_completed: int = 0
self._active_capture_all_btn: Optional[QPushButton] = None
```

- [ ] **Step 2: Add "Capture All (BTC/ETH)" button to the controls row**

In `_setup_ui`, after the existing `self.capture_all_btn` block (around line 326), add:

```python
# Capture all (both currencies) button
self.capture_all_both_btn = QPushButton("Capture All (BTC/ETH)")
self.capture_all_both_btn.setStyleSheet(f"""
    QPushButton {{
        background-color: {Colors.BUTTON_SECONDARY};
        color: {Colors.TEXT_PRIMARY};
        border: 1px solid {Colors.BORDER};
        padding: 6px 12px;
        border-radius: 6px;
    }}
    QPushButton:hover {{
        background-color: {Colors.BUTTON_SECONDARY_HOVER};
    }}
    QPushButton:disabled {{
        background-color: {Colors.BUTTON_SECONDARY};
        color: {Colors.TEXT_MUTED};
    }}
""")
self.capture_all_both_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
controls_layout.addWidget(self.capture_all_both_btn)
```

- [ ] **Step 3: Update `_connect_signals` to wire the new button**

Add to `_connect_signals`:
```python
self.capture_all_both_btn.clicked.connect(self._on_capture_all_both)
```

- [ ] **Step 4: Update `_on_capture_all` to use typed queue and progress counter**

Replace the existing `_on_capture_all` method:

```python
def _on_capture_all(self) -> None:
    """Handle capture all button (current currency)."""
    if self.worker is not None and self.worker.isRunning():
        self.log_viewer.log_warning("A capture is already in progress")
        return

    currency = self.currency_combo.currentText()
    capture_types = ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
    self._capture_queue = [(ct, currency) for ct in capture_types]
    self._capture_all_total = len(self._capture_queue)
    self._capture_all_completed = 0
    self._capture_all_in_progress = True
    self._active_capture_all_btn = self.capture_all_btn
    self.capture_all_btn.setEnabled(False)
    self.capture_all_both_btn.setEnabled(False)
    self.cancel_btn.show()
    self.log_viewer.log_info(f"Starting Capture All ({currency})...")
    self._start_next_capture()
```

- [ ] **Step 5: Add `_on_capture_all_both` method**

```python
def _on_capture_all_both(self) -> None:
    """Handle capture all for BTC then ETH."""
    if self.worker is not None and self.worker.isRunning():
        self.log_viewer.log_warning("A capture is already in progress")
        return

    capture_types = ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
    self._capture_queue = [(ct, "BTC") for ct in capture_types] + [(ct, "ETH") for ct in capture_types]
    self._capture_all_total = len(self._capture_queue)
    self._capture_all_completed = 0
    self._capture_all_in_progress = True
    self._active_capture_all_btn = self.capture_all_both_btn
    self.capture_all_btn.setEnabled(False)
    self.capture_all_both_btn.setEnabled(False)
    self.cancel_btn.show()
    self.log_viewer.log_info("Starting Capture All (BTC/ETH)...")
    self._start_next_capture()
```

- [ ] **Step 6: Update `_start_next_capture` to read currency from tuple**

Replace the existing `_start_next_capture` method:

```python
def _start_next_capture(self) -> None:
    """Start next capture in queue."""
    if not self._capture_queue:
        self._finish_capture_all()
        return

    capture_type, currency = self._capture_queue.pop(0)
    self.tiles[capture_type].set_capturing(True)
    self.log_viewer.log_info(f"[Capture All] Starting {capture_type} for {currency}...")

    self.worker = CaptureWorker(capture_type, currency)
    self.worker.progress.connect(self._on_progress)
    self.worker.finished.connect(self._on_capture_finished)
    self.worker.error.connect(self._on_capture_error)
    self.worker.start()
```

- [ ] **Step 7: Add `_finish_capture_all` and `_update_capture_all_progress` helpers**

```python
def _finish_capture_all(self) -> None:
    """Reset state after capture all completes or is cancelled."""
    self._capture_all_in_progress = False
    self.capture_all_btn.setEnabled(True)
    self.capture_all_both_btn.setEnabled(True)
    self.cancel_btn.hide()
    if self._active_capture_all_btn is not None:
        if self._active_capture_all_btn is self.capture_all_btn:
            self._active_capture_all_btn.setText("Capture All")
        else:
            self._active_capture_all_btn.setText("Capture All (BTC/ETH)")
        self._active_capture_all_btn = None

def _update_capture_all_progress(self) -> None:
    """Update the active button label with current progress."""
    if self._active_capture_all_btn is None:
        return
    completed = self._capture_all_completed
    total = self._capture_all_total
    if self._active_capture_all_btn is self.capture_all_btn:
        self._active_capture_all_btn.setText(f"Capture All ({completed}/{total})")
    else:
        self._active_capture_all_btn.setText(f"Capture All BTC/ETH ({completed}/{total})")
```

- [ ] **Step 8: Update `_on_capture_finished` to increment counter and call `_start_next_capture`**

Replace the existing `_on_capture_finished` method:

```python
def _on_capture_finished(self, capture_type: str, currency: str, count: int, chart_paths: List[str]) -> None:
    """Handle successful capture."""
    self.tiles[capture_type].set_capturing(False)
    self.tiles[capture_type].set_success(count, len(chart_paths))
    self.log_viewer.log_info(f"{capture_type} capture complete: {count} records saved")

    if chart_paths:
        self.log_viewer.log_info(f"Generated {len(chart_paths)} chart(s):")
        for path in chart_paths:
            self.log_viewer.log_info(f"  - {path}")
    elif capture_type != "snapshot":
        self.log_viewer.log_info("Need at least 2 data points to generate trend charts")

    if self._capture_all_in_progress:
        self._capture_all_completed += 1
        self._update_capture_all_progress()
        self._refresh_tile_timestamp(capture_type, currency)
        self._start_next_capture()
    elif self._active_capture_all_btn is not None:
        # Was in Capture All mode but cancel was clicked — finish cleanup now
        self._refresh_tile_timestamp(capture_type, currency)
        self._finish_capture_all()
    else:
        # Single tile capture — refresh its timestamp
        self._refresh_tile_timestamp(capture_type, currency)
```

- [ ] **Step 9: Update `_on_capture_error` to increment counter and call `_start_next_capture`**

Replace the existing `_on_capture_error` method:

```python
def _on_capture_error(self, error_message: str) -> None:
    """Handle capture error."""
    for tile in self.tiles.values():
        if not tile.capture_btn.isEnabled():
            tile.set_capturing(False)
            tile.set_error(error_message)
            break

    self.log_viewer.log_error(f"Capture failed: {error_message}")

    if self._capture_all_in_progress:
        self._capture_all_completed += 1
        self._update_capture_all_progress()
        self._start_next_capture()
    elif self._active_capture_all_btn is not None:
        # Was in Capture All mode but cancel was clicked — finish cleanup now
        self._finish_capture_all()
```

- [ ] **Step 10: Remove the old `capture_all_btn.setEnabled(False)` call that's now in `_on_capture_all`**

The old `_on_capture_all` had `self.capture_all_btn.setEnabled(False)` and the old `_start_next_capture` had `self.capture_all_btn.setEnabled(True)` inside it. These are now handled by `_finish_capture_all`. Verify the replacement methods above don't leave stale calls. If any remain, remove them.

- [ ] **Step 11: Commit**

```bash
git add coding/gui/tabs/database_tab.py
git commit -m "feat: typed queue, Capture All (BTC/ETH) button, and progress counter"
```

---

### Task 5: Cancel button

**Files:**
- Modify: `coding/gui/tabs/database_tab.py`

- [ ] **Step 1: Add cancel button to the controls row in `_setup_ui`**

After the `capture_all_both_btn` block, add:

```python
# Cancel button (hidden by default)
self.cancel_btn = QPushButton("Cancel")
self.cancel_btn.setStyleSheet(f"""
    QPushButton {{
        background-color: {Colors.ERROR};
        color: white;
        border: none;
        padding: 6px 12px;
        border-radius: 6px;
    }}
    QPushButton:hover {{
        background-color: {Colors.ERROR};
        opacity: 0.85;
    }}
""")
self.cancel_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
self.cancel_btn.hide()
controls_layout.addWidget(self.cancel_btn)
```

- [ ] **Step 2: Wire cancel button in `_connect_signals`**

Add:
```python
self.cancel_btn.clicked.connect(self._on_cancel)
```

- [ ] **Step 3: Add `_on_cancel` method**

```python
def _on_cancel(self) -> None:
    """Cancel the ongoing Capture All run."""
    self._capture_queue.clear()
    self._capture_all_in_progress = False
    self.cancel_btn.setText("Cancelling...")
    self.cancel_btn.setEnabled(False)
    self.log_viewer.log_warning("Capture All cancelled — waiting for current capture to finish...")
```

- [ ] **Step 4: Update `_finish_capture_all` to reset cancel button**

In `_finish_capture_all` (added in Task 4), add at the start:
```python
self.cancel_btn.setText("Cancel")
self.cancel_btn.setEnabled(True)
self.cancel_btn.hide()
```
(Replace the `self.cancel_btn.hide()` that was already planned there to include the reset.)

The full updated `_finish_capture_all`:

```python
def _finish_capture_all(self) -> None:
    """Reset state after capture all completes or is cancelled."""
    self._capture_all_in_progress = False
    self.capture_all_btn.setEnabled(True)
    self.capture_all_both_btn.setEnabled(True)
    self.cancel_btn.setText("Cancel")
    self.cancel_btn.setEnabled(True)
    self.cancel_btn.hide()
    if self._active_capture_all_btn is not None:
        if self._active_capture_all_btn is self.capture_all_btn:
            self._active_capture_all_btn.setText("Capture All")
        else:
            self._active_capture_all_btn.setText("Capture All (BTC/ETH)")
        self._active_capture_all_btn = None
```

Note: `_finish_capture_all` is called by `_start_next_capture` (when queue is empty) and indirectly by the cancel flow (because `_on_capture_finished`/`_on_capture_error` call `_start_next_capture`, which finds the queue empty and calls `_finish_capture_all`).

- [ ] **Step 5: Commit**

```bash
git add coding/gui/tabs/database_tab.py
git commit -m "feat: add cancel button to database tab Capture All"
```

---

### Task 6: Last captured timestamps

**Files:**
- Modify: `coding/gui/tabs/database_tab.py`

- [ ] **Step 1: Add `last_captured_label` to `CaptureTile._setup_ui`**

In `CaptureTile._setup_ui`, after `desc_label` is added to layout (around line 152), add:

```python
# Last captured label
self.last_captured_label = QLabel("Last: —")
self.last_captured_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
layout.addWidget(self.last_captured_label)
```

- [ ] **Step 2: Add `set_last_captured` method to `CaptureTile`**

```python
def set_last_captured(self, dt: Optional[datetime]) -> None:
    """Update the last captured timestamp label."""
    if dt is None:
        self.last_captured_label.setText("Last: Never")
    else:
        self.last_captured_label.setText(f"Last: {dt.strftime('%H:%M')}")
```

- [ ] **Step 3: Add `Optional` and `datetime` to imports at top of `database_tab.py`**

`Optional` is already imported. Add `datetime` if not present:
```python
from datetime import datetime
```

- [ ] **Step 4: Add `_timestamps_loaded` flag and `DatabaseCaptureService` import to `DatabaseTab.__init__`**

`DatabaseCaptureService` is already imported. In `__init__`, add:
```python
self._timestamps_loaded: bool = False
```

- [ ] **Step 5: Add `showEvent` override to `DatabaseTab`**

```python
def showEvent(self, event) -> None:
    """Load timestamps on first show."""
    super().showEvent(event)
    if not self._timestamps_loaded:
        self._timestamps_loaded = True
        self._refresh_timestamps()
```

- [ ] **Step 6: Add `_refresh_timestamps` method**

```python
def _refresh_timestamps(self) -> None:
    """Load last captured times for all tiles from the database."""
    currency = self.currency_combo.currentText()
    try:
        service = DatabaseCaptureService()
        times = service.get_last_captured(currency)
        for capture_type, tile in self.tiles.items():
            tile.set_last_captured(times.get(capture_type))
    except Exception as e:
        logger.warning(f"Could not load last captured timestamps: {e}")
```

- [ ] **Step 7: Add `_refresh_tile_timestamp` method**

```python
def _refresh_tile_timestamp(self, capture_type: str) -> None:
    """Refresh the timestamp for a single tile after a successful capture."""
    currency = self.currency_combo.currentText()
    try:
        service = DatabaseCaptureService()
        times = service.get_last_captured(currency)
        self.tiles[capture_type].set_last_captured(times.get(capture_type))
    except Exception as e:
        logger.warning(f"Could not refresh timestamp for {capture_type}: {e}")
```

- [ ] **Step 8: Wire currency combo change to refresh timestamps**

In `_connect_signals`, add:
```python
self.currency_combo.currentTextChanged.connect(self._on_currency_changed)
```

Add the handler:
```python
def _on_currency_changed(self, currency: str) -> None:
    """Refresh timestamps when currency selection changes."""
    self._refresh_timestamps()
```

- [ ] **Step 9: Update `CaptureWorker` to emit currency in the finished signal**

`_on_capture_finished` in Task 4 already has the updated signature `(capture_type, currency, count, chart_paths)`. Now update `CaptureWorker` to match.

In `CaptureWorker`, change the signal declaration:
```python
finished = Signal(str, str, int, list)  # (capture_type, currency, count, chart_paths)
```

In `CaptureWorker.run`, update the emit call:
```python
self.finished.emit(
    result.capture_type,
    self.currency,
    result.record_count,
    result.chart_paths
)
```

Update `_refresh_tile_timestamp` signature to take currency:
```python
def _refresh_tile_timestamp(self, capture_type: str, currency: str) -> None:
    """Refresh the timestamp for a single tile after a successful capture."""
    try:
        service = DatabaseCaptureService()
        times = service.get_last_captured(currency)
        self.tiles[capture_type].set_last_captured(times.get(capture_type))
    except Exception as e:
        logger.warning(f"Could not refresh timestamp for {capture_type}: {e}")
```

- [ ] **Step 10: Run full test suite**

```bash
pytest tests/unit/ -v
```

Expected: all tests pass (GUI code is not unit-tested; verify no import errors)

- [ ] **Step 11: Commit**

```bash
git add coding/gui/tabs/database_tab.py
git commit -m "feat: last captured timestamps on database tab tiles"
```

---

## Final Verification

- [ ] Launch the GUI and verify:
  - Default currency is BTC
  - All 6 tiles show "Last: HH:MM" or "Last: Never"
  - Switching currency refreshes timestamps
  - "Capture All" runs 6 captures for selected currency with progress counter
  - "Capture All (BTC/ETH)" runs 12 captures (6 BTC + 6 ETH) with progress counter
  - Cancel button appears during Capture All, stops the queue, shows "Cancelling...", hides after current capture finishes
  - After capture, tile timestamp updates
  - Error messages show up to 3 lines (not truncated at 30 chars)
