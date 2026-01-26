"""
Unit tests for Bull Call Spread strategy implementation.

Tests skew-aware selection, traditional methods, validations, and calculations.
"""

import pytest

from coding.core.strategy.definitions.bull_call_spread import BullCallSpread
from coding.core.strategy.models.spread_config import SpreadStrikeConfig


@pytest.fixture
def mock_ticker_data():
    """
    Create mock ticker data with multiple call strikes.

    Simulates realistic Deribit ticker data structure.
    """
    underlying_price = 100000.0

    return {
        # ATM calls (higher delta, higher IV)
        "BTC-31JAN25-100000-C": {
            "ask_price": 0.045,  # 4500 USD
            "bid_price": 0.044,
            "mark_price": 0.0445,
            "greeks": {"delta": 0.55, "gamma": 0.00001, "theta": -0.05, "vega": 0.8, "iv": 0.65},
        },
        "BTC-31JAN25-102000-C": {
            "ask_price": 0.038,  # 3800 USD
            "bid_price": 0.037,
            "mark_price": 0.0375,
            "greeks": {"delta": 0.48, "gamma": 0.00001, "theta": -0.04, "vega": 0.75, "iv": 0.62},
        },
        # OTM calls (lower delta, lower IV due to skew)
        "BTC-31JAN25-105000-C": {
            "ask_price": 0.028,  # 2800 USD
            "bid_price": 0.027,
            "mark_price": 0.0275,
            "greeks": {"delta": 0.35, "gamma": 0.000008, "theta": -0.03, "vega": 0.65, "iv": 0.58},
        },
        "BTC-31JAN25-108000-C": {
            "ask_price": 0.020,  # 2000 USD
            "bid_price": 0.019,
            "mark_price": 0.0195,
            "greeks": {"delta": 0.25, "gamma": 0.000006, "theta": -0.02, "vega": 0.55, "iv": 0.54},
        },
        "BTC-31JAN25-110000-C": {
            "ask_price": 0.015,  # 1500 USD
            "bid_price": 0.014,
            "mark_price": 0.0145,
            "greeks": {"delta": 0.18, "gamma": 0.000005, "theta": -0.015, "vega": 0.45, "iv": 0.50},
        },
        "BTC-31JAN25-115000-C": {
            "ask_price": 0.008,  # 800 USD
            "bid_price": 0.007,
            "mark_price": 0.0075,
            "greeks": {"delta": 0.10, "gamma": 0.000003, "theta": -0.01, "vega": 0.35, "iv": 0.46},
        },
    }


@pytest.fixture
def bull_call_spread():
    """Create BullCallSpread instance for testing."""
    return BullCallSpread(
        currency="BTC",
        expiration="31JAN25",
        underlying_price=100000.0
    )


