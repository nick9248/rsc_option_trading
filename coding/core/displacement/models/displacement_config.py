from pydantic import BaseModel, ConfigDict, field_validator


class DisplacementConfig(BaseModel):
    """
    Configuration for the displacement scanner system.
    All values are frozen (immutable) after creation.
    """

    model_config = ConfigDict(frozen=True)

    # Drop thresholds (as fractions)
    drop_1h_threshold: float = 0.08  # 8%
    drop_4h_threshold: float = 0.12  # 12%
    drop_24h_threshold: float = 0.20  # 20%
    drop_7d_threshold: float = 0.30  # 30%

    # Cooldown after event fires (hours)
    cooldown_hours: int = 24

    # Delta range for OTM calls
    min_delta: float = 0.10
    max_delta: float = 0.20
    preferred_delta: float = 0.15

    # Days to expiry (DTE) range
    min_dte: int = 90
    max_dte: int = 270
    preferred_dte_min: int = 120
    preferred_dte_max: int = 180

    # Open interest minimums (by asset)
    min_oi_btc: int = 50
    min_oi_eth: int = 200

    # Bid/ask spread filter (relative to mid IV)
    max_bid_ask_spread_relative: float = 0.08  # 8%

    # Conviction thresholds for alerts
    alert_high_threshold: float = 0.70  # 70%
    alert_medium_threshold: float = 0.50  # 50%

    # Risk management
    risk_budget_usd: float = 10000.0
    position_size_pct: float = 0.02  # 2% per trade

    # DVOL signal thresholds (in standard deviations)
    dvol_sweet_spot_low: float = 1.5
    dvol_sweet_spot_high: float = 2.5

    # Max pain distance (for 100% signal score)
    max_pain_distance_full_score: float = 0.10  # 10% below pain

    @field_validator("drop_1h_threshold", "drop_4h_threshold", "drop_24h_threshold", "drop_7d_threshold")
    @classmethod
    def validate_drop_threshold(cls, v: float) -> float:
        if not (0 < v <= 1.0):
            raise ValueError(f"Drop threshold must be between 0 and 1, got {v}")
        return v

    @field_validator("min_delta", "max_delta", "preferred_delta")
    @classmethod
    def validate_delta(cls, v: float) -> float:
        if not (0 < v <= 1.0):
            raise ValueError(f"Delta must be between 0 and 1, got {v}")
        return v

    @field_validator("alert_high_threshold", "alert_medium_threshold", "position_size_pct", "max_bid_ask_spread_relative")
    @classmethod
    def validate_percentage(cls, v: float) -> float:
        if not (0 <= v <= 1.0):
            raise ValueError(f"Value must be between 0 and 1, got {v}")
        return v
