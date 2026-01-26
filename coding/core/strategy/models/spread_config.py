"""
Pydantic configuration models for spread strategies.

This module provides type-safe, validated configuration models for multi-leg spread strategies
using Pydantic for enhanced validation and immutability.
"""

import logging
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

logger = logging.getLogger(__name__)


class SpreadStrikeConfig(BaseModel):
    """
    Pydantic configuration model for spread strike selection.

    Provides type-safe, validated configuration for Bull Call Spread and other multi-leg spreads.
    Supports both skew-aware optimization and traditional manual strike selection methods.

    Benefits of Pydantic approach:
    - Type Safety: Compile-time checking, IDE autocomplete
    - Validation: Automatic validators ensure config correctness
    - Immutability: Frozen configs prevent accidental mutation
    - Clear API: Self-documenting with field types and defaults
    - No Typos: Eliminates kwargs dictionary key errors

    Methods:
    - skew_aware: Dynamic optimization using volatility skew analysis
        - profit_debit_ratio: Find spread with best risk/reward ratio
        - max_width_for_budget: Find widest spread within budget constraint
    - by_delta: Manual selection using long/short deltas
    - by_moneyness: Manual selection using % OTM from current price
    - by_strike: Manual selection using specific strike values
    """

    # Strike selection method
    method: Literal["skew_aware", "by_delta", "by_moneyness", "by_strike"]

    # Skew-aware optimization parameters
    optimize_for: Optional[Literal["profit_debit_ratio", "max_width_for_budget"]] = "profit_debit_ratio"
    max_budget: Optional[float] = None  # Maximum debit to pay (for max_width_for_budget mode)
    target_width_pct: Optional[float] = None  # Optional spread width as % of underlying
    min_profit_debit_ratio: Optional[float] = None  # Minimum acceptable profit/debit ratio

    # Traditional method parameters (backward compatibility)
    long_target_delta: Optional[float] = None  # Delta for long leg
    short_target_delta: Optional[float] = None  # Delta for short leg
    long_moneyness_pct: Optional[float] = None  # % OTM for long leg
    short_moneyness_pct: Optional[float] = None  # % OTM for short leg
    long_specific_strike: Optional[float] = None  # Specific strike for long leg
    short_specific_strike: Optional[float] = None  # Specific strike for short leg

    # Position sizing
    quantity: int = 1  # Number of contracts per leg

    model_config = ConfigDict(
        frozen=True,  # Make config immutable
        validate_assignment=True  # Validate on attribute assignment
    )

    @field_validator('max_budget')
    @classmethod
    def validate_max_budget(cls, v: Optional[float]) -> Optional[float]:
        """Validate max_budget is positive if specified."""
        if v is not None and v <= 0:
            raise ValueError(f"max_budget must be positive, got {v}")
        return v

    @field_validator('target_width_pct')
    @classmethod
    def validate_target_width_pct(cls, v: Optional[float]) -> Optional[float]:
        """Validate target_width_pct is reasonable if specified."""
        if v is not None:
            if v <= 0:
                raise ValueError(f"target_width_pct must be positive, got {v}")
            if v > 100:
                raise ValueError(
                    f"target_width_pct must be <= 100% of underlying, got {v}"
                )
        return v

    @field_validator('min_profit_debit_ratio')
    @classmethod
    def validate_min_profit_debit_ratio(cls, v: Optional[float]) -> Optional[float]:
        """Validate min_profit_debit_ratio is reasonable if specified."""
        if v is not None and v < 0:
            raise ValueError(f"min_profit_debit_ratio must be non-negative, got {v}")
        return v

    @field_validator('long_target_delta', 'short_target_delta')
    @classmethod
    def validate_delta(cls, v: Optional[float]) -> Optional[float]:
        """Validate delta is in valid range [0, 1]."""
        if v is not None and not (0 <= v <= 1):
            raise ValueError(f"Delta must be between 0 and 1, got {v}")
        return v

    @field_validator('long_specific_strike', 'short_specific_strike')
    @classmethod
    def validate_strike(cls, v: Optional[float]) -> Optional[float]:
        """Validate strike is positive if specified."""
        if v is not None and v <= 0:
            raise ValueError(f"Strike must be positive, got {v}")
        return v

    @field_validator('quantity')
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """Validate quantity is positive."""
        if v <= 0:
            raise ValueError(f"quantity must be positive, got {v}")
        return v

    @model_validator(mode='after')
    def validate_method_requirements(self) -> 'SpreadStrikeConfig':
        """
        Validate that required parameters are provided for each method.

        This validator runs after all field validators and checks cross-field constraints.
        """
        method = self.method

        # Skew-aware method validation
        if method == "skew_aware":
            if self.optimize_for == "max_width_for_budget" and self.max_budget is None:
                raise ValueError(
                    "max_budget required when optimize_for='max_width_for_budget'"
                )

        # By-delta method validation
        elif method == "by_delta":
            if self.long_target_delta is None or self.short_target_delta is None:
                raise ValueError(
                    "long_target_delta and short_target_delta required for by_delta method"
                )
            # Validate delta ordering for call spreads (long should have higher delta)
            if self.long_target_delta <= self.short_target_delta:
                raise ValueError(
                    f"For call spreads, long_target_delta ({self.long_target_delta}) must be > "
                    f"short_target_delta ({self.short_target_delta})"
                )

        # By-moneyness method validation
        elif method == "by_moneyness":
            if self.long_moneyness_pct is None or self.short_moneyness_pct is None:
                raise ValueError(
                    "long_moneyness_pct and short_moneyness_pct required for by_moneyness method"
                )
            # Validate moneyness ordering for call spreads (long closer to ATM)
            if self.long_moneyness_pct >= self.short_moneyness_pct:
                raise ValueError(
                    f"For call spreads, long_moneyness_pct ({self.long_moneyness_pct}) must be < "
                    f"short_moneyness_pct ({self.short_moneyness_pct})"
                )

        # By-strike method validation
        elif method == "by_strike":
            if self.long_specific_strike is None or self.short_specific_strike is None:
                raise ValueError(
                    "long_specific_strike and short_specific_strike required for by_strike method"
                )
            # Validate strike ordering for call spreads
            if self.long_specific_strike >= self.short_specific_strike:
                raise ValueError(
                    f"For call spreads, long_specific_strike ({self.long_specific_strike}) must be < "
                    f"short_specific_strike ({self.short_specific_strike})"
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
        if self.method == "skew_aware":
            return (
                f"SpreadStrikeConfig(method='skew_aware', "
                f"optimize_for='{self.optimize_for}', "
                f"quantity={self.quantity})"
            )
        elif self.method == "by_delta":
            return (
                f"SpreadStrikeConfig(method='by_delta', "
                f"long_delta={self.long_target_delta}, "
                f"short_delta={self.short_target_delta}, "
                f"quantity={self.quantity})"
            )
        elif self.method == "by_moneyness":
            return (
                f"SpreadStrikeConfig(method='by_moneyness', "
                f"long_otm={self.long_moneyness_pct}%, "
                f"short_otm={self.short_moneyness_pct}%, "
                f"quantity={self.quantity})"
            )
        else:  # by_strike
            return (
                f"SpreadStrikeConfig(method='by_strike', "
                f"long_strike={self.long_specific_strike}, "
                f"short_strike={self.short_specific_strike}, "
                f"quantity={self.quantity})"
            )
