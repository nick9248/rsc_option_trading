# coding/gui/tabs/otm_contracts_view.py
"""
OTMContractsView — two-panel layout for the OTM contract finder.

Left (300px): Live conditions + Quick Setup + Advanced Filters + Find button
Right: Results scroll area with OTMSignalCard widgets

Advanced Filters (spec §15):
  - Min Conviction slider (40-100, default 60)
  - Min Delta spinbox (0.05-0.45, default 0.20)
  - Max Delta spinbox (0.05-0.45, default 0.35)
  - Kelly Multiplier spinbox (0.05-0.50, default 0.25)
  - Gate 2 Override checkbox
  - Show Suppressed checkbox
"""
import logging
from typing import List, Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QScrollArea,
    QLabel, QPushButton, QDoubleSpinBox, QSlider, QFrame,
    QButtonGroup, QCheckBox, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal
from coding.gui.theme.colors import Colors
from coding.gui.components.gate_score_bar import GateScoreBar
from coding.gui.components.otm_signal_card import OTMSignalCard

logger = logging.getLogger(__name__)


# ── Worker thread ─────────────────────────────────────────────────────────────

class OTMFinderWorker(QThread):
    """Runs OTMFinderService in background thread.

    Emits gate2_updated BEFORE find_signals() completes so the live-conditions
    panel shows a score while the full pipeline is still running.
    """

    finished = Signal(list)       # List[OTMSignal]
    gate2_updated = Signal(float) # mid-run Gate 2 score for conditions panel
    error = Signal(str)

    def __init__(self, finder_service, config, assets: List[str],
                 direction: str, expiry_pref: str,
                 gate2_override: bool = False,
                 min_conviction: int = 0,
                 min_delta: float = 0.20,
                 max_delta: float = 0.35,
                 kelly_multiplier: float = 0.25,
                 show_suppressed: bool = False,
                 parent=None):
        super().__init__(parent)
        self._service = finder_service
        self._config = config
        self._assets = assets
        self._direction = direction
        self._expiry_pref = expiry_pref
        self._gate2_override = gate2_override
        self._min_conviction = min_conviction
        self._min_delta = min_delta
        self._max_delta = max_delta
        self._kelly_multiplier = kelly_multiplier
        self._show_suppressed = show_suppressed

    def run(self) -> None:
        try:
            # Emit Gate 2 score MID-RUN (before the full 4-gate pipeline)
            if self._assets:
                gate2_result = self._service.score_gate2(self._assets[0])
                if gate2_result:
                    self.gate2_updated.emit(float(gate2_result.get("total_score", 0.0)))

            signals = self._service.find_signals(
                assets=self._assets,
                direction=self._direction,
                expiry_pref=self._expiry_pref,
                gate2_override=self._gate2_override,
            )

            # Apply client-side filters
            if self._min_conviction > 0:
                signals = [s for s in signals if s.conviction_score >= self._min_conviction]
            if not self._show_suppressed:
                signals = [s for s in signals if not s.gate2_suppressed]

            self.finished.emit(signals)
        except Exception as exc:
            logger.error("OTMFinderWorker error: %s", exc)
            self.error.emit(str(exc))


# ── Two-panel view ────────────────────────────────────────────────────────────

