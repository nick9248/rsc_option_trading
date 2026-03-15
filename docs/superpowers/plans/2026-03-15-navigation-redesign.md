# Navigation Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the QTabWidget tab bar with a navigation home page (3-column tile grid) and a slim 3-button top bar (Prev / Home / Next), while applying a midnight navy + gold luxury theme with Playfair Display font.

**Architecture:** `QStackedWidget` holds all pages at fixed indices (0 = NavigationPage, 1–8 = active modules, 9–11 = placeholders). A slim top bar at the top of the window provides Prev/Home/Next navigation. All existing tab widgets are zero-touch — they are inserted into the stack identically to how they were added to the old QTabWidget.

**Tech Stack:** PySide6, Python 3.13, QSS stylesheets, QFontDatabase, QStackedWidget

**Spec:** `docs/superpowers/specs/2026-03-15-navigation-redesign-design.md`

**Task order rationale:** `main_window.py` is replaced first (Task 1) because the current version contains inline `Colors.TAB_INACTIVE` / `Colors.TAB_HOVER` references. Replacing it before `colors.py` is updated ensures there is never a state where the app references removed tokens.

---

## Chunk 1: Core Structure

### Task 1: Replace MainWindow

**Files:**
- Modify: `coding/gui/main_window.py`

- [ ] **Step 1: Replace main_window.py**

