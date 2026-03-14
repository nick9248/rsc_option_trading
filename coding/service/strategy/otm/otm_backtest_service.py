"""
OTMBacktestService — interface stub for backtesting OTM signals.

Full implementation is deferred to phase 2 (requires historical signal reconstruction).
The interface is defined here so the GUI and service layer can reference it.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    asset: str
    start_date: datetime
    end_date: datetime
    total_signals: int
    win_rate: float
    avg_return_multiple: float
    calibrated_p_win_bands: Dict[str, float]
    notes: str = ""


@dataclass
class LabeledTrade:
    signal_id: str
    asset: str
    instrument_name: str
    conviction_score: float
    outcome: str          # "take_profit" | "stop_loss" | "time_stop" | "expired_worthless"
    return_multiple: float
    holding_period_days: int


class OTMBacktestService:
    """
    Backtests OTM signals against historical data.

    Phase 2 implementation: reconstructs all signals historically, labels outcomes,
    and returns calibrated P_win values per conviction band.
    """

    def run_backtest(
        self,
        asset: str,
        start_date: datetime,
        end_date: datetime,
        config,
    ) -> BacktestResult:
        """Run backtest over historical period. NOT YET IMPLEMENTED."""
        raise NotImplementedError(
            "OTMBacktestService.run_backtest is deferred to phase 2. "
            "Run forward testing first to accumulate labeled trades."
        )

    def label_outcomes(
        self,
        signals,
        price_history: Dict,
    ) -> List[LabeledTrade]:
        """Label historical signal outcomes. NOT YET IMPLEMENTED."""
        raise NotImplementedError("Deferred to phase 2.")

    def calibrate_conviction_bands(
        self,
        labeled_trades: List[LabeledTrade],
    ) -> Dict[str, float]:
        """Return empirical P_win per conviction band. NOT YET IMPLEMENTED."""
        raise NotImplementedError("Deferred to phase 2.")
