"""
API Connection tab for testing and executing API endpoints.

Provides interface to select endpoints, configure parameters, and view results.
"""

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
    QSpacerItem,
    QSizePolicy,
    QLineEdit,
    QSpinBox,
    QCheckBox,
)
from PySide6.QtCore import Qt, QThread, Signal

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.theme.colors import Colors
from coding.core.endpoints.deribit_endpoints import DeribitEndpoints
from coding.service.deribit.deribit_api_service import DeribitApiService


logger = logging.getLogger(__name__)


class ApiWorker(QThread):
    """
    Worker thread for executing API calls without blocking the UI.

    Emits signals for progress updates and completion.
    """

    started = Signal()
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        endpoint_name: str,
        parameters: Dict[str, Any],
        parent: Optional[QWidget] = None
    ):
        """
        Initialize the worker.

        Args:
            endpoint_name: Name of the endpoint to call.
            parameters: Parameters for the API call.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.endpoint_name = endpoint_name
        self.parameters = parameters

    def run(self) -> None:
        """Execute the API call in background."""
        self.started.emit()

        try:
            with DeribitApiService() as service:
                method_map = {
                    "Test Connection": service.check_connectivity,
                    "Get Expirations": service.get_expirations,
                    "Get Instruments": service.get_instruments,
                    "Get Book Summary": service.get_book_summary,
                    "Get Ticker": service.get_ticker,
                    "Get Order Book": service.get_order_book,
                    "Get Funding Chart": service.get_funding_chart_data,
                    "Get Historical Volatility": service.get_historical_volatility,
                    "Get Volatility Index": service.get_volatility_index_data,
                }

                method = method_map.get(self.endpoint_name)
                if method:
                    if self.endpoint_name == "Test Connection":
                        result = method()
                    else:
                        result = method(**self.parameters)
                    self.finished.emit(result)
                else:
                    self.error.emit(f"Unknown endpoint: {self.endpoint_name}")

        except Exception as error:
            self.error.emit(str(error))


class ApiConnectionTab(QWidget):
    """
    Tab widget for API connection testing and execution.

    Provides:
    - Endpoint selection dropdown
    - Dynamic parameter inputs
    - Run button
    - Log viewer for results
    """

    ENDPOINTS = {
        "Test Connection": {
            "description": "Test API connectivity",
            "parameters": []
        },
        "Get Expirations": {
            "description": "Get available expiration dates",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC", "USDC", "USDT"], "default": "ETH"}
            ]
        },
        "Get Instruments": {
            "description": "Get trading instruments",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC", "USDC", "USDT", "any"], "default": "ETH"},
                {"name": "kind", "type": "dropdown", "options": ["option", "future", "spot", "future_combo", "option_combo"], "default": "option"}
            ]
        },
        "Get Book Summary": {
            "description": "Get order book summary",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC", "USDC", "USDT", "any"], "default": "ETH"},
                {"name": "kind", "type": "dropdown", "options": ["option", "future", "spot", "future_combo", "option_combo"], "default": "option"}
            ]
        },
        "Get Ticker": {
            "description": "Get ticker for an instrument",
            "parameters": [
                {"name": "instrument_name", "type": "text", "default": "ETH-PERPETUAL"}
            ]
        },
        "Get Order Book": {
            "description": "Get order book for an instrument",
            "parameters": [
                {"name": "instrument_name", "type": "text", "default": "ETH-PERPETUAL"},
                {"name": "depth", "type": "number", "default": 10, "min": 1, "max": 10000}
            ]
        },
        "Get Funding Chart": {
            "description": "Get funding rate data",
            "parameters": [
                {"name": "instrument_name", "type": "text", "default": "ETH-PERPETUAL"},
                {"name": "length", "type": "dropdown", "options": ["8h", "24h", "1m"], "default": "8h"}
            ]
        },
        "Get Historical Volatility": {
            "description": "Get historical volatility",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"}
            ]
        },
        "Get Volatility Index": {
            "description": "Get DVOL index data",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"},
                {"name": "resolution", "type": "number", "default": 3600, "min": 1, "max": 86400}
            ]
        },
    }

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the API Connection tab.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.parameter_widgets: Dict[str, QWidget] = {}
        self.worker: Optional[ApiWorker] = None

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)

        # Header
        header = QLabel("API Connection")
        header.setProperty("class", "heading")
        header.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        main_layout.addWidget(header)

        # Controls section
        controls_frame = QFrame()
        controls_frame.setProperty("class", "card")
        controls_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
                padding: 16px;
            }}
        """)
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(20, 20, 20, 20)
        controls_layout.setSpacing(16)

        # Endpoint selection row
        endpoint_row = QHBoxLayout()
        endpoint_row.setSpacing(16)

        endpoint_label = QLabel("Endpoint:")
        endpoint_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; min-width: 80px;")
        endpoint_row.addWidget(endpoint_label)

        self.endpoint_combo = QComboBox()
        self.endpoint_combo.addItems(list(self.ENDPOINTS.keys()))
        self.endpoint_combo.setMinimumWidth(250)
        endpoint_row.addWidget(self.endpoint_combo)

        endpoint_row.addStretch()
        controls_layout.addLayout(endpoint_row)

        # Parameters container
        self.parameters_frame = QFrame()
        self.parameters_layout = QVBoxLayout(self.parameters_frame)
        self.parameters_layout.setContentsMargins(0, 0, 0, 0)
        self.parameters_layout.setSpacing(12)
        controls_layout.addWidget(self.parameters_frame)

        # Save to CSV option
        csv_row = QHBoxLayout()
        csv_row.setSpacing(16)

        self.save_csv_checkbox = QCheckBox("Save to CSV")
        self.save_csv_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: {Colors.TEXT_SECONDARY};
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid {Colors.BORDER};
                background-color: {Colors.INPUT_BACKGROUND};
            }}
            QCheckBox::indicator:checked {{
                background-color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
            QCheckBox::indicator:hover {{
                border-color: {Colors.ACCENT};
            }}
        """)
        csv_row.addWidget(self.save_csv_checkbox)
        csv_row.addStretch()
        controls_layout.addLayout(csv_row)

        # Run button row
        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        self.run_button = QPushButton("Run")
        self.run_button.setMinimumWidth(120)
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self.run_button)

        self.clear_button = QPushButton("Clear Logs")
        self.clear_button.setProperty("class", "secondary")
        self.clear_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self.clear_button)

        button_row.addStretch()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        button_row.addWidget(self.status_label)

        controls_layout.addLayout(button_row)

        main_layout.addWidget(controls_frame)

        # Log viewer section
        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        main_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(300)
        main_layout.addWidget(self.log_viewer, 1)

        # Initial parameter setup
        self._update_parameters()

    def _setup_logging(self) -> None:
        """Set up logging to the GUI log viewer."""
        self.gui_handler = GuiLogHandler(self.log_viewer)
        self.gui_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)-8s] %(message)s", datefmt="%H:%M:%S")
        )

        root_logger = logging.getLogger()
        root_logger.addHandler(self.gui_handler)

    def _connect_signals(self) -> None:
        """Connect widget signals to slots."""
        self.endpoint_combo.currentTextChanged.connect(self._update_parameters)
        self.run_button.clicked.connect(self._run_endpoint)
        self.clear_button.clicked.connect(self.log_viewer.clear_logs)

    def _clear_layout(self, layout) -> None:
        """
        Recursively clear all items from a layout.

        Args:
            layout: Layout to clear.
        """
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
                item.layout().deleteLater()

    def _update_parameters(self) -> None:
        """Update parameter widgets based on selected endpoint."""
        # Clear existing parameter widgets
        self.parameter_widgets.clear()

        # Clear layout properly
        self._clear_layout(self.parameters_layout)

        # Get selected endpoint parameters
        endpoint_name = self.endpoint_combo.currentText()
        endpoint_config = self.ENDPOINTS.get(endpoint_name, {})
        parameters = endpoint_config.get("parameters", [])

        if not parameters:
            no_params_label = QLabel("No parameters required")
            no_params_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-style: italic;")
            self.parameters_layout.addWidget(no_params_label)
            return

        # Create parameter widgets
        for param in parameters:
            row = QHBoxLayout()
            row.setSpacing(16)

            label = QLabel(f"{param['name'].replace('_', ' ').title()}:")
            label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; min-width: 120px;")
            row.addWidget(label)

            if param["type"] == "dropdown":
                widget = QComboBox()
                widget.addItems(param["options"])
                if param.get("default"):
                    widget.setCurrentText(param["default"])
                widget.setMinimumWidth(200)

            elif param["type"] == "text":
                widget = QLineEdit()
                widget.setText(param.get("default", ""))
                widget.setMinimumWidth(200)

            elif param["type"] == "number":
                widget = QSpinBox()
                widget.setMinimum(param.get("min", 0))
                widget.setMaximum(param.get("max", 999999))
                widget.setValue(param.get("default", 0))
                widget.setMinimumWidth(200)

            else:
                widget = QLineEdit()
                widget.setText(str(param.get("default", "")))

            self.parameter_widgets[param["name"]] = widget
            row.addWidget(widget)
            row.addStretch()

            self.parameters_layout.addLayout(row)

    def _get_parameters(self) -> Dict[str, Any]:
        """
        Get current parameter values from widgets.

        Returns:
            Dictionary of parameter names and values.
        """
        parameters = {}
        endpoint_name = self.endpoint_combo.currentText()
        endpoint_config = self.ENDPOINTS.get(endpoint_name, {})
        param_configs = endpoint_config.get("parameters", [])

        for param_config in param_configs:
            name = param_config["name"]
            widget = self.parameter_widgets.get(name)

            if widget is None:
                continue

            if isinstance(widget, QComboBox):
                parameters[name] = widget.currentText()
            elif isinstance(widget, QSpinBox):
                parameters[name] = widget.value()
            elif isinstance(widget, QLineEdit):
                parameters[name] = widget.text()

        # Add save_to_csv option
        parameters["save_to_csv"] = self.save_csv_checkbox.isChecked()

        return parameters

    def _run_endpoint(self) -> None:
        """Execute the selected API endpoint."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A request is already in progress")
            return

        endpoint_name = self.endpoint_combo.currentText()
        parameters = self._get_parameters()

        self.log_viewer.log_info(f"Executing: {endpoint_name}")
        if parameters:
            self.log_viewer.log_info(f"Parameters: {parameters}")

        self.run_button.setEnabled(False)
        self.status_label.setText("Running...")
        self.status_label.setStyleSheet(f"color: {Colors.WARNING};")

        self.worker = ApiWorker(endpoint_name, parameters)
        self.worker.finished.connect(self._on_request_finished)
        self.worker.error.connect(self._on_request_error)
        self.worker.start()

    def _on_request_finished(self, result: Any) -> None:
        """
        Handle successful API response.

        Args:
            result: API response data.
        """
        self.run_button.setEnabled(True)
        self.status_label.setText("Success")
        self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")

        if isinstance(result, dict):
            for key, value in result.items():
                if isinstance(value, (list, dict)):
                    self.log_viewer.log_info(f"{key}: {len(value) if isinstance(value, list) else 'object'}")
                else:
                    self.log_viewer.log_info(f"{key}: {value}")
        elif isinstance(result, list):
            self.log_viewer.log_info(f"Received {len(result)} items")
            if result and len(result) > 0:
                self.log_viewer.log_info(f"First item keys: {list(result[0].keys()) if isinstance(result[0], dict) else 'N/A'}")
        else:
            self.log_viewer.log_info(f"Result: {result}")

        self.log_viewer.log_info("Request completed successfully")

    def _on_request_error(self, error_message: str) -> None:
        """
        Handle API error.

        Args:
            error_message: Error description.
        """
        self.run_button.setEnabled(True)
        self.status_label.setText("Error")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
        self.log_viewer.log_error(f"Request failed: {error_message}")
