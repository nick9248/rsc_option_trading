from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DisplacementEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: str                     # "BTC" or "ETH"
    detected_at: datetime
    current_price: float
    drop_1h_pct: float             # positive decimal = drop (e.g. 0.09 = 9% drop)
    drop_4h_pct: float
    drop_24h_pct: float
    drop_7d_pct: float
    triggering_timeframe: str      # "1h", "4h", "24h", or "7d"
