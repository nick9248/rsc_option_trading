"""
Proof-of-concept: Dynamic strategy configuration widgets.

This demonstrates the widget system working without modifying the main GUI.
Run this to see:
1. Widgets change based on strategy selection
2. Strategy defaults load correctly
3. Configuration can be extracted from widgets
"""

import sys
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QFrame
)
from PySide6.QtCore import Qt

from coding.gui.components.strategy_config_widgets import create_config_widget
from coding.core.strategy import create_strategy
from coding.gui.theme.colors import Colors

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DynamicWidgetDemo(QMainWindow):
    """Demo window showing dynamic strategy config widgets."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dynamic Strategy Config Widget - Proof of Concept")
        self.setGeometry(100, 100, 900, 700)

        # Current widget
        self.current_config_widget = None

        self._setup_ui()

    def _setup_ui(self):
        """Set up the demo UI."""
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Dynamic Strategy Configuration Widget System")
        title.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Instructions
        instructions = QLabel(
            "Click a strategy button to see its specific configuration widget.\n"
            "Notice how the fields change based on strategy type."
        )
        instructions.setStyleSheet(f"font-size: 14px; color: {Colors.TEXT_SECONDARY};")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Strategy buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        strategies = ["Long Call", "Long Put", "Bull Call Spread"]
        self.strategy_buttons = {}

        for strategy_name in strategies:
            btn = QPushButton(strategy_name)
            btn.setCheckable(True)
            btn.setStyleSheet(self._get_button_style())
            btn.clicked.connect(lambda checked, name=strategy_name: self._on_strategy_selected(name))
            button_layout.addWidget(btn)
            self.strategy_buttons[strategy_name] = btn

        layout.addLayout(button_layout)

        # Config widget container
        container_frame = QFrame()
        container_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 2px solid {Colors.ACCENT};
                border-radius: 12px;
                padding: 16px;
            }}
        """)

        self.config_widget_layout = QVBoxLayout(container_frame)
        self.config_widget_layout.setContentsMargins(0, 0, 0, 0)

        placeholder = QLabel("← Select a strategy to see its configuration widget")
        placeholder.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 16px;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.config_widget_layout.addWidget(placeholder)

        layout.addWidget(container_frame, stretch=1)

        # Output section
        output_label = QLabel("Configuration Output:")
        output_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(output_label)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 8px;
                font-family: 'Courier New', monospace;
            }}
        """)
        layout.addWidget(self.output_text, stretch=1)

        # Get Config button
        get_config_btn = QPushButton("Get Current Configuration")
        get_config_btn.setStyleSheet(self._get_button_style())
        get_config_btn.clicked.connect(self._show_config)
        layout.addWidget(get_config_btn)

        # Apply dark theme
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {Colors.BACKGROUND_PRIMARY};
            }}
            QWidget {{
                background-color: {Colors.BACKGROUND_PRIMARY};
                color: {Colors.TEXT_PRIMARY};
            }}
        """)

    def _on_strategy_selected(self, strategy_name: str):
        """Handle strategy selection - swap config widget."""
        # Uncheck other buttons
        for name, btn in self.strategy_buttons.items():
            if name != strategy_name:
                btn.setChecked(False)

        # Remove old widget
        if self.current_config_widget:
            self.config_widget_layout.removeWidget(self.current_config_widget)
            self.current_config_widget.deleteLater()
            self.current_config_widget = None

        # Create new widget
        self.current_config_widget = create_config_widget(strategy_name)

        # Load strategy defaults
        try:
            temp_strategy = create_strategy(
                name=strategy_name,
                currency="BTC",
                expiration="31JAN25",
                underlying_price=100000.0
            )
            defaults = temp_strategy.get_default_config()
            self.current_config_widget.set_defaults(defaults)

            logger.info(f"Loaded defaults for {strategy_name}: {defaults}")
        except Exception as e:
            logger.error(f"Failed to load defaults: {e}")

        # Add to layout
        self.config_widget_layout.addWidget(self.current_config_widget)

        # Show initial config
        self._show_config()

    def _show_config(self):
        """Display current configuration from widget."""
        if not self.current_config_widget:
            self.output_text.setText("No strategy selected")
            return

        try:
            config = self.current_config_widget.get_config()

            output = f"Strategy: {self.current_config_widget.strategy_name}\n"
            output += f"Widget Type: {type(self.current_config_widget).__name__}\n"
            output += "\nConfiguration:\n"
            output += "-" * 50 + "\n"

            for key, value in config.items():
                output += f"{key}: {value}\n"

            # Show which fields are strategy-specific
            output += "\n" + "=" * 50 + "\n"
            output += "Strategy-Specific Fields:\n"
            output += "=" * 50 + "\n"

            if "Spread" in self.current_config_widget.strategy_name:
                output += "• mode: optimal vs manual selection\n"
                output += "• method: how strikes are selected\n"
                if config.get("mode") == "manual":
                    if config.get("method") == "by_delta":
                        output += "• long_target_delta: Delta for leg you BUY\n"
                        output += "• short_target_delta: Delta for leg you SELL\n"
                    elif config.get("method") == "by_moneyness":
                        output += "• long_moneyness_pct: % OTM for leg you BUY\n"
                        output += "• short_moneyness_pct: % OTM for leg you SELL\n"
                    elif config.get("method") == "by_strike":
                        output += "• long_specific_strike: Strike for leg you BUY\n"
                        output += "• short_specific_strike: Strike for leg you SELL\n"
            else:
                output += "• method: by_delta, by_moneyness, or by_strike\n"
                if config.get("method") == "by_delta":
                    output += "• target_delta: Single delta value\n"
                elif config.get("method") == "by_moneyness":
                    output += "• moneyness_pct: Single moneyness value\n"
                elif config.get("method") == "by_strike":
                    output += "• specific_strike: Single strike value\n"

            self.output_text.setText(output)

        except Exception as e:
            self.output_text.setText(f"Error getting config: {e}")
            logger.exception("Error getting config")

    def _get_button_style(self) -> str:
        """Get button styling."""
        return f"""
            QPushButton {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: 2px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 12px 24px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
            QPushButton:checked {{
                background-color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
        """


def main():
    """Run the demo."""
    app = QApplication(sys.argv)

    window = DynamicWidgetDemo()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
