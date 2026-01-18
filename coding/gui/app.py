"""
Application entry point.

Initializes and runs the GUI application.
"""

import sys
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from coding.core.logging.logging_setup import init_logging
from coding.gui.main_window import MainWindow


def main():
    """
    Main entry point for the GUI application.

    Initializes logging, creates the application, and starts the event loop.
    """
    # Initialize logging
    init_logging(level="INFO")
    logger = logging.getLogger(__name__)
    logger.info("Starting Options Trading Platform")

    # Create application
    app = QApplication(sys.argv)

    # Set application properties
    app.setApplicationName("Options Trading Platform")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("RSC Trading")

    # Set default font
    font = QFont("Segoe UI", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    # Enable high DPI scaling
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Create and show main window
    window = MainWindow()
    window.show()

    logger.info("Application window displayed")

    # Run event loop
    exit_code = app.exec()

    logger.info(f"Application exited with code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