```python
"""
Main application window.

Navigation home page + QStackedWidget replaces the old QTabWidget.
A slim top bar provides Prev / Home / Next navigation between modules.
"""

import logging

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QPushButton,
    QLabel,
    QSizePolicy,
    QSpacerItem,
)
from PySide6.QtCore import Qt

from coding.gui.theme.styles import Styles
from coding.gui.theme.colors import Colors
from coding.gui.tabs.api_connection_tab import ApiConnectionTab
from coding.gui.tabs.snapshot_tab import SnapshotTab
from coding.gui.tabs.on_chain_analysis_tab import OnChainAnalysisTab
from coding.gui.tabs.database_tab import DatabaseTab
from coding.gui.tabs.strategy_tab import StrategyTab
from coding.gui.tabs.regime_tab import RegimeTab
from coding.gui.tabs.system_validation_tab import SystemValidationTab
from coding.gui.tabs.special_strategies_tab import SpecialStrategiesTab
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


logger = logging.getLogger(__name__)

# Stack indices 1–8 are active modules. 9–11 are placeholder modules.
# NavigationPage is index 0 (not counted in position indicator).
MODULE_DEFS: list[dict] = [
    {"index": 1,  "icon": "🔗", "name": "API Connection",    "subtitle": "Test endpoints"},
    {"index": 2,  "icon": "📸", "name": "Snapshot",          "subtitle": "Option chain capture"},
    {"index": 3,  "icon": "⛓",  "name": "On Chain Analysis", "subtitle": "GEX · DEX · Max Pain"},
    {"index": 4,  "icon": "🗄",  "name": "Database",          "subtitle": "Capture & sync"},
    {"index": 5,  "icon": "♟",  "name": "Strategies",        "subtitle": "Evaluate & rank"},
    {"index": 6,  "icon": "🎯", "name": "Special Strategies", "subtitle": "OTM finder"},
    {"index": 7,  "icon": "📊", "name": "Market Regime",     "subtitle": "Bull · Bear · Neutral"},
    {"index": 8,  "icon": "✅", "name": "System Health",     "subtitle": "Diagnostics"},
    {"index": 9,  "icon": "📈", "name": "Market Data",       "subtitle": "Coming soon"},
    {"index": 10, "icon": "💹", "name": "Trading",           "subtitle": "Coming soon"},
    {"index": 11, "icon": "🧮", "name": "Analytics",         "subtitle": "Coming soon"},
]

# Last active module index (used for wrap-around navigation)
_LAST_ACTIVE = 8


class MainWindow(QMainWindow):
    """
    Main application window.

    Layout:
        top_bar (fixed 36px)  —  logo | position_label | [← Prev][⌂ Home][Next →]
        stack (QStackedWidget) — index 0: NavigationPage, 1–11: module tabs
    """

    def __init__(self):
        super().__init__()
        self._setup_window()
        self._setup_ui()
        logger.info("Main window initialized")

    # ──────────────────────────────────────────────────────────────
    # Setup
    # ──────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowTitle("Options Trading Platform")
        self.setMinimumSize(600, 400)
        self.resize(1200, 800)
        self.setStyleSheet(Styles.get_main_stylesheet())

        screen = self.screen().availableGeometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

    def _setup_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_top_bar())
        main_layout.addWidget(self._build_stack())

    def _build_top_bar(self) -> QWidget:
        """Build the slim navigation bar (logo · position · prev/home/next)."""
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(36)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(4)

        # Logo (left)
        self.logo_label = QLabel("Options Trading Platform")
        self.logo_label.setObjectName("logoLabel")
        layout.addWidget(self.logo_label)

        # Centre spacers + position indicator
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.position_label = QLabel("")
        self.position_label.setObjectName("positionLabel")
        self.position_label.setVisible(False)
        layout.addWidget(self.position_label)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        # Nav buttons (right)
        self.btn_prev = QPushButton("← Prev")
        self.btn_prev.setObjectName("navBtn")
        self.btn_prev.setFixedSize(70, 26)
        self.btn_prev.setProperty("dimmed", True)  # start dimmed on home page
        self.btn_prev.clicked.connect(self._go_prev)

        self.btn_home = QPushButton("⌂ Home")
        self.btn_home.setObjectName("navBtn")
        self.btn_home.setFixedSize(70, 26)
        self.btn_home.clicked.connect(self._go_home)

        self.btn_next = QPushButton("Next →")
        self.btn_next.setObjectName("navBtn")
        self.btn_next.setFixedSize(70, 26)
        self.btn_next.setProperty("dimmed", True)  # start dimmed on home page
        self.btn_next.clicked.connect(self._go_next)

        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_home)
        layout.addWidget(self.btn_next)

        return bar

    def _build_stack(self) -> QStackedWidget:
        """Build the stacked widget and populate all pages."""
        self.stack = QStackedWidget()
        self.stack.currentChanged.connect(self._sync_nav_state)

        failed_indices: set[int] = set()

        # Index 0: temporary placeholder — replaced by NavigationPage below
        self.stack.addWidget(QWidget())

        # Index 1: API Connection
        self.stack.addWidget(ApiConnectionTab())

        # Index 2: Snapshot
        self.stack.addWidget(SnapshotTab())

        # Index 3: On Chain Analysis
        self.stack.addWidget(OnChainAnalysisTab())

        # Index 4: Database
        self.stack.addWidget(DatabaseTab())

        # Index 5: Strategies
        try:
            api_service = DeribitApiService()
            repository = DatabaseRepository()
            self.stack.addWidget(StrategyTab(api_service, repository))
        except Exception as exc:
            logger.error("Failed to initialize Strategies tab: %s", exc)
            self.stack.addWidget(self._placeholder_widget("Strategy evaluation unavailable"))
            failed_indices.add(5)

        # Index 6: Special Strategies
        try:
            from coding.core.strategy.otm.models.otm_config import OTMConfig
            from coding.service.strategy.otm.otm_finder_service import OTMFinderService
            _api = DeribitApiService()
            _repo = DatabaseRepository()
            _on_chain = OnChainAnalysisService(_api, _repo)
            _config = OTMConfig(risk_budget_usd=10_000.0)
            _otm = OTMFinderService(
                config=_config,
                deribit_service=_api,
                on_chain_service=_on_chain,
                repository=_repo,
            )
            self.stack.addWidget(SpecialStrategiesTab(finder_service=_otm, otm_config=_config))
        except Exception as exc:
            logger.error("Failed to initialize Special Strategies tab: %s", exc)
            self.stack.addWidget(self._placeholder_widget("Special strategies unavailable"))
            failed_indices.add(6)

        # Index 7: Market Regime
        self.stack.addWidget(RegimeTab())

        # Index 8: System Health
        self.stack.addWidget(SystemValidationTab())

        # Indices 9–11: Future placeholders
        self.stack.addWidget(self._placeholder_widget("Market data visualization coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Trading interface coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Analytics dashboard coming soon…"))

        # Index 0: NavigationPage — replaces the temporary slot
        # Import here to avoid circular import (navigation_page imports Colors)
        from coding.gui.tabs.navigation_page import NavigationPage
        nav_page = NavigationPage(module_defs=MODULE_DEFS, failed_indices=failed_indices)
        nav_page.module_selected.connect(self._go_to)
        self.stack.removeWidget(self.stack.widget(0))
        self.stack.insertWidget(0, nav_page)
        self.stack.setCurrentIndex(0)

        return self.stack

    # ──────────────────────────────────────────────────────────────
    # Navigation
    # ──────────────────────────────────────────────────────────────

    def _go_home(self) -> None:
        self.stack.setCurrentIndex(0)

    def _go_to(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def _go_prev(self) -> None:
        current = self.stack.currentIndex()
        if current <= 1:
            self._go_to(_LAST_ACTIVE)
        else:
            self._go_to(current - 1)

    def _go_next(self) -> None:
        current = self.stack.currentIndex()
        if current == 0 or current >= _LAST_ACTIVE:
            self._go_to(1)
        else:
            self._go_to(current + 1)

    def _sync_nav_state(self, index: int) -> None:
        """
        Keep top bar in sync with the current stack page.

        Position label shows "{index} / 8". This is valid because active modules
        occupy contiguous stack indices 1–8, so the raw stack index equals the
        1-based display position.

        Uses setProperty("dimmed", ...) + unpolish/polish so that QSS :hover
        and :pressed rules remain active — inline setStyleSheet would shadow them.
        """
        on_home = (index == 0)
        self.position_label.setVisible(not on_home)
        if not on_home:
            self.position_label.setText(f"{index} / {_LAST_ACTIVE}")

        for btn in (self.btn_prev, self.btn_next):
            btn.setProperty("dimmed", on_home)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _placeholder_widget(message: str) -> QWidget:
        """Return a simple centred label widget for unavailable / future modules."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(message)
        label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 14px; font-style: italic;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return widget

    def closeEvent(self, event) -> None:
        logger.info("Application closing")
        event.accept()
```

