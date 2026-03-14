# coding/gui/components/otm_signal_card.py
"""
OTMSignalCard — expandable card showing one OTMSignal.

Collapsed: rank badge, instrument, direction pill, delta, DTE,
           expiry category, conviction bar, premium, position USD,
           take profit target, thesis stop price.
           Buttons: [BREAKDOWN] [PAPER TRADE] [COPY]

Expanded: full sub-signal breakdown table with weights and score bars.

PAPER TRADE button is disabled when gate2_suppressed is True (spec: §15
"disabled if Gate 2 Override not checked and Gate 2 < 40").
"""
import logging
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from coding.gui.theme.colors import Colors
from coding.gui.components.gate_score_bar import GateScoreBar

logger = logging.getLogger(__name__)

_SIGNAL_LABELS = {
    "d1_d7_score": ("D1+D7 Dealer Positioning", "22% BTC / 24% ETH"),
    "d2_score":    ("D2 Funding Rate %ile",     "15% BTC / 17% ETH"),
    "d3_score":    ("D3 25\u0394 RR Z-Score",   "14% BTC / 15% ETH"),
    "d4_score":    ("D4 P/C OI Ratio",           "11% BTC / 12% ETH"),
    "d6_d9_score": ("D6+D9 Institutional Flow", "14% BTC / 15% ETH"),
    "d8_score":    ("D8 Stablecoin Inflow",      " 8% BTC /  9% ETH"),
    "d10_score":   ("D10 IBIT P/C Flow",          " 9% BTC /  0% ETH"),
    "ris_score":   ("RIS Realized vs Implied",    " 7% BTC /  8% ETH"),
}


