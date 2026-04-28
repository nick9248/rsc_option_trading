"""
Displacement Scanner tab — index 6 (Special Strategies).

Monitors BTC/ETH for large price displacements, scores the setup,
recommends an OTM call, and fires a Telegram alert. Auto-refreshes
every 5 minutes when the app is open.
"""
import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QSpinBox, QCheckBox, QDialog,
)

from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


# ── Worker ────────────────────────────────────────────────────────────────────

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
            self.finished.emit(self._service.scan(self._assets))
        except Exception as e:
            self.error.emit(str(e))


# ── Main tab ──────────────────────────────────────────────────────────────────

class DisplacementTab(QWidget):
    """Displacement scanner — simple, functional, clear."""

    def __init__(self, scanner_service, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._service = scanner_service
        self._worker: Optional[ScanWorker] = None
        self._last_signals: list[DisplacementSignal] = []
        self._init_ui()
        self._start_auto_refresh()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(0)

        # ── Header row: title + asset toggles ────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Displacement Scanner")
        font = QFont()
        font.setPointSize(15)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        header.addWidget(title)
        header.addStretch()

        self._btn_btc = self._toggle("BTC", True)
        self._btn_eth = self._toggle("ETH", True)
        header.addWidget(self._btn_btc)
        header.addSpacing(8)
        header.addWidget(self._btn_eth)
        root.addLayout(header)
        root.addSpacing(20)

        # ── Market Conditions ─────────────────────────────────────────────────
        root.addWidget(self._section("MARKET CONDITIONS"))
        root.addSpacing(8)

        cond = QGridLayout()
        cond.setHorizontalSpacing(24)
        cond.setVerticalSpacing(6)

        self._btc_price = QLabel("—")
        self._btc_change = QLabel("—")
        self._btc_status = QLabel("Waiting…")
        self._eth_price = QLabel("—")
        self._eth_change = QLabel("—")
        self._eth_status = QLabel("Waiting…")

        for col, lbl in enumerate(["Asset", "Price", "24h Change", "Status"]):
            h = QLabel(lbl)
            h.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
            cond.addWidget(h, 0, col)

        btc_name = QLabel("BTC")
        btc_name.setStyleSheet(f"color: {Colors.ACCENT}; font-weight: bold;")
        eth_name = QLabel("ETH")
        eth_name.setStyleSheet(f"color: {Colors.ACCENT}; font-weight: bold;")

        for row, (name, price, change, status) in enumerate([
            (btc_name, self._btc_price, self._btc_change, self._btc_status),
            (eth_name, self._eth_price, self._eth_change, self._eth_status),
        ], start=1):
            for col, w in enumerate([name, price, change, status]):
                w.setStyleSheet(w.styleSheet() + f" font-size: 13px;")
                cond.addWidget(w, row, col)

        for lbl in (self._btc_price, self._eth_price):
            lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 13px; font-family: monospace;")
        for lbl in (self._btc_change, self._eth_change):
            lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 13px; font-family: monospace;")
        for lbl in (self._btc_status, self._eth_status):
            lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px;")

        root.addLayout(cond)
        root.addSpacing(20)
        root.addWidget(self._divider())
        root.addSpacing(16)

        # ── Last Alert ────────────────────────────────────────────────────────
        root.addWidget(self._section("LAST ALERT"))
        root.addSpacing(8)

        self._alert_label = QLabel("No alerts yet.")
        self._alert_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 13px;")
        self._alert_label.setWordWrap(True)
        root.addWidget(self._alert_label)

        self._breakdown_btn = QPushButton("View Breakdown")
        self._breakdown_btn.setVisible(False)
        self._breakdown_btn.setFixedWidth(140)
        self._breakdown_btn.setFixedHeight(28)
        self._breakdown_btn.clicked.connect(self._show_breakdown)
        root.addSpacing(6)
        root.addWidget(self._breakdown_btn)

        root.addSpacing(20)
        root.addWidget(self._divider())
        root.addSpacing(16)

        # ── Configuration ─────────────────────────────────────────────────────
        root.addWidget(self._section("SETTINGS"))
        root.addSpacing(10)

        cfg = QHBoxLayout()
        cfg.setSpacing(24)

        # Drop threshold
        t_col = QVBoxLayout()
        t_col.setSpacing(4)
        t_col.addWidget(self._cfg_label("Drop threshold (24h)"))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(5, 50)
        self._threshold_spin.setValue(20)
        self._threshold_spin.setSuffix(" %")
        self._threshold_spin.setFixedWidth(100)
        self._threshold_spin.setFixedHeight(30)
        self._threshold_spin.setStyleSheet(self._spinbox_style())
        t_col.addWidget(self._threshold_spin)
        cfg.addLayout(t_col)

        # Min conviction
        c_col = QVBoxLayout()
        c_col.setSpacing(4)
        c_col.addWidget(self._cfg_label("Min conviction"))
        self._conviction_spin = QSpinBox()
        self._conviction_spin.setRange(30, 95)
        self._conviction_spin.setValue(50)
        self._conviction_spin.setSuffix(" %")
        self._conviction_spin.setFixedWidth(100)
        self._conviction_spin.setFixedHeight(30)
        self._conviction_spin.setStyleSheet(self._spinbox_style())
        c_col.addWidget(self._conviction_spin)
        cfg.addLayout(c_col)

        # Telegram toggle
        tg_col = QVBoxLayout()
        tg_col.setSpacing(4)
        tg_col.addWidget(self._cfg_label("Telegram alerts"))
        self._telegram_check = QCheckBox("Enabled")
        self._telegram_check.setChecked(True)
        self._telegram_check.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 13px;")
        tg_col.addWidget(self._telegram_check)
        cfg.addLayout(tg_col)

        cfg.addStretch()
        root.addLayout(cfg)

        root.addSpacing(24)
        root.addWidget(self._divider())
        root.addSpacing(16)

        # ── Run button ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run Scan Now")
        self._run_btn.setFixedHeight(38)
        self._run_btn.setFixedWidth(160)
        self._run_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {Colors.ACCENT};"
            f"  color: #000000;"
            f"  font-weight: bold;"
            f"  font-size: 13px;"
            f"  border-radius: 4px;"
            f"  border: none;"
            f"}}"
            f"QPushButton:disabled {{"
            f"  background-color: {Colors.TEXT_MUTED};"
            f"}}"
        )
        self._run_btn.clicked.connect(self._run_scan)
        btn_row.addWidget(self._run_btn)
        btn_row.addSpacing(16)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 12px;")
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        root.addLayout(btn_row)

        root.addStretch()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )
        return lbl

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        line.setFixedHeight(1)
        return line

    def _cfg_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px;")
        return lbl

    def _spinbox_style(self) -> str:
        return (
            f"QSpinBox {{"
            f"  color: {Colors.TEXT_PRIMARY};"
            f"  background-color: {Colors.BACKGROUND_ELEVATED};"
            f"  border: 1px solid {Colors.TEXT_SECONDARY};"
            f"  border-radius: 4px;"
            f"  padding: 2px 6px;"
            f"  font-size: 13px;"
            f"}}"
            f"QSpinBox::up-button, QSpinBox::down-button {{"
            f"  width: 18px;"
            f"}}"
        )

    def _toggle(self, text: str, checked: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  min-width: 56px; max-width: 56px;"
            f"  min-height: 26px; max-height: 26px;"
            f"  background-color: transparent;"
            f"  color: {Colors.TEXT_SECONDARY};"
            f"  border: 1px solid {Colors.TEXT_SECONDARY};"
            f"  border-radius: 4px;"
            f"  font-size: 11px;"
            f"  font-weight: bold;"
            f"  padding: 0px;"
            f"}}"
            f"QPushButton:checked {{"
            f"  background-color: {Colors.ACCENT};"
            f"  color: #1a1a2e;"
            f"  border: 1px solid {Colors.ACCENT};"
            f"}}"
        )
        return btn

    # ── Scan logic ────────────────────────────────────────────────────────────

    def _run_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        assets = []
        if self._btn_btc.isChecked():
            assets.append("BTC")
        if self._btn_eth.isChecked():
            assets.append("ETH")
        if not assets:
            self._status_label.setText("Select at least one asset.")
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
        mapping = {
            "BTC": (self._btc_price, self._btc_change, self._btc_status),
            "ETH": (self._eth_price, self._eth_change, self._eth_status),
        }
        for asset, data in prices.items():
            if asset not in mapping:
                continue
            price_lbl, change_lbl, status_lbl = mapping[asset]
            p = data.get("price", 0.0)
            c = data.get("change_24h_pct", 0.0)
            price_lbl.setText(f"${p:,.0f}")
            color = Colors.ERROR if c < -0.05 else (Colors.WARNING if c < 0 else Colors.SUCCESS if hasattr(Colors, "SUCCESS") else "#2ecc71")
            change_lbl.setText(f"{c * 100:+.2f}%")
            change_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-family: monospace;")
            status_lbl.setText("● Monitoring")

    def _on_scan_finished(self, signals: list) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("Run Scan Now")
        if signals:
            self._last_signals = signals
            self._update_alert(signals[-1])
            s = signals[-1]
            self._status_label.setText(
                f"Alert — {s.asset}  {s.conviction_pct:.0f}% conviction"
            )
        else:
            self._status_label.setText("No displacement detected.")

    def _on_scan_error(self, error: str) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("Run Scan Now")
        self._status_label.setText(f"Error: {error}")
        logger.error("Scan error: %s", error)

    def _update_alert(self, sig: DisplacementSignal) -> None:
        ts = sig.detected_at.strftime("%Y-%m-%d %H:%M UTC")
        color = Colors.ERROR if sig.conviction_label == "HIGH" else Colors.WARNING
        lines = [
            f"<b>{sig.asset}</b>  ·  {ts}  ·  "
            f"<span style='color:{color}; font-weight:bold;'>"
            f"{sig.conviction_pct:.0f}% conviction ({sig.conviction_label})</span>"
        ]
        if sig.instrument_name:
            lines.append(
                f"<span style='color:{Colors.TEXT_MUTED};'>"
                f"Contract: </span><b>{sig.instrument_name}</b>"
                f"<span style='color:{Colors.TEXT_MUTED};'>"
                f"  ·  Δ {sig.delta:.2f}  ·  ${sig.premium_usd:,.0f} premium  ·  {sig.dte}d</span>"
            )
        self._alert_label.setText("<br>".join(lines))
        self._alert_label.setTextFormat(Qt.TextFormat.RichText)
        self._alert_label.setStyleSheet(f"font-size: 13px;")
        self._breakdown_btn.setVisible(True)

    def _show_breakdown(self) -> None:
        if not self._last_signals:
            return
        BreakdownDialog(self._last_signals[-1], self).exec()

    def _start_auto_refresh(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._run_scan)
        self._timer.start(5 * 60 * 1000)


