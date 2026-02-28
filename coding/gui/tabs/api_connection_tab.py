"""
API Connection tab for testing and executing API endpoints.

Provides interface to select endpoints, configure parameters, and view results.
"""

import logging
import time as time_module
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
from coding.core.api.external_apis import ExternalMetricsFetcher


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
                    "Get Last Trades": service.get_last_trades_by_currency,
                    "Get Last Trades By Time": service.get_last_trades_by_currency_and_time,
                    "Get TradingView Chart": service.get_tradingview_chart_data,
                }

                method = method_map.get(self.endpoint_name)
                if method:
                    if self.endpoint_name == "Test Connection":
                        result = method()
                    else:
                        params = dict(self.parameters)
                        if self.endpoint_name == "Get Last Trades By Time":
                            params["start_timestamp"] = int(params["start_timestamp"])
                            params["end_timestamp"] = int(params["end_timestamp"])
                        result = method(**params)
                    self.finished.emit(result)
                else:
                    self.error.emit(f"Unknown endpoint: {self.endpoint_name}")

        except Exception as error:
            self.error.emit(str(error))


class InstrumentLoaderWorker(QThread):
    """Worker thread for loading instruments."""

    finished = Signal(list)  # Returns list of instrument dicts
    error = Signal(str)

    def __init__(self, currency: str, kind: str, parent: Optional[QWidget] = None):
        """
        Initialize the worker.

        Args:
            currency: Currency symbol (ETH, BTC).
            kind: Instrument kind (option, future, etc.).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currency = currency
        self.kind = kind

    def run(self) -> None:
        """Load instruments from API."""
        try:
            with DeribitApiService() as service:
                instruments = service.get_instruments(currency=self.currency, kind=self.kind)
                self.finished.emit(instruments)

        except Exception as error:
            logger.exception("Error loading instruments")
            self.error.emit(str(error))


class ExternalApiWorker(QThread):
    """Worker thread for executing external API calls (Fear & Greed, CoinGecko)."""

    finished = Signal(object)
    error = Signal(str)

    EXTERNAL_ENDPOINTS = {"Fear & Greed Index", "CoinGecko Market Data"}

    def __init__(self, endpoint_name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.endpoint_name = endpoint_name

    def run(self) -> None:
        """Execute the external API call in background."""
        try:
            fetcher = ExternalMetricsFetcher()
            if self.endpoint_name == "Fear & Greed Index":
                result = fetcher.fear_greed.get_latest()
            elif self.endpoint_name == "CoinGecko Market Data":
                result = fetcher.coingecko.get_global_market_data()
            else:
                self.error.emit(f"Unknown external endpoint: {self.endpoint_name}")
                return
            self.finished.emit(result)
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
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"},
                {"name": "kind", "type": "dropdown", "options": ["option", "future", "spot"], "default": "option"},
                {"name": "instrument_name", "type": "instrument_selector", "default": ""}
            ]
        },
        "Get Order Book": {
            "description": "Get order book for an instrument",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"},
                {"name": "kind", "type": "dropdown", "options": ["option", "future", "spot"], "default": "option"},
                {"name": "instrument_name", "type": "instrument_selector", "default": ""},
                {"name": "depth", "type": "number", "default": 10, "min": 1, "max": 10000}
            ]
        },
        "Get Funding Chart": {
            "description": "Get funding rate data (perpetuals only)",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"},
                {"name": "instrument_name", "type": "perpetual_selector", "default": ""},
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
        "Get Last Trades": {
            "description": "Get recent trades by currency",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"},
                {"name": "kind", "type": "dropdown", "options": ["option", "future", "spot"], "default": "option"},
                {"name": "count", "type": "number", "default": 100, "min": 1, "max": 1000}
            ]
        },
        "Get Last Trades By Time": {
            "description": "Get historical trades within a time range",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "ETH"},
                {"name": "kind", "type": "dropdown", "options": ["option", "future", "spot"], "default": "option"},
                {"name": "start_timestamp", "type": "timestamp", "default": "now-1h"},
                {"name": "end_timestamp", "type": "timestamp", "default": "now"},
                {"name": "count", "type": "number", "default": 100, "min": 1, "max": 1000}
            ]
        },
        "Get TradingView Chart": {
            "description": "Get historical OHLCV data (TradingView format)",
            "parameters": [
                {"name": "currency", "type": "dropdown", "options": ["ETH", "BTC"], "default": "BTC"},
                {"name": "instrument_name", "type": "perpetual_selector", "default": ""},
                {"name": "resolution", "type": "dropdown", "options": ["1", "3", "5", "10", "15", "30", "60", "120", "180", "360", "720", "1D"], "default": "1D"}
            ]
        },
        "Fear & Greed Index": {
            "description": "Get latest Fear & Greed Index (Alternative.me)",
            "parameters": []
        },
        "CoinGecko Market Data": {
            "description": "Get global crypto market data - BTC/ETH dominance (CoinGecko)",
            "parameters": []
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
        self.external_worker: Optional[ExternalApiWorker] = None

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header
        header = QLabel("API Connection")
        header.setProperty("class", "heading")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(header)

        # Controls section
        controls_frame = QFrame()
        controls_frame.setProperty("class", "card")
        controls_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        controls_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(12)

        # Endpoint selection row
        endpoint_row = QHBoxLayout()
        endpoint_row.setSpacing(12)

        endpoint_label = QLabel("Endpoint:")
        endpoint_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        endpoint_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        endpoint_row.addWidget(endpoint_label)

        self.endpoint_combo = QComboBox()
        self.endpoint_combo.addItems(list(self.ENDPOINTS.keys()))
        self.endpoint_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        endpoint_row.addWidget(self.endpoint_combo)

        controls_layout.addLayout(endpoint_row)

        # Parameters container
        self.parameters_frame = QFrame()
        self.parameters_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.parameters_layout = QVBoxLayout(self.parameters_frame)
        self.parameters_layout.setContentsMargins(0, 0, 0, 0)
        self.parameters_layout.setSpacing(8)
        controls_layout.addWidget(self.parameters_frame)

        # Save to CSV option
        csv_row = QHBoxLayout()
        csv_row.setSpacing(12)

        self.save_csv_checkbox = QCheckBox("Save to CSV")
        self.save_csv_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: {Colors.TEXT_SECONDARY};
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
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
        button_row.setSpacing(8)

        self.run_button = QPushButton("Run")
        self.run_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
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
        self.clear_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        button_row.addWidget(self.clear_button)

        button_row.addStretch()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        button_row.addWidget(self.status_label)

        controls_layout.addLayout(button_row)

        main_layout.addWidget(controls_frame)

        # Log viewer section
        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        log_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(100)
        self.log_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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
            row.setSpacing(12)

            label = QLabel(f"{param['name'].replace('_', ' ').title()}:")
            label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            row.addWidget(label)

            if param["type"] == "dropdown":
                widget = QComboBox()
                widget.addItems(param["options"])
                if param.get("default"):
                    widget.setCurrentText(param["default"])
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            elif param["type"] == "text":
                widget = QLineEdit()
                widget.setText(param.get("default", ""))
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            elif param["type"] == "number":
                widget = QSpinBox()
                widget.setMinimum(param.get("min", 0))
                widget.setMaximum(param.get("max", 999999))
                widget.setValue(param.get("default", 0))
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            elif param["type"] == "timestamp":
                widget = QLineEdit()
                default = param.get("default", "now")
                now_ms = int(time_module.time() * 1000)
                if default == "now":
                    widget.setText(str(now_ms))
                elif default == "now-1h":
                    widget.setText(str(now_ms - 3_600_000))
                else:
                    widget.setText(str(default))
                widget.setPlaceholderText("Timestamp in milliseconds")
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            elif param["type"] == "instrument_selector":
                widget = QComboBox()
                widget.setPlaceholderText("Click 'Load Instruments' first")
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

                load_btn = QPushButton("Load")
                load_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {Colors.BUTTON_SECONDARY};
                        color: {Colors.TEXT_PRIMARY};
                        border: 1px solid {Colors.BORDER};
                        padding: 8px 12px;
                        border-radius: 6px;
                    }}
                    QPushButton:hover {{
                        background-color: {Colors.BUTTON_SECONDARY_HOVER};
                    }}
                """)
                load_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                load_btn.clicked.connect(lambda checked, w=widget: self._load_instruments(w))
                row.addWidget(load_btn)

            elif param["type"] == "perpetual_selector":
                widget = QComboBox()
                currency_widget = self.parameter_widgets.get("currency")
                currency = currency_widget.currentText() if currency_widget else "ETH"
                widget.addItems([f"{currency}-PERPETUAL"])
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

                # Update perpetual when currency changes
                if currency_widget:
                    currency_widget.currentTextChanged.connect(
                        lambda curr, w=widget: self._update_perpetual(w, curr)
                    )

            else:
                widget = QLineEdit()
                widget.setText(str(param.get("default", "")))
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            self.parameter_widgets[param["name"]] = widget
            row.addWidget(widget)

            self.parameters_layout.addLayout(row)

    def _load_instruments(self, combo_widget: QComboBox) -> None:
        """
        Load instruments based on current currency and kind selection.

        Args:
            combo_widget: The combo box to populate with instruments.
        """
        currency_widget = self.parameter_widgets.get("currency")
        kind_widget = self.parameter_widgets.get("kind")

        currency = currency_widget.currentText() if currency_widget else "ETH"
        kind = kind_widget.currentText() if kind_widget else "option"

        self.log_viewer.log_info(f"Loading {kind} instruments for {currency}...")
        combo_widget.clear()
        combo_widget.addItem("Loading...")

        # Start worker thread
        self.instrument_worker = InstrumentLoaderWorker(currency, kind, self)
        self.instrument_worker.finished.connect(
            lambda instruments: self._on_instruments_loaded(combo_widget, instruments)
        )
        self.instrument_worker.error.connect(
            lambda error: self._on_instruments_error(combo_widget, error)
        )
        self.instrument_worker.start()

    def _on_instruments_loaded(self, combo_widget: QComboBox, instruments: List[Dict]) -> None:
        """
        Handle successful instrument loading.

        Args:
            combo_widget: The combo box to populate.
            instruments: List of instrument dictionaries.
        """
        combo_widget.clear()
        instrument_names = sorted([inst["instrument_name"] for inst in instruments])

        if instrument_names:
            combo_widget.addItems(instrument_names)
            self.log_viewer.log_info(f"Loaded {len(instrument_names)} instruments")
        else:
            combo_widget.addItem("No instruments found")
            self.log_viewer.log_warning("No instruments found")

    def _on_instruments_error(self, combo_widget: QComboBox, error_message: str) -> None:
        """
        Handle instrument loading error.

        Args:
            combo_widget: The combo box to update.
            error_message: Error description.
        """
        combo_widget.clear()
        combo_widget.addItem("Error loading")
        self.log_viewer.log_error(f"Failed to load instruments: {error_message}")

    def _update_perpetual(self, combo_widget: QComboBox, currency: str) -> None:
        """
        Update perpetual selector when currency changes.

        Args:
            combo_widget: The perpetual combo box.
            currency: The selected currency.
        """
        combo_widget.clear()
        combo_widget.addItem(f"{currency}-PERPETUAL")

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

        # Check if this endpoint uses instrument selector
        has_instrument_selector = any(
            p["type"] in ("instrument_selector", "perpetual_selector")
            for p in param_configs
        )

        for param_config in param_configs:
            name = param_config["name"]
            param_type = param_config["type"]

            # Skip currency/kind for instrument-based endpoints (used only for loading)
            if has_instrument_selector and name in ("currency", "kind"):
                continue

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
        active_worker = self.external_worker if self.external_worker and self.external_worker.isRunning() else self.worker
        if active_worker is not None and active_worker.isRunning():
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

        if endpoint_name in ExternalApiWorker.EXTERNAL_ENDPOINTS:
            self.external_worker = ExternalApiWorker(endpoint_name)
            self.external_worker.finished.connect(self._on_request_finished)
            self.external_worker.error.connect(self._on_request_error)
            self.external_worker.start()
        else:
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
