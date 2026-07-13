"""
Pydantic models for hourly snapshot data with strict validation.

Ensures type safety and data integrity throughout the aggregation pipeline.
"""

from datetime import datetime
from typing import Optional
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator


class HourlySnapshotData(BaseModel):
    """
    Validated hourly snapshot data model.

    Matches the hourly_snapshots database schema exactly.
    All fields are validated before database insertion.
    """

    # Identification
    currency: str = Field(..., min_length=3, max_length=10, description="Currency code (BTC, ETH)")
    instrument_name: str = Field(..., min_length=1, max_length=50, description="Deribit instrument name")
    timestamp: datetime = Field(..., description="Hour bucket timestamp")

    # Price data (DECIMAL(18,8) in database)
    mark_price: float = Field(..., gt=0, description="VWAP for the hour")
    bid_price: float = Field(..., gt=0, description="Estimated bid price")
    ask_price: float = Field(..., gt=0, description="Estimated ask price")

    # Volatility (DECIMAL(8,4) in database - 0 to 9999.9999%)
    mark_iv: Optional[float] = Field(None, ge=0, le=500.0, description="Average IV for the hour")

    # Underlying price (DECIMAL(18,8) in database)
    underlying_price: Optional[float] = Field(None, gt=0, description="Average index price")

    # Volume data
    volume: float = Field(..., ge=0, description="Total volume in the hour")
    trade_count: int = Field(..., ge=0, description="Number of trades")

    # Greeks - CRITICAL: Must match database precision
    # avg_delta: DECIMAL(10,8) → -99.99999999 to 99.99999999
    # But delta should be -1 to 1, so this is safe
    delta: Optional[float] = Field(None, ge=-1.0, le=1.0, description="Average delta")

    # avg_gamma: DECIMAL(12,10) → -99.9999999999 to 99.9999999999
    # Gamma is always positive, typically very small
    gamma: Optional[float] = Field(None, ge=0, description="Average gamma")

    # avg_theta: DECIMAL(10,8) → -99.99999999 to 99.99999999
    # Theta is typically negative, represents daily decay
    theta: Optional[float] = Field(None, description="Average theta (per day)")

    # avg_vega: DECIMAL(10,8) → -99.99999999 to 99.99999999
    # Vega is always positive, represents IV sensitivity
    vega: Optional[float] = Field(None, ge=0, description="Average vega")

    model_config = {"frozen": False, "validate_assignment": True}

    @field_validator('mark_iv')
    @classmethod
    def validate_iv(cls, v: Optional[float]) -> Optional[float]:
        """Validate IV is in reasonable range (0-500%)."""
        if v is not None:
            if v < 0:
                raise ValueError(f"IV cannot be negative: {v}")
            # Deep OTM and near-expiry options can have IV > 300%
            if v > 500:
                raise ValueError(f"IV too high (>500%): {v}")
        return v

    @field_validator('delta')
    @classmethod
    def validate_delta(cls, v: Optional[float]) -> Optional[float]:
        """Validate delta is in valid range (-1 to 1)."""
        if v is not None:
            if not -1.0 <= v <= 1.0:
                raise ValueError(f"Delta must be between -1 and 1: {v}")
        return v

    @field_validator('gamma')
    @classmethod
    def validate_gamma(cls, v: Optional[float]) -> Optional[float]:
        """Validate gamma is positive."""
        if v is not None:
            if v < 0:
                raise ValueError(f"Gamma must be positive: {v}")
            if v > 1.0:
                raise ValueError(f"Gamma too large (>1): {v}")
        return v

    @field_validator('theta')
    @classmethod
    def validate_theta(cls, v: Optional[float]) -> Optional[float]:
        """Validate theta is reasonable (per day decay)."""
        if v is not None:
            # BTC at $100k near expiry can have theta > 1000 for ATM options
            # Near-expiry ATM gamma spike causes very large theta
            if abs(v) > 5000:
                raise ValueError(f"Theta magnitude too large (>5000): {v}")
        return v

    @field_validator('vega')
    @classmethod
    def validate_vega(cls, v: Optional[float]) -> Optional[float]:
        """Validate vega is positive and reasonable."""
        if v is not None:
            if v < 0:
                raise ValueError(f"Vega must be positive: {v}")
            # BTC/ETH options can have large vega (especially longer-dated)
            # Reasonable range: 0 to 1000
            if v > 1000:
                raise ValueError(f"Vega too large (>1000): {v}")
        return v

    @field_validator('bid_price', 'ask_price', 'mark_price')
    @classmethod
    def validate_prices(cls, v: float) -> float:
        """Validate prices are positive and reasonable."""
        if v <= 0:
            raise ValueError(f"Price must be positive: {v}")
        if v > 1_000_000:
            raise ValueError(f"Price too high (>1M): {v}")
        return v

    def to_db_tuple(self) -> tuple:
        """
        Convert to database tuple for insertion.

        Returns tuple in exact order matching INSERT statement.
        """
        return (
            self.currency,
            self.instrument_name,
            self.timestamp,
            self.mark_price,
            self.bid_price,
            self.ask_price,
            self.mark_iv,
            self.underlying_price,
            self.volume,
            self.trade_count,
            self.delta,
            self.gamma,
            self.theta,
            self.vega
        )


class GreeksData(BaseModel):
    """
    Validated Greeks data from Black-Scholes calculation.

    Ensures Greeks are in valid ranges before use.
    """

    delta: float = Field(..., ge=-1.0, le=1.0, description="Delta: -1 to 1")
    gamma: float = Field(..., ge=0, le=1.0, description="Gamma: 0 to 1")
    theta: float = Field(..., description="Theta: daily decay")
    vega: float = Field(..., ge=0, description="Vega: IV sensitivity")
    rho: float = Field(..., description="Rho: rate sensitivity")

    model_config = {"frozen": True}

    @field_validator('delta')
    @classmethod
    def validate_delta(cls, v: float) -> float:
        """Validate delta is in valid range."""
        if not -1.0 <= v <= 1.0:
            raise ValueError(f"Delta must be between -1 and 1: {v}")
        return v

    @field_validator('gamma')
    @classmethod
    def validate_gamma(cls, v: float) -> float:
        """Validate gamma is positive."""
        if v < 0:
            raise ValueError(f"Gamma must be positive: {v}")
        # Gamma is typically small for BTC/ETH (0.00001 to 0.001)
        # But can be larger for ATM options
        if v > 0.01:
            raise ValueError(f"Gamma too large (>0.01): {v}")
        return v

    @field_validator('vega')
    @classmethod
    def validate_vega(cls, v: float) -> float:
        """Validate vega is positive."""
        if v < 0:
            raise ValueError(f"Vega must be positive: {v}")
        return v


class TradeData(BaseModel):
    """
    Validated trade data model.

    Represents a single trade from historical_trades table.
    """

    instrument_name: str = Field(..., description="Deribit instrument name")
    price: float = Field(..., gt=0, description="Trade price")
    amount: float = Field(..., gt=0, description="Trade amount")
    direction: str = Field(..., pattern="^(buy|sell)$", description="Trade direction")
    iv: Optional[float] = Field(None, ge=0, le=300, description="Implied volatility %")
    index_price: Optional[float] = Field(None, gt=0, description="Index price at trade time")
    mark_price: Optional[float] = Field(None, gt=0, description="Mark price at trade time")

    model_config = {"frozen": True}