class OTMContractsView(QWidget):
    """Full OTM Contracts view with config panel and results panel."""

    back_requested = Signal()

    def __init__(self, finder_service=None, otm_config=None,
                 forward_test_dir: str = "", parent=None):
        super().__init__(parent)
        self._service = finder_service
        self._config = otm_config
        self._forward_test_dir = forward_test_dir
        self._worker: Optional[OTMFinderWorker] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Back button
        back_btn = QPushButton("< Back to Special Strategies")
        back_btn.setStyleSheet(f"color: {Colors.ACCENT}; border: none; font-size: 12px;")
        back_btn.clicked.connect(self.back_requested)
        layout.addWidget(back_btn)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([300, 900])
        splitter.setHandleWidth(1)
        layout.addWidget(splitter)

    # ── Left panel ─────────────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(300)
        panel.setStyleSheet(f"background-color: {Colors.SURFACE};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Section 1: Live conditions
        layout.addWidget(self._section_label("LIVE CONDITIONS"))
        self._gate2_bar = GateScoreBar(label="Gate 2")
        layout.addWidget(self._gate2_bar)
        self._regime_badge = QLabel("—")
        self._regime_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._regime_badge.setStyleSheet(
            f"color: {Colors.TEXT_MUTED}; border: 1px solid {Colors.BORDER};"
            " border-radius: 10px; padding: 2px 10px; font-size: 11px;"
        )
        layout.addWidget(self._regime_badge)
        self._last_updated = QLabel("Not yet scanned")
        self._last_updated.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(self._last_updated)

        # Section 2: Quick Setup
        layout.addWidget(self._section_label("QUICK SETUP"))

        layout.addWidget(QLabel("Asset", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._asset_group = self._toggle_group(["BTC", "ETH", "BOTH"], default=2)
        layout.addLayout(self._asset_group[0])

        layout.addWidget(QLabel("Direction", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._dir_group = self._toggle_group(["CALLS", "PUTS", "AUTO"], default=2)
        layout.addLayout(self._dir_group[0])

        layout.addWidget(QLabel("Expiry", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._expiry_group = self._toggle_group(["SHORT", "MEDIUM", "LONG", "AUTO"], default=3)
        layout.addLayout(self._expiry_group[0])

        layout.addWidget(QLabel("Risk Budget (USD)", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._budget_spin = QDoubleSpinBox()
        self._budget_spin.setRange(100, 1_000_000)
        self._budget_spin.setSingleStep(500)
        budget = getattr(self._config, "risk_budget_usd", 10_000.0) if self._config else 10_000.0
        self._budget_spin.setValue(budget)
        self._budget_spin.setStyleSheet(self._input_style())
        layout.addWidget(self._budget_spin)

        # Section 3: Advanced filters (collapsible)
        self._adv_toggle = QPushButton("ADVANCED FILTERS +")
        self._adv_toggle.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; border: none; font-size: 11px; text-align: left;"
        )
        self._adv_toggle.clicked.connect(self._toggle_advanced)
        layout.addWidget(self._adv_toggle)

        self._adv_panel = self._build_advanced_panel()
        self._adv_panel.setVisible(False)
        layout.addWidget(self._adv_panel)

        layout.addStretch()

        # Find button
        self._find_btn = QPushButton("FIND OTM CONTRACTS")
        self._find_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON_PRIMARY};
                color: white; font-weight: 700; font-size: 13px;
                border: none; border-radius: 6px; padding: 10px;
            }}
            QPushButton:hover {{ background-color: {Colors.BUTTON_PRIMARY_HOVER}; }}
            QPushButton:disabled {{ background-color: {Colors.ACCENT_MUTED}; color: {Colors.TEXT_MUTED}; }}
        """)
        self._find_btn.clicked.connect(self._on_find)
        layout.addWidget(self._find_btn)

        self._scan_summary = QLabel("No scan yet")
        self._scan_summary.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(self._scan_summary)

        return panel

    def _build_advanced_panel(self) -> QFrame:
        """Build all 6 advanced filter controls per spec §15."""
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. Min Conviction slider
        layout.addWidget(QLabel("Min Conviction", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._conviction_slider = QSlider(Qt.Orientation.Horizontal)
        self._conviction_slider.setRange(40, 100)
        self._conviction_slider.setValue(60)
        self._conviction_value = QLabel("60")
        self._conviction_value.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-size: 11px;")
        self._conviction_slider.valueChanged.connect(
            lambda v: self._conviction_value.setText(str(v))
        )
        row = QHBoxLayout()
        row.addWidget(self._conviction_slider)
        row.addWidget(self._conviction_value)
        layout.addLayout(row)

        # 2. Min Delta
        layout.addWidget(QLabel("Min Delta", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._min_delta_spin = QDoubleSpinBox()
        self._min_delta_spin.setRange(0.05, 0.45)
        self._min_delta_spin.setSingleStep(0.05)
        self._min_delta_spin.setValue(0.20)
        self._min_delta_spin.setDecimals(2)
        self._min_delta_spin.setStyleSheet(self._input_style())
        layout.addWidget(self._min_delta_spin)

        # 3. Max Delta
        layout.addWidget(QLabel("Max Delta", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._max_delta_spin = QDoubleSpinBox()
        self._max_delta_spin.setRange(0.05, 0.45)
        self._max_delta_spin.setSingleStep(0.05)
        self._max_delta_spin.setValue(0.35)
        self._max_delta_spin.setDecimals(2)
        self._max_delta_spin.setStyleSheet(self._input_style())
        layout.addWidget(self._max_delta_spin)

        # 4. Kelly Multiplier
        layout.addWidget(QLabel("Kelly Multiplier", styleSheet=f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;"))
        self._kelly_spin = QDoubleSpinBox()
        self._kelly_spin.setRange(0.05, 0.50)
        self._kelly_spin.setSingleStep(0.05)
        self._kelly_spin.setValue(0.25)
        self._kelly_spin.setDecimals(2)
        self._kelly_spin.setStyleSheet(self._input_style())
        layout.addWidget(self._kelly_spin)

        # 5. Gate 2 Override
        self._gate2_override_cb = QCheckBox("Gate 2 Override (paper trading only)")
        self._gate2_override_cb.setStyleSheet(f"color: {Colors.WARNING}; font-size: 11px;")
        layout.addWidget(self._gate2_override_cb)

        # 6. Show Suppressed
        self._show_suppressed_cb = QCheckBox("Show Gate 2 suppressed signals")
        self._show_suppressed_cb.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(self._show_suppressed_cb)

        return panel

    def _toggle_advanced(self) -> None:
        visible = not self._adv_panel.isVisible()
        self._adv_panel.setVisible(visible)
        self._adv_toggle.setText(
            "ADVANCED FILTERS -" if visible else "ADVANCED FILTERS +"
        )

    # ── Right panel ────────────────────────────────────────────────────────────

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 0, 0, 0)

        self._header_label = QLabel("Press FIND OTM CONTRACTS to scan")
        self._header_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: 13px; font-weight: 600;"
        )
        layout.addWidget(self._header_label)

        self._warning_banner = QLabel()
        self._warning_banner.setStyleSheet(
            f"background-color: {Colors.WARNING_MUTED}; color: white;"
            " border-radius: 4px; padding: 6px; font-size: 11px;"
        )
        self._warning_banner.setVisible(False)
        layout.addWidget(self._warning_banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        self._results_container = QWidget()
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setSpacing(8)
        self._results_layout.addStretch()
        scroll.setWidget(self._results_container)
        layout.addWidget(scroll)

        return panel

    # ── Scan logic ─────────────────────────────────────────────────────────────

    def _on_find(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        self._find_btn.setEnabled(False)
        self._find_btn.setText("SCANNING...")
        self._clear_results()

        assets = self._get_selected_assets()
        direction = self._get_selected_direction()
        expiry = self._get_selected_expiry()
        gate2_override = self._gate2_override_cb.isChecked()

        self._worker = OTMFinderWorker(
            finder_service=self._service,
            config=self._config,
            assets=assets,
            direction=direction,
            expiry_pref=expiry,
            gate2_override=gate2_override,
            min_conviction=self._conviction_slider.value(),
            min_delta=self._min_delta_spin.value(),
            max_delta=self._max_delta_spin.value(),
            kelly_multiplier=self._kelly_spin.value(),
            show_suppressed=self._show_suppressed_cb.isChecked(),
        )
        self._worker.finished.connect(self._on_results)
        self._worker.finished.connect(self._worker.deleteLater)  # safe cleanup
        self._worker.gate2_updated.connect(self._on_gate2_update)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, signals: list) -> None:
        from datetime import datetime, timezone as tz
        self._find_btn.setEnabled(True)
        self._find_btn.setText("FIND OTM CONTRACTS")
        self._clear_results()

        now = datetime.now(tz.utc).strftime("%H:%M:%S")
        self._scan_summary.setText(f"{len(signals)} signals | {now}")
        self._last_updated.setText(f"Scanned {now}")

        # Header: "N signals found | Gate 2: XX | Regime: BULL" (spec §15)
        regime = signals[0].regime_flag.upper() if signals else "—"
        gate2 = signals[0].gate2_score if signals else 0.0
        self._header_label.setText(
            f"{len(signals)} signals found | Gate 2: {gate2:.0f} | Regime: {regime}"
        )

        if not signals:
            self._results_layout.insertWidget(
                0, QLabel("No signals found — try adjusting filters or enabling Gate 2 Override",
                          styleSheet=f"color: {Colors.TEXT_MUTED}; font-size: 12px;")
            )
            return

        suppressed = signals[0].gate2_suppressed
        if suppressed:
            self._warning_banner.setText(
                f"Gate 2 score {gate2:.0f} — suppressed. Showing paper trading signals only."
            )
            self._warning_banner.setVisible(True)

        # Update regime badge in left panel
        self._regime_badge.setText(regime)

        for i, signal in enumerate(signals, 1):
            card = OTMSignalCard(
                signal=signal, rank=i,
                forward_test_dir=self._forward_test_dir,
            )
            self._results_layout.insertWidget(i - 1, card)

    def _on_gate2_update(self, score: float) -> None:
        """Receives mid-run Gate 2 score emitted before full pipeline completes."""
        self._gate2_bar.set_score(score)

    def _on_error(self, message: str) -> None:
        self._find_btn.setEnabled(True)
        self._find_btn.setText("FIND OTM CONTRACTS")
        self._header_label.setText(f"Error: {message}")
        logger.error("OTMContractsView error: %s", message)

    def _clear_results(self) -> None:
        self._warning_banner.setVisible(False)
        while self._results_layout.count() > 1:  # keep the stretch at end
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_selected_assets(self) -> List[str]:
        idx = self._asset_group[1].checkedId()
        return ["BTC"] if idx == 0 else (["ETH"] if idx == 1 else ["BTC", "ETH"])

    def _get_selected_direction(self) -> str:
        idx = self._dir_group[1].checkedId()
        return ["call", "put", "auto"][idx]

    def _get_selected_expiry(self) -> str:
        idx = self._expiry_group[1].checkedId()
        return ["short", "medium", "long", "auto"][idx]

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {Colors.TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        )
        return lbl

    @staticmethod
    def _toggle_group(labels: List[str], default: int = 0):
        layout = QHBoxLayout()
        layout.setSpacing(4)
        group = QButtonGroup()
        group.setExclusive(True)
        for i, text in enumerate(labels):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(i == default)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.BUTTON_SECONDARY};
                    color: {Colors.TEXT_SECONDARY};
                    border: none; border-radius: 4px;
                    padding: 3px 8px; font-size: 11px;
                }}
                QPushButton:checked {{
                    background-color: {Colors.ACCENT};
                    color: white; font-weight: 700;
                }}
            """)
            group.addButton(btn, i)
            layout.addWidget(btn)
        return layout, group

    @staticmethod
    def _input_style() -> str:
        return f"""
            QDoubleSpinBox {{
                background-color: {Colors.INPUT_BACKGROUND};
                color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 4px; padding: 4px;
            }}
        """