# ── Breakdown dialog ──────────────────────────────────────────────────────────

class BreakdownDialog(QDialog):
    """Signal breakdown popup."""

    def __init__(self, sig: DisplacementSignal, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Signal Breakdown — {sig.asset}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        hdr = QLabel(
            f"<b>{sig.asset}</b> — Conviction <b>{sig.conviction_pct:.0f}%</b> ({sig.conviction_label})"
        )
        hdr.setTextFormat(Qt.TextFormat.RichText)
        hdr.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 14px;")
        layout.addWidget(hdr)

        layout.addWidget(self._divider())

        # Signals grid
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)

        rows = [
            ("Drop magnitude", sig.score_drop_magnitude, ""),
            ("Drop speed", sig.score_drop_speed, ""),
            ("Funding rate", sig.score_funding_rate, f"{sig.funding_rate_value * 100:.2f}%"),
            ("DVOL spike", sig.score_dvol_spike, f"{sig.dvol_sigma:.1f}σ above mean"),
            ("Max pain dist", sig.score_max_pain, f"{sig.max_pain_distance_pct * 100:.1f}% below pain"),
            ("Term structure", sig.score_term_structure, f"{sig.term_structure_inversion_pct * 100:.1f}% inversion"),
        ]
        for i, (name, score, detail) in enumerate(rows):
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 12px;")
            name_lbl.setFixedWidth(140)

            filled = round(score / 10)
            bar_lbl = QLabel("█" * filled + "░" * (10 - filled) + f"  {score:.0f}")
            bar_lbl.setStyleSheet(f"color: {Colors.ACCENT}; font-family: monospace; font-size: 12px;")

            detail_lbl = QLabel(detail)
            detail_lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")

            grid.addWidget(name_lbl, i, 0)
            grid.addWidget(bar_lbl, i, 1)
            grid.addWidget(detail_lbl, i, 2)

        layout.addLayout(grid)

        # Contract section
        if sig.instrument_name:
            layout.addWidget(self._divider())
            contract_lbl = QLabel(
                f"<b>Contract:</b> {sig.instrument_name}<br>"
                f"Δ {sig.delta:.2f}  ·  IV {(sig.mark_iv or 0) * 100:.0f}%  ·  DTE {sig.dte}  ·  ${sig.premium_usd:,.0f} premium"
            )
            contract_lbl.setTextFormat(Qt.TextFormat.RichText)
            contract_lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 13px;")
            layout.addWidget(contract_lbl)

            targets = QLabel(
                f"<span style='color:{Colors.TEXT_MUTED};'>Targets  </span>"
                f"50% → <b>${sig.target_50pct_price:,.0f}</b>  ·  "
                f"100% → <b>${sig.target_100pct_price:,.0f}</b>  ·  "
                f"200% → <b>${sig.target_200pct_price:,.0f}</b>"
            )
            targets.setTextFormat(Qt.TextFormat.RichText)
            targets.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 12px;")
            layout.addWidget(targets)

        layout.addWidget(self._divider())
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        return line
