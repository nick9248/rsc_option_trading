"""
Fullscreen flow charts dialog window.

Displays buy/sell flow charts with dynamic expiration selection.
Charts are generated from database on-demand.
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QTabWidget,
    QMessageBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView

from coding.core.analytics.chart_generator import inject_hover_js
from coding.core.database.repository import DatabaseRepository
from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


class FlowChartsWindow(QDialog):
    """
    Fullscreen flow charts viewer with dynamic chart generation.

    Features:
    - Expiration selector dropdown (sorted by OI)
    - Three charts: distribution, net flow, trend
    - Charts load from database (not static files)
    - Fullscreen modal dialog
    """

    def __init__(
        self,
        currency: str,
        repository: DatabaseRepository,
        parent: Optional[QDialog] = None
    ):
        """
        Initialize flow charts window.

        Args:
            currency: Currency symbol (BTC, ETH).
            repository: Database repository for querying flow metrics.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currency = currency
        self.repository = repository
        self.current_expiration = None
        self._setup_ui()
        self._load_expirations()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        # Window properties
        self.setWindowTitle(f"Buy/Sell Flow Charts - {self.currency}")
        self.setWindowState(Qt.WindowState.WindowMaximized)
        self.setModal(True)
        self.setStyleSheet(f"background-color: {Colors.BACKGROUND_PRIMARY};")

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Controls section
        controls = QHBoxLayout()

        controls_label = QLabel("Expiration:")
        controls_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px;")
        controls.addWidget(controls_label)

        self.expiration_combo = QComboBox()
        self.expiration_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 12px;
                min-width: 200px;
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox:hover {{
                border-color: {Colors.ACCENT};
            }}
        """)
        self.expiration_combo.currentIndexChanged.connect(self._on_expiration_changed)
        controls.addWidget(self.expiration_combo)

        controls.addStretch()

        # Info button
        info_btn = QPushButton("ℹ️ Chart Info")
        info_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        info_btn.clicked.connect(self._show_chart_info)
        controls.addWidget(info_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        close_btn.clicked.connect(self.close)
        controls.addWidget(close_btn)

        layout.addLayout(controls)

        # Tab widget for charts
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                background-color: {Colors.SURFACE};
            }}
            QTabBar::tab {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                padding: 8px 16px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
                border-bottom-color: {Colors.ACCENT};
            }}
            QTabBar::tab:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)

        # Create chart views with expanding size policy
        self.distribution_view = QWebEngineView()
        self.distribution_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.net_flow_view = QWebEngineView()
        self.net_flow_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.trend_view = QWebEngineView()
        self.trend_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Add tabs
        self.tab_widget.addTab(self.distribution_view, "Distribution by Strike")
        self.tab_widget.addTab(self.net_flow_view, "Net Flow by Strike")
        self.tab_widget.addTab(self.trend_view, "Flow Trend Over Time")

        layout.addWidget(self.tab_widget, 1)

    def _load_expirations(self) -> None:
        """Load active expirations from database."""
        try:
            expirations = self.repository.get_active_expirations_with_flow(self.currency)

            self.expiration_combo.clear()

            # Always add aggregated view as first option
            self.expiration_combo.addItem("🌐 All Expirations", "__ALL__")

            if not expirations:
                self.expiration_combo.addItem("No data available", None)
                logger.warning(f"No active expirations with flow data found for {self.currency}")
                return

            for exp in expirations:
                label = f"{exp['expiration']} (OI: {exp['total_oi']:,.0f})"
                self.expiration_combo.addItem(label, exp['expiration'])

            # Load default view: "All Expirations" is always index 0
            self._on_expiration_changed(0)

        except Exception as e:
            logger.error(f"Failed to load expirations: {e}")
            self.expiration_combo.addItem("Error loading data", None)

    def _on_expiration_changed(self, index: int) -> None:
        """
        Regenerate charts when expiration changes.

        Routes to aggregate charts when 'All Expirations' is selected.

        Args:
            index: Combo box index.
        """
        expiration = self.expiration_combo.itemData(index)
        if not expiration:
            return

        self.current_expiration = expiration

        if expiration == "__ALL__":
            logger.info("Loading aggregated charts for all expirations")
            self._generate_aggregate_charts()
        else:
            logger.info(f"Loading charts for {expiration}")
            self._generate_charts_from_db(expiration)

    def _generate_charts_from_db(self, expiration: str) -> None:
        """
        Query database and generate all three charts.

        Steps:
        1. Query latest flow_metrics for expiration
        2. Reconstruct flow_data structure
        3. Generate charts using chart_generator functions
        4. Load HTML into QWebEngineViews

        Args:
            expiration: Expiration date string.
        """
        try:
            # Get metrics from database
            logger.info(f"Fetching flow metrics for {self.currency} {expiration}")
            metrics = self.repository.get_flow_metrics(self.currency, expiration)

            if not metrics or not metrics.get("flow_data"):
                logger.warning(f"No flow data found for {self.currency} {expiration}")
                self._show_empty_charts()
                return

            logger.info(f"Found flow data with {len(metrics.get('flow_data', {}))} strikes")

            # Generate charts
            from coding.core.analytics.chart_generator import (
                generate_flow_distribution_chart,
                generate_net_flow_chart,
                generate_flow_trend_chart,
            )

            spot_price = metrics.get("spot_price", 0)
            logger.info(f"Generating charts for spot price: {spot_price}")

            # Chart A: Per-strike distribution
            logger.info("Generating distribution chart...")
            fig_dist = generate_flow_distribution_chart(
                flow_data=metrics,
                spot_price=spot_price,
                currency=self.currency,
                expiration=expiration
            )

            # Chart B: Net flow
            logger.info("Generating net flow chart...")
            fig_net = generate_net_flow_chart(
                flow_data=metrics,
                spot_price=spot_price,
                currency=self.currency,
                expiration=expiration
            )

            # Chart C: Trend over time
            logger.info("Generating trend chart...")
            fig_trend = generate_flow_trend_chart(
                repository=self.repository,
                currency=self.currency,
                expiration=expiration,
                lookback_days=7
            )

            logger.info("All charts generated successfully")

            # Save to temp files and load with responsive configuration
            temp_dir = Path(tempfile.gettempdir()) / "flow_charts"
            temp_dir.mkdir(exist_ok=True)

            dist_path = temp_dir / f"dist_{self.currency}_{expiration}.html"
            net_path = temp_dir / f"net_{self.currency}_{expiration}.html"
            trend_path = temp_dir / f"trend_{self.currency}_{expiration}.html"

            # Write charts with minimal config to ensure they load
            fig_dist.write_html(str(dist_path))
            fig_net.write_html(str(net_path))
            fig_trend.write_html(str(trend_path))

            logger.info(f"Charts saved to {temp_dir}")

            # Add hover highlighting via post-processing (optional)
            inject_hover_js(dist_path)
            inject_hover_js(net_path)
            inject_hover_js(trend_path)

            # Load charts into web views
            dist_url = QUrl.fromLocalFile(str(dist_path.resolve()))
            net_url = QUrl.fromLocalFile(str(net_path.resolve()))
            trend_url = QUrl.fromLocalFile(str(trend_path.resolve()))

            logger.info(f"Loading chart URLs:")
            logger.info(f"  Distribution: {dist_url.toString()}")
            logger.info(f"  Net Flow: {net_url.toString()}")
            logger.info(f"  Trend: {trend_url.toString()}")

            self.distribution_view.setUrl(dist_url)
            self.net_flow_view.setUrl(net_url)
            self.trend_view.setUrl(trend_url)

            logger.info(f"Charts loaded for {expiration}")

        except Exception as e:
            import traceback
            logger.error(f"Failed to generate charts for {expiration}: {e}")
            logger.error(traceback.format_exc())
            self._show_empty_charts()

    def _generate_aggregate_charts(self) -> None:
        """
        Generate all three charts aggregated across all expirations.

        Uses get_aggregated_flow_metrics for distribution and net flow,
        and expiration=None mode of generate_flow_trend_chart for the trend.
        """
        try:
            from coding.core.analytics.chart_generator import (
                generate_flow_distribution_chart,
                generate_net_flow_chart,
                generate_flow_trend_chart,
            )

            logger.info(f"Fetching aggregated flow metrics for {self.currency}")
            metrics = self.repository.get_aggregated_flow_metrics(self.currency)

            if not metrics or not metrics.get("flow_data"):
                logger.warning(f"No aggregated flow data for {self.currency}")
                self._show_empty_charts()
                return

            spot_price = metrics.get("spot_price", 0)
            label = "All Expirations"

            fig_dist = generate_flow_distribution_chart(
                flow_data=metrics,
                spot_price=spot_price,
                currency=self.currency,
                expiration=label,
            )
            fig_net = generate_net_flow_chart(
                flow_data=metrics,
                spot_price=spot_price,
                currency=self.currency,
                expiration=label,
            )
            fig_trend = generate_flow_trend_chart(
                repository=self.repository,
                currency=self.currency,
                expiration=None,
                lookback_days=7,
            )

            temp_dir = Path(tempfile.gettempdir()) / "flow_charts"
            temp_dir.mkdir(exist_ok=True)

            dist_path = temp_dir / f"dist_{self.currency}_all.html"
            net_path = temp_dir / f"net_{self.currency}_all.html"
            trend_path = temp_dir / f"trend_{self.currency}_all.html"

            fig_dist.write_html(str(dist_path))
            fig_net.write_html(str(net_path))
            fig_trend.write_html(str(trend_path))

            inject_hover_js(dist_path)
            inject_hover_js(net_path)
            inject_hover_js(trend_path)

            self.distribution_view.setUrl(QUrl.fromLocalFile(str(dist_path.resolve())))
            self.net_flow_view.setUrl(QUrl.fromLocalFile(str(net_path.resolve())))
            self.trend_view.setUrl(QUrl.fromLocalFile(str(trend_path.resolve())))

            logger.info("Aggregated charts loaded successfully")

        except Exception as e:
            import traceback
            logger.error(f"Failed to generate aggregate charts: {e}")
            logger.error(traceback.format_exc())
            self._show_empty_charts()

    def _show_empty_charts(self) -> None:
        """Show empty state when no data is available."""
        empty_html = f"""
        <html>
        <head><style>
            body {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_MUTED};
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }}
        </style></head>
        <body>
            <div>No data available for this expiration</div>
        </body>
        </html>
        """

        self.distribution_view.setHtml(empty_html)
        self.net_flow_view.setHtml(empty_html)
        self.trend_view.setHtml(empty_html)

    def _show_chart_info(self) -> None:
        """Show detailed information about the current chart."""
        current_tab = self.tab_widget.currentIndex()

        if current_tab == 0:  # Distribution chart
            title = "Distribution by Strike - Chart Guide"
            info = """
