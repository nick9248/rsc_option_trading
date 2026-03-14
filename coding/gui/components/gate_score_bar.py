# coding/gui/components/gate_score_bar.py
"""
GateScoreBar — reusable progress bar widget displaying a 0-100 score.

Color coding:
  score >= 60: SUCCESS (#2ECC71 green)
  score 40-59: WARNING (#F39C12 amber)
  score < 40:  ERROR (#E74C3C red)
"""
import logging
from PySide6.QtWidgets import QWidget, QHBoxLayout, QProgressBar, QLabel
from PySide6.QtCore import Qt
from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


class GateScoreBar(QWidget):
    """
    Horizontal score bar with color-coded progress and numeric label.

    Usage:
        bar = GateScoreBar(label="Gate 2")
        bar.set_score(72.0)
    """

    def __init__(self, label: str = "", parent=None) -> None:
        super().__init__(parent)
        self._setup_ui(label)

    def _setup_ui(self, label: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        if label:
            self._label = QLabel(label)
            self._label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
            self._label.setFixedWidth(60)
            layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        layout.addWidget(self._bar)

        self._score_label = QLabel("—")
        self._score_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: 12px; font-weight: 600;"
        )
        self._score_label.setFixedWidth(36)
        self._score_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._score_label)

    def set_score(self, score: float) -> None:
        """Update score (0-100) and apply color coding."""
        clamped = max(0.0, min(100.0, score))
        self._bar.setValue(int(clamped))
        self._score_label.setText(f"{clamped:.0f}")

        if clamped >= 60:
            color = Colors.SUCCESS
        elif clamped >= 40:
            color = Colors.WARNING
        else:
            color = Colors.ERROR

        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colors.BACKGROUND_TERTIARY};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

    def clear(self) -> None:
        """Reset to empty state."""
        self._bar.setValue(0)
        self._score_label.setText("—")
        self._bar.setStyleSheet("")
