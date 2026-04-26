from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict, field_validator


class DisplacementSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Event
    asset: str
    detected_at: datetime
    drop_24h_pct: float
    drop_1h_pct: float

    # Conviction
    conviction_pct: float       # 0–100
    conviction_label: str       # "HIGH" or "MEDIUM"

    # Signal scores (each 0–100)
    score_drop_magnitude: float
    score_drop_speed: float
    score_funding_rate: float
    score_dvol_spike: float
    score_max_pain: float
    score_term_structure: float

    # Raw signal values (for display in alert)
    funding_rate_value: float          # e.g. -0.008
    dvol_sigma: float                  # σ above historical mean
    max_pain_distance_pct: float       # spot distance below max pain
    term_structure_inversion_pct: float  # front_iv - back_iv (positive = inverted)

    # Recommended contract (None when no qualifying contract found)
    instrument_name: Optional[str] = None
    strike: Optional[float] = None
    expiry_date: Optional[date] = None
    dte: Optional[int] = None
    delta: Optional[float] = None
    mark_iv: Optional[float] = None
    premium_usd: Optional[float] = None

    # Profit targets (None when no contract)
    target_50pct_price: Optional[float] = None
    target_100pct_price: Optional[float] = None
    target_200pct_price: Optional[float] = None

    telegram_sent: bool = False

    @field_validator(
        "conviction_pct", "score_drop_magnitude", "score_drop_speed",
        "score_funding_rate", "score_dvol_spike", "score_max_pain",
        "score_term_structure",
    )
    @classmethod
    def validate_score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 100.0:
            raise ValueError(f"Score must be between 0 and 100, got {v}")
        return v
