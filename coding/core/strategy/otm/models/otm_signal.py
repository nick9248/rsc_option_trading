"""
OTMSignal — one record per contract that survived all four gates.

Immutable once created. Serialized to DB via model_dump().
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, field_validator


class OTMSignal(BaseModel):
    """Immutable signal representing a contract that passed all filtering gates."""

    model_config = ConfigDict(frozen=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    signal_id: str
    generated_at: datetime
    asset: Literal["BTC", "ETH"]
    instrument_name: str
    direction: Literal["call", "put"]
    strike: float
    expiry: str
    dte: int
    expiry_category: Literal["short", "medium", "long"]

    # ── Contract metrics at signal time ───────────────────────────────────────
    delta: float
    gamma: float
    vega: float
    theta: float
    mark_iv: float
    entry_premium: float
    underlying_price: float

    # ── Gate scores ───────────────────────────────────────────────────────────
    gate1_passed: bool
    gate2_score: float
    gate3_call_score: float
    gate3_put_score: float
    gate3_directional_score: float
    conviction_score: float

    # ── Gate 3 sub-signal breakdown (each in [−1, +1]) ────────────────────────
    d1_d7_score: float
    d2_score: float
    d3_score: float
    d4_score: float
    d6_d9_score: float
    d8_score: float
    d10_score: float     # 0.0 for ETH
    ris_score: float

    # ── Sizing ────────────────────────────────────────────────────────────────
    position_usd: float
    p_win_prior: float
    kelly_fraction: float

    # ── Exit thresholds (set at entry) ────────────────────────────────────────
    take_profit_multiple: float
    stop_loss_pct: float
    time_stop_dte: int

    # ── Gate 4 rationale ──────────────────────────────────────────────────────
    vega_theta_ratio: float
    gamma_premium_ratio: float
    breakeven_price: float

    # ── Regime context ────────────────────────────────────────────────────────
    regime_flag: Literal["bull", "bear", "neutral"]
    gate2_suppressed: bool

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("gate2_score", "gate3_call_score", "gate3_put_score",
                     "gate3_directional_score", "conviction_score")
    @classmethod
    def score_0_to_100(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"Score must be in [0, 100], got {v}")
        return v

    @field_validator("d1_d7_score", "d2_score", "d3_score", "d4_score",
                     "d6_d9_score", "d8_score", "d10_score", "ris_score")
    @classmethod
    def sub_signal_range(cls, v: float) -> float:
        if not (-1.0 <= v <= 1.0):
            raise ValueError(f"Sub-signal score must be in [−1, +1], got {v}")
        return v

    @field_validator("dte")
    @classmethod
    def dte_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("dte must be >= 1")
        return v