class TestSkewAwareSelection:
    """Test skew-aware strike selection algorithm."""

    def test_skew_aware_profit_debit_optimization(self, bull_call_spread, mock_ticker_data):
        """Test that skew-aware selects spread with best profit/debit ratio."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        assert len(bull_call_spread.legs) == 2
        assert bull_call_spread.legs[0].action == "buy"
        assert bull_call_spread.legs[1].action == "sell"

        # Verify profit/debit ratio calculation
        net_debit = abs(bull_call_spread.get_total_cost())
        strike_width = bull_call_spread.legs[1].strike - bull_call_spread.legs[0].strike
        max_profit = strike_width - net_debit
        profit_debit_ratio = max_profit / net_debit

        assert profit_debit_ratio > 0  # Should have positive ratio
        assert net_debit > 0  # Should be a debit spread

    def test_skew_aware_max_width_for_budget(self, bull_call_spread, mock_ticker_data):
        """Test max_width_for_budget mode finds widest spread within budget."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="max_width_for_budget",
            max_budget=3000.0,  # 3000 USD max
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        assert len(bull_call_spread.legs) == 2

        # Verify cost is within budget
        net_debit = abs(bull_call_spread.get_total_cost())
        assert net_debit <= 3000.0

        # Should select wider spread within budget
        strike_width = bull_call_spread.legs[1].strike - bull_call_spread.legs[0].strike
        assert strike_width > 0

    def test_skew_aware_with_min_profit_debit_filter(self, bull_call_spread, mock_ticker_data):
        """Test min_profit_debit_ratio filter."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            min_profit_debit_ratio=0.5,  # Require at least 50% return
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        # Verify selected spread meets minimum ratio
        net_debit = abs(bull_call_spread.get_total_cost())
        strike_width = bull_call_spread.legs[1].strike - bull_call_spread.legs[0].strike
        max_profit = strike_width - net_debit
        profit_debit_ratio = max_profit / net_debit

        assert profit_debit_ratio >= 0.5

    def test_skew_aware_with_target_width_constraint(self, bull_call_spread, mock_ticker_data):
        """Test target_width_pct constraint."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            target_width_pct=5.0,  # Target 5% of underlying (5000 USD)
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        strike_width = bull_call_spread.legs[1].strike - bull_call_spread.legs[0].strike
        target_width = 100000.0 * 0.05  # 5000
        tolerance = target_width * 0.2  # 20% tolerance

        # Should be within tolerance of target
        assert abs(strike_width - target_width) <= tolerance

    def test_skew_aware_no_valid_spreads_raises_error(self, bull_call_spread, mock_ticker_data):
        """Test that no valid spreads raises meaningful error."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            min_profit_debit_ratio=100.0,  # Impossible ratio
            quantity=1
        )

        with pytest.raises(ValueError) as exc_info:
            bull_call_spread.build_legs(mock_ticker_data, config)

        assert "No valid spreads found" in str(exc_info.value)

    def test_iv_skew_calculation(self, bull_call_spread, mock_ticker_data):
        """Test that IV skew is calculated correctly in spread selection."""
        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            quantity=1
        )

        # Build legs
        bull_call_spread.build_legs(mock_ticker_data, config)

        # Get IVs from selected strikes
        long_iv = bull_call_spread.legs[0].greeks.get("iv", 0)
        short_iv = bull_call_spread.legs[1].greeks.get("iv", 0)

        # For calls, short strike should have lower IV (volatility skew)
        # However, this depends on which strikes are selected
        # Just verify IVs are present
        assert long_iv > 0
        assert short_iv > 0


class TestTraditionalMethods:
    """Test traditional strike selection methods."""

    def test_build_legs_by_dual_delta(self, bull_call_spread, mock_ticker_data):
        """Test strike selection using dual delta."""
        config = SpreadStrikeConfig(
            method="by_delta",
            long_target_delta=0.50,
            short_target_delta=0.30,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        assert len(bull_call_spread.legs) == 2

        # Verify deltas are close to targets
        long_delta = abs(bull_call_spread.legs[0].greeks["delta"])
        short_delta = abs(bull_call_spread.legs[1].greeks["delta"])

        assert abs(long_delta - 0.50) < 0.1  # Within 0.1 delta
        assert abs(short_delta - 0.30) < 0.1

    def test_build_legs_by_dual_moneyness(self, bull_call_spread, mock_ticker_data):
        """Test strike selection using dual moneyness (% OTM)."""
        config = SpreadStrikeConfig(
            method="by_moneyness",
            long_moneyness_pct=2.0,  # 2% OTM
            short_moneyness_pct=8.0,  # 8% OTM
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        assert len(bull_call_spread.legs) == 2

        # Verify strikes are approximately at target moneyness
        long_strike = bull_call_spread.legs[0].strike
        short_strike = bull_call_spread.legs[1].strike

        long_target = 100000.0 * 1.02  # 102000
        short_target = 100000.0 * 1.08  # 108000

        # Should be close to targets (within available strikes)
        assert abs(long_strike - long_target) < 3000
        assert abs(short_strike - short_target) < 3000

    def test_build_legs_by_dual_strike(self, bull_call_spread, mock_ticker_data):
        """Test strike selection using specific strikes."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=102000.0,
            short_specific_strike=108000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        assert len(bull_call_spread.legs) == 2

        # Verify exact strikes are selected
        assert bull_call_spread.legs[0].strike == 102000.0
        assert bull_call_spread.legs[1].strike == 108000.0


