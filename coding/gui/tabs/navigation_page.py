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
        self.setFixedHeight(130)
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
            self.name_label.setStyleSheet("")  # clear inline style; QSS #tileName rule takes over
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

    def __init__(
        self,
        module_defs: list[dict],
        failed_indices: set[int],
        parent=None,
    ):
        """
        Args:
            module_defs: List of dicts with keys: index, icon, name, subtitle.
                         Covers stack indices 1–9 (all active + placeholder modules).
            failed_indices: Stack indices that should be shown as disabled.
                            Caller is responsible for including both service-failed indices
                            and any permanent placeholder indices.
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

            is_disabled = defn["index"] in failed_indices
            if is_disabled:
                tile.set_disabled_style()
            else:
                tile.clicked.connect(self.module_selected)

            row, col = divmod(i, 3)
            grid.addWidget(tile, row, col)

        outer.addLayout(grid)
        outer.addStretch()
