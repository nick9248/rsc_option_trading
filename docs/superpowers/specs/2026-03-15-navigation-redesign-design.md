# Navigation Redesign — Design Spec
**Date:** 2026-03-15
**Branch:** `layout/navigation_page`

---

## 1. Overview

Replace the existing `QTabWidget` top tab bar with a navigation home page and a slim 3-button top bar. The app opens to a tile-based home screen; clicking a tile navigates into that module. Inside a module, three small buttons (Prev / Home / Next) allow movement between modules.

---

## 2. Navigation Structure

### 2.1 Navigation Home Page
- Full-page widget shown on app launch (index 0 of `QStackedWidget`)
- Displays all modules as a **3-column card grid**
- Each card contains:
  - Icon (emoji or label)
  - Module name (Playfair Display, gold on hover)
  - Short subtitle (Inter, muted text)
- Clicking an **active** card calls `_go_to(index)` on MainWindow
- **Placeholder tiles** (Market Data, Trading, Analytics) are shown with muted styling and click does nothing

### 2.2 Top Bar (replaces tab bar, visible on all pages)
- Fixed height (~36px), always present at top of window
- Layout: `[Logo] ——— spacer ——— [position_label] ——— spacer ——— [← Prev] [⌂ Home] [Next →]`
  - Implemented as `QHBoxLayout` with two `QSpacerItem(QSizePolicy.Expanding)` separators
  - Logo: left-aligned, Playfair Display, gold
  - Position label: center-aligned, Inter, muted — hidden when on Home (index 0)
  - Nav buttons: right-aligned, Playfair Display, small (e.g. 80×26px)
- Prev/Next are **visually dimmed** (TEXT_MUTED color) when on Home page, but remain clickable — pressing them from Home goes to the last or first active module respectively

### 2.3 Module Order
```
Stack index 0  →  NavigationPage  (Home, not counted in position indicator)
Stack index 1  →  API Connection
Stack index 2  →  Snapshot
Stack index 3  →  On Chain Analysis
Stack index 4  →  Database
Stack index 5  →  Strategies
Stack index 6  →  Special Strategies
Stack index 7  →  Market Regime
Stack index 8  →  System Health
Stack index 9  →  Market Data      (placeholder)
Stack index 10 →  Trading          (placeholder)
Stack index 11 →  Analytics        (placeholder)
```

**Active module range:** indices 1–8 (count = 8). Position indicator shows `N / 8`.

### 2.4 Navigation Logic
- `_go_home()` → `stack.setCurrentIndex(0)`, hide position label, dim Prev/Next
- `_go_to(index)` → `stack.setCurrentIndex(index)`, show `{index} / 8`, enable Prev/Next
- `_go_prev()`:
  - If on Home (0): go to index 8 (last active module)
  - If on index 1: go to index 8 (wrap)
  - Otherwise: go to `current - 1`
  - Skips placeholder indices (9–11) entirely — they are never a Prev/Next destination
- `_go_next()`:
  - If on Home (0): go to index 1 (first active module)
  - If on index 8: go to index 1 (wrap)
  - Otherwise: go to `current + 1`
  - Skips placeholder indices (9–11) entirely
- All index changes route through `_go_home()` or `_go_to()` — never call `stack.setCurrentIndex()` directly. Connect `stack.currentChanged` to a `_sync_nav_state()` slot as a safety net to keep button states consistent

### 2.5 Service-Failed Tabs (Strategies, Special Strategies)
- These tabs are added inside try/except in `_add_tabs()` — on failure, a `QLabel("Unavailable")` placeholder is inserted at the same index
- The `NavigationPage` tile list is **static**: all 8 active tiles always shown regardless of initialization success
- Tiles for service-failed modules show with muted styling identical to placeholder tiles and click does nothing
- `MainWindow` passes a `failed_indices: set[int]` to `NavigationPage` after `_add_tabs()` completes

---

## 3. Visual Design

### 3.1 Font
- **Playfair Display** — logo, module tile names, page section headings, nav button labels
- **Inter** — body text, data labels, values, log output, subtitles (unchanged, readability priority)
- **Loading**: bundle `PlayfairDisplay-Regular.ttf` and `PlayfairDisplay-Italic.ttf` in `coding/gui/assets/fonts/`. Load via `QFontDatabase.addApplicationFont()` in `app.py` before `MainWindow` is created. If loading fails (missing file), log a warning and fall back to system serif — success criterion 7 is met by best-effort render.
- **New directory**: `coding/gui/assets/fonts/` — add to git

### 3.2 Color Palette (Theme B — Midnight)

