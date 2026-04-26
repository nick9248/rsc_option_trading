"""
Displacement Scanner tab.

Replaces the old OTM Contracts view (index 6). Shows current prices,
last alert, configuration, and a run button. Auto-refreshes every 5 minutes.
"""
import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSpinBox, QCheckBox, QDialog,
)

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


class ScanWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    prices_updated = Signal(dict)

    def __init__(self, service, assets: list[str]):
        super().__init__()
        self._service = service
        self._assets = assets

    def run(self) -> None:
        try:
            prices = {a: self._service.get_current_prices(a) for a in self._assets}
            self.prices_updated.emit(prices)
            signals = self._service.scan(self._assets)
            self.finished.emit(signals)
        except Exception as e:
            self.error.emit(str(e))


class DisplacementTab(QWidget):
    """
    Simple displacement scanner tab. Single panel, no collapsible sections.
    Shows current prices, last alert, config, and a run button.
    """

    def __init__(self, scanner_service, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._service = scanner_service
        self._worker: Optional[ScanWorker] = None
        self._last_signals: list[DisplacementSignal] = []
        self._init_ui()
        self._start_auto_refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("DISPLACEMENT SCANNER")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Asset selector
        asset_row = QHBoxLayout()
        asset_label = QLabel("Asset:")
        asset_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        asset_row.addWidget(asset_label)
        self._btn_btc = self._make_toggle_btn("BTC", True)
        self._btn_eth = self._make_toggle_btn("ETH", True)
        asset_row.addWidget(self._btn_btc)
        asset_row.addWidget(self._btn_eth)
        asset_row.addStretch()
        layout.addLayout(asset_row)

        # Current conditions
        layout.addWidget(self._make_section_label("CURRENT CONDITIONS"))
        self._conditions_frame = self._make_conditions_panel()
        layout.addWidget(self._conditions_frame)

        # Last alert
        layout.addWidget(self._make_section_label("LAST ALERT"))
        self._alert_frame = self._make_alert_panel()
        layout.addWidget(self._alert_frame)

        # Configuration
        layout.addWidget(self._make_section_label("CONFIGURATION"))
        layout.addWidget(self._make_config_panel())

        # Run button + status
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("RUN SCAN NOW")
        self._run_btn.setFixedHeight(36)
        self._run_btn.setStyleSheet(
            f"background-color: {Colors.ACCENT}; color: white; "
            f"font-weight: bold; border-radius: 4px; padding: 0 16px;"
        )
        self._run_btn.clicked.connect(self._run_scan)
        btn_row.addWidget(self._run_btn)
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        self.setLayout(layout)

    def _make_section_label(self, text: str) -> QLabel:
        lbl = QLabel(f"── {text} ──────────────────────────")
        lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        return lbl

    def _make_toggle_btn(self, text: str, checked: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setFixedSize(60, 28)
        return btn

    def _make_conditions_panel(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        self._btc_label = QLabel("BTC   Loading…")
        self._eth_label = QLabel("ETH   Loading…")
        for lbl in (self._btc_label, self._eth_label):
            lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-family: monospace;")
            layout.addWidget(lbl)
        layout.addStretch()
        return frame

    def _make_alert_panel(self) -> QFrame:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        self._alert_summary_label = QLabel("No alerts yet")
        self._alert_summary_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        self._alert_summary_label.setWordWrap(True)
        self._breakdown_btn = QPushButton("VIEW BREAKDOWN")
        self._breakdown_btn.setVisible(False)
        self._breakdown_btn.setFixedWidth(160)
        self._breakdown_btn.clicked.connect(self._show_breakdown)
        layout.addWidget(self._alert_summary_label)
        layout.addWidget(self._breakdown_btn)
        return frame

    def _make_config_panel(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Drop threshold 24h:"))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(5, 50)
        self._threshold_spin.setValue(20)
        self._threshold_spin.setSuffix("%")
        self._threshold_spin.setFixedWidth(70)
        layout.addWidget(self._threshold_spin)

        layout.addWidget(QLabel("  Min conviction:"))
        self._conviction_spin = QSpinBox()
        self._conviction_spin.setRange(30, 95)
        self._conviction_spin.setValue(50)
        self._conviction_spin.setSuffix("%")
        self._conviction_spin.setFixedWidth(70)
        layout.addWidget(self._conviction_spin)

        layout.addWidget(QLabel("  Telegram alerts:"))
        self._telegram_check = QCheckBox()
        self._telegram_check.setChecked(True)
        layout.addWidget(self._telegram_check)

        layout.addStretch()
        return frame

    def _run_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        assets = []
        if self._btn_btc.isChecked():
            assets.append("BTC")
        if self._btn_eth.isChecked():
            assets.append("ETH")
        if not assets:
            self._status_label.setText("Select at least one asset")
            return

        self._run_btn.setEnabled(False)
        self._run_btn.setText("Scanning…")
        self._status_label.setText("Scanning…")

        self._worker = ScanWorker(self._service, assets)
        self._worker.prices_updated.connect(self._on_prices_updated)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_prices_updated(self, prices: dict) -> None:
        for asset, data in prices.items():
            price = data.get("price", 0.0)
            change = data.get("change_24h_pct", 0.0)
            text = f"{asset}   ${price:,.0f}   {change * 100:+.1f}% 24h   Monitoring"
            label = self._btc_label if asset == "BTC" else self._eth_label
            label.setText(text)

    def _on_scan_finished(self, signals: list) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("RUN SCAN NOW")
        if signals:
            self._last_signals = signals
            self._update_alert_panel(signals[-1])
            self._status_label.setText(
                f"Alert — {signals[-1].asset} conviction {signals[-1].conviction_pct:.0f}%"
            )
        else:
            self._status_label.setText("Scan complete — no displacement detected")

    def _on_scan_error(self, error: str) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("RUN SCAN NOW")
        self._status_label.setText(f"Error: {error}")
        logger.error("Scan error: %s", error)

    def _update_alert_panel(self, signal: DisplacementSignal) -> None:
        ts = signal.detected_at.strftime("%Y-%m-%d %H:%M")
        conviction_color = Colors.ERROR if signal.conviction_label == "HIGH" else Colors.WARNING
        summary = (
            f"<b>{signal.asset}</b>  |  {ts}  |  "
            f"<span style='color:{conviction_color}'>"
            f"Conviction: {signal.conviction_pct:.0f}% ({signal.conviction_label})</span>"
        )
        if signal.instrument_name:
            summary += (
                f"<br>Contract: <b>{signal.instrument_name}</b><br>"
                f"Premium: ${signal.premium_usd:,.0f}  |  "
                f"Delta: {signal.delta:.2f}  |  DTE: {signal.dte}"
            )
        self._alert_summary_label.setText(summary)
        self._alert_summary_label.setTextFormat(Qt.TextFormat.RichText)
        self._breakdown_btn.setVisible(True)

    def _show_breakdown(self) -> None:
        if not self._last_signals:
            return
        dialog = BreakdownDialog(self._last_signals[-1], self)
        dialog.exec()

    def _start_auto_refresh(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._run_scan)
        self._timer.start(5 * 60 * 1000)  # every 5 minutes


class BreakdownDialog(QDialog):
    """Popup showing full signal breakdown for the last alert."""

    def __init__(self, signal: DisplacementSignal, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Signal Breakdown — {signal.asset}")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)

        scores = [
            ("Drop magnitude", signal.score_drop_magnitude, ""),
            ("Drop speed", signal.score_drop_speed, ""),
            (f"Funding rate", signal.score_funding_rate, f"({signal.funding_rate_value * 100:.2f}% funding)"),
            (f"DVOL spike", signal.score_dvol_spike, f"({signal.dvol_sigma:.1f}σ above mean)"),
            (f"Max pain dist", signal.score_max_pain, f"({signal.max_pain_distance_pct * 100:.1f}% below pain)"),
            (f"Term structure", signal.score_term_structure, f"({signal.term_structure_inversion_pct * 100:.1f}% inversion)"),
        ]
        for label, score, detail in scores:
            row = QHBoxLayout()
            bar = "█" * round(score / 10) + "░" * (10 - round(score / 10))
            row.addWidget(QLabel(f"{label}:"))
            bar_lbl = QLabel(f"{bar}  {score:.0f}  {detail}")
            bar_lbl.setStyleSheet("font-family: monospace;")
            row.addWidget(bar_lbl)
            layout.addLayout(row)

        if signal.instrument_name:
            layout.addWidget(QLabel(""))
            layout.addWidget(QLabel(f"<b>Contract:</b> {signal.instrument_name}", textFormat=Qt.TextFormat.RichText))
            layout.addWidget(QLabel(f"Delta: {signal.delta:.2f}  |  IV: {(signal.mark_iv or 0) * 100:.0f}%  |  DTE: {signal.dte}"))
            layout.addWidget(QLabel(f"50% target:  ${signal.target_50pct_price:,.0f}"))
            layout.addWidget(QLabel(f"100% target: ${signal.target_100pct_price:,.0f}"))
            layout.addWidget(QLabel(f"200% target: ${signal.target_200pct_price:,.0f}"))

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
