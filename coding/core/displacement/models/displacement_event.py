from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class DisplacementEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: Literal["BTC", "ETH"]
    detected_at: datetime
    current_price: float = Field(..., gt=0)
    drop_1h_pct: float = Field(..., ge=0)   # positive decimal = drop (e.g. 0.22 = 22% drop)
    drop_4h_pct: float = Field(..., ge=0)   # positive decimal = drop (e.g. 0.22 = 22% drop)
    drop_24h_pct: float = Field(..., ge=0)  # positive decimal = drop (e.g. 0.22 = 22% drop)
    drop_7d_pct: float = Field(..., ge=0)   # positive decimal = drop (e.g. 0.22 = 22% drop)
    triggering_timeframe: Literal["1h", "4h", "24h", "7d"]
