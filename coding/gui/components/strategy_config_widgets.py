"""
Strategy-specific configuration widgets for dynamic GUI.

Each strategy type has its own configuration widget that shows
the appropriate fields. This allows single-leg strategies to have
one delta field while spreads have dual delta fields, etc.

Architecture:
- StrategyConfigWidget: Abstract base class
- SingleLegConfigWidget: For Long Call/Put (single delta/moneyness/strike)
- SpreadConfigWidget: For Bull Call Spread (method selector + dual fields)
- Factory function: create_config_widget(strategy_name) → widget instance

Future strategies (Iron Condor, Butterfly) can have their own widgets.
"""

import logging
from typing import Dict, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QComboBox, QDoubleSpinBox, QPushButton, QCheckBox, QSizePolicy, QMessageBox
)
from PySide6.QtCore import Signal, Qt

from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


class StrategyConfigWidget(QWidget):
    """
    Base class for strategy-specific configuration widgets.

    Each strategy type (single-leg, spread, complex) has its own widget
    that shows the appropriate configuration fields.

    Signals:
        config_changed: Emitted when configuration changes
    """

    config_changed = Signal()

    def __init__(self, strategy_name: str, parent: Optional[QWidget] = None):
        """
        Initialize strategy config widget.

        Args:
            strategy_name: Name of the strategy this widget configures
            parent: Parent widget
        """
        super().__init__(parent)
        self.strategy_name = strategy_name
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the UI widgets. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement _setup_ui")

    def get_config(self) -> Dict:
        """
        Get current configuration from widget state.

        Returns:
            Dictionary with configuration parameters for this strategy
        """
        raise NotImplementedError("Subclasses must implement get_config")

    def set_defaults(self, defaults: Dict) -> None:
        """
        Set widget values from strategy defaults.

        Args:
            defaults: Dictionary from strategy.get_default_config()
        """
        raise NotImplementedError("Subclasses must implement set_defaults")

    def _get_combo_style(self) -> str:
        """Get consistent combo box styling."""
        return f"""
            QComboBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 8px;
                min-height: 20px;
            }}
            QComboBox:hover {{
                border-color: {Colors.ACCENT};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                selection-background-color: {Colors.ACCENT};
            }}
        """

    def _get_spin_style(self) -> str:
        """Get consistent spin box styling."""
        return f"""
            QDoubleSpinBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 8px;
                min-height: 20px;
            }}
            QDoubleSpinBox:hover {{
                border-color: {Colors.ACCENT};
            }}
        """

    def _create_info_button(self, title: str, description: str) -> QPushButton:
        """Create info button matching existing GUI style."""
        btn = QPushButton("Info")
        btn.setFixedSize(60, 30)
        btn.setToolTip(title)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.SURFACE};
                color: {Colors.ACCENT};
                border: 2px solid {Colors.ACCENT};
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
            }}
            QPushButton:pressed {{
                background-color: {Colors.ACCENT_MUTED};
            }}
        """)
        btn.clicked.connect(lambda: self._show_info_dialog(title, description))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _show_info_dialog(self, title: str, message: str) -> None:
        """Show info dialog with detailed explanation."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setStyleSheet(f"""
            QMessageBox {{
                background-color: {Colors.SURFACE};
            }}
            QLabel {{
                color: {Colors.TEXT_PRIMARY};
            }}
        """)
        msg_box.exec()


class SingleLegConfigWidget(StrategyConfigWidget):
    """
    Configuration widget for single-leg strategies (Long Call, Long Put).

    Shows:
    - Strike selection method (By Delta, By Moneyness, By Strike)
    - Single delta/moneyness/strike field (changes based on method)
    """

    def _setup_ui(self) -> None:
        """Set up single-leg configuration UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Strike selection method
        method_grid = QGridLayout()
        method_grid.setSpacing(8)

        method_label = QLabel("Strike Selection:")
        method_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        method_label.setMinimumWidth(120)

        self.method_combo = QComboBox()
        self.method_combo.addItems(["By Delta", "By Moneyness (%)", "By Specific Strike"])
        self.method_combo.setStyleSheet(self._get_combo_style())
        self.method_combo.setMinimumWidth(100)
        self.method_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.method_combo.currentTextChanged.connect(self._on_method_changed)
        self.method_combo.currentTextChanged.connect(lambda: self.config_changed.emit())

        method_grid.addWidget(method_label, 0, 0)
        method_grid.addWidget(self.method_combo, 0, 1)
        method_grid.setColumnStretch(1, 1)

        layout.addLayout(method_grid)

        # Delta field
        delta_grid = QGridLayout()
        delta_grid.setSpacing(8)

        delta_label = QLabel("Target Delta:")
        delta_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        delta_label.setMinimumWidth(120)

        self.delta_spin = QDoubleSpinBox()
        self.delta_spin.setRange(0.01, 0.99)
        self.delta_spin.setValue(0.30)
        self.delta_spin.setSingleStep(0.05)
        self.delta_spin.setDecimals(2)
        self.delta_spin.setMinimumWidth(100)
        self.delta_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delta_spin.setStyleSheet(self._get_spin_style())
        self.delta_spin.valueChanged.connect(lambda: self.config_changed.emit())

        delta_info = self._create_info_button(
            "Target Delta",
            "Option's sensitivity to price changes.<br>"
            "0.30 = 30% probability of expiring ITM<br>"
            "Higher delta = closer to ATM, more expensive"
        )

        delta_grid.addWidget(delta_label, 0, 0)
        delta_grid.addWidget(self.delta_spin, 0, 1)
        delta_grid.addWidget(delta_info, 0, 2)
        delta_grid.setColumnStretch(1, 1)

        layout.addLayout(delta_grid)
        self.delta_layout_widgets = (delta_label, self.delta_spin, delta_info)

        # Moneyness field
        moneyness_grid = QGridLayout()
        moneyness_grid.setSpacing(8)

        moneyness_label = QLabel("Moneyness %:")
        moneyness_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        moneyness_label.setMinimumWidth(120)

        self.moneyness_spin = QDoubleSpinBox()
        self.moneyness_spin.setRange(-50.0, 50.0)
        self.moneyness_spin.setValue(5.0)
        self.moneyness_spin.setSingleStep(1.0)
        self.moneyness_spin.setDecimals(1)
        self.moneyness_spin.setMinimumWidth(100)
        self.moneyness_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.moneyness_spin.setStyleSheet(self._get_spin_style())
        self.moneyness_spin.valueChanged.connect(lambda: self.config_changed.emit())

        moneyness_info = self._create_info_button(
            "Moneyness",
            "Distance from current price.<br>"
            "Call: +5% = 5% above current (OTM)<br>"
            "Put: -5% = 5% below current (OTM)"
        )

        moneyness_grid.addWidget(moneyness_label, 0, 0)
        moneyness_grid.addWidget(self.moneyness_spin, 0, 1)
        moneyness_grid.addWidget(moneyness_info, 0, 2)
        moneyness_grid.setColumnStretch(1, 1)

        layout.addLayout(moneyness_grid)
        self.moneyness_layout_widgets = (moneyness_label, self.moneyness_spin, moneyness_info)
        for widget in self.moneyness_layout_widgets:
            widget.hide()

        # Specific strike field
        strike_grid = QGridLayout()
        strike_grid.setSpacing(8)

        strike_label = QLabel("Specific Strike:")
        strike_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        strike_label.setMinimumWidth(120)

        self.strike_spin = QDoubleSpinBox()
        self.strike_spin.setRange(0.0, 1000000.0)
        self.strike_spin.setValue(50000.0)
        self.strike_spin.setSingleStep(100.0)
        self.strike_spin.setDecimals(2)
        self.strike_spin.setMinimumWidth(100)
        self.strike_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.strike_spin.setStyleSheet(self._get_spin_style())
        self.strike_spin.valueChanged.connect(lambda: self.config_changed.emit())

        strike_info = self._create_info_button(
            "Specific Strike",
            "Exact strike price to select.<br>"
            "Will find closest available strike."
        )

        strike_grid.addWidget(strike_label, 0, 0)
        strike_grid.addWidget(self.strike_spin, 0, 1)
        strike_grid.addWidget(strike_info, 0, 2)
        strike_grid.setColumnStretch(1, 1)

        layout.addLayout(strike_grid)
        self.strike_layout_widgets = (strike_label, self.strike_spin, strike_info)
        for widget in self.strike_layout_widgets:
            widget.hide()

    def _on_method_changed(self, method: str) -> None:
        """Handle strike selection method change."""
        # Hide all method-specific fields
        for widget in self.delta_layout_widgets:
            widget.hide()
        for widget in self.moneyness_layout_widgets:
            widget.hide()
        for widget in self.strike_layout_widgets:
            widget.hide()

        # Show appropriate fields
        if method == "By Delta":
            for widget in self.delta_layout_widgets:
                widget.show()
        elif method == "By Moneyness (%)":
            for widget in self.moneyness_layout_widgets:
                widget.show()
        elif method == "By Specific Strike":
            for widget in self.strike_layout_widgets:
                widget.show()

    def get_config(self) -> Dict:
        """Get current configuration."""
        method = self.method_combo.currentText()

        if method == "By Delta":
            return {
                "method": "by_delta",
                "target_delta": self.delta_spin.value()
            }
        elif method == "By Moneyness (%)":
            return {
                "method": "by_moneyness",
                "moneyness_pct": self.moneyness_spin.value()
            }
        else:  # By Specific Strike
            return {
                "method": "by_strike",
                "specific_strike": self.strike_spin.value()
            }

    def set_defaults(self, defaults: Dict) -> None:
        """Set widget values from defaults."""
        if "target_delta" in defaults:
            self.delta_spin.setValue(defaults["target_delta"])
        if "moneyness_pct" in defaults:
            self.moneyness_spin.setValue(defaults["moneyness_pct"])


