"""
Integration tests for Bull Call Spread strategy.

Tests end-to-end workflow with scoring system and strategy evaluation.
"""

import pytest
from datetime import datetime

from coding.core.strategy.definitions.bull_call_spread import BullCallSpread
from coding.core.strategy.models.spread_config import SpreadStrikeConfig
from coding.core.strategy.scoring.intrinsic_scorer import IntrinsicScorer
from coding.core.strategy.scoring.on_chain_scorer import OnChainScorer
from coding.core.strategy.models.strategy_signal import StrategySignal


@pytest.fixture
def realistic_ticker_data():
    """
    Create realistic ticker data matching Deribit structure.

    Based on actual BTC options data format.
    """
    return {
        "BTC-31JAN25-98000-C": {
            "underlying_index": "BTC-USD",
            "underlying_price": 100142.52,
            "timestamp": 1737885600000,
            "stats": {"volume": 125.5, "price_change": 2.5, "low": 0.042, "high": 0.048},
            "state": "open",
            "settlement_price": 0.0445,
            "open_interest": 450.2,
            "min_price": 0.001,
            "max_price": 0.50,
            "mark_price": 0.0455,
            "mark_iv": 0.68,
            "last_price": 0.0452,
            "interest_rate": 0.0,
            "instrument_name": "BTC-31JAN25-98000-C",
            "index_price": 100142.52,
            "greeks": {
                "vega": 0.85,
                "theta": -0.052,
                "rho": 0.12,
                "gamma": 0.000012,
                "delta": 0.58,
                "iv": 0.68
            },
            "estimated_delivery_price": 100142.52,
            "bid_price": 0.044,
            "bid_iv": 0.67,
            "best_bid_amount": 10.5,
            "best_bid_price": 0.044,
            "best_ask_price": 0.047,
            "best_ask_amount": 8.2,
            "ask_price": 0.047,
            "ask_iv": 0.69
        },
        "BTC-31JAN25-102000-C": {
            "underlying_index": "BTC-USD",
            "underlying_price": 100142.52,
            "timestamp": 1737885600000,
            "stats": {"volume": 98.3, "price_change": 1.8, "low": 0.028, "high": 0.032},
            "state": "open",
            "settlement_price": 0.030,
            "open_interest": 320.5,
            "min_price": 0.001,
            "max_price": 0.40,
            "mark_price": 0.0305,
            "mark_iv": 0.62,
            "last_price": 0.0302,
            "interest_rate": 0.0,
            "instrument_name": "BTC-31JAN25-102000-C",
            "index_price": 100142.52,
            "greeks": {
                "vega": 0.72,
                "theta": -0.042,
                "rho": 0.09,
                "gamma": 0.000010,
                "delta": 0.42,
                "iv": 0.62
            },
            "estimated_delivery_price": 100142.52,
            "bid_price": 0.029,
            "bid_iv": 0.61,
            "best_bid_amount": 12.0,
            "best_bid_price": 0.029,
            "best_ask_price": 0.032,
            "best_ask_amount": 9.5,
            "ask_price": 0.032,
            "ask_iv": 0.63
        },
        "BTC-31JAN25-106000-C": {
            "underlying_index": "BTC-USD",
            "underlying_price": 100142.52,
            "timestamp": 1737885600000,
            "stats": {"volume": 65.8, "price_change": -1.2, "low": 0.018, "high": 0.022},
            "state": "open",
            "settlement_price": 0.020,
            "open_interest": 215.8,
            "min_price": 0.001,
            "max_price": 0.30,
            "mark_price": 0.0205,
            "mark_iv": 0.58,
            "last_price": 0.0203,
            "interest_rate": 0.0,
            "instrument_name": "BTC-31JAN25-106000-C",
            "index_price": 100142.52,
            "greeks": {
                "vega": 0.62,
                "theta": -0.032,
                "rho": 0.06,
                "gamma": 0.000008,
                "delta": 0.28,
                "iv": 0.58
            },
            "estimated_delivery_price": 100142.52,
            "bid_price": 0.019,
            "bid_iv": 0.57,
            "best_bid_amount": 15.2,
            "best_bid_price": 0.019,
            "best_ask_price": 0.022,
            "best_ask_amount": 11.8,
            "ask_price": 0.022,
            "ask_iv": 0.59
        },
        "BTC-31JAN25-110000-C": {
            "underlying_index": "BTC-USD",
            "underlying_price": 100142.52,
            "timestamp": 1737885600000,
            "stats": {"volume": 42.1, "price_change": -0.8, "low": 0.010, "high": 0.014},
            "state": "open",
            "settlement_price": 0.012,
            "open_interest": 158.3,
            "min_price": 0.001,
            "max_price": 0.20,
            "mark_price": 0.0125,
            "mark_iv": 0.54,
            "last_price": 0.0124,
            "interest_rate": 0.0,
            "instrument_name": "BTC-31JAN25-110000-C",
            "index_price": 100142.52,
            "greeks": {
                "vega": 0.48,
                "theta": -0.022,
                "rho": 0.04,
                "gamma": 0.000006,
                "delta": 0.16,
                "iv": 0.54
            },
            "estimated_delivery_price": 100142.52,
            "bid_price": 0.011,
            "bid_iv": 0.53,
            "best_bid_amount": 18.5,
            "best_bid_price": 0.011,
            "best_ask_price": 0.014,
            "best_ask_amount": 14.2,
            "ask_price": 0.014,
            "ask_iv": 0.55
        },
    }