<b>What This Chart Shows:</b><br>
Clustered bar chart showing total call and put flow activity across strike prices.<br><br>

<b>Chart Structure:</b><br>
• X-Axis: Strike prices<br>
• Y-Axis: Total flow (notional / volume / trade count)<br>
• <span style='color:#06b6d4'>■ Teal bar</span>: Total Call flow at that strike<br>
• <span style='color:#a855f7'>■ Purple bar</span>: Total Put flow at that strike<br><br>

<b>Metrics Toggle (Top Right):</b><br>
• <b>Notional ($):</b> Dollar value of trades (volume × price)<br>
• <b>Volume:</b> Number of contracts traded<br>
• <b>Trade Count:</b> Number of individual trades<br><br>

<b>Interactive Legend:</b><br>
• <b>Single Click</b> on legend item: Isolate that trace (hides others)<br>
• <b>Double Click</b> on legend item: Toggle that trace on/off<br>
• Click isolated trace again to restore all traces<br><br>

<b>How to Interpret:</b><br>
• <b>Larger bars</b> = More activity at that strike<br>
• <b>Call Buy dominance</b> = Bullish conviction<br>
• <b>Put Buy dominance</b> = Bearish hedging/conviction<br>
• <b>Balanced bars</b> = Market makers providing liquidity<br>
• <b>Clustered strikes</b> = Key price levels with high interest<br><br>

