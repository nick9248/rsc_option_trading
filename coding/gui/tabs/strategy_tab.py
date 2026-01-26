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
from PySide6.QtGui import QBrush, QColor
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
from coding.gui.components.strategy_config_widgets import create_config_widget, StrategyConfigWidget
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
        # Create main layout for the tab
        tab_layout = QVBoxLayout(self)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        # Create scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background-color: {Colors.BACKGROUND_PRIMARY};
            }}
        """)

        # Create content widget
        content_widget = QWidget()
        content_widget.setMinimumWidth(580)  # Minimum width for content
        scroll.setWidget(content_widget)

        # Content layout
        main_layout = QVBoxLayout(content_widget)
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

        # Add scroll area to tab
        tab_layout.addWidget(scroll, 1)

        # Log Viewer (fixed at bottom, not scrollable)
        self.log_viewer = LogViewer()
        self.log_viewer.setMaximumHeight(150)
        tab_layout.addWidget(self.log_viewer)

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

        # Strategy-specific configuration widget container
        # This will hold the dynamic widget that changes based on selected strategy
        self.config_widget_container = QWidget()
        self.config_widget_layout = QVBoxLayout(self.config_widget_container)
        self.config_widget_layout.setContentsMargins(0, 0, 0, 0)
        self.config_widget_layout.setSpacing(0)

        # Placeholder until strategy is selected
        placeholder = QLabel("← Select a strategy to configure")
        placeholder.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 14px; padding: 20px;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.config_widget_layout.addWidget(placeholder)

        layout.addWidget(self.config_widget_container)

        # Track current config widget
        self.current_config_widget: Optional[StrategyConfigWidget] = None

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

        # Budget constraint (optional, for spread strategies) - use grid
        budget_grid = QGridLayout()
        budget_grid.setSpacing(8)

        budget_check = QCheckBox("Max Budget ($):")
        budget_check.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        budget_check.setMinimumWidth(120)
        budget_check.stateChanged.connect(self._on_budget_check_changed)

        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setRange(0.0, 1000000.0)
        self.budget_spin.setValue(1000.0)
        self.budget_spin.setSingleStep(100.0)
        self.budget_spin.setDecimals(0)
        self.budget_spin.setEnabled(False)
        self.budget_spin.setMinimumWidth(100)
        self.budget_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.budget_spin.setStyleSheet(self._get_spin_style())

        budget_info = self._create_info_button(
            "Budget Constraint",
            """Optional budget constraint for spread strategies.

WHAT IT DOES:
When enabled, the strategy finder will:
• Only consider spreads that cost less than this budget
• Maximize spread width within the budget
• Useful for capital-constrained accounts

EXAMPLE:
Budget = $500
- System finds widest spread that costs ≤ $500
- Could be 100/200 spread at $450 cost
- Or 150/250 spread at $490 cost
- Picks the one with best width-to-cost ratio

WHEN TO USE:
• Limited capital available for the trade
• Want to control maximum position size
• Optimize for 'max_width_for_budget' mode

