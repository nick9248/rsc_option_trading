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
        self.current_filter: str = "all"
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

        # Block filter toggle group
        filter_label = QLabel("Filter:")
        filter_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; margin-left: 16px;")
        controls.addWidget(filter_label)

        self._filter_buttons = {}
        filter_btn_style_active = f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.ACCENT};
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
                font-size: 12px;
            }}
        """
        filter_btn_style_inactive = f"""
            QPushButton {{
                background-color: {Colors.SURFACE};
                color: {Colors.TEXT_SECONDARY};
                border: 1px solid {Colors.BORDER};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
                color: {Colors.TEXT_PRIMARY};
            }}
        """

        for filter_key, filter_label_text in [("all", "All"), ("block", "Block"), ("non_block", "Non-Block")]:
            btn = QPushButton(filter_label_text)
            btn.setStyleSheet(filter_btn_style_active if filter_key == "all" else filter_btn_style_inactive)
            btn.clicked.connect(lambda checked, k=filter_key: self._on_filter_changed(k))
            controls.addWidget(btn)
            self._filter_buttons[filter_key] = (btn, filter_btn_style_active, filter_btn_style_inactive)

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

    def _on_filter_changed(self, filter_mode: str) -> None:
        """
        Update active filter and regenerate charts.

        Args:
            filter_mode: "all", "block", or "non_block".
        """
        self.current_filter = filter_mode

        # Update button styles
        for key, (btn, active_style, inactive_style) in self._filter_buttons.items():
            btn.setStyleSheet(active_style if key == filter_mode else inactive_style)

        # Regenerate charts with new filter
        expiration = self.expiration_combo.currentData()
        if not expiration:
            return

        if expiration == "__ALL__":
            self._generate_aggregate_charts()
        else:
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
                lookback_days=7,
                trade_filter=self.current_filter,
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

        When filter == "all": uses get_aggregated_flow_metrics (fast, pre-aggregated).
        When filter != "all": runs BuySellFlowAnalyzer per expiration and aggregates
        in Python so the block filter can be applied to raw historical_trades.
        """
        try:
            from coding.core.analytics.chart_generator import (
                generate_flow_distribution_chart,
                generate_net_flow_chart,
                generate_flow_trend_chart,
            )
            from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
            from collections import defaultdict

            label = "All Expirations"

            if self.current_filter == "all":
                # Fast path: use pre-aggregated metrics table
                logger.info(f"Fetching aggregated flow metrics for {self.currency}")
                metrics = self.repository.get_aggregated_flow_metrics(self.currency)
            else:
                # Filtered path: re-run BuySellFlowAnalyzer per expiration
                logger.info(
                    f"Fetching per-expiration flow with filter={self.current_filter} for {self.currency}"
                )
                expirations = self.repository.get_active_expirations_with_flow(self.currency)
                if not expirations:
                    logger.warning(f"No active expirations found for {self.currency}")
                    self._show_empty_charts()
                    return

                # Aggregate flow_data across all expirations in Python
                agg_flow: dict = defaultdict(lambda: {
                    "C": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                          "buy_notional": 0.0, "sell_notional": 0.0, "net_flow": 0.0,
                          "buy_sell_ratio": None},
                    "P": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                          "buy_notional": 0.0, "sell_notional": 0.0, "net_flow": 0.0,
                          "buy_sell_ratio": None},
                })
                spot_prices = []

                for exp_info in expirations:
                    exp = exp_info["expiration"]
                    try:
                        analyzer = BuySellFlowAnalyzer(
                            repository=self.repository,
                            currency=self.currency,
                            expiration=exp,
                            spot_price=0.0,  # placeholder — not used for aggregation
                            trade_filter=self.current_filter,
                        )
                        result = analyzer.calculate()
                        exp_flow = result.get("flow_data", {})
                        if result.get("spot_price"):
                            spot_prices.append(result["spot_price"])

                        for strike, type_data in exp_flow.items():
                            for opt_type, vals in type_data.items():
                                target = agg_flow[strike][opt_type]
                                for field in ("buy_count", "sell_count", "buy_volume",
                                              "sell_volume", "buy_notional", "sell_notional"):
                                    target[field] += vals.get(field, 0.0)

                    except Exception as exp_err:
                        logger.warning(f"Skipping {exp} during aggregation: {exp_err}")

                # Recompute net_flow and buy_sell_ratio from aggregated values
                for strike_data in agg_flow.values():
                    for opt_data in strike_data.values():
                        opt_data["net_flow"] = opt_data["buy_volume"] - opt_data["sell_volume"]
                        sv = opt_data["sell_volume"]
                        opt_data["buy_sell_ratio"] = (
                            opt_data["buy_volume"] / sv if sv > 0 else None
                        )

                spot_price = (sum(spot_prices) / len(spot_prices)) if spot_prices else 0.0
                metrics = {"flow_data": dict(agg_flow), "spot_price": spot_price}

            if not metrics or not metrics.get("flow_data"):
                logger.warning(f"No aggregated flow data for {self.currency}")
                self._show_empty_charts()
                return

            spot_price = metrics.get("spot_price", 0)

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
                trade_filter=self.current_filter,
            )

            temp_dir = Path(tempfile.gettempdir()) / "flow_charts"
            temp_dir.mkdir(exist_ok=True)

            filter_suffix = f"_{self.current_filter}" if self.current_filter != "all" else ""
            dist_path = temp_dir / f"dist_{self.currency}_all{filter_suffix}.html"
            net_path = temp_dir / f"net_{self.currency}_all{filter_suffix}.html"
            trend_path = temp_dir / f"trend_{self.currency}_all{filter_suffix}.html"

            fig_dist.write_html(str(dist_path))
            fig_net.write_html(str(net_path))
            fig_trend.write_html(str(trend_path))

            inject_hover_js(dist_path)
            inject_hover_js(net_path)
            inject_hover_js(trend_path)

            self.distribution_view.setUrl(QUrl.fromLocalFile(str(dist_path.resolve())))
            self.net_flow_view.setUrl(QUrl.fromLocalFile(str(net_path.resolve())))
            self.trend_view.setUrl(QUrl.fromLocalFile(str(trend_path.resolve())))

            logger.info(f"Aggregated charts loaded (filter={self.current_filter})")

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
Population pyramid showing call and put flow activity per strike — calls extend right (positive), puts extend left (negative). Each strike has up to 4 bars split by direction and option type.<br><br>

