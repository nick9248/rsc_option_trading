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
                font-family: "Segoe UI", "SF Pro Display", sans-serif;
                font-size: 13px;
            }}

            /* Tab Widget */
            QTabWidget::pane {{
                background-color: {Colors.BACKGROUND_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                margin-top: -1px;
            }}

            QTabBar {{
                background-color: transparent;
            }}

            QTabBar::tab {{
                background-color: {Colors.TAB_INACTIVE};
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-bottom: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                padding: 10px 24px;
                margin-right: 4px;
                min-width: 120px;
            }}

            QTabBar::tab:selected {{
                background-color: {Colors.TAB_ACTIVE};
                color: {Colors.TEXT_PRIMARY};
                border-color: {Colors.BORDER};
                border-bottom: 1px solid {Colors.TAB_ACTIVE};
            }}

            QTabBar::tab:hover:!selected {{
                background-color: {Colors.TAB_HOVER};
                color: {Colors.TEXT_PRIMARY};
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