- [ ] **Step 2: Verify main_window.py imports cleanly (no AttributeError on Colors)**

Run:
```bash
python -c "from coding.gui.main_window import MainWindow; print('OK')"
```
Expected: `OK` (DB/API connection warnings are fine — no AttributeError or ImportError)

- [ ] **Step 3: Verify no TAB_* references remain in main_window.py**

Run:
```bash
grep -n "TAB_" coding/gui/main_window.py
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add coding/gui/main_window.py
git commit -m "feat: replace QTabWidget with QStackedWidget + top bar navigation"
```

---

### Task 2: Update Color Palette

**Files:**
- Modify: `coding/gui/theme/colors.py`

> **Safe to do now:** `main_window.py` no longer references `TAB_*` tokens (replaced in Task 1).

- [ ] **Step 1: Replace colors.py**

```python
"""
Color palette for the luxury dark theme.

Defines all colors used throughout the application.
"""


class Colors:
    """
    Midnight navy luxury theme — Theme B.

    Deep navy backgrounds, warm platinum gold accents, Inter + Playfair Display fonts.
    """

    # Background colors
    BACKGROUND_PRIMARY = "#080D18"
    BACKGROUND_SECONDARY = "#0A1020"
    BACKGROUND_TERTIARY = "#0D1428"
    BACKGROUND_ELEVATED = "#111E35"

    # Surface colors (for cards, panels)
    SURFACE = "#0D1428"
    SURFACE_HOVER = "#111E35"
    SURFACE_ACTIVE = "#192840"

    # Border colors
    BORDER = "#141E30"
    BORDER_SUBTLE = "#0F1828"
    BORDER_FOCUS = "#D4B896"  # Use rgba(212,184,150,0.4) in QSS for alpha variant

    # Text colors
    TEXT_PRIMARY = "#E8EAF0"
    TEXT_SECONDARY = "#5A6A7C"
    TEXT_MUTED = "#2A3848"
    TEXT_DISABLED = "#1A2638"

    # Accent colors (warm platinum gold)
    ACCENT = "#D4B896"
    ACCENT_HOVER = "#E8CEAD"
    ACCENT_MUTED = "#A8956A"

    # Status colors (unchanged)
    SUCCESS = "#2ECC71"
    SUCCESS_MUTED = "#1E8449"
    WARNING = "#F39C12"
    WARNING_MUTED = "#B7950B"
    ERROR = "#E74C3C"
    ERROR_MUTED = "#A93226"
    INFO = "#3498DB"
    INFO_MUTED = "#2171A9"

    # Input colors
    INPUT_BACKGROUND = "#0A1020"
    INPUT_BORDER = "#141E30"
    INPUT_FOCUS = "#D4B896"

    # Button colors
    BUTTON_PRIMARY = "#D4B896"
    BUTTON_PRIMARY_HOVER = "#E8CEAD"
    BUTTON_SECONDARY = "#111E35"
    BUTTON_SECONDARY_HOVER = "#192840"

    # Scrollbar colors
    SCROLLBAR_TRACK = "#0A1020"
    SCROLLBAR_HANDLE = "#141E30"
    SCROLLBAR_HANDLE_HOVER = "#1E2D45"

    # Financial colors (unchanged)
    PROFIT = "#2ECC71"
    LOSS = "#E74C3C"
```

