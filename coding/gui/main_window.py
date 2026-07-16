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
from coding.gui.tabs.system_validation_tab import SystemValidationTab
from coding.gui.tabs.automation_tab import AutomationTab


logger = logging.getLogger(__name__)

# Stack indices 1–6 are active modules. 7–9 are placeholder modules.
# NavigationPage is index 0 (not counted in position indicator).
MODULE_DEFS: list[dict] = [
    {"index": 1,  "icon": "🔗", "name": "API Connection",    "subtitle": "Test endpoints"},
    {"index": 2,  "icon": "📸", "name": "Snapshot",          "subtitle": "Option chain capture"},
    {"index": 3,  "icon": "⛓",  "name": "On Chain Analysis", "subtitle": "GEX · DEX · Max Pain"},
    {"index": 4,  "icon": "🗄",  "name": "Database",          "subtitle": "Capture & sync"},
    {"index": 5,  "icon": "✅", "name": "System Health",     "subtitle": "Diagnostics"},
    {"index": 6,  "icon": "🤖", "name": "Automation",        "subtitle": "Scanners · manual trigger"},
    {"index": 7,  "icon": "📈", "name": "Market Data",       "subtitle": "Coming soon"},
    {"index": 8,  "icon": "💹", "name": "Trading",           "subtitle": "Coming soon"},
    {"index": 9,  "icon": "🧮", "name": "Analytics",         "subtitle": "Coming soon"},
]

# Last active module index (used for wrap-around navigation)
_LAST_ACTIVE = 6

# Stack indices that are permanent placeholders (never active modules)
_PLACEHOLDER_INDICES = {7, 8, 9}


class MainWindow(QMainWindow):
    """
    Main application window.

    Layout:
        top_bar (fixed 36px)  —  logo | position_label | [← Prev][⌂ Home][Next →]
        stack (QStackedWidget) — index 0: NavigationPage, 1–9: module tabs
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

        # Order is load-bearing: _build_top_bar sets self.btn_prev/btn_next/position_label
        # which _sync_nav_state (called from _build_stack via currentChanged) depends on.
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

        # Nav buttons (right) — hidden on home page, visible inside modules
        self.btn_prev = QPushButton("← Prev")
        self.btn_prev.setObjectName("navBtn")
        self.btn_prev.setFixedSize(70, 26)
        self.btn_prev.setVisible(False)
        self.btn_prev.clicked.connect(self._go_prev)

        self.btn_home = QPushButton("⌂ Home")
        self.btn_home.setObjectName("navBtn")
        self.btn_home.setFixedSize(70, 26)
        self.btn_home.setVisible(False)
        self.btn_home.clicked.connect(self._go_home)

        self.btn_next = QPushButton("Next →")
        self.btn_next.setObjectName("navBtn")
        self.btn_next.setFixedSize(70, 26)
        self.btn_next.setVisible(False)
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

        # Index 5: System Health
        self.stack.addWidget(SystemValidationTab())

        # Index 6: Automation
        self.stack.addWidget(AutomationTab())

        # Indices 7–9: Future placeholders
        self.stack.addWidget(self._placeholder_widget("Market data visualization coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Trading interface coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Analytics dashboard coming soon…"))

        # Index 0: NavigationPage — replaces the temporary slot
        # Deferred import to avoid circular import at module level
        from coding.gui.tabs.navigation_page import NavigationPage
        nav_page = NavigationPage(module_defs=MODULE_DEFS, failed_indices=failed_indices | _PLACEHOLDER_INDICES)
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

        Nav buttons (Prev / Home / Next) are hidden on the home page and
        shown inside any module. Position label shows "{index} / {_LAST_ACTIVE}"
        and is hidden on home.
        """
        on_home = (index == 0)
        self.position_label.setVisible(not on_home)
        if not on_home:
            self.position_label.setText(f"{index} / {_LAST_ACTIVE}")

        for btn in (self.btn_prev, self.btn_home, self.btn_next):
            btn.setVisible(not on_home)

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
