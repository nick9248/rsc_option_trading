from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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

    @model_validator(mode="after")
    def validate_field_relationships(self) -> "DisplacementConfig":
        if self.cooldown_hours < 0:
            raise ValueError("cooldown_hours must be non-negative")
        if not 0.0 < self.position_size_pct <= 1.0:
            raise ValueError("position_size_pct must be between 0 and 1")
        if self.min_delta >= self.max_delta:
            raise ValueError("min_delta must be less than max_delta")
        if not (self.min_delta <= self.preferred_delta <= self.max_delta):
            raise ValueError("preferred_delta must be within [min_delta, max_delta]")
        if self.min_dte >= self.max_dte:
            raise ValueError("min_dte must be less than max_dte")
        if self.preferred_dte_min >= self.preferred_dte_max:
            raise ValueError("preferred_dte_min must be less than preferred_dte_max")
        if self.alert_medium_threshold >= self.alert_high_threshold:
            raise ValueError("alert_medium_threshold must be less than alert_high_threshold")
        if self.dvol_sweet_spot_low >= self.dvol_sweet_spot_high:
            raise ValueError("dvol_sweet_spot_low must be less than dvol_sweet_spot_high")
        return self