- [ ] **Step 2: Verify no TAB_* tokens remain**

Run:
```bash
grep -n "TAB_" coding/gui/theme/colors.py
```
Expected: no output.

- [ ] **Step 3: Verify colors.py imports cleanly**

Run:
```bash
python -c "from coding.gui.theme.colors import Colors; print(Colors.ACCENT)"
```
Expected: `#D4B896`

- [ ] **Step 4: Commit**

```bash
git add coding/gui/theme/colors.py
git commit -m "feat: apply midnight navy + gold palette (Theme B) to colors.py"
```

---

### Task 3: Update Stylesheet

**Files:**
- Modify: `coding/gui/theme/styles.py`

- [ ] **Step 1: Replace styles.py**

```python
"""
QSS Stylesheets for the luxury dark theme.

Provides complete styling for all widgets.
"""

from coding.gui.theme.colors import Colors


class Styles:
    """
    QSS stylesheet generator for the application.

    All styles use the Colors palette for consistency.
    """

    @staticmethod
    def get_main_stylesheet() -> str:
        """
        Get the complete application stylesheet.

        Returns:
            QSS stylesheet string.
        """
        return f"""
            /* Main Window */
            QMainWindow {{
                background-color: {Colors.BACKGROUND_PRIMARY};
            }}

            QWidget {{
                background-color: {Colors.BACKGROUND_PRIMARY};
                color: {Colors.TEXT_PRIMARY};
                font-family: "Segoe UI", "Inter", sans-serif;
                font-size: 13px;
            }}

            /* Top Navigation Bar */
            QWidget#topBar {{
                background-color: {Colors.BACKGROUND_PRIMARY};
                border-bottom: 1px solid {Colors.BORDER};
            }}

            /* Navigation Buttons (Prev / Home / Next) */
            QPushButton#navBtn {{
                background-color: transparent;
                color: {Colors.ACCENT};
                border: 1px solid {Colors.BORDER};
                border-radius: 4px;
                padding: 4px 14px;
                font-family: "Playfair Display", "Georgia", serif;
                font-size: 11px;
                min-width: 60px;
            }}

            QPushButton#navBtn:hover {{
                background-color: {Colors.SURFACE};
                border-color: {Colors.ACCENT};
                color: {Colors.ACCENT_HOVER};
            }}

            QPushButton#navBtn:pressed {{
                background-color: {Colors.SURFACE_ACTIVE};
            }}

            /* Dimmed state — on home page, Prev/Next use TEXT_MUTED color.
               Uses dynamic property so :hover rules remain active. */
            QPushButton#navBtn[dimmed="true"] {{
                color: {Colors.TEXT_MUTED};
                border-color: {Colors.BORDER_SUBTLE};
            }}

            /* Position label (e.g. "3 / 8") */
            QLabel#positionLabel {{
                background-color: transparent;
                color: {Colors.TEXT_SECONDARY};
                font-family: "Segoe UI", "Inter", sans-serif;
                font-size: 11px;
                letter-spacing: 1px;
            }}

            /* Logo label */
            QLabel#logoLabel {{
                background-color: transparent;
                color: {Colors.ACCENT};
                font-family: "Playfair Display", "Georgia", serif;
                font-size: 13px;
                font-style: italic;
                letter-spacing: 2px;
            }}

            /* Navigation Home Page */
            QWidget#navigationPage {{
                background-color: {Colors.BACKGROUND_PRIMARY};
            }}

            QLabel#navPageHeader {{
                background-color: transparent;
                color: {Colors.TEXT_MUTED};
                font-family: "Playfair Display", "Georgia", serif;
                font-size: 12px;
                font-style: italic;
                letter-spacing: 3px;
            }}

            /* Module Tile */
            QFrame#moduleTile {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
            }}

            QLabel#tileIcon {{
                background-color: transparent;
                font-size: 22px;
            }}

            QLabel#tileName {{
                background-color: transparent;
                color: {Colors.TEXT_SECONDARY};
                font-family: "Playfair Display", "Georgia", serif;
                font-size: 11px;
                letter-spacing: 0.5px;
            }}

            QLabel#tileSub {{
                background-color: transparent;
                color: {Colors.TEXT_MUTED};
                font-family: "Segoe UI", "Inter", sans-serif;
                font-size: 10px;
            }}

            /* Labels */
            QLabel {{
                background-color: transparent;
                color: {Colors.TEXT_PRIMARY};
                padding: 2px;
            }}

            QLabel[class="heading"] {{
                font-size: 16px;
                font-weight: 600;
                color: {Colors.TEXT_PRIMARY};
            }}

            QLabel[class="subheading"] {{
                font-size: 12px;
                color: {Colors.TEXT_SECONDARY};
            }}

            /* Combo Box (Dropdown) */
            QComboBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 10px 16px;
                color: {Colors.TEXT_PRIMARY};
                min-width: 200px;
                min-height: 20px;
            }}

            QComboBox:hover {{
                border-color: {Colors.BORDER_FOCUS};
            }}

            QComboBox:focus {{
                border-color: {Colors.ACCENT};
            }}

            QComboBox::drop-down {{
                border: none;
                width: 30px;
            }}

            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {Colors.TEXT_SECONDARY};
                margin-right: 10px;
            }}

            QComboBox QAbstractItemView {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                selection-background-color: {Colors.SURFACE_ACTIVE};
                selection-color: {Colors.TEXT_PRIMARY};
                padding: 4px;
            }}

            QComboBox QAbstractItemView::item {{
                padding: 8px 16px;
                border-radius: 4px;
            }}

            QComboBox QAbstractItemView::item:hover {{
                background-color: {Colors.SURFACE_HOVER};
            }}

            /* Push Button */
            QPushButton {{
                background-color: {Colors.BUTTON_PRIMARY};
                color: {Colors.BACKGROUND_PRIMARY};
                border: none;
                border-radius: 8px;
                padding: 12px 32px;
                font-weight: 600;
                min-width: 100px;
            }}

            QPushButton:hover {{
                background-color: {Colors.BUTTON_PRIMARY_HOVER};
            }}

            QPushButton:pressed {{
                background-color: {Colors.ACCENT_MUTED};
            }}

            QPushButton:disabled {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_DISABLED};
            }}

            QPushButton[class="secondary"] {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}

            QPushButton[class="secondary"]:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}

            /* Text Edit (Log Viewer) */
            QTextEdit {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 12px;
                font-family: "Consolas", "SF Mono", monospace;
                font-size: 12px;
                selection-background-color: {Colors.SURFACE_ACTIVE};
            }}

            QPlainTextEdit {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 12px;
                font-family: "Consolas", "SF Mono", monospace;
                font-size: 12px;
                selection-background-color: {Colors.SURFACE_ACTIVE};
            }}

            /* Scroll Bar */
            QScrollBar:vertical {{
                background-color: {Colors.SCROLLBAR_TRACK};
                width: 10px;
                border-radius: 5px;
                margin: 0;
            }}

            QScrollBar::handle:vertical {{
                background-color: {Colors.SCROLLBAR_HANDLE};
                border-radius: 5px;
                min-height: 30px;
            }}

            QScrollBar::handle:vertical:hover {{
                background-color: {Colors.SCROLLBAR_HANDLE_HOVER};
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}

            QScrollBar:horizontal {{
                background-color: {Colors.SCROLLBAR_TRACK};
                height: 10px;
                border-radius: 5px;
                margin: 0;
            }}

            QScrollBar::handle:horizontal {{
                background-color: {Colors.SCROLLBAR_HANDLE};
                border-radius: 5px;
                min-width: 30px;
            }}

            QScrollBar::handle:horizontal:hover {{
                background-color: {Colors.SCROLLBAR_HANDLE_HOVER};
            }}

            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
            }}

            /* Frame */
            QFrame {{
                background-color: transparent;
            }}

            QFrame[class="card"] {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}

            /* Group Box */
            QGroupBox {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                margin-top: 16px;
                padding-top: 16px;
                font-weight: 600;
            }}

            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 16px;
                padding: 0 8px;
                color: {Colors.TEXT_PRIMARY};
            }}

            /* Line Edit */
            QLineEdit {{
                background-color: {Colors.INPUT_BACKGROUND};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 10px 16px;
                color: {Colors.TEXT_PRIMARY};
            }}

            QLineEdit:focus {{
                border-color: {Colors.ACCENT};
            }}

            /* Spin Box */
            QSpinBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 10px 16px;
                color: {Colors.TEXT_PRIMARY};
            }}

            QSpinBox:focus {{
                border-color: {Colors.ACCENT};
            }}

            /* Separator */
            QFrame[class="separator"] {{
                background-color: {Colors.BORDER};
                max-height: 1px;
            }}

            /* Tool Tip */
            QToolTip {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 8px;
            }}
        """

    @staticmethod
    def get_log_colors() -> dict:
        """
        Get colors for different log levels.

        Returns:
            Dictionary mapping log levels to colors.
        """
        return {
            "DEBUG": Colors.TEXT_MUTED,
            "INFO": Colors.TEXT_PRIMARY,
            "WARNING": Colors.WARNING,
            "ERROR": Colors.ERROR,
            "CRITICAL": Colors.ERROR,
        }
```

