"""
Snapshot tab for capturing option chain data by expiration.

Provides interface to:
- Load expiration dates
- Select multiple expirations
- Filter by volume
- Export snapshot to CSV
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QFrame,
    QSizePolicy,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from PySide6.QtCore import Qt, QThread, Signal

from coding.gui.components.log_viewer import LogViewer, GuiLogHandler
from coding.gui.theme.colors import Colors
from coding.service.deribit.deribit_api_service import DeribitApiService


logger = logging.getLogger(__name__)


class SnapshotWorker(QThread):
    """
    Worker thread for fetching snapshot data.

    Fetches book summary and optionally enriches with Greeks from ticker.
    """

    progress = Signal(str)
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        currency: str,
        expirations: List[str],
        min_volume: float,
        fetch_greeks: bool,
        parent: Optional[QWidget] = None
    ):
        """
        Initialize the worker.

        Args:
            currency: Currency symbol (ETH, BTC).
            expirations: List of expiration dates to include.
            min_volume: Minimum volume filter (0 = no filter).
            fetch_greeks: Whether to fetch Greeks from ticker endpoint.
            parent: Parent widget.
        """
        super().__init__(parent)
        self.currency = currency
        self.expirations = expirations
        self.min_volume = min_volume
        self.fetch_greeks = fetch_greeks

    def run(self) -> None:
        """Execute the snapshot data fetch."""
        try:
            with DeribitApiService() as service:
                self.progress.emit(f"Fetching book summary for {self.currency} options...")

                # Get all options book summary
                all_data = service.get_book_summary(
                    currency=self.currency,
                    kind="option"
                )

                self.progress.emit(f"Received {len(all_data)} instruments")

                # Filter by expiration dates
                filtered_data = []
                for item in all_data:
                    instrument_name = item.get("instrument_name", "")
                    # Extract expiration from instrument name (e.g., ETH-10JAN25-3400-C)
                    parts = instrument_name.split("-")
                    if len(parts) >= 2:
                        expiry = parts[1]
                        if expiry in self.expirations:
                            filtered_data.append(item)

                self.progress.emit(f"Filtered to {len(filtered_data)} instruments for selected expirations")

                # Filter by volume
                if self.min_volume > 0:
                    filtered_data = [
                        item for item in filtered_data
                        if item.get("volume", 0) >= self.min_volume
                    ]
                    self.progress.emit(f"After volume filter: {len(filtered_data)} instruments")

                # Optionally fetch Greeks from ticker
                if self.fetch_greeks and filtered_data:
                    self.progress.emit("Fetching Greeks from ticker...")
                    for i, item in enumerate(filtered_data):
                        try:
                            ticker = service.get_ticker(item["instrument_name"])
                            greeks = ticker.get("greeks", {})
                            item["delta"] = greeks.get("delta")
                            item["gamma"] = greeks.get("gamma")
                            item["vega"] = greeks.get("vega")
                            item["theta"] = greeks.get("theta")
                            item["rho"] = greeks.get("rho")

                            if (i + 1) % 10 == 0:
                                self.progress.emit(f"Fetched Greeks for {i + 1}/{len(filtered_data)} instruments")
                        except Exception as e:
                            logger.warning(f"Failed to fetch Greeks for {item['instrument_name']}: {e}")

                # Sort by instrument name
                filtered_data.sort(key=lambda x: x.get("instrument_name", ""))

                self.finished.emit(filtered_data)

        except Exception as error:
            self.error.emit(str(error))


class SnapshotTab(QWidget):
    """
    Tab widget for capturing option chain snapshots.

    Features:
    - Currency selection
    - Expiration date multi-select
    - Volume filtering
    - Greeks fetching option
    - CSV export
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize the Snapshot tab.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.snapshot_data: List[Dict] = []
        self.worker: Optional[SnapshotWorker] = None

        self._setup_ui()
        self._setup_logging()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header
        header = QLabel("Option Chain Snapshot")
        header.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {Colors.TEXT_PRIMARY};")
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        main_layout.addWidget(header)

        # Controls section
        controls_frame = QFrame()
        controls_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 12px;
            }}
        """)
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(12)

        # Currency row
        currency_row = QHBoxLayout()
        currency_row.setSpacing(12)

        currency_label = QLabel("Currency:")
        currency_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        currency_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        currency_row.addWidget(currency_label)

        self.currency_combo = QComboBox()
        self.currency_combo.addItems(["ETH", "BTC"])
        self.currency_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        currency_row.addWidget(self.currency_combo)

        self.load_expiry_btn = QPushButton("Load Expirations")
        self.load_expiry_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
                padding: 8px 16px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.load_expiry_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        currency_row.addWidget(self.load_expiry_btn)

        controls_layout.addLayout(currency_row)

        # Expiration list with label
        expiry_label = QLabel("Select Expirations:")
        expiry_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        controls_layout.addWidget(expiry_label)

        # Select All checkbox
        self.select_all_checkbox = QCheckBox("Select All")
        self.select_all_checkbox.setStyleSheet(f"""
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
        """)
        controls_layout.addWidget(self.select_all_checkbox)

        # Expiration list widget (multi-select)
        self.expiry_list = QListWidget()
        self.expiry_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.expiry_list.setMaximumHeight(120)
        self.expiry_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {Colors.INPUT_BACKGROUND};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {Colors.ACCENT};
                color: {Colors.BACKGROUND_PRIMARY};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {Colors.SURFACE_HOVER};
            }}
        """)
        self.expiry_list.addItem("Load expirations first...")
        controls_layout.addWidget(self.expiry_list)

        # Filter options row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(16)

        # Minimum volume filter
        volume_label = QLabel("Min Volume:")
        volume_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        filter_row.addWidget(volume_label)

        self.volume_spin = QSpinBox()
        self.volume_spin.setMinimum(0)
        self.volume_spin.setMaximum(1000000)
        self.volume_spin.setValue(0)
        self.volume_spin.setToolTip("0 = no filter")
        self.volume_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        filter_row.addWidget(self.volume_spin)

        filter_row.addStretch()

        # Fetch Greeks checkbox
        self.greeks_checkbox = QCheckBox("Fetch Greeks")
        self.greeks_checkbox.setToolTip("Fetch Greeks from ticker (slower but complete)")
        self.greeks_checkbox.setStyleSheet(f"""
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
        """)
        filter_row.addWidget(self.greeks_checkbox)

        # Save to CSV checkbox
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
        """)
        filter_row.addWidget(self.save_csv_checkbox)

        # Modified format checkbox (checked by default)
        self.modified_format_checkbox = QCheckBox("Modified Format")
        self.modified_format_checkbox.setChecked(True)
        self.modified_format_checkbox.setToolTip("Reorder columns and add USD prices. Uncheck for raw data.")
        self.modified_format_checkbox.setStyleSheet(f"""
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
        """)
        filter_row.addWidget(self.modified_format_checkbox)

        controls_layout.addLayout(filter_row)

        # Action buttons row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.load_snapshot_btn = QPushButton("Load Snapshot")
        self.load_snapshot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_snapshot_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.load_snapshot_btn)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_SECONDARY};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.BORDER};
            }}
            QPushButton:hover {{
                background-color: {Colors.BUTTON_SECONDARY_HOVER};
            }}
        """)
        self.clear_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.clear_btn)

        action_row.addStretch()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        action_row.addWidget(self.status_label)

        controls_layout.addLayout(action_row)

        main_layout.addWidget(controls_frame)

        # Log viewer section
        log_label = QLabel("Output")
        log_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        main_layout.addWidget(log_label)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(100)
        self.log_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(self.log_viewer, 1)

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
        self.load_expiry_btn.clicked.connect(self._load_expirations)
        self.select_all_checkbox.stateChanged.connect(self._toggle_select_all)
        self.load_snapshot_btn.clicked.connect(self._load_snapshot)
        self.clear_btn.clicked.connect(self._clear_all)

    def _load_expirations(self) -> None:
        """Load available expiration dates for selected currency."""
        currency = self.currency_combo.currentText()
        self.log_viewer.log_info(f"Loading expirations for {currency}...")

        self.expiry_list.clear()
        self.load_expiry_btn.setEnabled(False)

        try:
            with DeribitApiService() as service:
                result = service.get_expirations(currency=currency)

                # Get option expirations for the selected currency
                currency_data = result.get(currency.lower(), {})
                option_expirations = currency_data.get("option", [])

                if option_expirations:
                    for expiry in sorted(option_expirations):
                        item = QListWidgetItem(expiry)
                        self.expiry_list.addItem(item)
                    self.log_viewer.log_info(f"Loaded {len(option_expirations)} expirations")
                else:
                    self.expiry_list.addItem("No expirations found")
                    self.log_viewer.log_warning("No option expirations found")

        except Exception as error:
            self.expiry_list.addItem("Error loading expirations")
            self.log_viewer.log_error(f"Failed to load expirations: {error}")

        finally:
            self.load_expiry_btn.setEnabled(True)

    def _toggle_select_all(self, state: int) -> None:
        """Toggle selection of all expiration dates."""
        select_all = state == Qt.CheckState.Checked.value
        for i in range(self.expiry_list.count()):
            item = self.expiry_list.item(i)
            if item and not item.text().startswith(("Load", "No", "Error")):
                item.setSelected(select_all)

    def _get_selected_expirations(self) -> List[str]:
        """Get list of selected expiration dates."""
        selected = []
        for item in self.expiry_list.selectedItems():
            text = item.text()
            if not text.startswith(("Load", "No", "Error")):
                selected.append(text)
        return selected

    def _load_snapshot(self) -> None:
        """Load snapshot data for selected expirations."""
        if self.worker is not None and self.worker.isRunning():
            self.log_viewer.log_warning("A request is already in progress")
            return

        expirations = self._get_selected_expirations()
        if not expirations:
            self.log_viewer.log_warning("Please select at least one expiration date")
            return

        currency = self.currency_combo.currentText()
        min_volume = self.volume_spin.value()
        fetch_greeks = self.greeks_checkbox.isChecked()

        self.log_viewer.log_info(f"Loading snapshot for {len(expirations)} expiration(s)...")
        self.load_snapshot_btn.setEnabled(False)
        self.status_label.setText("Loading...")
        self.status_label.setStyleSheet(f"color: {Colors.WARNING};")

        self.worker = SnapshotWorker(
            currency=currency,
            expirations=expirations,
            min_volume=min_volume,
            fetch_greeks=fetch_greeks
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_snapshot_finished)
        self.worker.error.connect(self._on_snapshot_error)
        self.worker.start()

    def _on_progress(self, message: str) -> None:
        """Handle progress updates."""
        self.log_viewer.log_info(message)

    def _on_snapshot_finished(self, data: List[Dict]) -> None:
        """Handle successful snapshot fetch."""
        self.snapshot_data = data
        self.load_snapshot_btn.setEnabled(True)
        self.status_label.setText(f"Loaded {len(data)} instruments")
        self.status_label.setStyleSheet(f"color: {Colors.SUCCESS};")

        self.log_viewer.log_info(f"Snapshot complete: {len(data)} instruments")

        if data:
            # Log sample data
            sample = data[0]
            self.log_viewer.log_info(f"Sample fields: {list(sample.keys())}")

            # Save to CSV if requested
            if self.save_csv_checkbox.isChecked():
                self._save_to_csv(data)

    def _on_snapshot_error(self, error_message: str) -> None:
        """Handle snapshot fetch error."""
        self.load_snapshot_btn.setEnabled(True)
        self.status_label.setText("Error")
        self.status_label.setStyleSheet(f"color: {Colors.ERROR};")
        self.log_viewer.log_error(f"Snapshot failed: {error_message}")

    def _save_to_csv(self, data: List[Dict]) -> None:
        """Save snapshot data to CSV file."""
        try:
            from coding.core.api.response_parser import ResponseParser

            parser = ResponseParser()
            currency = self.currency_combo.currentText().lower()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            if self.modified_format_checkbox.isChecked():
                # Transform data to modified format
                modified_data = self._transform_to_modified_format(data)
                filename = f"snapshot_{currency}_{timestamp}_modified"
                parser.to_csv(modified_data, filename, "snapshots")
            else:
                # Save raw data
                filename = f"snapshot_{currency}_{timestamp}_raw"
                parser.to_csv(data, filename, "snapshots")

            self.log_viewer.log_info(f"Saved to output/data/snapshots/{filename}.csv")

        except Exception as error:
            self.log_viewer.log_error(f"Failed to save CSV: {error}")

    def _transform_to_modified_format(self, data: List[Dict]) -> List[Dict]:
        """
        Transform raw data to modified format with ordered columns and USD prices.

        Args:
            data: Raw snapshot data.

        Returns:
            Transformed data with ordered columns and calculated USD prices.
        """
        modified_data = []

        for item in data:
            underlying_price = item.get("underlying_price") or 0

            # Calculate USD prices
            bid_price = item.get("bid_price")
            mark_price = item.get("mark_price")
            mid_price = item.get("mid_price")
            ask_price = item.get("ask_price")

            bid_price_usd = (bid_price * underlying_price) if bid_price and underlying_price else None
            mark_price_usd = (mark_price * underlying_price) if mark_price and underlying_price else None
            mid_price_usd = (mid_price * underlying_price) if mid_price and underlying_price else None
            ask_price_usd = (ask_price * underlying_price) if ask_price and underlying_price else None

            # Convert timestamp to human readable
            creation_timestamp = item.get("creation_timestamp")
            if creation_timestamp:
                try:
                    timestamp_readable = datetime.fromtimestamp(creation_timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError, OSError):
                    timestamp_readable = str(creation_timestamp)
            else:
                timestamp_readable = None

            # Build ordered row
            row = {
                "instrument_name": item.get("instrument_name"),
                "bid_price": bid_price,
                "bid_price_usd": round(bid_price_usd, 4) if bid_price_usd else None,
                "mark_price": mark_price,
                "mark_price_usd": round(mark_price_usd, 4) if mark_price_usd else None,
                "mid_price": mid_price,
                "mid_price_usd": round(mid_price_usd, 4) if mid_price_usd else None,
                "ask_price": ask_price,
                "ask_price_usd": round(ask_price_usd, 4) if ask_price_usd else None,
                "open_interest": item.get("open_interest"),
                "underlying_price": underlying_price,
                "volume": item.get("volume"),
                "volume_usd": item.get("volume_usd"),
                "delta": item.get("delta"),
                "gamma": item.get("gamma"),
                "vega": item.get("vega"),
                "theta": item.get("theta"),
                "rho": item.get("rho"),
                "timestamp": timestamp_readable,
            }

            modified_data.append(row)

        return modified_data

    def _clear_all(self) -> None:
        """Clear all data and selections."""
        self.snapshot_data = []
        self.log_viewer.clear_logs()
        self.status_label.setText("")
        self.select_all_checkbox.setChecked(False)
        for i in range(self.expiry_list.count()):
            item = self.expiry_list.item(i)
            if item:
                item.setSelected(False)
