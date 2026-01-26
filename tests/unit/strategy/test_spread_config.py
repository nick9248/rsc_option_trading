"""
Unit tests for SpreadStrikeConfig Pydantic model.

Tests configuration validation, immutability, and error handling.
"""

import pytest
from pydantic import ValidationError

from coding.core.strategy.models.spread_config import SpreadStrikeConfig


class TestSpreadConfigCreation:
    """Test basic configuration creation."""

    def test_skew_aware_config_creation(self):
        """Test creating skew-aware configuration."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            min_profit_debit_ratio=0.5,
            quantity=1
        )

        assert config.method == "skew_aware"
        assert config.optimize_for == "profit_debit_ratio"
        assert config.min_profit_debit_ratio == 0.5
        assert config.quantity == 1

    def test_by_delta_config_creation(self):
        """Test creating by-delta configuration."""
        config = SpreadStrikeConfig(
            method="by_delta",
            long_target_delta=0.50,
            short_target_delta=0.30,
            quantity=2
        )

        assert config.method == "by_delta"
        assert config.long_target_delta == 0.50
        assert config.short_target_delta == 0.30
        assert config.quantity == 2

    def test_by_moneyness_config_creation(self):
        """Test creating by-moneyness configuration."""
        config = SpreadStrikeConfig(
            method="by_moneyness",
            long_moneyness_pct=2.0,
            short_moneyness_pct=5.0,
            quantity=1
        )

        assert config.method == "by_moneyness"
        assert config.long_moneyness_pct == 2.0
        assert config.short_moneyness_pct == 5.0

    def test_by_strike_config_creation(self):
        """Test creating by-strike configuration."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        assert config.method == "by_strike"
        assert config.long_specific_strike == 100000.0
        assert config.short_specific_strike == 105000.0


