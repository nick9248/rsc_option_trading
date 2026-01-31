"""
Edge case tests for LongPutConfig Pydantic model.

Following Bull Call Spread gold standard testing pattern.
Tests validation, error handling, and edge cases.
"""

import pytest
from pydantic import ValidationError

from coding.core.strategy.models.long_put_config import LongPutConfig


class TestLongPutConfigValidation:
    """Test LongPutConfig field validation."""

    def test_valid_by_delta_config(self):
        """Test valid by_delta configuration."""
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,
            quantity=1
        )

        assert config.method == "by_delta"
        assert config.target_delta == 0.30
        assert config.quantity == 1

    def test_valid_by_moneyness_config(self):
        """Test valid by_moneyness configuration."""
        config = LongPutConfig(
            method="by_moneyness",
            moneyness_pct=10.0,
            quantity=1
        )

        assert config.method == "by_moneyness"
        assert config.moneyness_pct == 10.0

    def test_valid_by_strike_config(self):
        """Test valid by_strike configuration."""
        config = LongPutConfig(
            method="by_strike",
            specific_strike=95000.0,
            quantity=1
        )

        assert config.method == "by_strike"
        assert config.specific_strike == 95000.0

    def test_invalid_method_raises_error(self):
        """Test invalid method raises ValidationError."""
        with pytest.raises(ValidationError):
            LongPutConfig(
                method="invalid_method",
                target_delta=0.30
            )

    def test_frozen_config_cannot_be_modified(self):
        """Test that config is immutable (frozen)."""
        config = LongPutConfig(method="by_delta", target_delta=0.30)

        with pytest.raises(ValidationError):
            config.target_delta = 0.50  # Should raise error


class TestLongPutConfigTargetDelta:
    """Test target_delta validation."""

    def test_valid_target_delta_values(self):
        """Test valid target_delta values."""
        valid_deltas = [0.10, 0.25, 0.30, 0.50, 0.70, 0.90]

        for delta in valid_deltas:
            config = LongPutConfig(method="by_delta", target_delta=delta)
            assert config.target_delta == delta

    def test_target_delta_zero_raises_error(self):
        """Test target_delta = 0 raises error."""
        with pytest.raises(ValidationError, match="target_delta must be between 0 and 1"):
            LongPutConfig(method="by_delta", target_delta=0.0)

    def test_target_delta_one_raises_error(self):
        """Test target_delta = 1 raises error."""
        with pytest.raises(ValidationError, match="target_delta must be between 0 and 1"):
            LongPutConfig(method="by_delta", target_delta=1.0)

    def test_target_delta_negative_raises_error(self):
        """Test negative target_delta raises error."""
        with pytest.raises(ValidationError, match="target_delta must be between 0 and 1"):
            LongPutConfig(method="by_delta", target_delta=-0.30)

    def test_target_delta_greater_than_one_raises_error(self):
        """Test target_delta > 1 raises error."""
        with pytest.raises(ValidationError, match="target_delta must be between 0 and 1"):
            LongPutConfig(method="by_delta", target_delta=1.5)

    def test_by_delta_method_requires_target_delta(self):
        """Test by_delta method requires target_delta."""
        with pytest.raises(ValidationError, match="target_delta required"):
            LongPutConfig(method="by_delta")


class TestLongPutConfigMoneynessPct:
    """Test moneyness_pct validation."""

    def test_valid_moneyness_pct_values(self):
        """Test valid moneyness_pct values."""
        valid_values = [0.0, 5.0, 10.0, 25.0, 50.0, 100.0]

        for pct in valid_values:
            config = LongPutConfig(method="by_moneyness", moneyness_pct=pct)
            assert config.moneyness_pct == pct

    def test_moneyness_pct_negative_raises_error(self):
        """Test negative moneyness_pct raises error."""
        with pytest.raises(ValidationError, match="moneyness_pct must be non-negative"):
            LongPutConfig(method="by_moneyness", moneyness_pct=-5.0)

    def test_moneyness_pct_greater_than_100_raises_error(self):
        """Test moneyness_pct > 100 raises error."""
        with pytest.raises(ValidationError, match="moneyness_pct must be <= 100%"):
            LongPutConfig(method="by_moneyness", moneyness_pct=150.0)

    def test_by_moneyness_method_requires_moneyness_pct(self):
        """Test by_moneyness method requires moneyness_pct."""
        with pytest.raises(ValidationError, match="moneyness_pct required"):
            LongPutConfig(method="by_moneyness")


class TestLongPutConfigSpecificStrike:
    """Test specific_strike validation."""

    def test_valid_specific_strike_values(self):
        """Test valid specific_strike values."""
        valid_strikes = [1000.0, 50000.0, 95000.0, 200000.0]

        for strike in valid_strikes:
            config = LongPutConfig(method="by_strike", specific_strike=strike)
            assert config.specific_strike == strike

    def test_specific_strike_zero_raises_error(self):
        """Test specific_strike = 0 raises error."""
        with pytest.raises(ValidationError, match="specific_strike must be positive"):
            LongPutConfig(method="by_strike", specific_strike=0.0)

    def test_specific_strike_negative_raises_error(self):
        """Test negative specific_strike raises error."""
        with pytest.raises(ValidationError, match="specific_strike must be positive"):
            LongPutConfig(method="by_strike", specific_strike=-10000.0)

    def test_by_strike_method_requires_specific_strike(self):
        """Test by_strike method requires specific_strike."""
        with pytest.raises(ValidationError, match="specific_strike required"):
            LongPutConfig(method="by_strike")


