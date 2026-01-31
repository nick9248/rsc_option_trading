"""
Unit tests for Long Put strategy implementation.

Following Bull Call Spread gold standard testing pattern.
"""

import pytest
from unittest.mock import Mock

from coding.core.strategy.definitions.long_put import LongPut
from coding.core.strategy.models.long_put_config import LongPutConfig


class TestLongPutBasics:
    """Test basic Long Put functionality."""

    def test_strategy_name(self):
        """Test strategy name is correct."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        assert strategy.name == "Long Put"

    def test_strategy_type(self):
        """Test strategy type is directional_bearish."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        assert strategy.strategy_type == "directional_bearish"

    def test_get_default_config(self):
        """Test default configuration values."""
        config = LongPut.get_default_config()
        assert "target_delta" in config
        assert "max_loss_percentage" in config
        assert config["target_delta"] == 0.30  # Absolute value
        assert config["max_loss_percentage"] == 5.0


class TestLongPutBuildLegs:
    """Test Long Put leg building with different selection methods."""

    @pytest.fixture
    def mock_ticker_data(self):
        """Create mock ticker data for testing."""
        return {
            "BTC-31JAN25-90000-P": {
                "best_ask_price": 0.015,
                "best_bid_price": 0.013,
                "mark_price": 0.014,
                "greeks": {
                    "delta": -0.15,
                    "gamma": 0.000005,
                    "theta": -30,
                    "vega": 150,
                    "iv": 0.75
                }
            },
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,
                "best_bid_price": 0.028,
                "mark_price": 0.029,
                "greeks": {
                    "delta": -0.30,
                    "gamma": 0.000008,
                    "theta": -40,
                    "vega": 180,
                    "iv": 0.70
                }
            },
            "BTC-31JAN25-100000-P": {
                "best_ask_price": 0.05,
                "best_bid_price": 0.048,
                "mark_price": 0.049,
                "greeks": {
                    "delta": -0.50,
                    "gamma": 0.00001,
                    "theta": -50,
                    "vega": 200,
                    "iv": 0.65
                }
            }
        }

    def test_build_legs_by_delta(self, mock_ticker_data):
        """Test building legs with by_delta method."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(mock_ticker_data, config)

        assert len(strategy.legs) == 1
        leg = strategy.legs[0]

        assert leg.action == "buy"
        assert leg.option_type == "put"
        assert leg.strike == 95000.0
        assert leg.quantity == 1
        assert leg.greeks["delta"] == -0.30  # Put delta is negative
        assert leg.greeks["iv"] == 0.70  # Verify IV is included
        assert leg.cost > 0  # Cost should be positive (debit)

    def test_build_legs_by_moneyness(self, mock_ticker_data):
        """Test building legs with by_moneyness method."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        config = LongPutConfig(method="by_moneyness", moneyness_pct=5.0, quantity=1)

        strategy.build_legs(mock_ticker_data, config)

        assert len(strategy.legs) == 1
        leg = strategy.legs[0]

        # Should select 95000 strike (closest to 5% OTM = 95000)
        assert leg.strike == 95000.0
        assert leg.action == "buy"

    def test_build_legs_by_strike(self, mock_ticker_data):
        """Test building legs with by_strike method."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        config = LongPutConfig(method="by_strike", specific_strike=90000.0, quantity=1)

        strategy.build_legs(mock_ticker_data, config)

        assert len(strategy.legs) == 1
        leg = strategy.legs[0]

        assert leg.strike == 90000.0

    def test_build_legs_with_quantity(self, mock_ticker_data):
        """Test building legs with quantity > 1."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=5)

        strategy.build_legs(mock_ticker_data, config)

        leg = strategy.legs[0]
        assert leg.quantity == 5

    def test_build_legs_fallback_to_mark_price(self):
        """Test fallback to mark_price when ask_price is 0."""
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0,  # Zero ask price
                "best_bid_price": 0,
                "mark_price": 0.029,
                "greeks": {
                    "delta": -0.30,
                    "gamma": 0.000008,
                    "theta": -40,
                    "vega": 180,
                    "iv": 0.70
                }
            }
        }

        strategy = LongPut("BTC", "31JAN25", 100000.0)
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(ticker_data, config)

        leg = strategy.legs[0]
        # Should use mark_price (0.029 * 100000 = 2900)
        assert leg.cost == pytest.approx(2900.0, abs=1.0)

    def test_build_legs_no_matching_puts_raises_error(self):
        """Test that ValueError is raised when no matching puts found."""
        strategy = LongPut("ETH", "31JAN25", 3000.0)
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        # Ticker data has BTC puts, not ETH
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,
                "greeks": {"delta": -0.30}
            }
        }

        with pytest.raises(ValueError, match="No put options found"):
            strategy.build_legs(ticker_data, config)


