"""
Log viewer component for displaying application logs.

Provides a styled text display with color-coded log levels.
"""

import logging
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import QPlainTextEdit, QWidget
from PySide6.QtGui import QTextCharFormat, QColor, QFont
from PySide6.QtCore import Qt, Signal, QObject

from coding.gui.theme.colors import Colors


class LogSignal(QObject):
    """Signal emitter for thread-safe log updates."""

    message_received = Signal(str, str)


class LogViewer(QPlainTextEdit):
    """
    Custom log viewer widget with color-coded log levels.

    Displays logs in a read-only text area with syntax highlighting
    based on log level.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the log viewer.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)

        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        self.log_signal = LogSignal()
        self.log_signal.message_received.connect(self._append_log)

        self.level_colors = {
            "DEBUG": Colors.TEXT_MUTED,
            "INFO": Colors.INFO,
            "WARNING": Colors.WARNING,
            "ERROR": Colors.ERROR,
            "CRITICAL": Colors.ERROR,
        }

    def _append_log(self, level: str, message: str) -> None:
        """
        Append a log message with appropriate coloring.

        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            message: Log message text.
        """
        color = self.level_colors.get(level, Colors.TEXT_PRIMARY)

        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))

        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(message + "\n", text_format)

        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def log(self, level: str, message: str) -> None:
        """
        Log a message to the viewer (thread-safe).

        Args:
            level: Log level string.
            message: Message to display.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] [{level:8}] {message}"
        self.log_signal.message_received.emit(level, formatted_message)

    def log_debug(self, message: str) -> None:
        """Log a debug message."""
        self.log("DEBUG", message)

    def log_info(self, message: str) -> None:
        """Log an info message."""
        self.log("INFO", message)

    def log_warning(self, message: str) -> None:
        """Log a warning message."""
        self.log("WARNING", message)

    def log_error(self, message: str) -> None:
        """Log an error message."""
        self.log("ERROR", message)

    def clear_logs(self) -> None:
        """Clear all log messages."""
        self.clear()


class GuiLogHandler(logging.Handler):
    """
    Custom logging handler that sends logs to a LogViewer widget.

    Bridges the Python logging system with the GUI log display.
    """

    def __init__(self, log_viewer: LogViewer):
        """
        Initialize the handler.

        Args:
            log_viewer: LogViewer widget to send logs to.
        """
        super().__init__()
        self.log_viewer = log_viewer

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to the GUI.

        Args:
            record: Log record to emit.
        """
        try:
            message = self.format(record)
            level = record.levelname
            self.log_viewer.log_signal.message_received.emit(level, message)
        except Exception:
            self.handleError(record)
