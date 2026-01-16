"""
Database tab for capturing and visualizing on-chain data.

Provides a grid of tiles for capturing different data types
(snapshots, max pain, OI, volume, levels) and displaying charts.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
    QSizePolicy,
    QScrollArea,
)
from PySide6.QtCore import Qt, QThread, Signal

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.theme.colors import Colors
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics import chart_generator
from coding.core.database import DatabaseRepository

logger = logging.getLogger(__name__)


class CaptureWorker(QThread):
    """
    Worker thread for capturing data to database and generating charts.
    """

    progress = Signal(str)
    finished = Signal(str, int, list)  # (capture_type, count, chart_paths)
    error = Signal(str)

    def __init__(
        self,
        capture_type: str,
        currency: str,
        parent: Optional[QWidget] = None
    ):
        """
        Initialize the capture worker.

        Args:
            capture_type: Type of capture (snapshot, max_pain, open_interest, volume, levels).
            currency: Currency symbol (ETH, BTC).
            parent: Parent widget.
        """
        super().__init__(parent)
        self.capture_type = capture_type
        self.currency = currency

    def run(self) -> None:
        """Execute the data capture and chart generation."""
        try:
            repo = DatabaseRepository()
            captured_at = datetime.now()
            chart_paths = []

            with DeribitApiService() as service:
                self.progress.emit(f"Fetching data for {self.currency}...")

                # Fetch book summary
                all_data = service.get_book_summary(
                    currency=self.currency,
                    kind="option"
                )

                self.progress.emit(f"Received {len(all_data)} instruments")

                # Create analyzer
                analyzer = OnChainAnalyzer(all_data, self.currency)
                analyzer.parse_instruments()

                if self.capture_type == "snapshot":
                    count = self._capture_snapshot(repo, all_data, captured_at)
                    chart_paths = self._generate_snapshot_charts(analyzer)

                elif self.capture_type == "max_pain":
                    count = self._capture_max_pain(repo, analyzer, captured_at)
                    chart_paths = self._generate_max_pain_charts(repo, analyzer)

                elif self.capture_type == "open_interest":
                    count = self._capture_open_interest(repo, analyzer, captured_at)
                    chart_paths = self._generate_oi_charts(repo, analyzer)

                elif self.capture_type == "volume":
                    count = self._capture_volume(repo, analyzer, captured_at)
                    chart_paths = self._generate_volume_charts(repo, analyzer)

                elif self.capture_type == "levels":
                    count = self._capture_levels(repo, analyzer, service, captured_at)
                    chart_paths = self._generate_levels_charts(repo, analyzer)

                elif self.capture_type == "gex_dex":
                    count = self._capture_gex_dex(repo, analyzer, service, captured_at)
                    chart_paths = self._generate_gex_dex_charts(repo, analyzer)

                else:
                    raise ValueError(f"Unknown capture type: {self.capture_type}")

                self.finished.emit(self.capture_type, count, chart_paths)

        except Exception as e:
            logger.exception(f"Error during {self.capture_type} capture")
            self.error.emit(str(e))

    def _capture_snapshot(
        self,
        repo: DatabaseRepository,
        data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture raw snapshot data."""
        self.progress.emit("Saving snapshot to database...")
        return repo.save_snapshot(self.currency, data, captured_at)

    def _capture_max_pain(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer,
        captured_at: datetime
    ) -> int:
        """Capture max pain for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.progress.emit(f"Calculating max pain for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if analysis and analysis["max_pain"]["max_pain_strike"]:
                repo.save_max_pain(
                    currency=self.currency,
                    expiration=exp,
                    max_pain_strike=analysis["max_pain"]["max_pain_strike"],
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += 1

        return count

    def _capture_open_interest(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer,
        captured_at: datetime
    ) -> int:
        """Capture open interest for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.progress.emit(f"Calculating OI for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if analysis:
                pcr = analysis["put_call_ratio"]
                repo.save_open_interest(
                    currency=self.currency,
                    expiration=exp,
                    total_call_oi=pcr["total_call_oi"],
                    total_put_oi=pcr["total_put_oi"],
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += 1

        return count

    def _capture_volume(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer,
        captured_at: datetime
    ) -> int:
        """Capture volume for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.progress.emit(f"Calculating volume for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if analysis:
                vol = analysis["volume_stats"]
                repo.save_volume(
                    currency=self.currency,
                    expiration=exp,
                    total_call_volume=vol["total_call_volume"],
                    total_put_volume=vol["total_put_volume"],
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += 1

        return count

    def _capture_levels(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer,
        service: DeribitApiService,
        captured_at: datetime
    ) -> int:
        """Capture support/resistance levels for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.progress.emit(f"Calculating levels for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if not analysis:
                continue

            sr = analysis["support_resistance"]
            levels = []

            # Add resistance levels
            for i, level in enumerate(sr.get("resistance_levels", []), 1):
                levels.append({
                    "level_type": f"resistance_{i}",
                    "strike": level["strike"],
                    "value": level["call_oi"]
                })

            # Add support levels
            for i, level in enumerate(sr.get("support_levels", []), 1):
                levels.append({
                    "level_type": f"support_{i}",
                    "strike": level["strike"],
                    "value": level["put_oi"]
                })

            # Add short-term levels
            if sr.get("short_term_resistance"):
                levels.append({
                    "level_type": "short_term_resistance",
                    "strike": sr["short_term_resistance"]["strike"],
                    "value": sr["short_term_resistance"]["call_oi"]
                })

            if sr.get("short_term_support"):
                levels.append({
                    "level_type": "short_term_support",
                    "strike": sr["short_term_support"]["strike"],
                    "value": sr["short_term_support"]["put_oi"]
                })

            if levels:
                repo.save_levels(
                    currency=self.currency,
                    expiration=exp,
                    levels=levels,
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += len(levels)

        return count

    def _generate_max_pain_charts(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer
    ) -> List[str]:
        """Generate max pain trend charts for all expirations with data."""
        chart_paths = []
        expirations = repo.get_available_expirations(self.currency, "max_pain")

        for exp in expirations:
            self.progress.emit(f"Generating max pain chart for {exp}...")
            data = repo.get_max_pain_history(self.currency, exp)

            if len(data) >= 2:  # Need at least 2 points for a trend
                path = chart_generator.generate_max_pain_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths

    def _generate_oi_charts(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer
    ) -> List[str]:
        """Generate OI trend and P/C ratio charts for all expirations with data."""
        chart_paths = []
        expirations = repo.get_available_expirations(self.currency, "open_interest")

        for exp in expirations:
            self.progress.emit(f"Generating OI charts for {exp}...")
            data = repo.get_open_interest_history(self.currency, exp)

            if len(data) >= 2:
                # OI trend chart
                path = chart_generator.generate_open_interest_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

                # P/C ratio chart
                path = chart_generator.generate_pc_ratio_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths

    def _generate_volume_charts(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer
    ) -> List[str]:
        """Generate volume trend charts for all expirations with data."""
        chart_paths = []
        expirations = repo.get_available_expirations(self.currency, "volume")

        for exp in expirations:
            self.progress.emit(f"Generating volume chart for {exp}...")
            data = repo.get_volume_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_volume_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths

    def _generate_levels_charts(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer
    ) -> List[str]:
        """Generate levels trend charts for all expirations with data."""
        chart_paths = []
        expirations = repo.get_available_expirations(self.currency, "levels")

        for exp in expirations:
            self.progress.emit(f"Generating levels chart for {exp}...")
            data = repo.get_levels_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_levels_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths

    def _generate_snapshot_charts(
        self,
        analyzer: OnChainAnalyzer
    ) -> List[str]:
        """Generate OI and Volume distribution charts for each expiration."""
        chart_paths = []
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.progress.emit(f"Generating snapshot charts for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if not analysis:
                continue

            # Use strike_data from analysis which has call_oi, put_oi, call_volume, put_volume
            strike_data = analysis.get("strike_data", {})

            # Generate OI distribution chart
            max_pain_strike = analysis["max_pain"]["max_pain_strike"] or 0
            path = chart_generator.generate_snapshot_oi_distribution(
                strike_data,
                self.currency,
                exp,
                analyzer.underlying_price,
                max_pain_strike
            )
            if path:
                chart_paths.append(path)

            # Generate Volume distribution chart
            path = chart_generator.generate_snapshot_volume_distribution(
                strike_data,
                self.currency,
                exp,
                analyzer.underlying_price
            )
            if path:
                chart_paths.append(path)

        return chart_paths

    def _capture_gex_dex(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer,
        service: DeribitApiService,
        captured_at: datetime
    ) -> int:
        """Capture GEX/DEX data for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.progress.emit(f"Calculating GEX/DEX for {exp}...")

            # Get instruments for this expiration with Greeks
            instruments = analyzer.parsed_data.get(exp, [])
            if not instruments:
                continue

            # Fetch Greeks for each instrument
            instruments_with_greeks = []
            for inst in instruments:
                instrument_name = inst.get("instrument_name")
                if not instrument_name:
                    continue

                try:
                    ticker = service.get_ticker(instrument_name)
                    greeks = ticker.get("greeks", {})

                    instruments_with_greeks.append({
                        "instrument_name": instrument_name,
                        "strike": inst["strike"],
                        "option_type": inst["option_type"],
                        "open_interest": inst["open_interest"],
                        "gamma": greeks.get("gamma"),
                        "delta": greeks.get("delta"),
                    })
                except Exception as e:
                    logger.debug(f"Failed to get Greeks for {instrument_name}: {e}")
                    continue

            if not instruments_with_greeks:
                continue

            # Calculate GEX/DEX
            calculator = GexDexCalculator(instruments_with_greeks, analyzer.underlying_price)
            result = calculator.calculate()

            key_levels = result.get("key_levels", {})
            call_res = key_levels.get("call_resistance")
            put_sup = key_levels.get("put_support")

            repo.save_gex_dex(
                currency=self.currency,
                expiration=exp,
                total_net_gex=result.get("total_net_gex", 0),
                total_net_dex=result.get("total_net_dex", 0),
                call_resistance_strike=call_res["strike"] if call_res else None,
                call_resistance_gex=call_res["net_gex"] if call_res else None,
                put_support_strike=put_sup["strike"] if put_sup else None,
                put_support_gex=put_sup["net_gex"] if put_sup else None,
                hvl_strike=key_levels.get("hvl"),
                underlying_price=analyzer.underlying_price,
                captured_at=captured_at
            )
            count += 1

        return count

    def _generate_gex_dex_charts(
        self,
        repo: DatabaseRepository,
        analyzer: OnChainAnalyzer
    ) -> List[str]:
        """Generate GEX/DEX trend charts for all expirations with data."""
        chart_paths = []
        expirations = repo.get_available_expirations(self.currency, "gex_dex")

        for exp in expirations:
            self.progress.emit(f"Generating GEX/DEX chart for {exp}...")
            data = repo.get_gex_dex_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_gex_dex_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths


class CaptureTile(QFrame):
    """
    A tile widget for capturing a specific data type.
    """

    capture_clicked = Signal(str)  # Emits capture type

    def __init__(
        self,
        title: str,
        capture_type: str,
        description: str,
        parent: Optional[QWidget] = None
    ):
        """
        Initialize the capture tile.

        Args:
            title: Display title for the tile.
            capture_type: Type identifier for capture.
            description: Short description of what's captured.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.capture_type = capture_type
        self._setup_ui(title, description)

    def _setup_ui(self, title: str, description: str) -> None:
        """Set up the tile UI."""
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
            QFrame:hover {{
                border-color: {Colors.ACCENT};
            }}
        """)
        self.setMinimumSize(150, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet(f"""
            font-size: 16px;
            font-weight: 600;
            color: {Colors.TEXT_PRIMARY};
        """)
        layout.addWidget(title_label)

        # Description
        desc_label = QLabel(description)
        desc_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 12px;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addStretch()

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self.status_label)

        # Chart status label
        self.chart_label = QLabel("")
        self.chart_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
        self.chart_label.setWordWrap(True)
        layout.addWidget(self.chart_label)

        # Capture button
        self.capture_btn = QPushButton("Capture & Chart")
        self.capture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.capture_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.ACCENT};
                color: {Colors.BACKGROUND_PRIMARY};
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {Colors.ACCENT_HOVER};
            }}
            QPushButton:disabled {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_MUTED};
            }}
        """)
        self.capture_btn.clicked.connect(self._on_capture_click)
        layout.addWidget(self.capture_btn)

    def _on_capture_click(self) -> None:
        """Handle capture button click."""
        self.capture_clicked.emit(self.capture_type)

    def set_capturing(self, capturing: bool) -> None:
        """Set the tile to capturing state."""
        self.capture_btn.setEnabled(not capturing)
        if capturing:
            self.status_label.setText("Capturing...")
            self.status_label.setStyleSheet(f"color: {Colors.WARNING}; font-size: 11px;")
            self.chart_label.setText("")
        else:
            self.status_label.setText("")

    def set_success(self, count: int, chart_count: int = 0) -> None:
        """Set success status."""
        self.status_label.setText(f"Captured {count} records")
        self.status_label.setStyleSheet(f"color: {Colors.SUCCESS}; font-size: 11px;")

        if chart_count > 0:
            self.chart_label.setText(f"Generated {chart_count} chart(s)")
            self.chart_label.setStyleSheet(f"color: {Colors.ACCENT}; font-size: 10px;")
        else:
            self.chart_label.setText("Need more data for charts")
            self.chart_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")

    def set_error(self, message: str) -> None:
        """Set error status."""
        self.status_label.setText(f"Error: {message[:30]}...")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR}; font-size: 11px;")
        self.chart_label.setText("")


class DatabaseTab(QWidget):
    """
    Tab widget for database operations and visualization.

    Features:
    - Grid of capture tiles for different data types
    - Currency selection
    - Charts for visualizing historical data
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the Database tab.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.worker: Optional[CaptureWorker] = None
        self.tiles: Dict[str, CaptureTile] = {}
        self._capture_queue: List[str] = []
        self._capture_all_in_progress: bool = False

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Scroll area for responsiveness
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        # Content widget inside scroll area
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(12)

        # Header
        header = QLabel("Database Capture & Charts")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(header)

        # Controls row
        controls_frame = QFrame()
        controls_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        controls_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(12, 8, 12, 8)

        # Currency selector
        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        currency_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["ETH", "BTC"])
        self.currency_combo.setMinimumWidth(80)
        self.currency_combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.currency_combo)

        controls_layout.addStretch()

        # Open charts folder button
        self.open_charts_btn = QPushButton("Open Charts")
        self.open_charts_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                padding: 6px 12px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.open_charts_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.open_charts_btn)

        # Capture all button
        self.capture_all_btn = QPushButton("Capture All")
        self.capture_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                padding: 6px 12px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.capture_all_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.capture_all_btn)

        content_layout.addWidget(controls_frame)

        # Tiles grid
        tiles_frame = QFrame()
        tiles_frame.setStyleSheet("background: transparent; border: none;")
        tiles_layout = QGridLayout(tiles_frame)
        tiles_layout.setSpacing(8)
        tiles_layout.setContentsMargins(0, 0, 0, 0)

        # Create tiles
        tile_configs = [
            ("Snapshot", "snapshot", "Capture raw book summary data"),
            ("Max Pain", "max_pain", "Record max pain + trend chart"),
            ("Open Interest", "open_interest", "Track OI + P/C ratio charts"),
            ("Volume", "volume", "Track volume + trend charts"),
            ("Levels", "levels", "S/R levels + trend charts"),
            ("GEX/DEX", "gex_dex", "Gamma/Delta exposure trends"),
        ]

        for i, (title, capture_type, desc) in enumerate(tile_configs):
            tile = CaptureTile(title, capture_type, desc)
            tile.capture_clicked.connect(self._on_tile_capture)
            self.tiles[capture_type] = tile
            row, col = divmod(i, 3)  # 3 columns
            tiles_layout.addWidget(tile, row, col)

        content_layout.addWidget(tiles_frame)

        # Log viewer section
        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        log_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(100)
        self.log_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content_layout.addWidget(self.log_viewer, 1)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

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
        self.capture_all_btn.clicked.connect(self._on_capture_all)
        self.open_charts_btn.clicked.connect(self._on_open_charts_folder)

    def _on_tile_capture(self, capture_type: str) -> None:
        """Handle single tile capture."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A capture is already in progress")
            return

        currency = self.currency_combo.currentText()
        self.tiles[capture_type].set_capturing(True)

        self.log_viewer.log_info(f"Starting {capture_type} capture for {currency}...")

        self.worker = CaptureWorker(capture_type, currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_capture_finished)
        self.worker.error.connect(self._on_capture_error)
        self.worker.start()

    def _on_capture_all(self) -> None:
        """Handle capture all button click - sequentially capture all types."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A capture is already in progress")
            return

        # Start sequential capture
        self._capture_queue = ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
        self._capture_all_in_progress = True
        self.capture_all_btn.setEnabled(False)
        self.log_viewer.log_info("Starting Capture All...")
        self._start_next_capture()

    def _start_next_capture(self) -> None:
        """Start the next capture in the queue."""
        if not self._capture_queue:
            # All captures complete
            self._capture_all_in_progress = False
            self.capture_all_btn.setEnabled(True)
            self.log_viewer.log_info("Capture All completed!")
            return

        capture_type = self._capture_queue.pop(0)
        currency = self.currency_combo.currentText()
        self.tiles[capture_type].set_capturing(True)

        self.log_viewer.log_info(f"[Capture All] Starting {capture_type} for {currency}...")

        self.worker = CaptureWorker(capture_type, currency)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_capture_finished)
        self.worker.error.connect(self._on_capture_error)
        self.worker.start()

    def _on_open_charts_folder(self) -> None:
        """Open the charts output folder."""
        import os
        import subprocess
        from pathlib import Path

        charts_dir = Path(__file__).parent.parent.parent.parent / "output" / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":  # Windows
            os.startfile(str(charts_dir))
        elif os.name == "posix":  # macOS/Linux
            subprocess.run(["open" if os.uname().sysname == "Darwin" else "xdg-open", str(charts_dir)])

        self.log_viewer.log_info(f"Opened charts folder: {charts_dir}")

    def _on_progress(self, message: str) -> None:
        """Handle progress updates."""
        self.log_viewer.log_info(message)

    def _on_capture_finished(self, capture_type: str, count: int, chart_paths: List[str]) -> None:
        """Handle successful capture."""
        self.tiles[capture_type].set_capturing(False)
        self.tiles[capture_type].set_success(count, len(chart_paths))

        self.log_viewer.log_info(f"{capture_type} capture complete: {count} records saved")

        if chart_paths:
            self.log_viewer.log_info(f"Generated {len(chart_paths)} chart(s):")
            for path in chart_paths:
                self.log_viewer.log_info(f"  - {path}")
        elif capture_type != "snapshot":
            self.log_viewer.log_info("Need at least 2 data points to generate trend charts")

        # Continue with queue if in Capture All mode
        if self._capture_all_in_progress:
            self._start_next_capture()

    def _on_capture_error(self, error_message: str) -> None:
        """Handle capture error."""
        # Find which tile was capturing
        for tile in self.tiles.values():
            if not tile.capture_btn.isEnabled():
                tile.set_capturing(False)
                tile.set_error(error_message)
                break

        self.log_viewer.log_error(f"Capture failed: {error_message}")

        # Continue with queue if in Capture All mode
        if self._capture_all_in_progress:
            self._start_next_capture()
