"""
Strategy tab for evaluating and finding best option strategies.

Provides interface for:
- Selecting strategies (Long Call, Long Put)
- Configuring strike selection and filters
- Evaluating strategies and viewing ranked results
"""

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QCheckBox,
    QAbstractItemView,
)

from coding.core.database.repository import DatabaseRepository
from coding.core.strategy.definitions import get_available_strategies
from coding.core.strategy.models import StrategyConfig, StrikeConfig
from coding.gui.components.log_viewer import GuiLogHandler, LogViewer
from coding.gui.theme.colors import Colors
from coding.service.strategy import StrategyEvaluationService

logger = logging.getLogger(__name__)


class LoadExpiriesWorker(QThread):
    """
    Worker thread for loading expiry dates.

    Runs in background to avoid blocking GUI.
    """

    finished = Signal(list)  # List of expirations
    error = Signal(str)

    def __init__(
        self,
        api_service,
        currency: str,
        parent: Optional[QWidget] = None
    ):
        """Initialize worker."""
        super().__init__(parent)
        self.api_service = api_service
        self.currency = currency

    def run(self) -> None:
        """Load expirations from API sorted by OI."""
        try:
            # Use the new service method that handles everything
            sorted_expirations = self.api_service.get_expirations_sorted_by_oi(
                currency=self.currency,
                include_oi=True  # Include OI in the display
            )

            if not sorted_expirations:
                self.error.emit(f"No option expirations found for {self.currency}")
                return

            self.finished.emit(sorted_expirations)

        except Exception as e:
            logger.exception("Error loading expirations")
            self.error.emit(str(e))


class StrategyEvaluationWorker(QThread):
    """
    Worker thread for strategy evaluation.

    Runs evaluation in background to avoid blocking GUI.
    """

    progress = Signal(str, int, int)  # (message, current, total)
    finished = Signal(object)  # EvaluationResult
    error = Signal(str)

    def __init__(
        self,
        api_service,
        repository: DatabaseRepository,
        currency: str,
        expiration: str,
        config: StrategyConfig,
        parent: Optional[QWidget] = None
    ):
        """Initialize evaluation worker."""
        super().__init__(parent)
        self.api_service = api_service
        self.repository = repository
        self.currency = currency
        self.expiration = expiration
        self.config = config

    def run(self) -> None:
        """Execute evaluation via service layer."""
        try:
            service = StrategyEvaluationService(
                api_service=self.api_service,
                repository=self.repository,
                progress_callback=self.progress.emit
            )

            result = service.evaluate_strategies(
                currency=self.currency,
                expiration=self.expiration,
                config=self.config
            )

            if result.success:
                self.finished.emit(result)
            else:
                error_msg = "; ".join([e["error"] for e in result.errors])
                self.error.emit(error_msg or "Unknown error")

        except Exception as e:
            logger.exception("Error during strategy evaluation")
            self.error.emit(str(e))