Token names are **kept identical** to the existing `colors.py` to avoid cascading breakage in `styles.py`. Only values change. `TAB_*` tokens are **removed** (no more tab widget). All other tokens are remapped to the new palette.

| Token | Old Value | New Value | Notes |
|---|---|---|---|
| `BACKGROUND_PRIMARY` | `#0D0D0F` | `#080D18` | Deeper navy |
| `BACKGROUND_SECONDARY` | `#141418` | `#0A1020` | |
| `BACKGROUND_TERTIARY` | `#1A1A1F` | `#0D1428` | Merged with SURFACE role |
| `BACKGROUND_ELEVATED` | `#1F1F26` | `#111E35` | |
| `SURFACE` | `#1A1A1F` | `#0D1428` | |
| `SURFACE_HOVER` | `#222228` | `#111E35` | |
| `SURFACE_ACTIVE` | `#2A2A32` | `#192840` | |
| `BORDER` | `#2A2A32` | `#141E30` | |
| `BORDER_SUBTLE` | `#222228` | `#0F1828` | |
| `BORDER_FOCUS` | `#B8860B` | `#D4B89666` | Gold with alpha (use hex string `#D4B896` for Qt; alpha handled in QSS as `rgba`) |
| `TEXT_PRIMARY` | `#F5F5F7` | `#E8EAF0` | Slightly warmer white |
| `TEXT_SECONDARY` | `#A0A0A8` | `#5A6A7C` | |
| `TEXT_MUTED` | `#6B6B73` | `#2A3848` | |
| `TEXT_DISABLED` | `#4A4A52` | `#1A2638` | |
| `ACCENT` | `#B8860B` | `#D4B896` | Warm platinum gold |
| `ACCENT_HOVER` | `#D4A017` | `#E8CEAD` | |
| `ACCENT_MUTED` | `#8B6914` | `#A8956A` | |
| `SUCCESS` | `#2ECC71` | `#2ECC71` | Unchanged |
| `SUCCESS_MUTED` | `#1E8449` | `#1E8449` | Unchanged |
| `WARNING` | `#F39C12` | `#F39C12` | Unchanged |
| `WARNING_MUTED` | `#B7950B` | `#B7950B` | Unchanged |
| `ERROR` | `#E74C3C` | `#E74C3C` | Unchanged |
| `ERROR_MUTED` | `#A93226` | `#A93226` | Unchanged |
| `INFO` | `#3498DB` | `#3498DB` | Unchanged |
| `INFO_MUTED` | `#2171A9` | `#2171A9` | Unchanged |
| `TAB_INACTIVE` | `#141418` | **REMOVED** | No tab widget |
| `TAB_ACTIVE` | `#1F1F26` | **REMOVED** | No tab widget |
| `TAB_HOVER` | `#1A1A1F` | **REMOVED** | No tab widget |
| `INPUT_BACKGROUND` | `#141418` | `#0A1020` | |
| `INPUT_BORDER` | `#2A2A32` | `#141E30` | |
| `INPUT_FOCUS` | `#B8860B` | `#D4B896` | |
| `BUTTON_PRIMARY` | `#B8860B` | `#D4B896` | |
| `BUTTON_PRIMARY_HOVER` | `#D4A017` | `#E8CEAD` | |
| `BUTTON_SECONDARY` | `#2A2A32` | `#111E35` | |
| `BUTTON_SECONDARY_HOVER` | `#3A3A44` | `#192840` | |
| `SCROLLBAR_TRACK` | `#141418` | `#0A1020` | |
| `SCROLLBAR_HANDLE` | `#2A2A32` | `#141E30` | |
| `SCROLLBAR_HANDLE_HOVER` | `#3A3A44` | `#1E2D45` | |
| `PROFIT` | `#2ECC71` | `#2ECC71` | Unchanged |
| `LOSS` | `#E74C3C` | `#E74C3C` | Unchanged |

**Note on `BORDER_FOCUS`**: Qt QSS does not support 8-digit hex colors. Where focus border with alpha is needed, use `rgba(212, 184, 150, 0.4)` in the QSS string; the `Colors.BORDER_FOCUS` token stores `#D4B896` (no alpha) and the alpha is applied in `styles.py` directly.

---

## 4. Architecture

### 4.1 Files Changed / Created

