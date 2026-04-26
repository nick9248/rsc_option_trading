from pydantic import BaseModel, ConfigDict, field_validator


class DisplacementConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Trigger thresholds (as decimals, e.g. 0.08 = 8%)
    drop_1h_threshold: float = 0.08
    drop_4h_threshold: float = 0.12
    drop_24h_threshold: float = 0.20
    drop_7d_threshold: float = 0.30
    cooldown_hours: int = 24

    # Strike selection
    min_delta: float = 0.10
    max_delta: float = 0.20
    preferred_delta: float = 0.15
    min_dte: int = 90
    max_dte: int = 270
    preferred_dte_min: int = 120
    preferred_dte_max: int = 180
    min_oi_btc: int = 50
    min_oi_eth: int = 200
    max_bid_ask_spread_relative: float = 0.08

    # Alert thresholds (as decimals, e.g. 0.70 = 70%)
    alert_high_threshold: float = 0.70
    alert_medium_threshold: float = 0.50

    # Sizing
    risk_budget_usd: float = 10_000.0
    position_size_pct: float = 0.02

    # Conviction scoring parameters
    dvol_sweet_spot_low: float = 1.5     # σ above mean
    dvol_sweet_spot_high: float = 2.5    # σ above mean
    max_pain_distance_full_score: float = 0.10  # 10% below max pain = score 100

    @field_validator(
        "drop_1h_threshold", "drop_4h_threshold",
        "drop_24h_threshold", "drop_7d_threshold"
    )
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError("Threshold must be between 0 and 1 exclusive")
        return v

    @field_validator("risk_budget_usd")
    @classmethod
    def validate_budget(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("risk_budget_usd must be positive")
        return v