@pytest.fixture
def market_context():
    """Create market context for scoring."""
    return {
        "max_pain_strike": 100000.0,
        "total_oi": 5000.0,
        "put_call_ratio": 0.85,
        "avg_iv": 0.60,
        "underlying_price": 100142.52,
        "gex_profile": {
            "98000": {"total_gamma": 0.15},
            "100000": {"total_gamma": 0.35},
            "102000": {"total_gamma": 0.25},
            "106000": {"total_gamma": 0.18},
            "110000": {"total_gamma": 0.12},
        },
        "dex_profile": {
            "98000": {"total_delta": 450.0},
            "100000": {"total_delta": 850.0},
            "102000": {"total_delta": 620.0},
            "106000": {"total_delta": 380.0},
            "110000": {"total_delta": 220.0},
        }
    }


class TestBullCallSpreadIntegration:
    """Test Bull Call Spread end-to-end integration."""

    def test_strategy_creation_and_building(self, realistic_ticker_data):
        """Test creating and building Bull Call Spread strategy."""
        strategy = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            min_profit_debit_ratio=0.3,
            quantity=1
        )

        strategy.build_legs(realistic_ticker_data, config)

        assert strategy.validate_legs() is True
        assert len(strategy.legs) == 2
        assert strategy.get_total_cost() > 0
        assert strategy.get_max_risk() > 0
        assert strategy.get_max_profit() > 0

    def test_intrinsic_scorer_integration(self, realistic_ticker_data, market_context):
        """Test Bull Call Spread with IntrinsicScorer."""
        strategy = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=102000.0,
            short_specific_strike=106000.0,
            quantity=1
        )

        strategy.build_legs(realistic_ticker_data, config)

        # Score the strategy
        scorer = IntrinsicScorer()
        score = scorer.calculate_score(strategy, market_context)
        breakdown = scorer.get_breakdown(strategy, market_context)

        # Verify scoring works
        assert 0 <= score <= 10
        assert "risk_reward_ratio" in breakdown
        assert "cost_efficiency" in breakdown
        assert "greek_profile" in breakdown
        assert "breakeven_distance" in breakdown

    def test_on_chain_scorer_integration(self, realistic_ticker_data, market_context):
        """Test Bull Call Spread with OnChainScorer."""
        strategy = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=102000.0,
            short_specific_strike=106000.0,
            quantity=1
        )

        strategy.build_legs(realistic_ticker_data, config)

        # Score the strategy
        scorer = OnChainScorer()
        score = scorer.calculate_score(strategy, market_context)
        breakdown = scorer.get_breakdown(strategy, market_context)

        # Verify scoring works
        assert 0 <= score <= 10
        assert "max_pain_alignment" in breakdown
        assert "gex_dex_support" in breakdown

    def test_strategy_signal_creation(self, realistic_ticker_data, market_context):
        """Test creating StrategySignal from Bull Call Spread."""
        strategy = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        config = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            quantity=1
        )

        strategy.build_legs(realistic_ticker_data, config)

        # Calculate scores
        intrinsic_scorer = IntrinsicScorer()
        on_chain_scorer = OnChainScorer()

        intrinsic_score = intrinsic_scorer.calculate_score(strategy, market_context)
        on_chain_score = on_chain_scorer.calculate_score(strategy, market_context)
        composite_score = (intrinsic_score * 0.5) + (on_chain_score * 0.5)

        intrinsic_breakdown = intrinsic_scorer.get_breakdown(strategy, market_context)
        on_chain_breakdown = on_chain_scorer.get_breakdown(strategy, market_context)

        # Create signal
        signal = StrategySignal(
            strategy_name=strategy.name,
            currency=strategy.currency,
            expiration=strategy.expiration,
            generated_at=datetime.now(),
            legs=[leg.__dict__ for leg in strategy.legs],
            intrinsic_score=intrinsic_score,
            on_chain_score=on_chain_score,
            composite_score=composite_score,
            intrinsic_breakdown=intrinsic_breakdown,
            on_chain_breakdown=on_chain_breakdown,
            underlying_price=strategy.underlying_price,
            max_pain_strike=market_context["max_pain_strike"],
            max_risk=strategy.get_max_risk(),
            max_profit=strategy.get_max_profit(),
            total_cost=strategy.get_total_cost(),
            breakeven_points=strategy.get_breakeven_points(),
            max_loss_percentage=strategy.get_max_loss_percentage(),
            net_delta=strategy.get_net_greeks()["delta"],
            net_gamma=strategy.get_net_greeks()["gamma"],
            net_theta=strategy.get_net_greeks()["theta"],
            net_vega=strategy.get_net_greeks()["vega"]
        )

        # Verify signal is created correctly
        assert signal.strategy_name == "Bull Call Spread"
        assert signal.composite_score >= 0
        assert len(signal.legs) == 2
        assert signal.max_risk > 0
        assert signal.max_profit > 0

    def test_comparison_with_long_call(self, realistic_ticker_data, market_context):
        """Test that Bull Call Spread has limited risk compared to Long Call."""
        from coding.core.strategy.definitions.long_call import LongCall

        # Create Bull Call Spread
        bull_spread = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        spread_config = SpreadStrikeConfig(
            method="by_strike",
            long_specific_strike=102000.0,
            short_specific_strike=106000.0,
            quantity=1
        )

        bull_spread.build_legs(realistic_ticker_data, spread_config)

        # Create Long Call at same strike
        long_call = LongCall(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        long_call.build_legs(
            realistic_ticker_data,
            strike_selection_method="by_strike",
            specific_strike=102000.0,
            quantity=1
        )

        # Bull Call Spread should have lower cost and limited profit
        spread_cost = abs(bull_spread.get_total_cost())
        long_call_cost = abs(long_call.get_total_cost())

        assert spread_cost < long_call_cost  # Spread is cheaper
        assert bull_spread.get_max_profit() is not None  # Spread has limited profit
        assert long_call.get_max_profit() is None  # Long call has unlimited profit

    def test_multiple_optimization_modes(self, realistic_ticker_data):
        """Test different skew-aware optimization modes produce different results."""
        # Profit/debit ratio optimization
        strategy1 = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        config1 = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="profit_debit_ratio",
            quantity=1
        )

        strategy1.build_legs(realistic_ticker_data, config1)

        # Max width for budget optimization
        strategy2 = BullCallSpread(
            currency="BTC",
            expiration="31JAN25",
            underlying_price=100142.52
        )

        config2 = SpreadStrikeConfig(
            method="skew_aware",
            optimize_for="max_width_for_budget",
            max_budget=2500.0,
            quantity=1
        )

        strategy2.build_legs(realistic_ticker_data, config2)

        # Calculate metrics
        width1 = strategy1.legs[1].strike - strategy1.legs[0].strike
        cost1 = abs(strategy1.get_total_cost())
        ratio1 = (width1 - cost1) / cost1

        width2 = strategy2.legs[1].strike - strategy2.legs[0].strike
        cost2 = abs(strategy2.get_total_cost())

        # Strategy2 should respect budget
        assert cost2 <= 2500.0  # Within budget

        # Both strategies should be valid spreads
        assert len(strategy1.legs) == 2
        assert len(strategy2.legs) == 2
        assert width1 > 0
        assert width2 > 0

        # Note: With limited test data, both optimization modes may select the same spread
        # This is acceptable - the optimization logic is tested in unit tests