<b>Key Insights:</b><br>
• Spot price shown as yellow marker (current price)<br>
• High put buying near spot = fear of downside<br>
• High call buying above spot = bullish speculation<br>
• Heavy selling at strikes = potential resistance/support
            """

        elif current_tab == 1:  # Net flow chart
            title = "Net Flow by Strike - Chart Guide"
            info = """
<b>What This Chart Shows:</b><br>
Net buying/selling pressure per strike (Buy Volume - Sell Volume).<br><br>

<b>Chart Structure:</b><br>
• Separate bars for Calls and Puts at each strike<br>
• <span style='color:#22c55e'>Green bars</span> = Net Buying (buy > sell)<br>
• <span style='color:#f87171'>Red bars</span> = Net Selling (sell > buy)<br>
• Bar height = magnitude of net flow<br><br>

<b>Interactive Legend:</b><br>
• <b>Single Click</b>: Isolate Call Net Flow or Put Net Flow<br>
• <b>Double Click</b>: Toggle trace on/off<br><br>

<b>How to Interpret:</b><br>
• <b>Tall green call bars</b> = Strong bullish conviction at that strike<br>
• <b>Tall red call bars</b> = Bearish pressure, selling calls<br>
• <b>Tall green put bars</b> = Hedging/bearish positioning<br>
• <b>Tall red put bars</b> = Put selling (bullish, selling protection)<br><br>