<b>4-Bar Structure per Strike:</b><br>
• <span style='color:#10b981'>■ Call Buying</span> (emerald, right) — aggressive call buyers, bullish conviction<br>
• <span style='color:#f43f5e'>■ Call Selling</span> (rose, right) — writing calls, capping upside or closing longs<br>
• <span style='color:#818cf8'>■ Put Buying</span> (indigo, left) — bearish hedging or directional shorts<br>
• <span style='color:#f59e0b'>■ Put Selling</span> (amber, left) — selling downside protection, bullish premium collection<br><br>

<b>Metric Toggle (Top Right):</b><br>
• <b>Notional ($):</b> Dollar value of trades (contracts × price × multiplier). Best for sizing institutional flow.<br>
• <b>Volume:</b> Number of contracts traded. Best for measuring frequency.<br>
• <b>Trade Count:</b> Number of individual trades. Reveals fragmentation (retail) vs. block activity (institutional).<br><br>

<b>Spot Price Marker:</b><br>
Gold annotation marks the current underlying price. Strikes near spot are most relevant for directional reads.<br><br>

<b>How to Interpret:</b><br>
• <b>Dominant call buying near/above spot</b> = Bullish speculation or delta hedging by dealers<br>
• <b>Dominant put buying near/below spot</b> = Fear, downside hedging, or short positioning<br>
• <b>Large put selling</b> = Institutions selling downside protection (bullish carry trade)<br>
• <b>Symmetric buying + selling at same strike</b> = Market makers providing liquidity, not directional<br>
• <b>Strike clusters with large bars</b> = Key gamma levels; dealers hedge here → price magnetic effect<br><br>

<b>Legend Hover:</b> Hover over a legend item to dim all other traces to 15% opacity for isolation.
            """

        elif current_tab == 1:  # Net flow chart
            title = "Net Flow by Strike - Chart Guide"
            info = """
