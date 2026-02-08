"""
ML training configuration models.

Pydantic models for type-safe, validated ML configuration.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class LightGBMParams(BaseModel):
    """
    LightGBM hyperparameters.

    Default values are conservative for financial time-series data.
    """
    num_leaves: int = Field(default=31, ge=2, le=131072)
    max_depth: int = Field(default=-1, ge=-1)  # -1 = unlimited
    learning_rate: float = Field(default=0.05, gt=0, le=1)
    n_estimators: int = Field(default=500, ge=1, le=10000)
    min_child_samples: int = Field(default=20, ge=1)
    subsample: float = Field(default=0.8, gt=0, le=1)
    colsample_bytree: float = Field(default=0.8, gt=0, le=1)
    reg_alpha: float = Field(default=0.1, ge=0)  # L1 regularization
    reg_lambda: float = Field(default=0.1, ge=0)  # L2 regularization
    class_weight: Optional[str] = Field(default="balanced")
    random_state: int = Field(default=42)
    verbose: int = Field(default=-1)

    class Config:
        frozen = True  # Immutable after creation


class WalkForwardConfig(BaseModel):
    """
    Walk-forward validation settings.

    Time-series aware validation that respects temporal order.
    NO random splitting - always train on past, test on future.
    """
    min_train_hours: int = Field(
        default=720,  # 30 days
        ge=24,  # Minimum 1 day (reduced for testing with limited data)
        description="Minimum hours for initial training set"
    )
    test_hours: int = Field(
        default=168,  # 7 days
        ge=12,  # Minimum 12 hours (reduced for testing with limited data)
        description="Hours in each test fold"
    )
    step_hours: int = Field(
        default=168,  # 7 days
        ge=1,
        description="Hours to advance between folds"
    )
    expanding: bool = Field(
        default=True,
        description="Expanding window (grows) vs sliding window (fixed size)"
    )
    min_folds: int = Field(
        default=3,
        ge=2,
        description="Minimum number of folds required for valid results"
    )

    class Config:
        frozen = True


class MLTrainingConfig(BaseModel):
    """
    Complete ML training configuration.

    Defines targets, features, validation strategy, and model params.
    """
    # Targets
    classification_target: str = Field(
        default="market_regime",
        description="Target for classification (market regime)"
    )
    regression_targets: List[str] = Field(
        default=["realized_vol_24h"],
        description="Targets for regression tasks"
    )

    # Feature selection
    feature_categories: List[str] = Field(
        default=["technical", "onchain", "market_structure", "derived"],
        description="Feature categories to include"
    )
    min_feature_importance: float = Field(
        default=0.01,
        ge=0,
        le=1,
        description="Drop features with importance below this threshold"
    )

    # Validation
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)

    # Model parameters
    classifier_params: LightGBMParams = Field(default_factory=LightGBMParams)
    regressor_params: LightGBMParams = Field(
        default_factory=lambda: LightGBMParams(class_weight=None)
    )

    # Data quality
    max_missing_feature_pct: float = Field(
        default=20.0,
        ge=0,
        le=100,
        description="Max % missing values allowed per feature"
    )
    min_samples: int = Field(
        default=500,
        ge=100,
        description="Minimum training samples required"
    )

    # Output
    model_dir: str = Field(
        default="models",
        description="Directory to save trained models"
    )

    @field_validator("feature_categories")
    @classmethod
    def validate_feature_categories(cls, v):
        """Ensure valid feature categories."""
        valid = {"technical", "onchain", "market_structure", "sentiment", "derived"}
        invalid = set(v) - valid
        if invalid:
            raise ValueError(f"Invalid feature categories: {invalid}. Must be in {valid}")
        return v

    @field_validator("classification_target")
    @classmethod
    def validate_classification_target(cls, v):
        """Ensure valid classification target (single string)."""
        valid_targets = {
            "market_regime",
            "realized_vol_24h",
            "realized_vol_7d",
            "trend_strength",
            "trend_direction"
        }

        if v not in valid_targets:
            raise ValueError(f"Invalid target: {v}. Must be in {valid_targets}")

        return v  # Return as string

    @field_validator("regression_targets")
    @classmethod
    def validate_regression_targets(cls, v):
        """Ensure valid regression targets (list of strings)."""
        if isinstance(v, str):
            v = [v]

        valid_targets = {
            "market_regime",
            "realized_vol_24h",
            "realized_vol_7d",
            "trend_strength",
            "trend_direction"
        }

        invalid = set(v) - valid_targets
        if invalid:
            raise ValueError(f"Invalid targets: {invalid}. Must be in {valid_targets}")

        return v  # Return as list
