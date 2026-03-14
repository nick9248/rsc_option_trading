"""
OTMConfig — all tunable thresholds for the OTM contract finder.

Update this model (not code) to adjust strategy behavior after backtesting.
"""
import logging
from typing import Dict
from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)


class OTMConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ── Budget ────────────────────────────────────────────────────────────────
    risk_budget_usd: float
    max_single_trade_pct: float = 0.10
    max_correlated_pct: float = 0.10

    # ── Gate 1 — dual-threshold spread ───────────────────────────────────────
    max_bid_ask_spread_relative: float = 0.08
    max_bid_ask_spread_absolute: float = 4.0
    min_volume_oi_ratio: float = 0.05
    min_oi_btc: int = 50
    min_oi_eth: int = 200
    tx_cost_floor_multiplier: float = 5.0

    # ── Gate 2 ────────────────────────────────────────────────────────────────
    gate2_suppress_threshold: float = 40.0
    gate2_position_exit_threshold: float = 30.0
    dvol_percentile_threshold: float = 30.0
    dvol_lookback_months: int = 36
    dvol_floor_std_multiplier: float = 1.0
    vrp_cheap_threshold: float = 5.0
    garch_iv_ratio_threshold: float = 1.10
    term_structure_contango_threshold: float = 5.0
    term_structure_shallow_back_threshold: float = -5.0
    term_structure_deep_back_threshold: float = -15.0

    # ── Gate 3 ────────────────────────────────────────────────────────────────
    rr_z_score_threshold: float = 1.5
    pc_ratio_percentile_bull: float = 70.0
    pc_ratio_percentile_bear: float = 30.0
    funding_percentile_bull: float = 10.0
    funding_percentile_bear: float = 90.0
    block_trade_min_premium: float = 500_000.0
    stablecoin_inflow_threshold_pct: float = 0.5
    ris_divergence_threshold: float = 2.0

    # ── Regime (fast EMA dual-filter) ─────────────────────────────────────────
    ema_fast: int = 10
    ema_slow: int = 20
    trend_sma: int = 50
    regime_call_multiplier: float = 1.30
    regime_put_multiplier: float = 0.70

    # ── Gate 4 ────────────────────────────────────────────────────────────────
    min_delta_directional: float = 0.20
    max_delta_directional: float = 0.35
    min_delta_event: float = 0.10
    max_delta_event: float = 0.20
    vega_theta_short: float = 0.05
    vega_theta_medium: float = 0.30
    vega_theta_long: float = 0.80
    max_breakeven_move_multiplier: float = 2.0

    # ── Kelly / sizing ────────────────────────────────────────────────────────
    kelly_divisor: float = 4.0
    p_win_priors: Dict[str, float] = {
        "40_60": 0.35,
        "60_75": 0.40,
        "75_90": 0.45,
        "90_100": 0.50,
    }
    avg_return_priors: Dict[str, float] = {
        "40_60": 1.5,
        "60_75": 2.0,
        "75_90": 2.5,
        "90_100": 3.0,
    }

    # ── Exit thresholds ───────────────────────────────────────────────────────
    stop_loss_hard_floor_pct: float = 0.70
    theta_excess_loss_reduce_pct: float = 0.20
    theta_excess_loss_full_exit_pct: float = 0.40
    thesis_stop_call_pct: float = 0.85
    thesis_stop_put_pct: float = 1.15
    vega_windfall_iv_spike_threshold: float = 15.0
    vega_windfall_spot_move_max: float = 0.01
    vega_windfall_profit_threshold: float = 2.0
    liquidity_exit_spread_multiplier: float = 3.0
    liquidity_exit_reprice_interval_sec: int = 10
    liquidity_exit_reprice_concession_vol_pts: float = 0.5
    liquidity_exit_max_reprice_cycles: int = 8
    itm_threshold_for_hold: float = 0.10
    hold_through_expiry_max_dte: int = 5

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("risk_budget_usd")
    @classmethod
    def budget_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("risk_budget_usd must be positive")
        return v

    @field_validator("max_single_trade_pct")
    @classmethod
    def single_trade_pct_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_single_trade_pct must be positive")
        return v

    @field_validator("kelly_divisor")
    @classmethod
    def kelly_divisor_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("kelly_divisor must be positive")
        return v