- [ ] **Step 2: Verify no TAB_* token references or QTabBar/QTabWidget QSS remain**

Run:
```bash
grep -n "TAB_\|QTabBar\|QTabWidget" coding/gui/theme/styles.py
```
Expected: no output (both Python token refs and QSS selectors must be gone).

- [ ] **Step 3: Verify styles.py imports cleanly**

Run:
```bash
python -c "from coding.gui.theme.styles import Styles; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add coding/gui/theme/styles.py
git commit -m "feat: replace tab QSS with top-bar + nav tile styles, apply midnight theme"
```

---

## Chunk 2: Navigation Widget + Font

### Task 4: Bundle Playfair Display Font Files

**Files:**
- Create: `coding/gui/assets/fonts/.gitkeep`
- Create: `coding/gui/assets/fonts/PlayfairDisplay-Regular.ttf`
- Create: `coding/gui/assets/fonts/PlayfairDisplay-Italic.ttf`

- [ ] **Step 1: Create the assets directory**

Run:
```bash
mkdir -p coding/gui/assets/fonts
touch coding/gui/assets/fonts/.gitkeep
```

- [ ] **Step 2: Download Playfair Display font files from Google Fonts**

Run:
```bash
curl -L "https://fonts.gstatic.com/s/playfairdisplay/v37/nuFiD-vYSZviVYUb_rj3ij__anPXDTzYgEM86xRbPQ.ttf" \
  -o coding/gui/assets/fonts/PlayfairDisplay-Regular.ttf

curl -L "https://fonts.gstatic.com/s/playfairdisplay/v37/nuFkD-vYSZviVYUb_rj3ij__anPXBYf9lW4e5j5hNKc.ttf" \
  -o coding/gui/assets/fonts/PlayfairDisplay-Italic.ttf
```