class StrategyTab(QWidget):
    """
    Main strategy tab widget.

    Follows layered architecture:
    - GUI layer (this class): Only UI rendering and event handling
    - Service layer: Business logic and orchestration
    - Core layer: Strategy definitions and scoring
    """

    def __init__(
        self,
        api_service,
        repository: DatabaseRepository,
        parent: Optional[QWidget] = None
    ):
        """
        Initialize strategy tab.

        Args:
            api_service: Deribit API service instance
            repository: Database repository instance
            parent: Parent widget
        """
        super().__init__(parent)
        self.api_service = api_service
        self.repository = repository
        self.evaluation_worker = None
        self.expiry_worker = None
        self.available_expirations: List[str] = []
        self.accumulated_signals = []
        self.pending_evaluations = []
        self.current_evaluation_index = 0

        self._setup_ui()
        self._setup_logging()

        logger.info("StrategyTab initialized")

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # Set minimum width to prevent excessive shrinking
        self.setMinimumWidth(600)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Title
        title = QLabel("Strategy Evaluation")
        title.setStyleSheet(f"""
            font-size: 24px;
            font-weight: 700;
            color: {Colors.TEXT_PRIMARY};
        """)
        main_layout.addWidget(title)

        # General Controls Section
        general_frame = self._create_general_controls()
        main_layout.addWidget(general_frame)

        # Strategy Selector Section
        strategy_frame = self._create_strategy_selector()
        main_layout.addWidget(strategy_frame)

        # Configuration Section
        config_frame = self._create_configuration_section()
        main_layout.addWidget(config_frame)

        # Action Buttons
        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)

        evaluate_btn = QPushButton("Evaluate Strategy")
        evaluate_btn.setStyleSheet(self._get_button_style(Colors.ACCENT))
        evaluate_btn.setMinimumHeight(40)
        evaluate_btn.clicked.connect(self._on_evaluate_clicked)
        action_layout.addWidget(evaluate_btn)

        action_layout.addStretch()

        main_layout.addLayout(action_layout)

        # Results Section
        results_frame = self._create_results_section()
        main_layout.addWidget(results_frame, 1)  # Stretch to fill

        # Log Viewer
        self.log_viewer = LogViewer()
        main_layout.addWidget(self.log_viewer)

    def _create_general_controls(self) -> QFrame:
        """Create general controls section."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Section title
        title = QLabel("General Settings")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Currency selector - use grid for better control
        currency_grid = QGridLayout()
        currency_grid.setSpacing(8)

        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        currency_label.setMinimumWidth(120)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["BTC", "ETH"])
        self.currency_combo.setStyleSheet(self._get_combo_style())
        self.currency_combo.setMinimumWidth(100)
        self.currency_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        currency_grid.addWidget(currency_label, 0, 0)
        currency_grid.addWidget(self.currency_combo, 0, 1)
        currency_grid.setColumnStretch(1, 1)

        layout.addLayout(currency_grid)

        # Market regime - use grid for better control
        regime_grid = QGridLayout()
        regime_grid.setSpacing(8)

        regime_label = QLabel("Market Regime:")
        regime_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        regime_label.setMinimumWidth(120)

        self.regime_combo = QComboBox()
        self.regime_combo.addItems(["Neutral", "Bullish", "Bearish"])
        self.regime_combo.setStyleSheet(self._get_combo_style())
        self.regime_combo.setMinimumWidth(100)
        self.regime_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        regime_info = self._create_info_button(
            "Market Regime Info",
            """Market Regime affects scoring:

• Neutral: No bias, all strategies scored equally
• Bullish: Favors call strategies, applies 50% penalty to put strategies
• Bearish: Favors put strategies, applies 50% penalty to call strategies

The regime penalty helps filter strategies that go against the expected market direction.

Example: If you select "Bullish" regime and evaluate a Long Put, the composite score will be reduced by 50% since puts profit from price decreases."""
        )

        regime_grid.addWidget(regime_label, 0, 0)
        regime_grid.addWidget(self.regime_combo, 0, 1)
        regime_grid.addWidget(regime_info, 0, 2)
        regime_grid.setColumnStretch(1, 1)

        layout.addLayout(regime_grid)

        # Load expiries button
        load_btn = QPushButton("Load Expiry Dates")
        load_btn.setStyleSheet(self._get_button_style(Colors.BORDER))
        load_btn.clicked.connect(self._on_load_expiries_clicked)
        layout.addWidget(load_btn)

        # Expiry selector (multi-selection)
        expiry_label = QLabel("Select Expiries (multi-select):")
        expiry_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        self.expiry_list = QListWidget()
        self.expiry_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.expiry_list.setStyleSheet(self._get_list_style())
        self.expiry_list.setMaximumHeight(120)
        layout.addWidget(expiry_label)
        layout.addWidget(self.expiry_list)

        return frame

    def _create_strategy_selector(self) -> QFrame:
        """Create strategy selector section."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Section title
        title = QLabel("Select Strategy")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Strategy buttons grid
        grid = QGridLayout()
        grid.setSpacing(8)

        strategies = get_available_strategies()
        for i, strategy_name in enumerate(strategies):
            btn = QPushButton(strategy_name)
            btn.setCheckable(True)
            btn.setStyleSheet(self._get_strategy_button_style())
            btn.setMinimumHeight(50)
            btn.setMinimumWidth(150)  # Prevent text cutoff
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, name=strategy_name: self._on_strategy_selected(name))

            row = i // 2
            col = i % 2
            grid.addWidget(btn, row, col)

        # Make columns equal width
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        layout.addLayout(grid)

        # Store for later reference
        self.strategy_buttons = {btn.text(): btn for btn in frame.findChildren(QPushButton)}

        return frame

    def _create_configuration_section(self) -> QFrame:
        """Create configuration section."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Section title
        title = QLabel("Strategy Configuration")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Strike selection method - use grid for better control
        strike_grid = QGridLayout()
        strike_grid.setSpacing(8)

        strike_label = QLabel("Strike Selection:")
        strike_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        strike_label.setMinimumWidth(120)

        self.strike_method_combo = QComboBox()
        self.strike_method_combo.addItems(["By Delta", "By Moneyness", "By Specific Strike"])
        self.strike_method_combo.setStyleSheet(self._get_combo_style())
        self.strike_method_combo.setMinimumWidth(150)
        self.strike_method_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.strike_method_combo.currentTextChanged.connect(self._on_strike_method_changed)

        strike_info = self._create_info_button(
            "Strike Selection Methods",
            """Three methods to select option strikes:

1. BY DELTA (Recommended)
   • Delta measures how much the option price changes per $1 move in underlying
   • Call delta: 0 to 1 (e.g., 0.30 means option gains $0.30 per $1 rise)
   • Put delta: 0 to -1 (e.g., -0.30 means option gains $0.30 per $1 drop)
   • NOTE: Enter positive values (e.g., 0.30) - the sign is automatically adjusted for puts!

   • Common deltas:
     - 0.30 delta: Out-of-the-money (OTM), lower cost, higher leverage
     - 0.50 delta: At-the-money (ATM), balanced risk/reward
     - 0.70 delta: In-the-money (ITM), higher cost, behaves more like stock

   Example: Enter 0.30 for both Long Call and Long Put - the system automatically uses
           +0.30 for calls (strike above current) and -0.30 for puts (strike below current)

2. BY MONEYNESS
   • Moneyness = percentage distance from current price
   • 5% moneyness for calls = strike 5% above current price
   • 5% moneyness for puts = strike 5% below current price

   Example: If BTC is at $100,000, 5% OTM call = $105,000 strike

3. BY SPECIFIC STRIKE
   • Manually enter exact strike price
   • Use when you have a specific price target in mind

   Example: Enter 105000 to buy exactly $105,000 strike"""
        )

        strike_grid.addWidget(strike_label, 0, 0)
        strike_grid.addWidget(self.strike_method_combo, 0, 1)
        strike_grid.addWidget(strike_info, 0, 2)
        strike_grid.setColumnStretch(1, 1)

        layout.addLayout(strike_grid)

        # Delta value (for "By Delta" method) - use grid
        delta_grid = QGridLayout()
        delta_grid.setSpacing(8)

        delta_label = QLabel("Target Delta:")
        delta_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        delta_label.setMinimumWidth(120)

        self.delta_spin = QDoubleSpinBox()
        self.delta_spin.setRange(-1.0, 1.0)
        self.delta_spin.setValue(0.30)
        self.delta_spin.setSingleStep(0.05)
        self.delta_spin.setDecimals(2)
        self.delta_spin.setMinimumWidth(100)
        self.delta_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delta_spin.setStyleSheet(self._get_spin_style())

        delta_grid.addWidget(delta_label, 0, 0)
        delta_grid.addWidget(self.delta_spin, 0, 1)
        delta_grid.setColumnStretch(1, 1)

        layout.addLayout(delta_grid)
        self.delta_layout_widgets = (delta_label, self.delta_spin, delta_grid)

        # Moneyness % (for "By Moneyness" method) - use grid
        money_grid = QGridLayout()
        money_grid.setSpacing(8)

        money_label = QLabel("Moneyness %:")
        money_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        money_label.setMinimumWidth(120)

        self.moneyness_spin = QDoubleSpinBox()
        self.moneyness_spin.setRange(0.0, 50.0)
        self.moneyness_spin.setValue(5.0)
        self.moneyness_spin.setSingleStep(1.0)
        self.moneyness_spin.setDecimals(1)
        self.moneyness_spin.setMinimumWidth(100)
        self.moneyness_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.moneyness_spin.setStyleSheet(self._get_spin_style())

        money_grid.addWidget(money_label, 0, 0)
        money_grid.addWidget(self.moneyness_spin, 0, 1)
        money_grid.setColumnStretch(1, 1)

        layout.addLayout(money_grid)
        self.moneyness_layout_widgets = (money_label, self.moneyness_spin, money_grid)
        for widget in self.moneyness_layout_widgets:
            widget.hide()

        # Specific strike (for "By Specific Strike" method) - use grid
        specific_grid = QGridLayout()
        specific_grid.setSpacing(8)

        specific_label = QLabel("Strike Price:")
        specific_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        specific_label.setMinimumWidth(120)

        self.specific_strike_spin = QDoubleSpinBox()
        self.specific_strike_spin.setRange(0.0, 1000000.0)
        self.specific_strike_spin.setValue(100000.0)
        self.specific_strike_spin.setSingleStep(1000.0)
        self.specific_strike_spin.setDecimals(0)
        self.specific_strike_spin.setMinimumWidth(100)
        self.specific_strike_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.specific_strike_spin.setStyleSheet(self._get_spin_style())

        specific_grid.addWidget(specific_label, 0, 0)
        specific_grid.addWidget(self.specific_strike_spin, 0, 1)
        specific_grid.setColumnStretch(1, 1)

        layout.addLayout(specific_grid)
        self.specific_strike_layout_widgets = (specific_label, self.specific_strike_spin, specific_grid)
        for widget in self.specific_strike_layout_widgets:
            widget.hide()

        # Max loss filter - use grid
        max_loss_grid = QGridLayout()
        max_loss_grid.setSpacing(8)

        max_loss_label = QLabel("Max Loss %:")
        max_loss_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        max_loss_label.setMinimumWidth(120)

        self.max_loss_spin = QDoubleSpinBox()
        self.max_loss_spin.setRange(0.0, 100.0)
        self.max_loss_spin.setValue(5.0)
        self.max_loss_spin.setSingleStep(0.5)
        self.max_loss_spin.setDecimals(1)
        self.max_loss_spin.setMinimumWidth(100)
        self.max_loss_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.max_loss_spin.setStyleSheet(self._get_spin_style())

        max_loss_grid.addWidget(max_loss_label, 0, 0)
        max_loss_grid.addWidget(self.max_loss_spin, 0, 1)
        max_loss_grid.setColumnStretch(1, 1)

        layout.addLayout(max_loss_grid)

        # Take profit % - use grid
        tp_grid = QGridLayout()
        tp_grid.setSpacing(8)

        tp_check = QCheckBox("Take Profit %:")
        tp_check.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        tp_check.setMinimumWidth(120)
        tp_check.stateChanged.connect(self._on_tp_check_changed)

        self.tp_spin = QDoubleSpinBox()
        self.tp_spin.setRange(0.0, 1000.0)
        self.tp_spin.setValue(50.0)
        self.tp_spin.setSingleStep(10.0)
        self.tp_spin.setDecimals(0)
        self.tp_spin.setEnabled(False)
        self.tp_spin.setMinimumWidth(100)
        self.tp_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tp_spin.setStyleSheet(self._get_spin_style())

        tp_grid.addWidget(tp_check, 0, 0)
        tp_grid.addWidget(self.tp_spin, 0, 1)
        tp_grid.setColumnStretch(1, 1)

        layout.addLayout(tp_grid)
        self.tp_check = tp_check

        return frame

    def _create_results_section(self) -> QFrame:
        """Create results display section."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Section title
        title = QLabel("Results")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Results table
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(9)
        self.results_table.setHorizontalHeaderLabels([
            "Rank", "Strategy", "Expiry", "Composite", "Intrinsic", "On-Chain",
            "Max Loss %", "Breakeven", "Chart"
        ])
        self.results_table.setStyleSheet(self._get_table_style())
        self.results_table.setAlternatingRowColors(True)
        layout.addWidget(self.results_table)

        return frame

    def _setup_logging(self) -> None:
        """Set up GUI log handler."""
        gui_handler = GuiLogHandler(self.log_viewer)
        gui_handler.setLevel(logging.INFO)

        # Add handler to relevant loggers
        for logger_name in [
            "coding.service.strategy",
            "coding.core.strategy",
        ]:
            target_logger = logging.getLogger(logger_name)
            target_logger.addHandler(gui_handler)

    def _on_load_expiries_clicked(self) -> None:
        """Handle load expiries button click."""
        if self.expiry_worker and self.expiry_worker.isRunning():
            logger.warning("Already loading expirations")
            return

        currency = self.currency_combo.currentText()

        logger.info(f"Loading expirations for {currency}...")

        self.expiry_worker = LoadExpiriesWorker(
            api_service=self.api_service,
            currency=currency,
            parent=self
        )

        self.expiry_worker.finished.connect(self._on_expiries_loaded)
        self.expiry_worker.error.connect(self._on_expiry_load_error)

        self.expiry_worker.start()

    def _on_expiries_loaded(self, expirations: List[str]) -> None:
        """Handle expirations loaded successfully."""
        self.available_expirations = expirations
        self.expiry_list.clear()
        self.expiry_list.addItems(expirations)

        logger.info(f"Loaded {len(expirations)} expirations (sorted by OI descending)")

    def _on_expiry_load_error(self, error: str) -> None:
        """Handle expiry load error."""
        logger.error(f"Failed to load expirations: {error}")

    def _on_strategy_selected(self, strategy_name: str) -> None:
        """Handle strategy button click (multi-selection allowed)."""
        # Get all selected strategies
        selected = [name for name, btn in self.strategy_buttons.items() if btn.isChecked()]

        if selected:
            logger.info(f"Selected strategies: {', '.join(selected)}")
        else:
            logger.info("No strategies selected")

    def _on_strike_method_changed(self, method: str) -> None:
        """Handle strike method change."""
        # Hide all method-specific widgets (includes the grid layouts)
        for item in self.delta_layout_widgets:
            if hasattr(item, 'hide'):
                item.hide()
        for item in self.moneyness_layout_widgets:
            if hasattr(item, 'hide'):
                item.hide()
        for item in self.specific_strike_layout_widgets:
            if hasattr(item, 'hide'):
                item.hide()

        # Show relevant widgets
        if method == "By Delta":
            for item in self.delta_layout_widgets:
                if hasattr(item, 'show'):
                    item.show()
        elif method == "By Moneyness":
            for item in self.moneyness_layout_widgets:
                if hasattr(item, 'show'):
                    item.show()
        elif method == "By Specific Strike":
            for item in self.specific_strike_layout_widgets:
                if hasattr(item, 'show'):
                    item.show()

    def _on_tp_check_changed(self, state: int) -> None:
        """Handle take profit checkbox change."""
        self.tp_spin.setEnabled(state == Qt.CheckState.Checked.value)

    def _on_evaluate_clicked(self) -> None:
        """Handle evaluate button click."""
        # Validate selections - collect all selected strategies
        selected_strategies = [name for name, btn in self.strategy_buttons.items() if btn.isChecked()]

        if not selected_strategies:
            logger.error("Please select at least one strategy")
            return

        # Collect all selected expiries
        selected_expiries = [item.text() for item in self.expiry_list.selectedItems()]

        if not selected_expiries:
            logger.error("Please load and select at least one expiry date")
            return

        # Build config
        currency = self.currency_combo.currentText()

        # Get regime
        regime = self.regime_combo.currentText()
        regime_value = regime.lower() if regime != "Neutral" else None

        # Build strike config
        method = self.strike_method_combo.currentText()

        if method == "By Delta":
            strike_config = StrikeConfig(
                method="by_delta",
                target_delta=self.delta_spin.value(),
                quantity=1
            )
        elif method == "By Moneyness":
            strike_config = StrikeConfig(
                method="by_moneyness",
                moneyness_pct=self.moneyness_spin.value(),
                quantity=1
            )
        else:  # By Specific Strike
            strike_config = StrikeConfig(
                method="by_strike",
                specific_strike=self.specific_strike_spin.value(),
                quantity=1
            )

        # Build strike configs for all selected strategies
        # Important: Adjust delta sign based on strategy type (puts need negative delta)
        strike_configs = {}
        for strategy in selected_strategies:
            if method == "By Delta":
                # For put strategies, negate the delta
                if "Put" in strategy:
                    adjusted_delta = -abs(self.delta_spin.value())
                    strategy_strike_config = StrikeConfig(
                        method="by_delta",
                        target_delta=adjusted_delta,
                        quantity=1
                    )
                else:
                    # For calls and other strategies, use positive delta
                    adjusted_delta = abs(self.delta_spin.value())
                    strategy_strike_config = StrikeConfig(
                        method="by_delta",
                        target_delta=adjusted_delta,
                        quantity=1
                    )
                strike_configs[strategy] = strategy_strike_config
            else:
                # For moneyness and specific strike, use the same config
                strike_configs[strategy] = strike_config

        # Clear results table to prepare for new evaluation
        self.results_table.setRowCount(0)
        self.accumulated_signals = []

        logger.info(
            f"Starting evaluation for {len(selected_strategies)} strategies "
            f"across {len(selected_expiries)} expirations"
        )

        # Start evaluations for all combinations
        self._start_multi_evaluation(
            currency=currency,
            selected_strategies=selected_strategies,
            selected_expiries=selected_expiries,
            strike_configs=strike_configs,
            regime_value=regime_value
        )

    def _start_multi_evaluation(
        self,
        currency: str,
        selected_strategies: List[str],
        selected_expiries: List[str],
        strike_configs: Dict,
        regime_value: Optional[str]
    ) -> None:
        """Start multiple strategy evaluations across expirations."""
        # Build evaluation queue
        self.pending_evaluations = []

        for expiration_with_oi in selected_expiries:
            # Extract actual expiration from "30JAN26 (OI: 12345)" format
            expiration = expiration_with_oi.split(" (")[0] if " (" in expiration_with_oi else expiration_with_oi

            config = StrategyConfig(
                strategy_names=selected_strategies,
                expirations=[expiration],
                strike_configs=strike_configs,
                max_loss_filter=self.max_loss_spin.value(),
                take_profit_percentage=self.tp_spin.value() if self.tp_check.isChecked() else None,
                market_regime=regime_value,
                top_n=10
            )

            self.pending_evaluations.append((currency, expiration, config))

        # Start processing queue
        self.current_evaluation_index = 0
        self._process_next_evaluation()

    def _process_next_evaluation(self) -> None:
        """Process next evaluation in queue."""
        if self.current_evaluation_index >= len(self.pending_evaluations):
            # All evaluations complete
            logger.info(
                f"All evaluations complete: {len(self.accumulated_signals)} total signals"
            )
            return

        # Check if already running
        if self.evaluation_worker and self.evaluation_worker.isRunning():
            logger.warning("Evaluation already in progress")
            return

        # Get next evaluation
        currency, expiration, config = self.pending_evaluations[self.current_evaluation_index]

        logger.info(
            f"Processing evaluation {self.current_evaluation_index + 1}/"
            f"{len(self.pending_evaluations)}: {currency}-{expiration}"
        )

        self._start_evaluation(currency, expiration, config)

    def _start_evaluation(
        self,
        currency: str,
        expiration: str,
        config: StrategyConfig
    ) -> None:
        """Start strategy evaluation in background."""
        if self.evaluation_worker and self.evaluation_worker.isRunning():
            logger.warning("Evaluation already in progress")
            return

        logger.info(f"Starting evaluation: {currency}-{expiration}")

        self.evaluation_worker = StrategyEvaluationWorker(
            api_service=self.api_service,
            repository=self.repository,
            currency=currency,
            expiration=expiration,
            config=config,
            parent=self
        )

        self.evaluation_worker.progress.connect(self._on_evaluation_progress)
        self.evaluation_worker.finished.connect(self._on_evaluation_finished)
        self.evaluation_worker.error.connect(self._on_evaluation_error)

        self.evaluation_worker.start()

    def _on_evaluation_progress(self, message: str, current: int, total: int) -> None:
        """Handle evaluation progress update."""
        logger.info(f"Progress: {message} ({current}/{total})")

    def _on_evaluation_finished(self, result) -> None:
        """Handle evaluation completion."""
        logger.info(
            f"Evaluation complete: {len(result.signals)} signals, "
            f"{len(result.errors)} errors, "
            f"time={result.evaluation_time_seconds:.2f}s"
        )

        # Accumulate signals
        self.accumulated_signals.extend(result.signals)

        # Move to next evaluation
        self.current_evaluation_index += 1
        self._process_next_evaluation()

        # Update results table with all accumulated signals (sorted by composite score)
        all_signals = sorted(self.accumulated_signals, key=lambda s: s.composite_score, reverse=True)

        self.results_table.setRowCount(len(all_signals))

        for i, signal in enumerate(all_signals):
            self.results_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.results_table.setItem(i, 1, QTableWidgetItem(signal.strategy_name))
            self.results_table.setItem(i, 2, QTableWidgetItem(signal.expiration))
            self.results_table.setItem(i, 3, QTableWidgetItem(f"{signal.composite_score:.2f}"))
            self.results_table.setItem(i, 4, QTableWidgetItem(f"{signal.intrinsic_score:.2f}"))
            self.results_table.setItem(i, 5, QTableWidgetItem(f"{signal.on_chain_score:.2f}"))
            self.results_table.setItem(i, 6, QTableWidgetItem(f"{signal.max_loss_percentage:.2f}%"))

            # Breakeven points
            breakeven_str = ", ".join([f"{bp:.0f}" for bp in signal.breakeven_points])
            self.results_table.setItem(i, 7, QTableWidgetItem(breakeven_str))

            # View Chart button
            chart_btn = self._create_chart_button(signal)
            self.results_table.setCellWidget(i, 8, chart_btn)

        self.results_table.resizeColumnsToContents()

    def _on_evaluation_error(self, error: str) -> None:
        """Handle evaluation error."""
        logger.error(f"Evaluation failed: {error}")

    def _create_info_button(self, tooltip: str, detailed_text: str) -> QPushButton:
        """
        Create an info button with tooltip and detailed explanation.

        Args:
            tooltip: Short tooltip text
            detailed_text: Detailed explanation shown in dialog

        Returns:
            Info button widget
        """
        btn = QPushButton("Info")
        btn.setFixedSize(60, 30)
        btn.setToolTip(tooltip)
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
        btn.clicked.connect(lambda: self._show_info_dialog(tooltip, detailed_text))
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

    def _create_chart_button(self, signal) -> QPushButton:
        """
        Create a button to view strategy chart.

        Args:
            signal: Strategy signal with chart_path

        Returns:
            Button widget
        """
        from pathlib import Path

        btn = QPushButton("View")
        btn.setFixedSize(60, 30)

        # Check if chart path exists
        has_chart = signal.chart_path and Path(signal.chart_path).exists()

        if has_chart:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.ACCENT};
                    color: {Colors.TEXT_PRIMARY};
                    border: none;
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 600;
                    padding: 4px;
                }}
                QPushButton:hover {{
                    background-color: {Colors.ACCENT_MUTED};
                }}
                QPushButton:pressed {{
                    background-color: #1565c0;
                }}
            """)
            btn.clicked.connect(lambda: self._open_chart(signal.chart_path))
        else:
            btn.setText("N/A")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.SURFACE};
                    color: {Colors.TEXT_SECONDARY};
                    border: 1px solid {Colors.BORDER};
                    border-radius: 6px;
                    font-size: 12px;
                    padding: 4px;
                }}
            """)

        return btn

    def _open_chart(self, chart_path: str) -> None:
        """
        Open chart HTML file in default browser.

        Args:
            chart_path: Path to chart HTML file
        """
        import webbrowser
        from pathlib import Path

        path = Path(chart_path)

        if path.exists():
            # Convert to file:// URL
            file_url = path.as_uri()
            webbrowser.open(file_url)
            logger.info(f"Opened chart: {path.name}")
        else:
            QMessageBox.warning(
                self,
                "Chart Not Found",
                f"Chart file not found:\n{chart_path}\n\nTry re-evaluating the strategy."
            )
            logger.warning(f"Chart file not found: {chart_path}")

    def _get_button_style(self, color: str) -> str:
        """Get button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {color};
                color: {Colors.TEXT_PRIMARY};
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Colors.ACCENT_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {Colors.ACCENT_MUTED};
            }}
        """

    def _get_strategy_button_style(self) -> str:
        """Get strategy button stylesheet."""
        return f"""
            QPushButton {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 2px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {Colors.ACCENT};
            }}
            QPushButton:checked {{
                background-color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
        """

    def _get_combo_style(self) -> str:
        """Get combobox stylesheet."""
        return f"""
            QComboBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 12px;
            }}
        """

    def _get_spin_style(self) -> str:
        """Get spinbox stylesheet."""
        return f"""
            QDoubleSpinBox, QSpinBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """

    def _get_table_style(self) -> str:
        """Get table stylesheet."""
        return f"""
            QTableWidget {{
                background-color: {Colors.BACKGROUND_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                gridline-color: {Colors.BORDER};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
            }}
            QTableWidget::item {{
                padding: 8px;
            }}
            QHeaderView::section {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                padding: 8px;
                border: none;
                font-weight: 600;
            }}
        """

    def _get_list_style(self) -> str:
        """Get list widget stylesheet."""
        return f"""
            QListWidget {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 6px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
            }}
            QListWidget::item:hover {{
                background-color: {Colors.SURFACE};
            }}
        """