class TestSpreadConfigValidation:
    """Test Pydantic validation logic."""

    def test_invalid_method_raises_error(self):
        """Test that invalid method raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="invalid_method",
                quantity=1
            )

        assert "method" in str(exc_info.value)

    def test_missing_max_budget_for_max_width_mode(self):
        """Test that max_width_for_budget requires max_budget."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                optimize_for="max_width_for_budget",
                quantity=1
            )

        assert "max_budget" in str(exc_info.value)

    def test_missing_deltas_for_by_delta_method(self):
        """Test that by_delta method requires both deltas."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_delta",
                long_target_delta=0.50,
                quantity=1
            )

        assert "short_target_delta" in str(exc_info.value)

    def test_missing_moneyness_for_by_moneyness_method(self):
        """Test that by_moneyness method requires both moneyness values."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_moneyness",
                long_moneyness_pct=2.0,
                quantity=1
            )

        assert "short_moneyness_pct" in str(exc_info.value)

    def test_missing_strikes_for_by_strike_method(self):
        """Test that by_strike method requires both strikes."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_strike",
                long_specific_strike=100000.0,
                quantity=1
            )

        assert "short_specific_strike" in str(exc_info.value)

    def test_invalid_delta_ordering(self):
        """Test that long delta must be > short delta."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_delta",
                long_target_delta=0.30,
                short_target_delta=0.50,
                quantity=1
            )

        assert "long_target_delta" in str(exc_info.value)
        assert "short_target_delta" in str(exc_info.value)

    def test_invalid_moneyness_ordering(self):
        """Test that long moneyness must be < short moneyness."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_moneyness",
                long_moneyness_pct=5.0,
                short_moneyness_pct=2.0,
                quantity=1
            )

        assert "long_moneyness_pct" in str(exc_info.value)
        assert "short_moneyness_pct" in str(exc_info.value)

    def test_invalid_strike_ordering(self):
        """Test that long strike must be < short strike."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_strike",
                long_specific_strike=105000.0,
                short_specific_strike=100000.0,
                quantity=1
            )

        assert "long_specific_strike" in str(exc_info.value)
        assert "short_specific_strike" in str(exc_info.value)

    def test_negative_max_budget_raises_error(self):
        """Test that negative max_budget raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                optimize_for="max_width_for_budget",
                max_budget=-100.0,
                quantity=1
            )

        assert "max_budget" in str(exc_info.value)

    def test_negative_target_width_pct_raises_error(self):
        """Test that negative target_width_pct raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                target_width_pct=-5.0,
                quantity=1
            )

        assert "target_width_pct" in str(exc_info.value)

    def test_target_width_pct_over_100_raises_error(self):
        """Test that target_width_pct > 100 raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                target_width_pct=150.0,
                quantity=1
            )

        assert "target_width_pct" in str(exc_info.value)

    def test_negative_min_profit_debit_ratio_raises_error(self):
        """Test that negative min_profit_debit_ratio raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                min_profit_debit_ratio=-0.5,
                quantity=1
            )

        assert "min_profit_debit_ratio" in str(exc_info.value)

    def test_delta_out_of_range_raises_error(self):
        """Test that delta outside [0, 1] raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_delta",
                long_target_delta=1.5,
                short_target_delta=0.30,
                quantity=1
            )

        assert "Delta" in str(exc_info.value)

    def test_negative_strike_raises_error(self):
        """Test that negative strikes raise error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="by_strike",
                long_specific_strike=-100000.0,
                short_specific_strike=105000.0,
                quantity=1
            )

        assert "Strike" in str(exc_info.value)

    def test_zero_quantity_raises_error(self):
        """Test that zero quantity raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                quantity=0
            )

        assert "quantity" in str(exc_info.value)

    def test_negative_quantity_raises_error(self):
        """Test that negative quantity raises error."""
        with pytest.raises(ValidationError) as exc_info:
            SpreadStrikeConfig(
                method="skew_aware",
                quantity=-1
            )

        assert "quantity" in str(exc_info.value)


class TestSpreadConfigImmutability:
    """Test that configurations are immutable (frozen)."""

    def test_config_is_frozen(self):
        """Test that config cannot be modified after creation."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            quantity=1
        )

        with pytest.raises(ValidationError):
            config.quantity = 5

    def test_config_attributes_immutable(self):
        """Test that individual attributes cannot be changed."""
        config = SpreadStrikeConfig(
            method="by_delta",
            long_target_delta=0.50,
            short_target_delta=0.30,
            quantity=1
        )

        with pytest.raises(ValidationError):
            config.method = "skew_aware"


class TestSpreadConfigMethods:
    """Test configuration methods."""

    def test_to_dict_method(self):
        """Test to_dict conversion."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            min_profit_debit_ratio=0.5,
            quantity=2
        )

        config_dict = config.to_dict()

        assert config_dict["method"] == "skew_aware"
        assert config_dict["optimize_for"] == "profit_debit_ratio"
        assert config_dict["min_profit_debit_ratio"] == 0.5
        assert config_dict["quantity"] == 2

    def test_repr_skew_aware(self):
        """Test __repr__ for skew-aware config."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            quantity=1
        )

        repr_str = repr(config)

        assert "skew_aware" in repr_str
        assert "profit_debit_ratio" in repr_str

    def test_repr_by_delta(self):
        """Test __repr__ for by-delta config."""
        config = SpreadStrikeConfig(
            method="by_delta",
            long_target_delta=0.50,
            short_target_delta=0.30,
            quantity=1
        )

        repr_str = repr(config)

        assert "by_delta" in repr_str
        assert "0.5" in repr_str
        assert "0.3" in repr_str

    def test_repr_by_moneyness(self):
        """Test __repr__ for by-moneyness config."""
        config = SpreadStrikeConfig(
            method="by_moneyness",
            long_moneyness_pct=2.0,
            short_moneyness_pct=5.0,
            quantity=1
        )

        repr_str = repr(config)

        assert "by_moneyness" in repr_str
        assert "2.0" in repr_str
        assert "5.0" in repr_str

    def test_repr_by_strike(self):
        """Test __repr__ for by-strike config."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        repr_str = repr(config)

        assert "by_strike" in repr_str
        assert "100000" in repr_str
        assert "105000" in repr_str