class OTMSignalCard(QFrame):
    """Expandable card for one OTMSignal."""

    paper_trade_requested = Signal(object)   # emits the OTMSignal

    def __init__(self, signal, rank: int, forward_test_dir: str = "", parent=None):
        super().__init__(parent)
        self._signal = signal
        self._rank = rank
        self._forward_test_dir = forward_test_dir
        self._expanded = False
        self._setup_ui()

    def _direction_color(self) -> str:
        return Colors.SUCCESS if self._signal.direction == "call" else Colors.ERROR

    def _setup_ui(self) -> None:
        self.setObjectName("signalCard")
        self.setStyleSheet(f"""
            QFrame#signalCard {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 8px;
                padding: 0px;
            }}
        """)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(12, 10, 12, 10)
        self._main_layout.setSpacing(8)

        # ── Header row ────────────────────────────────────────────────────────
        header = QHBoxLayout()

        rank_badge = QLabel(f"#{self._rank}")
        rank_badge.setStyleSheet(
            f"color: {Colors.ACCENT}; font-weight: 700; font-size: 13px; min-width: 28px;"
        )
        header.addWidget(rank_badge)

        name_label = QLabel(self._signal.instrument_name)
        name_label.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-weight: 600; font-size: 13px;"
        )
        header.addWidget(name_label)
        header.addStretch()

        direction_pill = QLabel(self._signal.direction.upper())
        direction_pill.setStyleSheet(f"""
            background-color: {self._direction_color()};
            color: white; font-size: 11px; font-weight: 700;
            border-radius: 4px; padding: 2px 8px;
        """)
        header.addWidget(direction_pill)

        self._main_layout.addLayout(header)

        # ── Metrics row ───────────────────────────────────────────────────────
        metrics = QHBoxLayout()
        s = self._signal
        for text in [
            f"\u0394 {abs(s.delta):.2f}",
            f"DTE {s.dte}",
            s.expiry_category.upper(),
            f"IV {s.mark_iv*100:.0f}%",
        ]:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color: {Colors.TEXT_SECONDARY}; font-size: 11px; margin-right: 12px;"
            )
            metrics.addWidget(lbl)
        metrics.addStretch()
        self._main_layout.addLayout(metrics)

        # ── Conviction score bar ───────────────────────────────────────────────
        self._conviction_bar = GateScoreBar(label="Conviction")
        self._conviction_bar.set_score(s.conviction_score)
        self._main_layout.addWidget(self._conviction_bar)

        # ── Financial row ──────────────────────────────────────────────────────
        fin = QHBoxLayout()
        for label, value in [
            ("Premium", f"${s.entry_premium:,.0f}"),
            ("Position", f"${s.position_usd:,.0f}"),
            ("TP", f"{s.take_profit_multiple:.1f}×"),
        ]:
            col = QVBoxLayout()
            col.addWidget(_make_label(label, Colors.TEXT_MUTED, 10))
            col.addWidget(_make_label(value, Colors.TEXT_PRIMARY, 12))
            fin.addLayout(col)
            fin.addSpacing(16)
        fin.addStretch()
        self._main_layout.addLayout(fin)

        # ── Buttons row ───────────────────────────────────────────────────────
        buttons = QHBoxLayout()
        self._breakdown_btn = QPushButton("BREAKDOWN")
        self._breakdown_btn.setStyleSheet(_secondary_btn_style())
        self._breakdown_btn.clicked.connect(self._toggle_breakdown)
        buttons.addWidget(self._breakdown_btn)

        self._paper_btn = QPushButton("PAPER TRADE")
        self._paper_btn.setStyleSheet(_secondary_btn_style())
        self._paper_btn.clicked.connect(self._on_paper_trade)
        # Spec: "disabled if Gate 2 Override not checked and Gate 2 < 40"
        if self._signal.gate2_suppressed:
            self._paper_btn.setEnabled(False)
            self._paper_btn.setToolTip(
                "Disabled: Gate 2 suppressed — enable Gate 2 Override to paper trade"
            )
        buttons.addWidget(self._paper_btn)

        copy_btn = QPushButton("COPY")
        copy_btn.setStyleSheet(_secondary_btn_style())
        copy_btn.clicked.connect(self._on_copy)
        buttons.addWidget(copy_btn)
        buttons.addStretch()
        self._main_layout.addLayout(buttons)

        # ── Expandable breakdown panel (hidden by default) ────────────────────
        self._breakdown_panel = self._build_breakdown_panel()
        self._breakdown_panel.setVisible(False)
        self._main_layout.addWidget(self._breakdown_panel)

    def _build_breakdown_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_TERTIARY}; border-radius: 6px;"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        s = self._signal
        for field, (label, weight) in _SIGNAL_LABELS.items():
            score = getattr(s, field, 0.0)
            row = QHBoxLayout()
            row.addWidget(_make_label(label, Colors.TEXT_SECONDARY, 11))
            row.addWidget(_make_label(f"{score:+.2f}", Colors.TEXT_PRIMARY, 11))
            row.addWidget(_make_label(weight, Colors.TEXT_MUTED, 10))
            layout.addLayout(row)

        layout.addWidget(_make_label(
            f"Regime: {s.regime_flag.upper()}  |  Vega/Theta: {s.vega_theta_ratio:.2f}  |  "
            f"Breakeven: ${s.breakeven_price:,.0f}",
            Colors.TEXT_SECONDARY, 11
        ))
        return panel

    def _toggle_breakdown(self) -> None:
        self._expanded = not self._expanded
        self._breakdown_panel.setVisible(self._expanded)
        self._breakdown_btn.setText("HIDE" if self._expanded else "BREAKDOWN")

    def _on_paper_trade(self) -> None:
        # Guard: spec requires button disabled when suppressed; re-check defensively
        if self._signal.gate2_suppressed:
            logger.warning("Paper trade blocked: Gate 2 suppressed (score below threshold)")
            return
        if not self._forward_test_dir:
            logger.warning("forward_test_dir not set — cannot log paper trade")
            return
        try:
            Path(self._forward_test_dir).mkdir(parents=True, exist_ok=True)
            filename = (f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_"
                        f"{self._signal.instrument_name}.json")
            filepath = os.path.join(self._forward_test_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self._signal.model_dump(mode="json"), f, indent=2, default=str)
            logger.info("Paper trade logged: %s", filepath)
            self._paper_btn.setText("LOGGED!")
            self.paper_trade_requested.emit(self._signal)
        except Exception as exc:
            logger.error("Failed to log paper trade: %s", exc)

    def _on_copy(self) -> None:
        s = self._signal
        text = (f"{s.instrument_name} | {s.direction.upper()} | "
                f"Delta {abs(s.delta):.2f} | DTE {s.dte} | "
                f"Premium ${s.entry_premium:,.0f} | Conviction {s.conviction_score:.0f}")
        QGuiApplication.clipboard().setText(text)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_label(text: str, color: str, size: int) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {color}; font-size: {size}px;")
    return lbl


def _secondary_btn_style() -> str:
    return f"""
        QPushButton {{
            background-color: {Colors.BUTTON_SECONDARY};
            color: {Colors.TEXT_PRIMARY};
            border: none; border-radius: 4px;
            padding: 4px 10px; font-size: 11px; font-weight: 600;
        }}
        QPushButton:hover {{ background-color: {Colors.BUTTON_SECONDARY_HOVER}; }}
    """
