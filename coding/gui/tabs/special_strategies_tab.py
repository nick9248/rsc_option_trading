# coding/gui/tabs/special_strategies_tab.py
"""
SpecialStrategiesTab — tile grid of available special strategies.

Tiles display:
  - Title + status dot
  - Gate 2 score bar
  - Regime badge
  - Last scan timestamp
  - Active signal count
  - OPEN button

Auto-refreshes Gate 2 score every 30 minutes via QTimer + score_gate2().
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QScrollArea,
    QFrame, QLabel, QPushButton, QStackedWidget, QHBoxLayout,
)
from PySide6.QtCore import Qt, QTimer
from coding.gui.theme.colors import Colors
from coding.gui.components.gate_score_bar import GateScoreBar
from coding.gui.tabs.otm_contracts_view import OTMContractsView

logger = logging.getLogger(__name__)


class StrategyTileWidget(QFrame):
    """Single strategy tile in the grid.

    Shows title, status dot, Gate 2 score bar, regime badge,
    last scan time, active signal count, and OPEN button.
    """

    def __init__(self, title: str, description: str,
                 gate2_score: float = 0.0,
                 regime: str = "—",
                 last_scan: str = "Never scanned",
                 active_signals: int = 0,
                 enabled: bool = True, parent=None):
        super().__init__(parent)
        self._enabled = enabled
        self._setup_ui(title, description, gate2_score, regime, last_scan, active_signals)

    def _status_color(self, score: float) -> str:
        if score >= 60:
            return Colors.SUCCESS
        elif score >= 40:
            return Colors.WARNING
        return Colors.ERROR

    def _setup_ui(self, title, description, gate2_score, regime, last_scan, active_signals) -> None:
        self.setFixedSize(280, 190)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.SURFACE};
                border: 1px solid {Colors.BORDER};
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)

        # Title row with status dot
        title_row = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: 14px; font-weight: 700;"
        )
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        if self._enabled:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {self._status_color(gate2_score)}; font-size: 14px;")
            title_row.addWidget(dot)
        layout.addLayout(title_row)

        if self._enabled:
            # Gate 2 score bar
            self._score_bar = GateScoreBar(label="Gate 2")
            self._score_bar.set_score(gate2_score)
            layout.addWidget(self._score_bar)

            # Regime badge
            self._regime_badge = QLabel(regime)
            self._regime_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._regime_badge.setStyleSheet(
                f"color: {Colors.TEXT_MUTED}; border: 1px solid {Colors.BORDER};"
                " border-radius: 8px; padding: 1px 8px; font-size: 10px;"
            )
            layout.addWidget(self._regime_badge)

            # Last scan time
            self._last_scan_lbl = QLabel(f"Last: {last_scan}")
            self._last_scan_lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 10px;")
            layout.addWidget(self._last_scan_lbl)

            # Active signals count
            self._signals_lbl = QLabel(f"{active_signals} active signals")
            self._signals_lbl.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")
            layout.addWidget(self._signals_lbl)
        else:
            coming = QLabel(description)
            coming.setStyleSheet(f"color: {Colors.TEXT_DISABLED}; font-size: 11px;")
            coming.setWordWrap(True)
            layout.addWidget(coming)

        layout.addStretch()

        if self._enabled:
            self._open_btn = QPushButton("OPEN ▶")
            self._open_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.BUTTON_PRIMARY};
                    color: white; font-weight: 700; font-size: 12px;
                    border: none; border-radius: 5px; padding: 6px;
                }}
                QPushButton:hover {{ background-color: {Colors.BUTTON_PRIMARY_HOVER}; }}
            """)
            layout.addWidget(self._open_btn)

    def update_status(self, gate2_score: float, regime: str,
                      last_scan: str, active_signals: int) -> None:
        """Refresh tile data after auto-refresh or completed scan."""
        if not self._enabled:
            return
        self._score_bar.set_score(gate2_score)
        self._regime_badge.setText(regime)
        self._last_scan_lbl.setText(f"Last: {last_scan}")
        self._signals_lbl.setText(f"{active_signals} active signals")


class SpecialStrategiesTab(QWidget):
    """
    Tab with tile grid of special strategies.
    Clicking a tile opens that strategy's dedicated view.
    Tiles auto-refresh Gate 2 score every 30 minutes.
    """

    def __init__(self, finder_service=None, otm_config=None,
                 forward_test_dir: str = "strategies/otm_contracts/forward_test_log",
                 parent=None):
        super().__init__(parent)
        self._service = finder_service
        self._config = otm_config
        self._forward_test_dir = forward_test_dir
        self._otm_tile: Optional[StrategyTileWidget] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        # Page 0: tile grid
        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(16, 16, 16, 16)

        header = QLabel("Special Strategies")
        header.setStyleSheet(
            f"color: {Colors.TEXT_PRIMARY}; font-size: 20px; font-weight: 700;"
        )
        grid_layout.addWidget(header)
        grid_layout.addSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        tile_container = QWidget()
        grid = QGridLayout(tile_container)
        grid.setSpacing(12)

        # OTM Contracts tile
        otm_tile = StrategyTileWidget(
            title="OTM Contracts",
            description="Regime-gated OTM option finder",
            gate2_score=0.0,
            regime="—",
            last_scan="Never scanned",
            active_signals=0,
            enabled=True,
        )
        if hasattr(otm_tile, "_open_btn"):
            otm_tile._open_btn.clicked.connect(self._open_otm_view)
        grid.addWidget(otm_tile, 0, 0)
        self._otm_tile = otm_tile  # hold reference for refresh updates

        # Future strategy tiles (disabled)
        for i, (title, desc) in enumerate([
            ("OTM Spreads", "Debit spread strategies — coming in phase 2"),
            ("Iron Condor", "Neutral range strategies — coming in phase 3"),
        ], 1):
            tile = StrategyTileWidget(title=title, description=desc, enabled=False)
            grid.addWidget(tile, 0, i)

        grid.setColumnStretch(3, 1)
        grid.setRowStretch(1, 1)
        scroll.setWidget(tile_container)
        grid_layout.addWidget(scroll)
        self._stack.addWidget(grid_page)

        # Page 1: OTM Contracts view
        self._otm_view = OTMContractsView(
            finder_service=self._service,
            otm_config=self._config,
            forward_test_dir=self._forward_test_dir,
        )
        self._otm_view.back_requested.connect(self._back_to_grid)
        self._stack.addWidget(self._otm_view)

        # 30-minute auto-refresh for tile Gate 2 scores (spec §15)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30 * 60 * 1000)  # 30 minutes
        self._refresh_timer.timeout.connect(self._refresh_tile_scores)
        self._refresh_timer.start()

    def _open_otm_view(self) -> None:
        self._stack.setCurrentIndex(1)

    def _back_to_grid(self) -> None:
        self._stack.setCurrentIndex(0)

    def _refresh_tile_scores(self) -> None:
        """Refresh OTM tile Gate 2 score in background (fires every 30 min)."""
        if self._service is None or self._otm_tile is None:
            return
        try:
            result = self._service.score_gate2("BTC")
            if result:
                score = float(result.get("total_score", 0.0))
                action = result.get("action", "—")
                now = datetime.now(timezone.utc).strftime("%H:%M UTC")
                self._otm_tile.update_status(
                    gate2_score=score,
                    regime=action.replace("_", " ").upper(),
                    last_scan=now,
                    active_signals=0,
                )
        except Exception as exc:
            logger.warning("Tile auto-refresh failed: %s", exc)