| File | Action | Description |
|---|---|---|
| `coding/gui/theme/colors.py` | Modify | Update all values per migration table; remove TAB_* tokens |
| `coding/gui/theme/styles.py` | Modify | Remove all QTabWidget/tab QSS; add top-bar and navigation tile QSS; update any inline TAB_* references |
| `coding/gui/main_window.py` | Modify | Replace QTabWidget with QStackedWidget + top bar; add navigation logic methods |
| `coding/gui/app.py` | Modify | Load Playfair Display fonts via QFontDatabase before MainWindow creation |
| `coding/gui/tabs/navigation_page.py` | Create | NavigationPage widget with ModuleTile grid |
| `coding/gui/assets/fonts/PlayfairDisplay-Regular.ttf` | Create | Bundle font file |
| `coding/gui/assets/fonts/PlayfairDisplay-Italic.ttf` | Create | Bundle font file |
| `coding/gui/assets/fonts/__init__.py` | Create | Empty, marks as package resource dir |

### 4.2 NavigationPage Widget

```
NavigationPage(QWidget)
  └── QVBoxLayout
        ├── header_label  ("Select Module", Playfair Display italic, TEXT_MUTED, centered)
        └── QGridLayout (3 columns, uniform spacing)
              └── ModuleTile × 11  (one per active + placeholder module)
```

`ModuleTile(QFrame)`:
- Children: `icon_label (QLabel)`, `name_label (QLabel, Playfair Display)`, `sub_label (QLabel, Inter)`
- Emits `clicked = Signal(int)` with its stack index
- Hover: implemented via `enterEvent` / `leaveEvent` — explicitly call `name_label.setStyleSheet(...)` to change color; do NOT rely on QSS `:hover` cascade to child labels (Qt limitation)
- Disabled/muted style applied via a `set_disabled(True)` method that sets muted colors and clears the click signal connection

`NavigationPage`:
- Emits `module_selected = Signal(int)`
- Constructor: `__init__(self, module_defs: list[dict], failed_indices: set[int])`
  - `module_defs`: `[{"index": 1, "icon": "🔗", "name": "API Connection", "subtitle": "Test endpoints"}, ...]`
  - `failed_indices`: set of stack indices that failed to initialize

### 4.3 MainWindow Structure

```
MainWindow(QMainWindow)
  └── central_widget (QWidget)
        └── main_layout (QVBoxLayout, spacing=0, margins=0)
              ├── top_bar (QWidget, fixed height 36px)
              │     └── QHBoxLayout
              │           ├── logo_label          (left, Playfair Display, ACCENT)
              │           ├── QSpacerItem(Expanding)
              │           ├── position_label      (center, Inter, TEXT_MUTED, hidden on home)
              │           ├── QSpacerItem(Expanding)
              │           └── nav_btn_layout (QHBoxLayout)
              │                 ├── btn_prev  (← Prev)
              │                 ├── btn_home  (⌂ Home)
              │                 └── btn_next  (Next →)
              └── stack (QStackedWidget)
                    ├── index 0: NavigationPage
                    ├── index 1: ApiConnectionTab
                    └── ... (indices 1–11)
```

### 4.4 MainWindow Navigation Methods

```python
def _go_home(self):
    self.stack.setCurrentIndex(0)

def _go_to(self, index: int):
    self.stack.setCurrentIndex(index)

def _go_prev(self):
    current = self.stack.currentIndex()
    if current <= 1:
        self._go_to(8)  # last active module
    else:
        self._go_to(current - 1)

def _go_next(self):
    current = self.stack.currentIndex()
    if current == 0 or current >= 8:
        self._go_to(1)  # first active module
    else:
        self._go_to(current + 1)

def _sync_nav_state(self, index: int):
    """Connected to stack.currentChanged — keeps top bar in sync."""
    on_home = (index == 0)
    self.position_label.setVisible(not on_home)
    if not on_home:
        self.position_label.setText(f"{index} / 8")
    dim = Colors.TEXT_MUTED if on_home else Colors.TEXT_SECONDARY
    self.btn_prev.setStyleSheet(f"color: {dim};")
    self.btn_next.setStyleSheet(f"color: {dim};")
```

---

## 5. Out of Scope

- No changes to any tab's internal layout or business logic
- No changes to service layer, core, or database
- No new features added to existing tabs

---

## 6. Success Criteria

1. App launches to navigation home page with no tab bar visible
2. All 8 active module tiles are clickable and navigate to the correct module
3. Placeholder tiles (Market Data, Trading, Analytics) are visible but not clickable
4. `← Prev` and `Next →` cycle through indices 1–8 only, wrapping correctly
5. `⌂ Home` always returns to the navigation page from any module
6. Position indicator shows `N / 8` on module pages and is hidden on home
7. New color palette applied globally; no `TAB_*` token references remain
8. Playfair Display renders on tile names, logo, and nav buttons (best-effort; fallback to serif is acceptable if font files fail to load)
9. No regressions in any tab's functionality (manual smoke test: open each tab, run one operation)