class TestLongPutRiskMetrics:
    """Test Long Put risk and profit calculations."""

    def test_get_max_risk(self):
        """Test max risk calculation (premium paid)."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,
                "greeks": {"delta": -0.30, "iv": 0.70}
            }
        }
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(ticker_data, config)

        max_risk = strategy.get_max_risk()
        # Premium = 0.03 * 100000 = 3000
        assert max_risk == pytest.approx(3000.0, abs=1.0)

    def test_get_max_profit(self):
        """Test max profit calculation (strike - premium)."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,  # Premium = 3000
                "greeks": {"delta": -0.30, "iv": 0.70}
            }
        }
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(ticker_data, config)

        max_profit = strategy.get_max_profit()
        # Max profit = strike - premium = 95000 - 3000 = 92000
        assert max_profit == pytest.approx(92000.0, abs=10.0)

    def test_get_breakeven_points(self):
        """Test breakeven calculation (strike - premium)."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,  # Premium = 0.03 * 100000 = 3000
                "greeks": {"delta": -0.30, "iv": 0.70}
            }
        }
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(ticker_data, config)

        breakeven_points = strategy.get_breakeven_points()
        assert len(breakeven_points) == 1

        # Breakeven = strike - premium = 95000 - 3000 = 92000
        assert breakeven_points[0] == pytest.approx(92000.0, abs=10.0)

    def test_get_breakeven_with_multiple_contracts(self):
        """Test breakeven calculation with quantity > 1."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,
                "greeks": {"delta": -0.30, "iv": 0.70}
            }
        }
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=3)

        strategy.build_legs(ticker_data, config)

        breakeven_points = strategy.get_breakeven_points()

        # Breakeven per contract should be same regardless of quantity
        # Total premium = 0.03 * 100000 * 3 = 9000
        # Premium per contract = 9000 / 3 = 3000
        # Breakeven = 95000 - 3000 = 92000
        assert breakeven_points[0] == pytest.approx(92000.0, abs=10.0)


class TestLongPutHelperMethods:
    """Test Long Put helper methods."""

    def test_is_matching_put_valid(self):
        """Test _is_matching_put returns True for valid put."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_put("BTC-31JAN25-95000-P", {}) is True

    def test_is_matching_put_wrong_currency(self):
        """Test _is_matching_put returns False for wrong currency."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_put("ETH-31JAN25-95000-P", {}) is False

    def test_is_matching_put_wrong_expiration(self):
        """Test _is_matching_put returns False for wrong expiration."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_put("BTC-28FEB25-95000-P", {}) is False

    def test_is_matching_put_call_instead(self):
        """Test _is_matching_put returns False for call option."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_put("BTC-31JAN25-95000-C", {}) is False

    def test_extract_strike_from_name(self):
        """Test _extract_strike_from_name returns correct strike."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)

        strike = strategy._extract_strike_from_name("BTC-31JAN25-95000-P")
        assert strike == 95000.0

    def test_extract_strike_from_name_invalid_format(self):
        """Test _extract_strike_from_name raises error for invalid format."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)

        with pytest.raises(ValueError, match="Invalid instrument name format"):
            strategy._extract_strike_from_name("INVALID")


class TestLongPutGreeks:
    """Test Long Put greek aggregation."""

    def test_greeks_single_leg(self):
        """Test greeks are correctly extracted for single leg."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,
                "greeks": {
                    "delta": -0.30,
                    "gamma": 0.000008,
                    "theta": -40,
                    "vega": 180,
                    "iv": 0.70
                }
            }
        }
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(ticker_data, config)

        net_greeks = strategy.get_net_greeks()

        assert net_greeks["delta"] == pytest.approx(-0.30, abs=0.01)
        assert net_greeks["gamma"] == pytest.approx(0.000008, abs=0.000001)
        assert net_greeks["theta"] == pytest.approx(-40, abs=1)
        assert net_greeks["vega"] == pytest.approx(180, abs=1)

    def test_greeks_multiple_contracts(self):
        """Test greeks scale with quantity."""
        strategy = LongPut("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-95000-P": {
                "best_ask_price": 0.03,
                "greeks": {
                    "delta": -0.30,
                    "gamma": 0.000008,
                    "theta": -40,
                    "vega": 180,
                    "iv": 0.70
                }
            }
        }
        config = LongPutConfig(method="by_delta", target_delta=0.30, quantity=3)

        strategy.build_legs(ticker_data, config)

        net_greeks = strategy.get_net_greeks()

        # Greeks should be multiplied by quantity
        assert net_greeks["delta"] == pytest.approx(-0.90, abs=0.01)
        assert net_greeks["gamma"] == pytest.approx(0.000024, abs=0.000001)
        assert net_greeks["theta"] == pytest.approx(-120, abs=1)
        assert net_greeks["vega"] == pytest.approx(540, abs=1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