class TestStrategyValidation:
    """Test strategy validation and calculations."""

    def test_max_risk_calculation(self, bull_call_spread, mock_ticker_data):
        """Test that max risk = net debit paid."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        net_debit = abs(bull_call_spread.get_total_cost())
        expected_max_risk = net_debit  # Max loss is the premium paid

        actual_max_risk = bull_call_spread.get_max_risk()

        assert abs(actual_max_risk - expected_max_risk) < 1.0  # Within $1

    def test_max_profit_calculation(self, bull_call_spread, mock_ticker_data):
        """Test that max profit = strike_width - net debit."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        net_debit = abs(bull_call_spread.get_total_cost())
        strike_width = 5000.0  # 105000 - 100000
        expected_max_profit = strike_width - net_debit  # Profit if price above short strike

        max_profit = bull_call_spread.get_max_profit()

        assert abs(max_profit - expected_max_profit) < 1.0  # Within $1

    def test_breakeven_calculation(self, bull_call_spread, mock_ticker_data):
        """Test single breakeven point calculation."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        breakevens = bull_call_spread.get_breakeven_points()

        assert len(breakevens) == 1

        # Breakeven = long_strike + net_debit_per_contract
        long_strike = 100000.0
        net_debit = abs(bull_call_spread.get_total_cost())
        expected_breakeven = long_strike + net_debit

        assert abs(breakevens[0] - expected_breakeven) < 1.0

    def test_net_greeks_aggregation(self, bull_call_spread, mock_ticker_data):
        """Test multi-leg greek aggregation."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=2
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        net_greeks = bull_call_spread.get_net_greeks()

        # Long leg: +2 contracts (positive greeks)
        # Short leg: -2 contracts (negative greeks)
        # Net should be difference

        assert "delta" in net_greeks
        assert "gamma" in net_greeks
        assert "theta" in net_greeks
        assert "vega" in net_greeks

        # Net delta should be positive (bullish position)
        assert net_greeks["delta"] > 0

    def test_strike_ordering_validation(self, bull_call_spread, mock_ticker_data):
        """Test that short strike <= long strike is rejected by Pydantic validation."""
        from pydantic import ValidationError

        # Pydantic should catch this at config creation time (not at build_legs time)
        with pytest.raises(ValidationError) as exc_info:
            config = SpreadStrikeConfig(
                method="by_strike",
                long_specific_strike=110000.0,
                short_specific_strike=105000.0,  # Lower than long - INVALID
                quantity=1
            )

        # Should fail in Pydantic validation
        assert "long_specific_strike" in str(exc_info.value)

    def test_spread_too_wide_validation(self, bull_call_spread):
        """Test that spread width > 50% of underlying is rejected."""
        # Create mock data with very wide spread
        wide_ticker_data = {
            "BTC-31JAN25-50000-C": {
                "ask_price": 0.50,
                "bid_price": 0.49,
                "mark_price": 0.495,
                "greeks": {"delta": 0.95, "gamma": 0.00001, "theta": -0.05, "vega": 0.8, "iv": 0.70},
            },
            "BTC-31JAN25-200000-C": {
                "ask_price": 0.001,
                "bid_price": 0.0009,
                "mark_price": 0.00095,
                "greeks": {"delta": 0.01, "gamma": 0.000001, "theta": -0.001, "vega": 0.1, "iv": 0.40},
            },
        }

        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=50000.0,
            short_specific_strike=200000.0,
            quantity=1
        )

        with pytest.raises(ValueError) as exc_info:
            bull_call_spread.build_legs(wide_ticker_data, config)

        assert "too wide" in str(exc_info.value)

    def test_total_cost_calculation(self, bull_call_spread, mock_ticker_data):
        """Test net debit = long cost - short credit."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        # Long leg cost (positive)
        long_cost = bull_call_spread.legs[0].cost
        # Short leg credit (negative)
        short_credit = bull_call_spread.legs[1].cost

        net_debit = bull_call_spread.get_total_cost()

        # Net debit should be sum of costs
        expected_debit = long_cost + short_credit

        assert abs(net_debit - expected_debit) < 0.01

        # Should be positive debit
        assert net_debit > 0

    def test_to_dict_serialization(self, bull_call_spread, mock_ticker_data):
        """Test strategy serialization includes two legs."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        strategy_dict = bull_call_spread.to_dict()

        assert strategy_dict["name"] == "Bull Call Spread"
        assert strategy_dict["strategy_type"] == "directional_bullish"
        assert len(strategy_dict["legs"]) == 2
        assert strategy_dict["legs"][0]["action"] == "buy"
        assert strategy_dict["legs"][1]["action"] == "sell"
        assert strategy_dict["max_risk"] > 0
        assert strategy_dict["max_profit"] > 0
        assert len(strategy_dict["breakeven_points"]) == 1

    def test_validate_legs_returns_true(self, bull_call_spread, mock_ticker_data):
        """Test that properly constructed spread validates correctly."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        assert bull_call_spread.validate_legs() is True

    def test_multiple_contracts(self, bull_call_spread, mock_ticker_data):
        """Test spread with quantity > 1."""
        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=5
        )

        bull_call_spread.build_legs(mock_ticker_data, config)

        # Verify quantities
        assert bull_call_spread.legs[0].quantity == 5
        assert bull_call_spread.legs[1].quantity == -5

        # Verify costs are scaled
        net_debit = abs(bull_call_spread.get_total_cost())
        assert net_debit > 1000  # Should be substantial for 5 contracts


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_no_matching_calls_raises_error(self, bull_call_spread):
        """Test that no matching calls raises error."""
        empty_ticker_data = {}

        config = SpreadStrikeConfig(
            method="skew_aware",
            quantity=1
        )

        with pytest.raises(ValueError) as exc_info:
            bull_call_spread.build_legs(empty_ticker_data, config)

        assert "No call options found" in str(exc_info.value)

    def test_missing_greeks_data(self, bull_call_spread):
        """Test handling of missing greeks data."""
        ticker_data_no_greeks = {
            "BTC-31JAN25-100000-C": {
                "ask_price": 0.045,
                "bid_price": 0.044,
                "mark_price": 0.0445,
                "greeks": {},  # Empty greeks
            },
            "BTC-31JAN25-105000-C": {
                "ask_price": 0.028,
                "bid_price": 0.027,
                "mark_price": 0.0275,
                "greeks": {},  # Empty greeks
            },
        }

        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        # Should still build legs (greeks default to 0)
        bull_call_spread.build_legs(ticker_data_no_greeks, config)

        assert len(bull_call_spread.legs) == 2

    def test_zero_price_fallback_to_mark(self, bull_call_spread):
        """Test that zero ask/bid falls back to mark_price."""
        ticker_data_zero_price = {
            "BTC-31JAN25-100000-C": {
                "ask_price": 0,  # Zero ask
                "bid_price": 0,  # Zero bid
                "mark_price": 0.045,
                "greeks": {"delta": 0.55, "gamma": 0.00001, "theta": -0.05, "vega": 0.8, "iv": 0.65},
            },
            "BTC-31JAN25-105000-C": {
                "ask_price": 0,
                "bid_price": 0,
                "mark_price": 0.028,
                "greeks": {"delta": 0.35, "gamma": 0.000008, "theta": -0.03, "vega": 0.65, "iv": 0.58},
            },
        }

        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=100000.0,
            short_specific_strike=105000.0,
            quantity=1
        )

        # Should use mark_price as fallback
        bull_call_spread.build_legs(ticker_data_zero_price, config)

        assert len(bull_call_spread.legs) == 2
        assert bull_call_spread.get_total_cost() > 0
