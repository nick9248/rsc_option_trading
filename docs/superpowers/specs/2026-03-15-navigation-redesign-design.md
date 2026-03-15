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
  - Icon (emoji or SVG)
  - Module name (Playfair Display, gold on hover)
  - Short subtitle (Inter, muted text)
- Clicking a card calls `stack.setCurrentIndex(n)` to show that module

### 2.2 Top Bar (replaces tab bar, visible on all pages)
- Always present at the top of the window
- Contains three controls, right-aligned:
  - `← Prev` — go to previous module (wraps around; disabled/dimmed on Home page)
  - `⌂ Home` — return to navigation home page
  - `Next →` — go to next module (wraps around; disabled/dimmed on Home page)
- Position indicator label (e.g. `3 / 9`) shown between logo and nav buttons when inside a module; hidden on home page
- Logo/app name left-aligned in top bar

### 2.3 Module Order (matches current tab order)
0. Navigation Home *(not counted in position indicator)*
1. API Connection
2. Snapshot
3. On Chain Analysis
4. Database
5. Strategies
6. Special Strategies
7. Market Regime
8. System Health
9. Market Data *(placeholder)*
10. Trading *(placeholder)*
11. Analytics *(placeholder)*

---

## 3. Visual Design

### 3.1 Font
- **Playfair Display** — headings, module names, logo, nav button labels, page titles
- **Inter** — body text, data labels, values, log output (unchanged, readability priority)
- Playfair Display loaded via `QFontDatabase` from bundled `.ttf` files or fallback to system serif

### 3.2 Color Palette (Theme B — Midnight)

| Token | Hex | Usage |
|---|---|---|
| `BACKGROUND_PRIMARY` | `#080D18` | Main window background |
| `BACKGROUND_SECONDARY` | `#0A1020` | Tab/page background |
| `SURFACE` | `#0D1428` | Cards, panels |
| `SURFACE_HOVER` | `#111E35` | Card hover state |
| `BORDER` | `#141E30` | All borders |
| `BORDER_FOCUS` | `#D4B89666` | Focus ring |
| `ACCENT` | `#D4B896` | Gold — primary accent |
| `ACCENT_HOVER` | `#E8CEAD` | Gold hover |
| `ACCENT_MUTED` | `#A8956A` | Gold muted/secondary |
| `TEXT_PRIMARY` | `#E8EAF0` | Primary text |
| `TEXT_SECONDARY` | `#5A6A7C` | Secondary/label text |
| `TEXT_MUTED` | `#2A3848` | Muted/disabled text |
| `SUCCESS` | `#2ECC71` | Unchanged |
| `WARNING` | `#F39C12` | Unchanged |
| `ERROR` | `#E74C3C` | Unchanged |
| `INFO` | `#3498DB` | Unchanged |
| `PROFIT` | `#2ECC71` | Unchanged |
| `LOSS` | `#E74C3C` | Unchanged |

---

## 4. Architecture

### 4.1 Files Changed

| File | Change |
|---|---|
| `coding/gui/theme/colors.py` | Replace entire palette with Theme B tokens above |
| `coding/gui/theme/styles.py` | Update QSS to use new palette; set Playfair Display for headings; restyle top bar |
| `coding/gui/main_window.py` | Replace `QTabWidget` with `QStackedWidget` + custom top bar (`QWidget`); wire Prev/Home/Next; add position indicator |
| `coding/gui/app.py` | Set default font to Inter 10pt (unchanged); load Playfair Display via `QFontDatabase` |
| `coding/gui/tabs/navigation_page.py` | **New file** — `NavigationPage(QWidget)` with 3-col tile grid |

### 4.2 NavigationPage Widget
```
NavigationPage(QWidget)
  └── QVBoxLayout
        ├── header_label  (app subtitle, Playfair Display, gold)
        └── QGridLayout (3 columns)
              └── ModuleTile × N  (custom QFrame subclass)
                    ├── icon_label (QLabel)
                    ├── name_label (QLabel, Playfair Display)
                    └── sub_label  (QLabel, Inter, muted)
```
- `ModuleTile` emits `clicked(index: int)` signal
- `NavigationPage` emits `module_selected(index: int)` signal
- Tiles for placeholder modules (Market Data, Trading, Analytics) shown with muted styling and no click action

### 4.3 MainWindow Structure
```
MainWindow
  └── central_widget (QWidget)
        └── QVBoxLayout
              ├── top_bar (QWidget, fixed height ~36px)
              │     ├── logo_label (left)
              │     ├── position_label (center, hidden on home)
              │     └── nav_buttons (right): [← Prev] [⌂ Home] [Next →]
              └── stack (QStackedWidget)
                    ├── index 0: NavigationPage
                    ├── index 1: ApiConnectionTab
                    ├── index 2: SnapshotTab
                    └── ... (all existing tabs, same instances)
```

### 4.4 Navigation Logic
- `_go_home()` → `stack.setCurrentIndex(0)`, hide position label, dim Prev/Next
- `_go_to(index)` → `stack.setCurrentIndex(index)`, update position label, enable Prev/Next
- `_go_prev()` → `_go_to(current - 1)`, wraps: index 1 → last module (not 0/Home)
- `_go_next()` → `_go_to(current + 1)`, wraps: last → index 1
- Prev/Next skip over Home (index 0) — they only cycle through modules 1..N

---

## 5. Out of Scope

- No changes to any tab's internal layout or business logic
- No changes to service layer, core, or database
- No new features added to existing tabs
- Font files not bundled — Playfair Display loaded from system or Qt fallback to generic serif if unavailable

---

## 6. Success Criteria

- App launches to navigation home page (no tab bar visible)
- All 9 existing modules accessible via tile click
- Prev / Next cycle correctly through modules 1–9
- Home button always returns to navigation page
- Position indicator shows correct `N / 9` on all module pages
- New color palette applied globally (no leftover old colors)
- Playfair Display renders on module names, page titles, and logo
- No regressions in any tab's functionality