<b>Key Patterns:</b><br>
• Net buying at OTM calls + Net selling at OTM puts = Bullish<br>
• Net buying at OTM puts + Net selling at OTM calls = Bearish<br>
• Balanced net flow = Neutral/rangebound market<br><br>

<b>Spot Price Reference:</b><br>
Yellow line indicates current underlying price for context.
            """

        else:  # Trend chart
            title = "Flow Trend Over Time - Chart Guide"
            info = """
<b>What This Chart Shows:</b><br>
Historical buy/sell flow trends over the past 7 days (hourly aggregation).<br><br>

<b>Chart Structure:</b><br>
• Time series showing 4 flows over time<br>
• <span style='color:#00ff88'>Call Buy</span> (bullish aggression)<br>
• <span style='color:#ff4444'>Call Sell</span> (bearish on calls)<br>
• <span style='color:#00d4ff'>Put Buy</span> (bearish/hedging)<br>
• <span style='color:#ff9500'>Put Sell</span> (bullish, selling protection)<br><br>

<b>Interactive Legend:</b><br>
• <b>Single Click</b>: Isolate that flow type<br>
• <b>Double Click</b>: Toggle flow on/off<br><br>

<b>How to Interpret:</b><br>
• <b>Spikes in Call Buy</b> = Sudden bullish interest<br>
• <b>Spikes in Put Buy</b> = Fear events, hedging<br>
• <b>Sustained high Call Buy</b> = Persistent bullish sentiment<br>
• <b>Sustained high Put Sell</b> = Bullish (selling downside protection)<br><br>

<b>Trend Detection:</b><br>
• <b>Accelerating flows</b> = Increasing conviction<br>
• <b>Decelerating flows</b> = Weakening sentiment<br>
• <b>Regime shifts</b> = Flow reversal (buy → sell or vice versa)<br><br>

<b>Use Cases:</b><br>
• Identify when large participants entered/exited positions<br>
• Detect sentiment changes before price moves<br>
• Confirm price moves with flow alignment<br>
• Spot divergences (price up, but put buying increases)
            """

        # Show dialog
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(info)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.setStyleSheet(f"""
            QMessageBox {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_PRIMARY};
            }}
            QMessageBox QLabel {{
                color: {Colors.TEXT_PRIMARY};
                font-size: 12px;
                min-width: 600px;
            }}
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        msg_box.exec()
