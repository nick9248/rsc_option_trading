"""
Vol Regressor Forward Testing Tile.

Displays: scorecard (hit rate, mean error, bias, n tests) + history table
+ action buttons (Predict BTC/ETH, Verify BTC/ETH).
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from coding.gui.theme.colors import Colors
from coding.service.ml.forward_testing_service import ForwardTestingService

logger = logging.getLogger(__name__)


# ── Workers ───────────────────────────────────────────────────────────────────

class PredictWorker(QThread):
    """Run ForwardTestingService.make_prediction in a background thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent=None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = ForwardTestingService()
            result = service.make_prediction(self.currency)
            if "error" in result:
                self.error.emit(result["error"])
            else:
                self.finished.emit(result)
        except Exception as exc:
            logger.error(f"PredictWorker failed: {exc}", exc_info=True)
            self.error.emit(str(exc))


class VerifyWorker(QThread):
    """Run ForwardTestingService.verify_prediction in a background thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent=None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = ForwardTestingService()
            result = service.verify_prediction(self.currency)
            if "error" in result:
                self.error.emit(result["error"])
            else:
                self.finished.emit(result)
        except Exception as exc:
            logger.error(f"VerifyWorker failed: {exc}", exc_info=True)
            self.error.emit(str(exc))


# ── Tile ──────────────────────────────────────────────────────────────────────

class VolRegressorTile(QFrame):
    """
    Tile for Vol Regressor forward testing.

    Layout (top to bottom):
        Title bar
        Scorecard row (4 stats)
        History table (last 14 rows, all currencies)
        Button row + status label
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list = []  # keep workers alive while running
        self._init_ui()
        self._refresh_data()

    # ── UI init ───────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"background-color: {Colors.SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: 6px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_title())
        layout.addWidget(self._build_scorecard())
        layout.addWidget(self._build_table())
        layout.addWidget(self._build_buttons())

    def _build_title(self) -> QLabel:
        label = QLabel("Vol Regressor — 24h Realized Volatility")
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        label.setFont(font)
        label.setStyleSheet(f"color: {Colors.ACCENT};")
        return label

    def _build_scorecard(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_ELEVATED}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: 4px;"
        )

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        self._stat_labels = {}
        stats = [
            ("hit_rate",   "Hit Rate",   "—"),
            ("mean_error", "Mean Error", "—"),
            ("bias",       "Bias",       "—"),
            ("n_verified", "N Tests",    "0"),
        ]

        for key, title, default in stats:
            col = QFrame()
            col_layout = QVBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(2)
            col_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")

            value_label = QLabel(default)
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = QFont()
            font.setPointSize(14)
            font.setBold(True)
            value_label.setFont(font)
            value_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")

            col_layout.addWidget(title_label)
            col_layout.addWidget(value_label)

            self._stat_labels[key] = value_label
            layout.addWidget(col, stretch=1)

        return frame

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Date", "CCY", "Pred Vol", "Daily +/-1s", "Actual", "Result"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_ELEVATED}; "
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-weight: bold;"
        )
        self._table.setStyleSheet(
            f"background-color: {Colors.INPUT_BACKGROUND}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"gridline-color: {Colors.BORDER}; "
            f"alternate-background-color: {Colors.BACKGROUND_ELEVATED};"
        )
        self._table.setMinimumHeight(220)
        return self._table

    def _build_buttons(self) -> QFrame:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_style = (
            f"background-color: {Colors.BUTTON_PRIMARY}; "
            f"color: #080D18; "
            f"border: none; "
            f"padding: 7px 16px; "
            f"border-radius: 4px; "
            f"font-weight: bold;"
        )
        btn_secondary_style = (
            f"background-color: {Colors.BUTTON_SECONDARY}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"padding: 7px 16px; "
            f"border-radius: 4px;"
        )

        self._btn_predict_btc = QPushButton("Predict BTC")
        self._btn_predict_btc.setStyleSheet(btn_style)
        self._btn_predict_btc.clicked.connect(lambda: self._on_predict("BTC"))

        self._btn_predict_eth = QPushButton("Predict ETH")
        self._btn_predict_eth.setStyleSheet(btn_style)
        self._btn_predict_eth.clicked.connect(lambda: self._on_predict("ETH"))

        self._btn_verify_btc = QPushButton("Verify BTC")
        self._btn_verify_btc.setStyleSheet(btn_secondary_style)
        self._btn_verify_btc.clicked.connect(lambda: self._on_verify("BTC"))

        self._btn_verify_eth = QPushButton("Verify ETH")
        self._btn_verify_eth.setStyleSheet(btn_secondary_style)
        self._btn_verify_eth.clicked.connect(lambda: self._on_verify("ETH"))

        btn_row.addWidget(self._btn_predict_btc)
        btn_row.addWidget(self._btn_predict_eth)
        btn_row.addWidget(self._btn_verify_btc)
        btn_row.addWidget(self._btn_verify_eth)
        btn_row.addStretch()

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")

        layout.addLayout(btn_row)
        layout.addWidget(self._status_label)
        return frame

    # ── Data loading ──────────────────────────────────────────────────────────

    def _refresh_data(self) -> None:
        """Reload scorecard and history from DB and update UI."""
        try:
            service = ForwardTestingService()
            scorecard = service.get_scorecard()
            history = service.get_history(limit=14)
            self._update_scorecard(scorecard)
            self._update_table(history)
        except Exception as exc:
            logger.error(f"Failed to refresh forward testing data: {exc}")
            self._set_status(f"Error loading data: {exc}", error=True)

    def _update_scorecard(self, scorecard: dict) -> None:
        n = scorecard["n_verified"]
        self._stat_labels["n_verified"].setText(str(n))

        if n == 0:
            self._stat_labels["hit_rate"].setText("—")
            self._stat_labels["mean_error"].setText("—")
            self._stat_labels["bias"].setText("—")
        else:
            self._stat_labels["hit_rate"].setText(f"{scorecard['hit_rate']:.1f}%")
            self._stat_labels["mean_error"].setText(f"{scorecard['mean_error']:.1f}%")
            bias = scorecard["bias"]
            sign = "+" if bias >= 0 else ""
            self._stat_labels["bias"].setText(f"{sign}{bias:.1f}%")

    def _update_table(self, rows: list) -> None:
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            date_str = row["predicted_at"].strftime("%b %d %H:%M") if row["predicted_at"] else "—"
            actual_str = f"{row['actual_vol_24h']:.1f}%" if row["actual_vol_24h"] is not None else "—"

            if row["within_1sigma"] is True:
                result_str = "PASS"
            elif row["within_1sigma"] is False:
                result_str = "FAIL"
            else:
                result_str = "pending"

            self._table.setItem(i, 0, QTableWidgetItem(date_str))
            self._table.setItem(i, 1, QTableWidgetItem(row["currency"]))
            self._table.setItem(i, 2, QTableWidgetItem(f"{row['predicted_vol_24h']:.1f}%"))
            self._table.setItem(i, 3, QTableWidgetItem(f"+-{row['predicted_daily_move']:.2f}%"))
            self._table.setItem(i, 4, QTableWidgetItem(actual_str))

            result_item = QTableWidgetItem(result_str)
            if row["within_1sigma"] is True:
                result_item.setForeground(Qt.GlobalColor.green)
            elif row["within_1sigma"] is False:
                result_item.setForeground(Qt.GlobalColor.red)
            else:
                result_item.setForeground(Qt.GlobalColor.gray)
            self._table.setItem(i, 5, result_item)

    # ── Button handlers ───────────────────────────────────────────────────────

    def _on_predict(self, currency: str) -> None:
        self._set_all_buttons_enabled(False)
        self._set_status(f"Running vol regressor for {currency}... (~90s)")
        worker = PredictWorker(currency, parent=self)
        worker.finished.connect(self._on_predict_done)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(lambda _: self._cleanup_worker(worker))
        worker.error.connect(lambda _: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _on_verify(self, currency: str) -> None:
        self._set_all_buttons_enabled(False)
        self._set_status(f"Verifying latest {currency} prediction...")
        worker = VerifyWorker(currency, parent=self)
        worker.finished.connect(self._on_verify_done)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(lambda _: self._cleanup_worker(worker))
        worker.error.connect(lambda _: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _cleanup_worker(self, worker: QThread) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def _on_predict_done(self, result: dict) -> None:
        self._set_all_buttons_enabled(True)
        self._set_status(
            f"Predicted {result['currency']}: "
            f"{result['predicted_vol_24h']:.1f}% vol, "
            f"+-{result['predicted_daily_move']:.2f}% daily move"
        )
        self._refresh_data()

    def _on_verify_done(self, result: dict) -> None:
        self._set_all_buttons_enabled(True)
        outcome = "PASS" if result["within_1sigma"] else "FAIL"
        self._set_status(
            f"Verified {result['currency']}: "
            f"predicted {result['predicted_vol_24h']:.1f}% vs actual {result['actual_vol_24h']:.1f}% "
            f"— {outcome}",
            error=(not result["within_1sigma"])
        )
        self._refresh_data()

    def _on_worker_error(self, message: str) -> None:
        self._set_all_buttons_enabled(True)
        self._set_status(f"Error: {message}", error=True)

    def _set_all_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self._btn_predict_btc, self._btn_predict_eth,
                    self._btn_verify_btc, self._btn_verify_eth):
            btn.setEnabled(enabled)

    def _set_status(self, message: str, error: bool = False) -> None:
        color = Colors.ERROR if error else Colors.TEXT_SECONDARY
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(message)
