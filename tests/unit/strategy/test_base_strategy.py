"""Unit tests for BaseStrategy abstract class."""

import pytest

from coding.core.strategy.definitions import LongCall, StrategyLeg


def test_strategy_leg_creation():
    """Test creating a strategy leg."""
    leg = StrategyLeg(
        action="buy",
        option_type="call",
        strike=100000.0,
        quantity=1,
        cost=1000.0,
        greeks={"delta": 0.5, "gamma": 0.01, "theta": -0.5, "vega": 0.2},
        instrument_name="BTC-31JAN25-100000-C"
    )

    assert leg.action == "buy"
    assert leg.option_type == "call"
    assert leg.strike == 100000.0
    assert leg.quantity == 1
    assert leg.cost == 1000.0
    assert leg.greeks["delta"] == 0.5


def test_strategy_leg_validation():
    """Test strategy leg validation."""
    # Invalid action
    with pytest.raises(ValueError, match="Invalid action"):
        StrategyLeg(
            action="invalid",
            option_type="call",
            strike=100000.0,
            quantity=1,
            cost=1000.0
        )

    # Invalid option_type
    with pytest.raises(ValueError, match="Invalid option_type"):
        StrategyLeg(
            action="buy",
            option_type="invalid",
            strike=100000.0,
            quantity=1,
            cost=1000.0
        )

    # Invalid strike
    with pytest.raises(ValueError, match="Strike must be positive"):
        StrategyLeg(
            action="buy",
            option_type="call",
            strike=-100.0,
            quantity=1,
            cost=1000.0
        )


def test_long_call_creation():
    """Test creating a Long Call strategy."""
    strategy = LongCall(
        currency="BTC",
        expiration="31JAN25",
        underlying_price=100000.0
    )

    assert strategy.name == "Long Call"
    assert strategy.strategy_type == "directional_bullish"
    assert strategy.currency == "BTC"
    assert strategy.expiration == "31JAN25"
    assert strategy.underlying_price == 100000.0


def test_long_call_max_loss_percentage():
    """Test max loss percentage calculation."""
    strategy = LongCall(
        currency="BTC",
        expiration="31JAN25",
        underlying_price=100000.0
    )

    # Manually add a leg for testing
    strategy.legs = [
        StrategyLeg(
            action="buy",
            option_type="call",
            strike=105000.0,
            quantity=1,
            cost=2000.0,  # $2000 cost
            greeks={"delta": 0.3, "gamma": 0.01, "theta": -0.5, "vega": 0.2}
        )
    ]

    max_loss_pct = strategy.get_max_loss_percentage()

    # Max loss is $2000 on $100000 underlying = 2%
    assert abs(max_loss_pct - 2.0) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
