"""
Unit tests for Long Call strategy implementation.

Following Bull Call Spread gold standard testing pattern.
"""

import pytest
from unittest.mock import Mock

from coding.core.strategy.definitions.long_call import LongCall
from coding.core.strategy.models.long_call_config import LongCallConfig


class TestLongCallBasics:
    """Test basic Long Call functionality."""

    def test_strategy_name(self):
        """Test strategy name is correct."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        assert strategy.name == "Long Call"

    def test_strategy_type(self):
        """Test strategy type is directional_bullish."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        assert strategy.strategy_type == "directional_bullish"

    def test_get_default_config(self):
        """Test default configuration values."""
        config = LongCall.get_default_config()
        assert "target_delta" in config
        assert "max_loss_percentage" in config
        assert config["target_delta"] == 0.30
        assert config["max_loss_percentage"] == 5.0


class TestLongCallBuildLegs:
    """Test Long Call leg building with different selection methods."""

    @pytest.fixture
    def mock_ticker_data(self):
        """Create mock ticker data for testing."""
        return {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0.05,
                "best_bid_price": 0.048,
                "mark_price": 0.049,
                "greeks": {
                    "delta": 0.50,
                    "gamma": 0.00001,
                    "theta": -50,
                    "vega": 200,
                    "iv": 0.65
                }
            },
            "BTC-31JAN25-105000-C": {
                "best_ask_price": 0.03,
                "best_bid_price": 0.028,
                "mark_price": 0.029,
                "greeks": {
                    "delta": 0.30,
                    "gamma": 0.000008,
                    "theta": -40,
                    "vega": 180,
                    "iv": 0.70
                }
            },
            "BTC-31JAN25-110000-C": {
                "best_ask_price": 0.015,
                "best_bid_price": 0.013,
                "mark_price": 0.014,
                "greeks": {
                    "delta": 0.15,
                    "gamma": 0.000005,
                    "theta": -30,
                    "vega": 150,
                    "iv": 0.75
                }
            }
        }

    def test_build_legs_by_delta(self, mock_ticker_data):
        """Test building legs with by_delta method."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        config = LongCallConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(mock_ticker_data, config)

        assert len(strategy.legs) == 1
        leg = strategy.legs[0]

        assert leg.action == "buy"
        assert leg.option_type == "call"
        assert leg.strike == 105000.0
        assert leg.quantity == 1
        assert leg.greeks["delta"] == 0.30
        assert leg.greeks["iv"] == 0.70  # Verify IV is included
        assert leg.cost > 0  # Cost should be positive (debit)

    def test_build_legs_by_moneyness(self, mock_ticker_data):
        """Test building legs with by_moneyness method."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        config = LongCallConfig(method="by_moneyness", moneyness_pct=5.0, quantity=1)

        strategy.build_legs(mock_ticker_data, config)

        assert len(strategy.legs) == 1
        leg = strategy.legs[0]

        # Should select 105000 strike (closest to 5% OTM = 105000)
        assert leg.strike == 105000.0
        assert leg.action == "buy"

    def test_build_legs_by_strike(self, mock_ticker_data):
        """Test building legs with by_strike method."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        config = LongCallConfig(method="by_strike", specific_strike=100000.0, quantity=1)

        strategy.build_legs(mock_ticker_data, config)

        assert len(strategy.legs) == 1
        leg = strategy.legs[0]

        assert leg.strike == 100000.0

    def test_build_legs_with_quantity(self, mock_ticker_data):
        """Test building legs with quantity > 1."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        config = LongCallConfig(method="by_delta", target_delta=0.30, quantity=5)

        strategy.build_legs(mock_ticker_data, config)

        leg = strategy.legs[0]
        assert leg.quantity == 5

    def test_build_legs_fallback_to_mark_price(self):
        """Test fallback to mark_price when ask_price is 0."""
        ticker_data = {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0,  # Zero ask price
                "best_bid_price": 0,
                "mark_price": 0.049,
                "greeks": {
                    "delta": 0.50,
                    "gamma": 0.00001,
                    "theta": -50,
                    "vega": 200,
                    "iv": 0.65
                }
            }
        }

        strategy = LongCall("BTC", "31JAN25", 100000.0)
        config = LongCallConfig(method="by_delta", target_delta=0.50, quantity=1)

        strategy.build_legs(ticker_data, config)

        leg = strategy.legs[0]
        # Should use mark_price (0.049 * 100000 = 4900)
        assert leg.cost == pytest.approx(4900.0, abs=1.0)

    def test_build_legs_no_matching_calls_raises_error(self):
        """Test that ValueError is raised when no matching calls found."""
        strategy = LongCall("ETH", "31JAN25", 3000.0)
        config = LongCallConfig(method="by_delta", target_delta=0.30, quantity=1)

        # Ticker data has BTC calls, not ETH
        ticker_data = {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0.05,
                "greeks": {"delta": 0.50}
            }
        }

        with pytest.raises(ValueError, match="No call options found"):
            strategy.build_legs(ticker_data, config)


