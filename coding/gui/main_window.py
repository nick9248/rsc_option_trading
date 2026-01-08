"""
Main application window.

Contains the primary window with tab bar and content area.
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTabWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from coding.gui.theme.styles import Styles
from coding.gui.theme.colors import Colors
from coding.gui.tabs.api_connection_tab import ApiConnectionTab


logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Main application window with tabbed interface.

    Features:
    - Modern minimal design with luxury aesthetics
    - Rounded tab bar at the top
    - Dynamic content area based on selected tab
    """

    def __init__(self):
        """Initialize the main window."""
        super().__init__()

        self._setup_window()
        self._setup_ui()

        logger.info("Main window initialized")

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.setWindowTitle("Options Trading Platform")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # Apply stylesheet
        self.setStyleSheet(Styles.get_main_stylesheet())

        # Center on screen
        screen = self.screen().availableGeometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # Central widget
        central_widget = QWidget()
        central_widget.setObjectName("centralWidget")
        self.setCentralWidget(central_widget)

        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(0)

        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)

        # Custom styling for rounded tabs
        self.tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                background-color: {Colors.BACKGROUND_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                padding: 8px;
            }}

            QTabBar {{
                background-color: {Colors.BACKGROUND_PRIMARY};
            }}

            QTabBar::tab {{
                background-color: {Colors.TAB_INACTIVE};
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-bottom: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 12px 28px;
                margin-right: 4px;
                min-width: 140px;
                font-weight: 500;
            }}

            QTabBar::tab:selected {{
                background-color: {Colors.BACKGROUND_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border-bottom: 1px solid {Colors.BACKGROUND_SECONDARY};
            }}

            QTabBar::tab:hover:!selected {{
                background-color: {Colors.TAB_HOVER};
                color: {Colors.TEXT_PRIMARY};
            }}

            QTabBar::tab:first {{
                margin-left: 8px;
            }}
        """)

        # Add tabs
        self._add_tabs()

        main_layout.addWidget(self.tab_widget)

    def _add_tabs(self) -> None:
        """Add all application tabs."""
        # API Connection tab
        api_tab = ApiConnectionTab()
        self.tab_widget.addTab(api_tab, "API Connection")

        # Placeholder tabs for future features
        self._add_placeholder_tab("Market Data", "Market data visualization coming soon...")
        self._add_placeholder_tab("Trading", "Trading interface coming soon...")
        self._add_placeholder_tab("Analytics", "Analytics dashboard coming soon...")

    def _add_placeholder_tab(self, title: str, message: str) -> None:
        """
        Add a placeholder tab for future features.

        Args:
            title: Tab title.
            message: Placeholder message to display.
        """
        from PySide6.QtWidgets import QLabel

        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel(message)
        label.setStyleSheet(f"""
            color: {Colors.TEXT_MUTED};
            font-size: 16px;
            font-style: italic;
        """)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

        self.tab_widget.addTab(placeholder, title)

    def closeEvent(self, event) -> None:
        """
        Handle window close event.

        Args:
            event: Close event.
        """
        logger.info("Application closing")
        event.accept()