- [ ] **Step 3: Verify files exist and are valid TrueType fonts**

Run:
```bash
ls -lh coding/gui/assets/fonts/
```
Expected: both `.ttf` files present, each > 50KB (a curl 404 HTML page would be <10KB).

Then verify they are real font files (not HTML error pages):
```bash
python -c "
for name in ['PlayfairDisplay-Regular.ttf', 'PlayfairDisplay-Italic.ttf']:
    with open(f'coding/gui/assets/fonts/{name}', 'rb') as f:
        header = f.read(4)
    assert header in (b'\x00\x01\x00\x00', b'OTTO', b'true'), f'Invalid: {name} header={header!r}'
    print(f'{name}: valid TTF')
"
```
Expected: both lines print `valid TTF`. If assertion fails, the download returned an error page — check the URL and retry.

- [ ] **Step 4: Commit**

```bash
git add coding/gui/assets/fonts/
git commit -m "feat: bundle PlayfairDisplay Regular and Italic font files"
```

---

### Task 5: Create NavigationPage Widget

**Files:**
- Create: `coding/gui/tabs/navigation_page.py`

- [ ] **Step 1: Create navigation_page.py**

```python
"""
Navigation home page.

Displays a 3-column tile grid for all application modules.
Clicking an active tile emits module_selected(stack_index).
"""

import logging

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


class ModuleTile(QFrame):
    """
    A clickable tile representing one application module.

    Emits clicked(stack_index) when the user clicks an active tile.
    Disabled tiles (placeholders and service-failed modules) show muted
    styling and do not emit clicks.
    """

    clicked = Signal(int)

    def __init__(self, stack_index: int, icon: str, name: str, subtitle: str, parent=None):
        super().__init__(parent)
        self._stack_index = stack_index
        self._enabled = True

        self.setObjectName("moduleTile")
        self.setFixedHeight(90)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel(icon)
        self.icon_label.setObjectName("tileIcon")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.name_label = QLabel(name)
        self.name_label.setObjectName("tileName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.sub_label = QLabel(subtitle)
        self.sub_label.setObjectName("tileSub")
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.name_label)
        layout.addWidget(self.sub_label)

    def set_disabled_style(self) -> None:
        """Apply muted styling and disable click for placeholder and failed modules."""
        self._enabled = False
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.name_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        self.sub_label.setStyleSheet(f"color: {Colors.TEXT_DISABLED};")
        self.setStyleSheet(f"""
            QFrame#moduleTile {{
                background-color: {Colors.BACKGROUND_SECONDARY};
                border: 1px solid {Colors.BORDER_SUBTLE};
                border-radius: 8px;
            }}
        """)

    def enterEvent(self, event) -> None:
        """Highlight tile name in gold on hover (active tiles only)."""
        if self._enabled:
            self.name_label.setStyleSheet(f"color: {Colors.ACCENT};")
            self.setStyleSheet(f"""
                QFrame#moduleTile {{
                    background-color: {Colors.SURFACE_HOVER};
                    border: 1px solid {Colors.ACCENT_MUTED};
                    border-radius: 8px;
                }}
            """)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """Restore default styling on mouse leave."""
        if self._enabled:
            self.name_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
            self.setStyleSheet("")  # revert to QSS #moduleTile default
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """Emit clicked signal with stack index when active tile is pressed."""
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._stack_index)
        super().mousePressEvent(event)


class NavigationPage(QWidget):
    """
    Home navigation page with 3-column module tile grid.

    Emits module_selected(stack_index) when an active tile is clicked.
    """

    module_selected = Signal(int)

    # Stack indices that are always placeholder (never active)
    _PLACEHOLDER_INDICES = {9, 10, 11}

    def __init__(
        self,
        module_defs: list[dict],
        failed_indices: set[int],
        parent=None,
    ):
        """
        Args:
            module_defs: List of dicts with keys: index, icon, name, subtitle.
                         Covers stack indices 1–11 (all active + placeholder modules).
            failed_indices: Stack indices that failed to initialize (service errors).
                            These tiles are shown as disabled.
        """
        super().__init__(parent)
        self.setObjectName("navigationPage")
        self._build_ui(module_defs, failed_indices)

    def _build_ui(self, module_defs: list[dict], failed_indices: set[int]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 32, 40, 32)
        outer.setSpacing(20)

        header = QLabel("Select Module")
        header.setObjectName("navPageHeader")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(header)

        grid = QGridLayout()
        grid.setSpacing(10)

        for i, defn in enumerate(module_defs):
            tile = ModuleTile(
                stack_index=defn["index"],
                icon=defn["icon"],
                name=defn["name"],
                subtitle=defn["subtitle"],
            )

            is_disabled = (
                defn["index"] in self._PLACEHOLDER_INDICES
                or defn["index"] in failed_indices
            )
            if is_disabled:
                tile.set_disabled_style()
            else:
                tile.clicked.connect(self.module_selected)

            row, col = divmod(i, 3)
            grid.addWidget(tile, row, col)

        outer.addLayout(grid)
        outer.addStretch()
```

