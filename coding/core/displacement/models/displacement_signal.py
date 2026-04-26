from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict, field_validator


class DisplacementSignal(BaseModel):
    """
    A scored displacement event with recommended strike selection and conviction probability.
    """

    model_config = ConfigDict(frozen=True)

    # Event details
    asset: str
    detected_at: datetime
    drop_24h_pct: float
    drop_1h_pct: float

    # Conviction score (0–100%)
    conviction_pct: float
    conviction_label: str  # "HIGH", "MEDIUM", "LOW"

    # Six signal scores (0–100 each)
    score_drop_magnitude: float
    score_drop_speed: float
    score_funding_rate: float
    score_dvol_spike: float
    score_max_pain: float
    score_term_structure: float

    # Signal details (for formatting)
    funding_rate_value: float
    dvol_sigma: float
    max_pain_distance_pct: float
    term_structure_inversion_pct: float

    # Recommended contract (optional if no qualifying contract exists)
    instrument_name: Optional[str] = None
    strike: Optional[float] = None
    expiry_date: Optional[date] = None
    dte: Optional[int] = None
    delta: Optional[float] = None
    mark_iv: Optional[float] = None
    premium_usd: Optional[float] = None
    target_50pct_price: Optional[float] = None
    target_100pct_price: Optional[float] = None
    target_200pct_price: Optional[float] = None

    @field_validator("conviction_pct")
    @classmethod
    def validate_conviction(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError(f"Conviction must be 0–100, got {v}")
        return v

    @field_validator(
        "score_drop_magnitude",
        "score_drop_speed",
        "score_funding_rate",
        "score_dvol_spike",
        "score_max_pain",
        "score_term_structure",
    )
    @classmethod
    def validate_score(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError(f"Score must be 0–100, got {v}")
        return v
