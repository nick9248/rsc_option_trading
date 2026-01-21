"""
Unit tests for PnL calculator.

Tests strategy P&L calculation for various scenarios.
"""

import pytest

from coding.core.strategy.definitions.base_strategy import StrategyLeg
from coding.core.strategy.pnl_calculator import StrategyPnLCalculator


class TestPnLCalculator:
    """Test cases for StrategyPnLCalculator."""

    def test_long_call_at_expiry_below_strike(self):
        """Test long call P&L when price is below strike (max loss)."""
        # Long 1 CALL @ $3500, premium = 0.0175 ETH = $58.35 USD
        # Underlying = $3334
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148, "gamma": 0.001180, "theta": -4.6113, "vega": 2.1141}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # At $3000 (below strike): intrinsic = 0, P&L = -premium
        pnl = calculator.calculate_pnl_at_price(3000.0, at_expiry=True)
        assert pnl == pytest.approx(-58.35, abs=0.01)

        # At strike: intrinsic = 0, P&L = -premium
        pnl = calculator.calculate_pnl_at_price(3500.0, at_expiry=True)
        assert pnl == pytest.approx(-58.35, abs=0.01)

    def test_long_call_at_expiry_above_strike(self):
        """Test long call P&L when price is above strike (profit)."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148, "gamma": 0.001180}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # At $3558.35 (breakeven): intrinsic = 58.35, P&L = 0
        pnl = calculator.calculate_pnl_at_price(3558.35, at_expiry=True)
        assert pnl == pytest.approx(0.0, abs=0.01)

        # At $4000: intrinsic = 500, P&L = 500 - 58.35 = 441.65
        pnl = calculator.calculate_pnl_at_price(4000.0, at_expiry=True)
        assert pnl == pytest.approx(441.65, abs=0.01)

    def test_long_put_at_expiry_below_strike(self):
        """Test long put P&L when price is below strike (profit)."""
        leg = StrategyLeg(
            action="buy",
            option_type="put",
            strike=3200.0,
            quantity=1,
            cost=45.0,
            greeks={"delta": -0.25, "gamma": 0.001}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # At $3000: intrinsic = 200, P&L = 200 - 45 = 155
        pnl = calculator.calculate_pnl_at_price(3000.0, at_expiry=True)
        assert pnl == pytest.approx(155.0, abs=0.01)

        # At $3155 (breakeven): intrinsic = 45, P&L = 0
        pnl = calculator.calculate_pnl_at_price(3155.0, at_expiry=True)
        assert pnl == pytest.approx(0.0, abs=0.01)

    def test_long_put_at_expiry_above_strike(self):
        """Test long put P&L when price is above strike (max loss)."""
        leg = StrategyLeg(
            action="buy",
            option_type="put",
            strike=3200.0,
            quantity=1,
            cost=45.0,
            greeks={"delta": -0.25}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # At $3500 (above strike): intrinsic = 0, P&L = -premium
        pnl = calculator.calculate_pnl_at_price(3500.0, at_expiry=True)
        assert pnl == pytest.approx(-45.0, abs=0.01)

    def test_pnl_profile_calculation(self):
        """Test P&L profile across price range."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # Calculate profile from 3000 to 4000
        profile = calculator.calculate_pnl_profile((3000.0, 4000.0), num_points=11)

        # Check profile contains expected price points
        assert len(profile) == 11
        assert 3000.0 in profile
        assert 4000.0 in profile

        # Check P&L values at known points
        assert profile[3000.0] == pytest.approx(-58.35, abs=0.01)  # Below strike
        assert profile[3500.0] == pytest.approx(-58.35, abs=0.01)  # At strike
        assert profile[4000.0] == pytest.approx(441.65, abs=0.01)  # Above strike

    def test_breakeven_calculation_long_call(self):
        """Test breakeven point calculation for long call."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        breakevens = calculator.get_breakeven_points((3000.0, 4000.0))

        # Long call has one breakeven: strike + premium
        assert len(breakevens) == 1
        assert breakevens[0] == pytest.approx(3558.35, abs=1.0)

    def test_breakeven_calculation_long_put(self):
        """Test breakeven point calculation for long put."""
        leg = StrategyLeg(
            action="buy",
            option_type="put",
            strike=3200.0,
            quantity=1,
            cost=45.0,
            greeks={"delta": -0.25}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        breakevens = calculator.get_breakeven_points((3000.0, 3500.0))

        # Long put has one breakeven: strike - premium
        assert len(breakevens) == 1
        assert breakevens[0] == pytest.approx(3155.0, abs=1.0)

    def test_max_profit_long_call(self):
        """Test max profit for long call (unlimited)."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        max_profit = calculator.get_max_profit((3000.0, 4000.0))

        # Long call has unlimited profit
        assert max_profit == float('inf')

    def test_max_loss_long_option(self):
        """Test max loss for long option (limited to premium)."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        max_loss = calculator.get_max_loss((3000.0, 4000.0))

        # Long option max loss = premium paid
        assert max_loss == pytest.approx(-58.35, abs=0.01)

    def test_invalid_price_range(self):
        """Test error handling for invalid price range."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # Min > Max should raise error
        with pytest.raises(ValueError):
            calculator.calculate_pnl_profile((4000.0, 3000.0))

    def test_invalid_num_points(self):
        """Test error handling for invalid num_points."""
        leg = StrategyLeg(
            action="buy",
            option_type="call",
            strike=3500.0,
            quantity=1,
            cost=58.35,
            greeks={"delta": 0.3148}
        )

        calculator = StrategyPnLCalculator(legs=[leg], underlying_price=3334.0)

        # num_points < 2 should raise error
        with pytest.raises(ValueError):
            calculator.calculate_pnl_profile((3000.0, 4000.0), num_points=1)

    def test_no_legs_error(self):
        """Test error when no legs provided."""
        with pytest.raises(ValueError):
            StrategyPnLCalculator(legs=[], underlying_price=3334.0)
