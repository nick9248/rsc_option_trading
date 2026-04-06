"""
Forward Testing Tab.

Container for all forward testing experiment tiles.
Add new tiles here as more experiments are built.
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QScrollArea,
    QLabel,
)
from PySide6.QtGui import QFont

from coding.gui.theme.colors import Colors
from coding.gui.forward_testing.vol_regressor_tile import VolRegressorTile

logger = logging.getLogger(__name__)


class ForwardTestingTab(QWidget):
    """Tab hosting all forward testing tiles."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        # Header
        header = QLabel("Forward Testing")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        header.setFont(font)
        header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        outer.addWidget(header)

        # Scrollable tile area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_PRIMARY}; "
            f"border: none;"
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # Tile 1: Vol Regressor
        content_layout.addWidget(VolRegressorTile())
        content_layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