NOTE: Only applies to spread strategies (Bull Call Spread, etc.)
      Ignored for single-leg strategies (Long Call/Put)"""
        )

        budget_grid.addWidget(budget_check, 0, 0)
        budget_grid.addWidget(self.budget_spin, 0, 1)
        budget_grid.addWidget(budget_info, 0, 2)
        budget_grid.setColumnStretch(1, 1)

        layout.addLayout(budget_grid)
        self.budget_check = budget_check

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
        self.results_table.setColumnCount(10)
        self.results_table.setHorizontalHeaderLabels([
            "Rank", "Strategy", "Expiry", "Market Regime", "Composite", "Intrinsic", "On-Chain",
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
        """Handle strategy button click and swap configuration widget."""
        # Get all selected strategies
        selected = [name for name, btn in self.strategy_buttons.items() if btn.isChecked()]

        if selected:
            logger.info(f"Selected strategies: {', '.join(selected)}")
            # Update config widget based on first selected strategy
            self._swap_config_widget(selected[0])
        else:
            logger.info("No strategies selected")
            # Clear config widget if no strategy selected
            self._clear_config_widget()

    def _swap_config_widget(self, strategy_name: str) -> None:
        """
        Swap out the current config widget for the selected strategy's widget.

        Args:
            strategy_name: Name of the strategy to create widget for
        """
        from coding.core.strategy import create_strategy

        try:
            # Remove old widget if exists
            if self.current_config_widget:
                self.config_widget_layout.removeWidget(self.current_config_widget)
                self.current_config_widget.deleteLater()
                self.current_config_widget = None

            # Create new config widget for this strategy type
            self.current_config_widget = create_config_widget(strategy_name)

            # Load strategy defaults
            temp_strategy = create_strategy(
                name=strategy_name,
                currency="BTC",
                expiration="31JAN25",
                underlying_price=100000.0
            )
            defaults = temp_strategy.get_default_config()
            self.current_config_widget.set_defaults(defaults)

            logger.info(f"Loaded config widget for {strategy_name} with defaults: {defaults}")

            # Add to layout
            self.config_widget_layout.addWidget(self.current_config_widget)

            # Update common fields (max loss) if present
            if "max_loss_percentage" in defaults:
                self.max_loss_spin.setValue(defaults["max_loss_percentage"])
                logger.info(f"Updated max loss default to {defaults['max_loss_percentage']:.1f}% for {strategy_name}")

        except Exception as e:
            logger.error(f"Failed to swap config widget for {strategy_name}: {e}")
            logger.exception("Widget swap error")
            # Show error to user
            self._clear_config_widget()
            error_label = QLabel(f"Error loading configuration for {strategy_name}")
            error_label.setStyleSheet(f"color: {Colors.ERROR}; padding: 20px;")
            self.config_widget_layout.addWidget(error_label)

    def _clear_config_widget(self) -> None:
        """Clear the config widget and show placeholder."""
        if self.current_config_widget:
            self.config_widget_layout.removeWidget(self.current_config_widget)
            self.current_config_widget.deleteLater()
            self.current_config_widget = None

        # Show placeholder
        placeholder = QLabel("← Select a strategy to configure")
        placeholder.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 14px; padding: 20px;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.config_widget_layout.addWidget(placeholder)

    def _convert_widget_config_to_strike_config(
        self,
        strategy_name: str,
        widget_config: Dict
    ):
        """
        Convert widget configuration dict to StrikeConfig or SpreadStrikeConfig.

        Args:
            strategy_name: Name of the strategy
            widget_config: Config dict from widget.get_config()

        Returns:
            StrikeConfig for single-leg strategies
            SpreadStrikeConfig for spread strategies (or widget_config dict for service to handle)
        """
        from coding.core.strategy.models.spread_config import SpreadStrikeConfig

        # Check if this is a spread strategy
        is_spread = "Spread" in strategy_name

        if is_spread:
            # Spread configuration
            mode = widget_config.get("mode", "optimal")

            if mode == "optimal":
                # Optimal (skew-aware) mode - return dict for service to handle
                # Service will use skew-aware with budget constraint if specified
                return widget_config
            else:
                # Manual mode - create SpreadStrikeConfig from widget config
                method = widget_config.get("method")

                if method == "by_delta":
                    return SpreadStrikeConfig(
                        method="by_delta",
                        long_target_delta=widget_config.get("long_target_delta", 0.45),
                        short_target_delta=widget_config.get("short_target_delta", 0.25),
                        quantity=1
                    )
                elif method == "by_moneyness":
                    return SpreadStrikeConfig(
                        method="by_moneyness",
                        long_moneyness_pct=widget_config.get("long_moneyness_pct", 10.0),
                        short_moneyness_pct=widget_config.get("short_moneyness_pct", 20.0),
                        quantity=1
                    )
                elif method == "by_strike":
                    return SpreadStrikeConfig(
                        method="by_strike",
                        long_specific_strike=widget_config.get("long_specific_strike", 50000.0),
                        short_specific_strike=widget_config.get("short_specific_strike", 55000.0),
                        quantity=1
                    )
                else:
                    logger.warning(f"Unknown spread method: {method}, defaulting to skew-aware")
                    return widget_config
        else:
            # Single-leg configuration - convert to StrikeConfig
            method = widget_config.get("method")

            # Adjust delta sign for puts
            if method == "by_delta":
                target_delta = widget_config.get("target_delta", 0.30)
                if "Put" in strategy_name:
                    target_delta = -abs(target_delta)
                else:
                    target_delta = abs(target_delta)

                return StrikeConfig(
                    method="by_delta",
                    target_delta=target_delta,
                    quantity=1
                )
            elif method == "by_moneyness":
                return StrikeConfig(
                    method="by_moneyness",
                    moneyness_pct=widget_config.get("moneyness_pct", 5.0),
                    quantity=1
                )
            elif method == "by_strike":
                return StrikeConfig(
                    method="by_strike",
                    specific_strike=widget_config.get("specific_strike", 100000.0),
                    quantity=1
                )
            else:
                logger.warning(f"Unknown method: {method}, defaulting to by_delta")
                return StrikeConfig(method="by_delta", target_delta=0.30, quantity=1)

    def _on_tp_check_changed(self, state: int) -> None:
        """Handle take profit checkbox change."""
        self.tp_spin.setEnabled(state == Qt.CheckState.Checked.value)

    def _on_budget_check_changed(self, state: int) -> None:
        """Handle budget constraint checkbox change."""
        self.budget_spin.setEnabled(state == Qt.CheckState.Checked.value)

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

        # Market regime is now auto-detected in the backend (no user input needed)
        regime_value = None

        # Get configuration from current config widget
        if not self.current_config_widget:
            logger.error("No strategy configuration widget loaded. Please select a strategy first.")
            return

        try:
            widget_config = self.current_config_widget.get_config()
            logger.info(f"Widget configuration: {widget_config}")
        except Exception as e:
            logger.error(f"Failed to get configuration from widget: {e}")
            return

        # Convert widget config to proper config objects
        strike_configs = {}
        for strategy in selected_strategies:
            strike_config_obj = self._convert_widget_config_to_strike_config(
                strategy, widget_config
            )
            strike_configs[strategy] = strike_config_obj

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
                max_budget=self.budget_spin.value() if self.budget_check.isChecked() else None,
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

            # Market regime (auto-detected by backend)
            regime_display = signal.market_regime.capitalize() if signal.market_regime else "Neutral"
            regime_item = QTableWidgetItem(regime_display)
            # Color code the regime
            if signal.market_regime == "bullish":
                regime_item.setForeground(QBrush(QColor("#4ade80")))  # Green
            elif signal.market_regime == "bearish":
                regime_item.setForeground(QBrush(QColor("#f87171")))  # Red
            self.results_table.setItem(i, 3, regime_item)

            self.results_table.setItem(i, 4, QTableWidgetItem(f"{signal.composite_score:.2f}"))
            self.results_table.setItem(i, 5, QTableWidgetItem(f"{signal.intrinsic_score:.2f}"))
            self.results_table.setItem(i, 6, QTableWidgetItem(f"{signal.on_chain_score:.2f}"))
            self.results_table.setItem(i, 7, QTableWidgetItem(f"{signal.max_loss_percentage:.2f}%"))

            # Breakeven points
            breakeven_str = ", ".join([f"{bp:.0f}" for bp in signal.breakeven_points])
            self.results_table.setItem(i, 8, QTableWidgetItem(breakeven_str))

            # View Chart button
            chart_btn = self._create_chart_button(signal)
            self.results_table.setCellWidget(i, 9, chart_btn)

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