- [ ] **Step 2: Verify the file imports without error**

Run:
```bash
python -c "from coding.gui.tabs.navigation_page import NavigationPage, ModuleTile; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add coding/gui/tabs/navigation_page.py
git commit -m "feat: add NavigationPage widget with 3-col ModuleTile grid"
```

---

### Task 6: Update app.py — Load Playfair Display Font

**Files:**
- Modify: `coding/gui/app.py`

- [ ] **Step 1: Replace app.py**

```python
"""
Application entry point.

Initializes and runs the GUI application.
"""

import sys
import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase

from coding.core.logging.logging_setup import init_logging
from coding.gui.main_window import MainWindow


_FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
_FONT_FILES = [
    "PlayfairDisplay-Regular.ttf",
    "PlayfairDisplay-Italic.ttf",
]


def _load_fonts(logger: logging.Logger) -> None:
    """Load bundled font files into Qt's font database."""
    for filename in _FONT_FILES:
        path = _FONTS_DIR / filename
        if not path.exists():
            logger.warning("Font file not found, skipping: %s", path)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            logger.warning("Failed to load font: %s", path)
        else:
            logger.info("Loaded font: %s", filename)


def main():
    """
    Main entry point for the GUI application.

    Initializes logging, creates the application, and starts the event loop.
    """
    init_logging(level="INFO")
    logger = logging.getLogger(__name__)
    logger.info("Starting Options Trading Platform")

    app = QApplication(sys.argv)
    app.setApplicationName("Options Trading Platform")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("RSC Trading")

    # Load bundled fonts before any widgets are created
    _load_fonts(logger)

    # Default body font — Segoe UI / Inter for data readability
    font = QFont("Segoe UI", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    window = MainWindow()
    window.show()

    logger.info("Application window displayed")

    exit_code = app.exec()
    logger.info("Application exited with code: %s", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify app.py imports cleanly**

Run:
```bash
python -c "import coding.gui.app; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add coding/gui/app.py
git commit -m "feat: load Playfair Display fonts via QFontDatabase in app.py"
```

---

### Task 7: Smoke Test and Final Verification

- [ ] **Step 1: Launch the application**

Run:
```bash
python -m coding.gui.app
```
Expected: app opens to the navigation home page — 3-column tile grid visible, no tab bar.

- [ ] **Step 2: Verify top bar appearance**

Check:
- Logo "Options Trading Platform" in italic gold on the left
- ← Prev, ⌂ Home, Next → buttons on the right
- Position indicator is hidden on the home page
- Prev/Next buttons are dimmed (dark color, not gold) on the home page

- [ ] **Step 3: Test tile navigation (click each active tile)**

Click each tile from API Connection through System Health and verify:
- The correct module content appears
- Position label shows `N / 8` (e.g. "1 / 8" for API Connection)
- Prev/Next buttons turn gold

- [ ] **Step 4: Test Prev/Next wrap-around**

| Start | Action | Expected destination |
|---|---|---|
| API Connection (1/8) | ← Prev | System Health (8/8) |
| System Health (8/8) | Next → | API Connection (1/8) |
| Home (hidden label) | ← Prev | System Health (8/8) |
| Home (hidden label) | Next → | API Connection (1/8) |

- [ ] **Step 5: Verify placeholder tiles are inert**

Click Market Data, Trading, Analytics tiles — cursor should be arrow (not pointer), nothing should happen.

- [ ] **Step 6: Verify color theme**

Check background is deep navy (`#080D18`), accents and buttons are warm gold, no charcoal-black old theme visible.

- [ ] **Step 7: Tab regression smoke test**

Open each of the 8 active modules via the tile or Prev/Next and verify one basic operation per tab. The app must not crash and the UI must render correctly:

| Module | Basic operation to verify |
|---|---|
| API Connection | Load instrument list dropdown (click "Load Instruments") |
| Snapshot | Load expirations dropdown |
| On Chain Analysis | Select BTC, click "Analyze" — report section appears |
| Database | Tiles visible, no crash |
| Strategies | Expiry selector loads without crashing |
| Special Strategies | Scan grid renders without crashing |
| Market Regime | "Detect Regime" button visible |
| System Health | Validation checks visible, "Run Validation" present |

Expected: all 8 modules render, no Python exceptions in the log output.

- [ ] **Step 8: Final commit**

```bash
git add .
git commit -m "feat: complete navigation redesign — home page, top bar, midnight navy theme"
```
