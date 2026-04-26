from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DisplacementEvent(BaseModel):
    """
    Represents a detected price displacement event.
    """

    model_config = ConfigDict(frozen=True)

    asset: str
    detected_at: datetime
    current_price: float
    drop_1h_pct: float
    drop_4h_pct: float
    drop_24h_pct: float
    drop_7d_pct: float
    triggering_timeframe: str  # "1h", "4h", "24h", or "7d"