class TestLongCallRiskMetrics:
    """Test Long Call risk and profit calculations."""

    def test_get_max_risk(self):
        """Test max risk calculation (premium paid)."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0.05,
                "greeks": {"delta": 0.50, "iv": 0.65}
            }
        }
        config = LongCallConfig(method="by_delta", target_delta=0.50, quantity=1)

        strategy.build_legs(ticker_data, config)

        max_risk = strategy.get_max_risk()
        # Premium = 0.05 * 100000 = 5000
        assert max_risk == pytest.approx(5000.0, abs=1.0)

    def test_get_max_profit_is_unlimited(self):
        """Test max profit is None (unlimited)."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0.05,
                "greeks": {"delta": 0.50, "iv": 0.65}
            }
        }
        config = LongCallConfig(method="by_delta", target_delta=0.50, quantity=1)

        strategy.build_legs(ticker_data, config)

        max_profit = strategy.get_max_profit()
        assert max_profit is None  # Unlimited

    def test_get_breakeven_points(self):
        """Test breakeven calculation (strike + premium)."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-105000-C": {
                "best_ask_price": 0.03,  # Premium = 0.03 * 100000 = 3000
                "greeks": {"delta": 0.30, "iv": 0.70}
            }
        }
        config = LongCallConfig(method="by_delta", target_delta=0.30, quantity=1)

        strategy.build_legs(ticker_data, config)

        breakeven_points = strategy.get_breakeven_points()
        assert len(breakeven_points) == 1

        # Breakeven = strike + premium = 105000 + 3000 = 108000
        assert breakeven_points[0] == pytest.approx(108000.0, abs=10.0)

    def test_get_breakeven_with_multiple_contracts(self):
        """Test breakeven calculation with quantity > 1."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-105000-C": {
                "best_ask_price": 0.03,
                "greeks": {"delta": 0.30, "iv": 0.70}
            }
        }
        config = LongCallConfig(method="by_delta", target_delta=0.30, quantity=3)

        strategy.build_legs(ticker_data, config)

        breakeven_points = strategy.get_breakeven_points()

        # Breakeven per contract should be same regardless of quantity
        # Total premium = 0.03 * 100000 * 3 = 9000
        # Premium per contract = 9000 / 3 = 3000
        # Breakeven = 105000 + 3000 = 108000
        assert breakeven_points[0] == pytest.approx(108000.0, abs=10.0)


class TestLongCallHelperMethods:
    """Test Long Call helper methods."""

    def test_is_matching_call_valid(self):
        """Test _is_matching_call returns True for valid call."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_call("BTC-31JAN25-100000-C", {}) is True

    def test_is_matching_call_wrong_currency(self):
        """Test _is_matching_call returns False for wrong currency."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_call("ETH-31JAN25-100000-C", {}) is False

    def test_is_matching_call_wrong_expiration(self):
        """Test _is_matching_call returns False for wrong expiration."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_call("BTC-28FEB25-100000-C", {}) is False

    def test_is_matching_call_put_instead(self):
        """Test _is_matching_call returns False for put option."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)

        assert strategy._is_matching_call("BTC-31JAN25-100000-P", {}) is False

    def testextract_strike_from_name(self):
        """Test extract_strike_from_name returns correct strike."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)

        strike = strategy.extract_strike_from_name("BTC-31JAN25-105000-C")
        assert strike == 105000.0

    def testextract_strike_from_name_invalid_format(self):
        """Test extract_strike_from_name raises error for invalid format."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)

        with pytest.raises(ValueError, match="Invalid instrument name format"):
            strategy.extract_strike_from_name("INVALID")


class TestLongCallGreeks:
    """Test Long Call greek aggregation."""

    def test_greeks_single_leg(self):
        """Test greeks are correctly extracted for single leg."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0.05,
                "greeks": {
                    "delta": 0.50,
                    "gamma": 0.00001,
                    "theta": -50,
                    "vega": 200,
                    "iv": 0.65
                }
            }
        }
        config = LongCallConfig(method="by_delta", target_delta=0.50, quantity=1)

        strategy.build_legs(ticker_data, config)

        net_greeks = strategy.get_net_greeks()

        assert net_greeks["delta"] == pytest.approx(0.50, abs=0.01)
        assert net_greeks["gamma"] == pytest.approx(0.00001, abs=0.000001)
        assert net_greeks["theta"] == pytest.approx(-50, abs=1)
        assert net_greeks["vega"] == pytest.approx(200, abs=1)

    def test_greeks_multiple_contracts(self):
        """Test greeks scale with quantity."""
        strategy = LongCall("BTC", "31JAN25", 100000.0)
        ticker_data = {
            "BTC-31JAN25-100000-C": {
                "best_ask_price": 0.05,
                "greeks": {
                    "delta": 0.50,
                    "gamma": 0.00001,
                    "theta": -50,
                    "vega": 200,
                    "iv": 0.65
                }
            }
        }
        config = LongCallConfig(method="by_delta", target_delta=0.50, quantity=3)

        strategy.build_legs(ticker_data, config)

        net_greeks = strategy.get_net_greeks()

        # Greeks should be multiplied by quantity
        assert net_greeks["delta"] == pytest.approx(1.50, abs=0.01)
        assert net_greeks["gamma"] == pytest.approx(0.00003, abs=0.000001)
        assert net_greeks["theta"] == pytest.approx(-150, abs=1)
        assert net_greeks["vega"] == pytest.approx(600, abs=1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
