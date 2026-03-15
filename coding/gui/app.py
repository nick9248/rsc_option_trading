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
    "Raleway-Light.ttf",
    "Raleway-Regular.ttf",
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

    # Default body font — Raleway for consistent luxury minimal look
    font = QFont("Raleway", 10)
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