class SpreadConfigWidget(StrategyConfigWidget):
    """
    Configuration widget for spread strategies (Bull Call Spread, etc.).

    Shows:
    - Strike selection mode: Optimal (Skew-Aware) vs Manual
    - If Manual:
      - Method selector (By Delta, By Moneyness, By Strike)
      - Dual fields for long/short legs
    - If Optimal:
      - Uses strategy defaults (no additional fields)
    """

    def _setup_ui(self) -> None:
        """Set up spread configuration UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Strike selection mode (Optimal vs Manual)
        mode_grid = QGridLayout()
        mode_grid.setSpacing(8)

        mode_label = QLabel("Strike Selection:")
        mode_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        mode_label.setMinimumWidth(120)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Optimal (Skew-Aware)", "Manual"])
        self.mode_combo.setStyleSheet(self._get_combo_style())
        self.mode_combo.setMinimumWidth(100)
        self.mode_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.mode_combo.currentTextChanged.connect(lambda: self.config_changed.emit())

        mode_info = self._create_info_button(
            "Strike Selection Mode",
            "<b>Optimal (Skew-Aware):</b> Algorithm scans volatility surface to find "
            "best profit/debit ratio. Professional approach.<br><br>"
            "<b>Manual:</b> You specify exact strikes or target deltas/moneyness."
        )

        mode_grid.addWidget(mode_label, 0, 0)
        mode_grid.addWidget(self.mode_combo, 0, 1)
        mode_grid.addWidget(mode_info, 0, 2)
        mode_grid.setColumnStretch(1, 1)

        layout.addLayout(mode_grid)

        # Top N field (for Optimal mode - controls number of spread variations)
        self.top_n_section = QWidget()
        top_n_layout = QGridLayout(self.top_n_section)
        top_n_layout.setContentsMargins(0, 0, 0, 0)
        top_n_layout.setSpacing(8)

        top_n_label = QLabel("Variations to Show:")
        top_n_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        top_n_label.setMinimumWidth(120)

        self.top_n_spin = QSpinBox()
        self.top_n_spin.setRange(1, 10)
        self.top_n_spin.setValue(5)
        self.top_n_spin.setSingleStep(1)
        self.top_n_spin.setMinimumWidth(100)
        self.top_n_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.top_n_spin.setStyleSheet(self._get_spin_style())
        self.top_n_spin.valueChanged.connect(lambda: self.config_changed.emit())

        top_n_info = self._create_info_button(
            "Number of Variations",
            "How many different spread combinations to show.<br><br>"
            "<b>1:</b> Show only the best spread<br>"
            "<b>5 (default):</b> Show top 5 spreads with different risk/reward profiles<br>"
            "<b>10:</b> Show all top variations for maximum choice<br><br>"
            "Each variation gets independently scored and ranked."
        )

        top_n_layout.addWidget(top_n_label, 0, 0)
        top_n_layout.addWidget(self.top_n_spin, 0, 1)
        top_n_layout.addWidget(top_n_info, 0, 2)
        top_n_layout.setColumnStretch(1, 1)

        layout.addWidget(self.top_n_section)

        # Manual configuration section (hidden by default)
        self.manual_section = QWidget()
        manual_layout = QVBoxLayout(self.manual_section)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(12)

        # Manual method selector
        manual_method_grid = QGridLayout()
        manual_method_grid.setSpacing(8)

        manual_method_label = QLabel("Manual Method:")
        manual_method_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        manual_method_label.setMinimumWidth(120)

        self.manual_method_combo = QComboBox()
        self.manual_method_combo.addItems(["By Delta", "By Moneyness (%)", "By Specific Strike"])
        self.manual_method_combo.setStyleSheet(self._get_combo_style())
        self.manual_method_combo.setMinimumWidth(100)
        self.manual_method_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.manual_method_combo.currentTextChanged.connect(self._on_manual_method_changed)
        self.manual_method_combo.currentTextChanged.connect(lambda: self.config_changed.emit())

        manual_method_grid.addWidget(manual_method_label, 0, 0)
        manual_method_grid.addWidget(self.manual_method_combo, 0, 1)
        manual_method_grid.setColumnStretch(1, 1)

        manual_layout.addLayout(manual_method_grid)

        # Delta fields (for "By Delta" method)
        self.delta_section = QWidget()
        delta_layout = QVBoxLayout(self.delta_section)
        delta_layout.setContentsMargins(0, 0, 0, 0)
        delta_layout.setSpacing(8)

        # Long leg delta
        long_delta_grid = QGridLayout()
        long_delta_grid.setSpacing(8)

        long_delta_label = QLabel("Long Leg Delta:")
        long_delta_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        long_delta_label.setMinimumWidth(120)

        self.long_delta_spin = QDoubleSpinBox()
        self.long_delta_spin.setRange(0.01, 0.99)
        self.long_delta_spin.setValue(0.45)
        self.long_delta_spin.setSingleStep(0.05)
        self.long_delta_spin.setDecimals(2)
        self.long_delta_spin.setMinimumWidth(100)
        self.long_delta_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.long_delta_spin.setStyleSheet(self._get_spin_style())
        self.long_delta_spin.valueChanged.connect(lambda: self.config_changed.emit())

        long_delta_info = self._create_info_button(
            "Long Leg Delta",
            "Delta for the leg you BUY.<br>"
            "Bull Call Spread: Buy lower strike call<br>"
            "Higher delta = closer to ATM"
        )

        long_delta_grid.addWidget(long_delta_label, 0, 0)
        long_delta_grid.addWidget(self.long_delta_spin, 0, 1)
        long_delta_grid.addWidget(long_delta_info, 0, 2)
        long_delta_grid.setColumnStretch(1, 1)

        delta_layout.addLayout(long_delta_grid)

        # Short leg delta
        short_delta_grid = QGridLayout()
        short_delta_grid.setSpacing(8)

        short_delta_label = QLabel("Short Leg Delta:")
        short_delta_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        short_delta_label.setMinimumWidth(120)

        self.short_delta_spin = QDoubleSpinBox()
        self.short_delta_spin.setRange(0.01, 0.99)
        self.short_delta_spin.setValue(0.25)
        self.short_delta_spin.setSingleStep(0.05)
        self.short_delta_spin.setDecimals(2)
        self.short_delta_spin.setMinimumWidth(100)
        self.short_delta_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.short_delta_spin.setStyleSheet(self._get_spin_style())
        self.short_delta_spin.valueChanged.connect(lambda: self.config_changed.emit())

        short_delta_info = self._create_info_button(
            "Short Leg Delta",
            "Delta for the leg you SELL.<br>"
            "Bull Call Spread: Sell higher strike call<br>"
            "Lower delta = further OTM"
        )

        short_delta_grid.addWidget(short_delta_label, 0, 0)
        short_delta_grid.addWidget(self.short_delta_spin, 0, 1)
        short_delta_grid.addWidget(short_delta_info, 0, 2)
        short_delta_grid.setColumnStretch(1, 1)

        delta_layout.addLayout(short_delta_grid)

        manual_layout.addWidget(self.delta_section)

        # Moneyness fields (for "By Moneyness" method)
        self.moneyness_section = QWidget()
        moneyness_layout = QVBoxLayout(self.moneyness_section)
        moneyness_layout.setContentsMargins(0, 0, 0, 0)
        moneyness_layout.setSpacing(8)

        # Long leg moneyness
        long_mon_grid = QGridLayout()
        long_mon_grid.setSpacing(8)

        long_mon_label = QLabel("Long Leg Moneyness %:")
        long_mon_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        long_mon_label.setMinimumWidth(120)

        self.long_moneyness_spin = QDoubleSpinBox()
        self.long_moneyness_spin.setRange(-50.0, 50.0)
        self.long_moneyness_spin.setValue(10.0)
        self.long_moneyness_spin.setSingleStep(1.0)
        self.long_moneyness_spin.setDecimals(1)
        self.long_moneyness_spin.setMinimumWidth(100)
        self.long_moneyness_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.long_moneyness_spin.setStyleSheet(self._get_spin_style())
        self.long_moneyness_spin.valueChanged.connect(lambda: self.config_changed.emit())

        long_mon_info = self._create_info_button(
            "Long Leg Moneyness",
            "% distance from current price for leg you BUY.<br>"
            "Bull Call Spread: +10% = 10% above current"
        )

        long_mon_grid.addWidget(long_mon_label, 0, 0)
        long_mon_grid.addWidget(self.long_moneyness_spin, 0, 1)
        long_mon_grid.addWidget(long_mon_info, 0, 2)
        long_mon_grid.setColumnStretch(1, 1)

        moneyness_layout.addLayout(long_mon_grid)

        # Short leg moneyness
        short_mon_grid = QGridLayout()
        short_mon_grid.setSpacing(8)

        short_mon_label = QLabel("Short Leg Moneyness %:")
        short_mon_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        short_mon_label.setMinimumWidth(120)

        self.short_moneyness_spin = QDoubleSpinBox()
        self.short_moneyness_spin.setRange(-50.0, 50.0)
        self.short_moneyness_spin.setValue(20.0)
        self.short_moneyness_spin.setSingleStep(1.0)
        self.short_moneyness_spin.setDecimals(1)
        self.short_moneyness_spin.setMinimumWidth(100)
        self.short_moneyness_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.short_moneyness_spin.setStyleSheet(self._get_spin_style())
        self.short_moneyness_spin.valueChanged.connect(lambda: self.config_changed.emit())

        short_mon_info = self._create_info_button(
            "Short Leg Moneyness",
            "% distance from current price for leg you SELL.<br>"
            "Bull Call Spread: +20% = 20% above current"
        )

        short_mon_grid.addWidget(short_mon_label, 0, 0)
        short_mon_grid.addWidget(self.short_moneyness_spin, 0, 1)
        short_mon_grid.addWidget(short_mon_info, 0, 2)
        short_mon_grid.setColumnStretch(1, 1)

        moneyness_layout.addLayout(short_mon_grid)

        manual_layout.addWidget(self.moneyness_section)
        self.moneyness_section.hide()

        # Strike fields (for "By Specific Strike" method)
        self.strike_section = QWidget()
        strike_layout = QVBoxLayout(self.strike_section)
        strike_layout.setContentsMargins(0, 0, 0, 0)
        strike_layout.setSpacing(8)

        # Long leg strike
        long_strike_grid = QGridLayout()
        long_strike_grid.setSpacing(8)

        long_strike_label = QLabel("Long Leg Strike:")
        long_strike_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        long_strike_label.setMinimumWidth(120)

        self.long_strike_spin = QDoubleSpinBox()
        self.long_strike_spin.setRange(0.0, 1000000.0)
        self.long_strike_spin.setValue(50000.0)
        self.long_strike_spin.setSingleStep(100.0)
        self.long_strike_spin.setDecimals(2)
        self.long_strike_spin.setMinimumWidth(100)
        self.long_strike_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.long_strike_spin.setStyleSheet(self._get_spin_style())
        self.long_strike_spin.valueChanged.connect(lambda: self.config_changed.emit())

        long_strike_info = self._create_info_button(
            "Long Leg Strike",
            "Exact strike for leg you BUY.<br>"
            "Bull Call Spread: Lower strike"
        )

        long_strike_grid.addWidget(long_strike_label, 0, 0)
        long_strike_grid.addWidget(self.long_strike_spin, 0, 1)
        long_strike_grid.addWidget(long_strike_info, 0, 2)
        long_strike_grid.setColumnStretch(1, 1)

        strike_layout.addLayout(long_strike_grid)

        # Short leg strike
        short_strike_grid = QGridLayout()
        short_strike_grid.setSpacing(8)

        short_strike_label = QLabel("Short Leg Strike:")
        short_strike_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        short_strike_label.setMinimumWidth(120)

        self.short_strike_spin = QDoubleSpinBox()
        self.short_strike_spin.setRange(0.0, 1000000.0)
        self.short_strike_spin.setValue(55000.0)
        self.short_strike_spin.setSingleStep(100.0)
        self.short_strike_spin.setDecimals(2)
        self.short_strike_spin.setMinimumWidth(100)
        self.short_strike_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.short_strike_spin.setStyleSheet(self._get_spin_style())
        self.short_strike_spin.valueChanged.connect(lambda: self.config_changed.emit())

        short_strike_info = self._create_info_button(
            "Short Leg Strike",
            "Exact strike for leg you SELL.<br>"
            "Bull Call Spread: Higher strike"
        )

        short_strike_grid.addWidget(short_strike_label, 0, 0)
        short_strike_grid.addWidget(self.short_strike_spin, 0, 1)
        short_strike_grid.addWidget(short_strike_info, 0, 2)
        short_strike_grid.setColumnStretch(1, 1)

        strike_layout.addLayout(short_strike_grid)

        manual_layout.addWidget(self.strike_section)
        self.strike_section.hide()

        layout.addWidget(self.manual_section)
        self.manual_section.hide()  # Hidden by default (Optimal mode)

    def _on_mode_changed(self, mode: str) -> None:
        """Handle strike selection mode change."""
        if mode == "Optimal (Skew-Aware)":
            self.manual_section.hide()
            self.top_n_section.show()  # Show top_n for optimal mode
        else:  # Manual
            self.manual_section.show()
            self.top_n_section.hide()  # Hide top_n for manual mode
            # Trigger update to show correct method fields
            self._on_manual_method_changed(self.manual_method_combo.currentText())

    def _on_manual_method_changed(self, method: str) -> None:
        """Handle manual method change."""
        # Hide all method sections
        self.delta_section.hide()
        self.moneyness_section.hide()
        self.strike_section.hide()

        # Show appropriate section
        if method == "By Delta":
            self.delta_section.show()
        elif method == "By Moneyness (%)":
            self.moneyness_section.show()
        elif method == "By Specific Strike":
            self.strike_section.show()

    def get_config(self) -> Dict:
        """Get current configuration."""
        mode = self.mode_combo.currentText()

        if mode == "Optimal (Skew-Aware)":
            return {
                "mode": "optimal",
                "method": "skew_aware",
                "return_top_n": self.top_n_spin.value()
            }
        else:  # Manual
            manual_method = self.manual_method_combo.currentText()

            if manual_method == "By Delta":
                return {
                    "mode": "manual",
                    "method": "by_delta",
                    "long_target_delta": self.long_delta_spin.value(),
                    "short_target_delta": self.short_delta_spin.value()
                }
            elif manual_method == "By Moneyness (%)":
                return {
                    "mode": "manual",
                    "method": "by_moneyness",
                    "long_moneyness_pct": self.long_moneyness_spin.value(),
                    "short_moneyness_pct": self.short_moneyness_spin.value()
                }
            else:  # By Specific Strike
                return {
                    "mode": "manual",
                    "method": "by_strike",
                    "long_specific_strike": self.long_strike_spin.value(),
                    "short_specific_strike": self.short_strike_spin.value()
                }

    def set_defaults(self, defaults: Dict) -> None:
        """Set widget values from defaults."""
        if "long_target_delta" in defaults:
            self.long_delta_spin.setValue(defaults["long_target_delta"])
        if "short_target_delta" in defaults:
            self.short_delta_spin.setValue(defaults["short_target_delta"])
        if "long_moneyness_pct" in defaults:
            self.long_moneyness_spin.setValue(defaults["long_moneyness_pct"])
        if "short_moneyness_pct" in defaults:
            self.short_moneyness_spin.setValue(defaults["short_moneyness_pct"])


def create_config_widget(strategy_name: str, parent: Optional[QWidget] = None) -> StrategyConfigWidget:
    """
    Factory function to create appropriate config widget for strategy.

    Args:
        strategy_name: Name of the strategy
        parent: Parent widget

    Returns:
        Appropriate StrategyConfigWidget subclass instance

    Examples:
        >>> widget = create_config_widget("Long Call")
        >>> isinstance(widget, SingleLegConfigWidget)
        True

        >>> widget = create_config_widget("Bull Call Spread")
        >>> isinstance(widget, SpreadConfigWidget)
        True
    """
    # Spread strategies
    if "Spread" in strategy_name:
        return SpreadConfigWidget(strategy_name, parent)

    # Single-leg strategies (Long Call, Long Put)
    else:
        return SingleLegConfigWidget(strategy_name, parent)