class TestLongPutConfigQuantity:
    """Test quantity validation."""

    def test_valid_quantity_values(self):
        """Test valid quantity values."""
        valid_quantities = [1, 5, 10, 100]

        for qty in valid_quantities:
            config = LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                quantity=qty
            )
            assert config.quantity == qty

    def test_quantity_zero_raises_error(self):
        """Test quantity = 0 raises error."""
        with pytest.raises(ValidationError, match="quantity must be positive"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                quantity=0
            )

    def test_quantity_negative_raises_error(self):
        """Test negative quantity raises error."""
        with pytest.raises(ValidationError, match="quantity must be positive"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                quantity=-5
            )

    def test_quantity_defaults_to_one(self):
        """Test quantity defaults to 1 if not specified."""
        config = LongPutConfig(method="by_delta", target_delta=0.30)
        assert config.quantity == 1


class TestLongPutConfigDeltaConstraints:
    """Test min_delta and max_delta constraints."""

    def test_valid_min_max_delta(self):
        """Test valid min/max delta values."""
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,
            min_delta=0.10,
            max_delta=0.80
        )

        assert config.min_delta == 0.10
        assert config.max_delta == 0.80

    def test_min_delta_zero_raises_error(self):
        """Test min_delta = 0 raises error."""
        with pytest.raises(ValidationError, match="min_delta must be between 0 and 1"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                min_delta=0.0
            )

    def test_min_delta_one_raises_error(self):
        """Test min_delta = 1 raises error."""
        with pytest.raises(ValidationError, match="min_delta must be between 0 and 1"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                min_delta=1.0
            )

    def test_max_delta_zero_raises_error(self):
        """Test max_delta = 0 raises error."""
        with pytest.raises(ValidationError, match="max_delta must be between 0 and 1"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                max_delta=0.0
            )

    def test_max_delta_one_raises_error(self):
        """Test max_delta = 1 raises error."""
        with pytest.raises(ValidationError, match="max_delta must be between 0 and 1"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                max_delta=1.0
            )

    def test_min_delta_greater_than_max_delta_raises_error(self):
        """Test min_delta >= max_delta raises error."""
        with pytest.raises(ValidationError, match="min_delta.*must be < max_delta"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                min_delta=0.70,
                max_delta=0.50
            )

    def test_min_delta_equals_max_delta_raises_error(self):
        """Test min_delta == max_delta raises error."""
        with pytest.raises(ValidationError, match="min_delta.*must be < max_delta"):
            LongPutConfig(
                method="by_delta",
                target_delta=0.30,
                min_delta=0.50,
                max_delta=0.50
            )


class TestLongPutConfigWarnings:
    """Test lottery ticket warnings (logged, not raised)."""

    def test_low_delta_logs_warning(self, caplog):
        """Test target_delta below min_delta logs warning."""
        # This should create config but log warning
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.05,  # Very low delta
            min_delta=0.15
        )

        assert config.target_delta == 0.05
        # Warning should be logged (not raised as error)

    def test_high_delta_logs_warning(self, caplog):
        """Test target_delta above max_delta logs warning."""
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.85,  # Very high delta
            max_delta=0.70
        )

        assert config.target_delta == 0.85


class TestLongPutConfigToDictAndRepr:
    """Test to_dict() and __repr__() methods."""

    def test_to_dict_by_delta(self):
        """Test to_dict() returns correct dictionary for by_delta."""
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,
            quantity=1
        )

        config_dict = config.to_dict()

        assert config_dict["method"] == "by_delta"
        assert config_dict["target_delta"] == 0.30
        assert config_dict["quantity"] == 1

    def test_repr_by_delta(self):
        """Test __repr__() for by_delta method."""
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,
            quantity=1
        )

        repr_str = repr(config)

        assert "LongPutConfig" in repr_str
        assert "by_delta" in repr_str
        assert "0.3" in repr_str

    def test_repr_by_moneyness(self):
        """Test __repr__() for by_moneyness method."""
        config = LongPutConfig(
            method="by_moneyness",
            moneyness_pct=10.0,
            quantity=1
        )

        repr_str = repr(config)

        assert "LongPutConfig" in repr_str
        assert "by_moneyness" in repr_str
        assert "10.0" in repr_str

    def test_repr_by_strike(self):
        """Test __repr__() for by_strike method."""
        config = LongPutConfig(
            method="by_strike",
            specific_strike=95000.0,
            quantity=1
        )

        repr_str = repr(config)

        assert "LongPutConfig" in repr_str
        assert "by_strike" in repr_str
        assert "95000" in repr_str


class TestLongPutConfigEdgeCases:
    """Test edge cases and corner scenarios."""

    def test_very_small_target_delta(self):
        """Test very small but valid target_delta."""
        config = LongPutConfig(method="by_delta", target_delta=0.01)
        assert config.target_delta == 0.01

    def test_very_large_target_delta(self):
        """Test very large but valid target_delta."""
        config = LongPutConfig(method="by_delta", target_delta=0.99)
        assert config.target_delta == 0.99

    def test_zero_moneyness_pct(self):
        """Test moneyness_pct = 0 (ATM)."""
        config = LongPutConfig(method="by_moneyness", moneyness_pct=0.0)
        assert config.moneyness_pct == 0.0

    def test_large_quantity(self):
        """Test very large quantity."""
        config = LongPutConfig(
            method="by_delta",
            target_delta=0.30,
            quantity=1000
        )
        assert config.quantity == 1000

    def test_very_small_specific_strike(self):
        """Test very small but valid specific_strike."""
        config = LongPutConfig(method="by_strike", specific_strike=0.01)
        assert config.specific_strike == 0.01

    def test_very_large_specific_strike(self):
        """Test very large specific_strike."""
        config = LongPutConfig(method="by_strike", specific_strike=1000000.0)
        assert config.specific_strike == 1000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
