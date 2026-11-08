"""
Widgets, selector choices and display formatters shared by the MenthorQ
tabs (Charts and Monitor). Display only: no calculation, no database access.
"""

from typing import Any, Iterable, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHeaderView, QPushButton, QScrollArea, QTableWidget

from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH,
    GAMMA_UNDERLYING_FORWARD,
    GAMMA_UNDERLYING_INDEX,
)
from coding.gui.theme.colors import Colors
from coding.service.on_chain.menthorq_replication_service import (
    RUN_MODE_LIVE,
    RUN_MODE_MENTHORQ_DAILY_CLOSE,
)

CURRENCIES = ("BTC", "ETH")
MODES = (("MenthorQ daily close", RUN_MODE_MENTHORQ_DAILY_CLOSE),
         ("Live (newest stored snapshot)", RUN_MODE_LIVE))
EXPIRY_UNIVERSE_CHOICES = (("Monthly", EXPIRY_UNIVERSE_MONTHLY),
                           ("All listed", EXPIRY_UNIVERSE_ALL),
                           ("Quarterly + front month", EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH))
GAMMA_UNDERLYING_CHOICES = (("Index", GAMMA_UNDERLYING_INDEX), ("Forward", GAMMA_UNDERLYING_FORWARD))

COMPARISON_COLUMN_LABELS = ("Item", "Ours", "MenthorQ", "Ours - MenthorQ")
NOT_COMPUTED = "not computed"


class ChartScrollArea(QScrollArea):
    """Scroll area that tells its owner when its width changes, to rescale the charts."""

    resized = Signal()

    def resizeEvent(self, event) -> None:
        """Emit ``resized`` after the normal resize handling."""
        super().resizeEvent(event)
        self.resized.emit()


def format_comparison_value(value: Any, missing_text: str) -> str:
    """A comparison cell: number with separators, text as is, None as ``missing_text``."""
    if value is None:
        return missing_text
    if isinstance(value, str):
        return value
    return f"{value:,.2f}"


def format_difference(value: Any) -> str:
    """Signed difference, 'same'/'different' as is, None as 'not computed'."""
    if value is None:
        return NOT_COMPUTED
    if isinstance(value, str):
        return value
    return f"{value:+,.2f}"


def panel_frame() -> QFrame:
    """Rounded panel in the style of the other tabs."""
    frame = QFrame()
    frame.setStyleSheet(f"""
        QFrame {{
            background-color: {Colors.SURFACE};
            border: 1px solid {Colors.BORDER};
            border-radius: 12px;
        }}
        QLabel {{ border: none; color: {Colors.TEXT_SECONDARY}; }}
        QDateEdit {{
            background-color: {Colors.INPUT_BACKGROUND};
            border: 1px solid {Colors.INPUT_BORDER};
            border-radius: 8px;
            padding: 6px 10px;
            color: {Colors.TEXT_PRIMARY};
        }}
        QDateEdit:disabled {{ color: {Colors.TEXT_DISABLED}; }}
    """)
    return frame


def choice_combo(choices: Iterable[Tuple[str, Any]]) -> QComboBox:
    """Combo box whose items carry (label, value) pairs."""
    combo = QComboBox()
    for label, value in choices:
        combo.addItem(label, value)
    return combo


def secondary_button(text: str) -> QPushButton:
    """Button in the secondary style used across tabs."""
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(f"""
        QPushButton {{
            background-color: {Colors.BUTTON_SECONDARY};
            color: {Colors.TEXT_PRIMARY};
            border: 1px solid {Colors.BORDER};
        }}
        QPushButton:hover {{ background-color: {Colors.BUTTON_SECONDARY_HOVER}; }}
        QPushButton:disabled {{ background-color: {Colors.SURFACE}; color: {Colors.TEXT_DISABLED}; }}
    """)
    return button


def read_only_table(column_labels: Sequence[str]) -> QTableWidget:
    """Read-only table in the style of the On Chain Analysis tab."""
    table = QTableWidget(0, len(column_labels))
    table.setHorizontalHeaderLabels(list(column_labels))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.setStyleSheet(f"""
        QTableWidget {{
            background-color: {Colors.SURFACE};
            color: {Colors.TEXT_PRIMARY};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            gridline-color: {Colors.BORDER};
        }}
        QHeaderView::section {{
            background-color: {Colors.BUTTON_SECONDARY};
            color: {Colors.TEXT_SECONDARY};
            padding: 4px;
            border: 1px solid {Colors.BORDER};
        }}
    """)
    return table
