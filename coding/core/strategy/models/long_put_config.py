"""
Pydantic configuration model for Long Put strategy.

This module provides type-safe, validated configuration for Long Put options strategy
using Pydantic for enhanced validation and immutability.
"""

import logging
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

logger = logging.getLogger(__name__)


class LongPutConfig(BaseModel):
    """
    Pydantic configuration model for Long Put strike selection.

    Provides type-safe, validated configuration for single-leg Long Put strategy.

    Benefits of Pydantic approach:
    - Type Safety: Compile-time checking, IDE autocomplete
    - Validation: Automatic validators ensure config correctness
    - Immutability: Frozen configs prevent accidental mutation
    - Clear API: Self-documenting with field types and defaults
    - No Typos: Eliminates kwargs dictionary key errors

    Methods:
    - by_delta: Select strike closest to target delta (e.g., -0.30, use absolute value 0.30)
    - by_moneyness: Select strike at X% OTM from current price (e.g., 5% below)
    - by_strike: Use specific strike value

    Example Usage (by_delta):
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,  # Absolute value, sign handled by strategy
            quantity=1
        )
        strategy.build_legs(ticker_data=data, config=config)

    Example Usage (by_moneyness):
        config = LongPutConfig(
            method="by_moneyness",
            moneyness_pct=10.0,  # 10% below current price
            quantity=1
        )
        strategy.build_legs(ticker_data=data, config=config)

    Example Usage (by_strike):
        config = LongPutConfig(
            method="by_strike",
            specific_strike=95000.0,
            quantity=1
        )
        strategy.build_legs(ticker_data=data, config=config)
    """

    # Strike selection method
    method: Literal["by_delta", "by_moneyness", "by_strike"]

    # Method-specific parameters
    target_delta: Optional[float] = None  # For by_delta method (absolute value, e.g., 0.30)
    moneyness_pct: Optional[float] = None  # For by_moneyness method (e.g., 5.0 for 5% below)
    specific_strike: Optional[float] = None  # For by_strike method

    # Position sizing
    quantity: int = 1  # Number of contracts

    # Validation constraints
    min_delta: float = 0.15  # Minimum delta (absolute value) to prevent lottery tickets
    max_delta: float = 0.70  # Maximum delta (absolute value) to avoid deep ITM

    model_config = ConfigDict(
        frozen=True,  # Make config immutable
        validate_assignment=True  # Validate on attribute assignment
    )

    @field_validator('target_delta')
    @classmethod
    def validate_target_delta(cls, v: Optional[float]) -> Optional[float]:
        """
        Validate target_delta is in valid range [0, 1].

        Note: For puts, delta is negative, but we use absolute value in config.
        """
        if v is not None:
            if not (0 < v < 1):
                raise ValueError(f"target_delta must be between 0 and 1 (absolute value), got {v}")
        return v

    @field_validator('moneyness_pct')
    @classmethod
    def validate_moneyness_pct(cls, v: Optional[float]) -> Optional[float]:
        """Validate moneyness_pct is reasonable."""
        if v is not None:
            if v < 0:
                raise ValueError(f"moneyness_pct must be non-negative for puts, got {v}")
            if v > 100:
                raise ValueError(
                    f"moneyness_pct must be <= 100% (strike cannot be negative), got {v}"
                )
        return v

    @field_validator('specific_strike')
    @classmethod
    def validate_specific_strike(cls, v: Optional[float]) -> Optional[float]:
        """Validate specific_strike is positive."""
        if v is not None and v <= 0:
            raise ValueError(f"specific_strike must be positive, got {v}")
        return v

    @field_validator('quantity')
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """Validate quantity is positive."""
        if v <= 0:
            raise ValueError(f"quantity must be positive, got {v}")
        return v

    @field_validator('min_delta')
    @classmethod
    def validate_min_delta(cls, v: float) -> float:
        """Validate min_delta is in valid range."""
        if not (0 < v < 1):
            raise ValueError(f"min_delta must be between 0 and 1, got {v}")
        return v

    @field_validator('max_delta')
    @classmethod
    def validate_max_delta(cls, v: float) -> float:
        """Validate max_delta is in valid range."""
        if not (0 < v < 1):
            raise ValueError(f"max_delta must be between 0 and 1, got {v}")
        return v

    @model_validator(mode='after')
    def validate_method_requirements(self) -> 'LongPutConfig':
        """
        Validate that required parameters are provided for each method.

        This validator runs after all field validators and checks cross-field constraints.
        """
        method = self.method

        # By-delta method validation
        if method == "by_delta":
            if self.target_delta is None:
                raise ValueError("target_delta required for by_delta method")

            # Validate delta is within allowed range
            if self.target_delta < self.min_delta:
                logger.warning(
                    f"target_delta ({self.target_delta:.3f}) < min_delta ({self.min_delta:.3f}). "
                    f"This may be a lottery ticket trade with low probability of profit."
                )
            if self.target_delta > self.max_delta:
                logger.warning(
                    f"target_delta ({self.target_delta:.3f}) > max_delta ({self.max_delta:.3f}). "
                    f"This may be deep ITM with low leverage."
                )

        # By-moneyness method validation
        elif method == "by_moneyness":
            if self.moneyness_pct is None:
                raise ValueError("moneyness_pct required for by_moneyness method")

        # By-strike method validation
        elif method == "by_strike":
            if self.specific_strike is None:
                raise ValueError("specific_strike required for by_strike method")

        # Validate min_delta < max_delta
        if self.min_delta >= self.max_delta:
            raise ValueError(
                f"min_delta ({self.min_delta}) must be < max_delta ({self.max_delta})"
            )

        return self

    def to_dict(self) -> dict:
        """
        Convert configuration to dictionary.

        Returns:
            Dictionary representation of configuration
        """
        return self.model_dump()

    def __repr__(self) -> str:
        """String representation of configuration."""
        if self.method == "by_delta":
            return (
                f"LongPutConfig(method='by_delta', "
                f"target_delta={self.target_delta}, "
                f"quantity={self.quantity})"
            )
        elif self.method == "by_moneyness":
            return (
                f"LongPutConfig(method='by_moneyness', "
                f"moneyness_pct={self.moneyness_pct}%, "
                f"quantity={self.quantity})"
            )
        else:  # by_strike
            return (
                f"LongPutConfig(method='by_strike', "
                f"specific_strike={self.specific_strike}, "
                f"quantity={self.quantity})"
            )