<b>What This Chart Shows:</b><br>
Net buying/selling pressure per strike — each bar equals Buy Volume minus Sell Volume. Positive bars = net buyers dominated at that strike. Negative bars = net sellers dominated. Calls and puts use separate color-coded traces.<br><br>

<b>4-Trace Color System:</b><br>
• <span style='color:#10b981'>■ Call Buying</span> (emerald) — strikes where call buyers outpaced sellers<br>
• <span style='color:#f43f5e'>■ Call Selling</span> (rose) — strikes where call sellers outpaced buyers<br>
• <span style='color:#818cf8'>■ Put Buying</span> (indigo) — strikes where put buyers outpaced sellers<br>
• <span style='color:#f59e0b'>■ Put Selling</span> (amber) — strikes where put sellers outpaced buyers<br>
Each trace only shows bars where that type had net dominance — absent bars mean the other side won.<br><br>

<b>How to Interpret:</b><br>
• <b>Tall emerald call bars above spot</b> = Bullish speculation; market positioned for upside<br>
• <b>Tall rose call bars</b> = Call writing dominant; expected ceiling or resistance forming<br>
• <b>Tall indigo put bars below spot</b> = Active hedging or directional short positioning<br>
• <b>Tall amber put bars</b> = Put selling (bullish carry); traders collecting premium by selling protection<br><br>

<b>Key Patterns:</b><br>
• Net call buying + Net put selling across strikes = Strong bullish signal<br>
• Net put buying + Net call selling across strikes = Strong bearish signal<br>
• Balanced net flow (small bars everywhere) = Neutral / range-bound market<br>
• Concentrated large net flow at one strike = Potential gamma magnet / pin level<br><br>

<b>Zero Line:</b> Dashed horizontal line — bars above zero = buyers won; below zero = sellers won.<br>
<b>Spot Price:</b> Yellow vertical dashed line marks current underlying price.<br><br>

<b>Legend Hover:</b> Hover over a legend item to isolate that trace.
            """

        else:  # Trend chart
            title = "Flow Trend Over Time - Chart Guide"
            info = """
<b>What This Chart Shows:</b><br>
Hourly aggregated option flow over the past 7 days — how buying and selling pressure evolved over time. Useful for detecting regime shifts, conviction buildup, and sentiment divergences from price action.<br><br>

<b>5 Lines:</b><br>
• <span style='color:#10b981'>── Call Buy</span> (emerald, solid) — call buying volume per hour<br>
• <span style='color:#f43f5e'>── Call Sell</span> (rose, solid) — call selling volume per hour<br>
• <span style='color:#a78bfa'>- - Put Buy</span> (violet, dashed) — put buying volume per hour<br>
• <span style='color:#fb923c'>- - Put Sell</span> (orange, dashed) — put selling volume per hour<br>
• <span style='color:#60a5fa'>━━ Net Flow</span> (blue, thick) — net direction = (call buy + put buy) − (call sell + put sell)<br><br>

<b>How to Interpret:</b><br>
• <b>Spikes in Call Buy</b> = Sudden bullish interest, often ahead of moves or on catalysts<br>
• <b>Spikes in Put Buy</b> = Fear events, tail-risk hedging demand<br>
• <b>Sustained high Call Buy</b> = Persistent bullish conviction accumulating over hours/days<br>
• <b>Sustained high Put Sell</b> = Carry trade — traders systematically collecting premium by selling puts<br>
• <b>Net Flow crossing above zero</b> = Market tilting net bullish in that window<br>
• <b>Net Flow crossing below zero</b> = Market tilting net bearish<br><br>

<b>Regime Detection:</b><br>
• <b>Accelerating flows</b> = Growing conviction; follow the direction<br>
• <b>Decelerating flows</b> = Weakening sentiment; potential reversal ahead<br>
• <b>Flow reversal (buy → sell)</b> = Regime change; smart money repositioning<br>
• <b>Divergence</b> (price rising but put buying increases) = Hedged rally; participants cautious despite the move<br><br>

<b>Timeframe:</b> Each data point = 1 hour aggregated. X-axis spans the last 7 calendar days.<br><br>

<b>Legend Hover:</b> Hover a legend item to isolate that line. Double-click to toggle it on/off.
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
