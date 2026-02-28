"""
Unit tests for SynthesisMapper and SynthesisEngine.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from coding.core.analytics.synthesis import (
    SynthesisEngine,
    SynthesisMapper,
    ScoringEngine,
    ExpiryMetrics,
    MarketWideMetrics,
    MarketRegime,
    VolRegime,
    Signal,
    build_from_current_data,
)


# =============================================================================
# FIXTURES
# =============================================================================

def make_expiry_metrics(**overrides) -> ExpiryMetrics:
    """Create a minimal valid ExpiryMetrics for testing."""
    defaults = dict(
        expiry="27MAR26",
        dte=27,
        total_oi=10000,
        notional=500_000_000,
        max_pain=70000,
        pc_ratio=0.80,
        volume_pc_ratio=0.70,
        total_gex=-5_000_000,
        total_dex=-200,
        gex_environment="Negative",
        call_resistance_strike=75000,
        call_resistance_gex=2_000_000,
        put_support_strike=60000,
        put_support_gex=-3_000_000,
        hvl_strike=67000,
        atm_iv=50.0,
        skew_25d=8.0,
        put_25d_iv=56.0,
        call_25d_iv=48.0,
        vwap_iv=51.0,
        mark_iv=50.5,
        pc_atm=1.2,
        pc_near_otm=0.9,
        pc_far_otm=0.5,
        net_vanna=0.001,
        net_charm=50.0,
        flow_bias="Moderate Buying",
        flow_trend="Steady Buy Pressure",
    )
    defaults.update(overrides)
    return ExpiryMetrics(**defaults)


def make_market_wide(**overrides) -> MarketWideMetrics:
    """Create minimal valid MarketWideMetrics for testing."""
    defaults = dict(
        spot_price=65000.0,
        dvol=52.0,
        iv_percentile_365d=75.0,
        funding_rate=0.0001,
        funding_8h=-0.0015,
        term_structure_shape="CONTANGO",
        term_structure_spread=5.0,
        iv_by_dte={6: 49.0, 13: 49.5, 27: 49.2, 55: 48.0, 90: 48.5},
        rv_10d=45.0,
        rv_20d=42.0,
        rv_30d=48.0,
        vrp=4.0,
        cone_10d_pctile=60.0,
        cone_20d_pctile=55.0,
        cone_30d_pctile=65.0,
        futures_basis={"27MAR26": 1.5, "25DEC26": 3.5},
        perp_oi=1_000_000_000,
        perp_funding_trend="Stable",
        btc_eth_price_corr=0.90,
        btc_eth_dvol_corr=0.85,
        block_trades=[],
    )
    defaults.update(overrides)
    return MarketWideMetrics(**defaults)


def make_analyzer_mock(expiration: str = "27MAR26") -> MagicMock:
    """Create a mock OnChainAnalyzer with structured data populated."""
    analyzer = MagicMock()
    analyzer.underlying_price = 65000.0
    analyzer.currency = "BTC"
    analyzer.market_wide_structured = {
        "spot_price": 65000.0,
        "dvol": 52.0,
        "iv_percentile_365d": 75.0,
        "funding_rate": 0.000001,   # decimal: ×100 → 0.0001% in MarketWideMetrics
        "funding_8h": -0.000015,    # decimal: ×100 → -0.0015% in MarketWideMetrics
        "shape": "CONTANGO",
        "spread": 5.0,
        "iv_by_dte": {6: 49.0, 13: 49.5, 27: 49.2},
        "rv_10d": 0.45,   # decimal: ×100 → 45.0% in MarketWideMetrics
        "rv_20d": 0.42,   # decimal: ×100 → 42.0%
        "rv_30d": 0.48,   # decimal: ×100 → 48.0%
        "vrp": 4.0,
        "cone_10d_pctile": 60.0,
        "cone_20d_pctile": 55.0,
        "cone_30d_pctile": 65.0,
        "futures_basis": {"27MAR26": 1.5},
        "perp_oi": 1_000_000_000,
        "perp_funding_trend": "Stable",
        "btc_eth_price_corr": 0.90,
        "btc_eth_dvol_corr": 0.85,
        "block_trades": [],
    }
    analyzer.gex_dex_structured = {
        expiration: {
            "total_net_gex": -5_000_000,
            "total_net_dex": -200,
            "key_levels": {
                "call_resistance": {"strike": 75000, "net_gex": 2_000_000},
                "put_support": {"strike": 60000, "net_gex": -3_000_000},
                "hvl": 67000,
                "gamma_flip": None,
            },
        }
    }
    analyzer.volatility_surface_structured = {
        expiration: {
            "atm_iv": 50.0,
            "skew_25d": {
                "skew": 8.0,
                "put_25d_iv": 56.0,
                "call_25d_iv": 48.0,
            },
            "pc_by_moneyness": {
                "atm": {"ratio": 1.2},
                "near_otm": {"ratio": 0.9},
                "far_otm": {"ratio": 0.5},
            },
            "second_order_greeks": {
                "net_vanna": 0.001,
                "net_charm": 50.0,
            },
        }
    }
    analyzer.buy_sell_flow_structured = {
        expiration: {
            "bias_interpretation": "Moderate Buying",
            "flow_trend": "Steady Buy Pressure",
            "top_buy_strikes": [],
            "top_sell_strikes": [],
        }
    }
    analyzer.parsed_data = {
        expiration: [
            {"instrument_name": f"BTC-{expiration}-70000-C", "expiration": expiration,
             "strike": 70000.0, "option_type": "C", "open_interest": 5000, "volume": 100},
            {"instrument_name": f"BTC-{expiration}-70000-P", "expiration": expiration,
             "strike": 70000.0, "option_type": "P", "open_interest": 4000, "volume": 80},
        ]
    }
    analyzer.get_expirations.return_value = [expiration]

    # group_by_strike and calculate_* need real return values
    analyzer.group_by_strike.return_value = {
        70000.0: {"call_oi": 5000, "put_oi": 4000, "call_volume": 100, "put_volume": 80}
    }
    analyzer.calculate_max_pain.return_value = {"max_pain_strike": 70000.0}
    analyzer.calculate_put_call_ratio.return_value = {"ratio": 0.80}

    return analyzer


# =============================================================================
# TESTS: ScoringEngine.score_funding — bug fix verification
# =============================================================================

class TestScoreFundingBugFix:
    """Verify that score_funding now uses funding_8h for the annualized calc."""

    def test_funding_8h_used_for_annualized_rate(self):
        """When funding_rate is non-zero but funding_8h is zero, result should be neutral."""
        score, weight, reason = ScoringEngine.score_funding(
            funding_rate=0.01,   # non-zero — old code would have used this
            funding_8h=0.0,      # zero — new code uses this
        )
        assert score == 0.0, "Annualized rate from funding_8h=0 should be neutral"
        assert "Neutral" in reason

    def test_funding_8h_crowded_long(self):
        """High funding_8h should produce bearish score (crowded long)."""
        # 0.01 × 3 × 365 = 10.95% annualized → crowded long
        score, weight, reason = ScoringEngine.score_funding(
            funding_rate=0.0,
            funding_8h=0.01,
        )
        assert score < 0, "High 8h funding should be bearish (crowded long)"
        assert "crowded" in reason.lower()

    def test_funding_8h_crowded_short(self):
        """Negative funding_8h should produce bullish score (crowded short)."""
        # -0.02 × 3 × 365 = -21.9% annualized → extremely crowded short
        score, weight, reason = ScoringEngine.score_funding(
            funding_rate=0.0,
            funding_8h=-0.02,
        )
        assert score > 0, "Negative 8h funding should be bullish (crowded short)"


# =============================================================================
# TESTS: SynthesisMapper.build_market_wide
# =============================================================================

class TestBuildMarketWide:
    """Tests for SynthesisMapper.build_market_wide()."""

    def test_returns_market_wide_metrics(self):
        analyzer = make_analyzer_mock()
        result = SynthesisMapper.build_market_wide(analyzer)

        assert isinstance(result, MarketWideMetrics)
        assert result.spot_price == 65000.0
        assert result.dvol == 52.0
        assert result.iv_percentile_365d == 75.0
        assert result.funding_8h == -0.0015
        assert result.term_structure_shape == "CONTANGO"
        assert result.rv_10d == 45.0
        assert result.vrp == 4.0
        assert result.futures_basis == {"27MAR26": 1.5}

    def test_empty_structured_returns_defaults(self):
        analyzer = MagicMock()
        analyzer.underlying_price = 50000.0
        analyzer.market_wide_structured = {}

        result = SynthesisMapper.build_market_wide(analyzer)
        assert result.spot_price == 50000.0
        assert result.dvol == 0.0
        assert result.term_structure_shape == "FLAT"
        assert result.futures_basis == {}


# =============================================================================
# TESTS: SynthesisMapper.build_expiry_metrics
# =============================================================================

class TestBuildExpiryMetrics:
    """Tests for SynthesisMapper.build_expiry_metrics()."""

    def test_complete_data_returns_expiry_metrics(self):
        analyzer = make_analyzer_mock("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")

        assert result is not None
        assert isinstance(result, ExpiryMetrics)
        assert result.expiry == "27MAR26"
        assert result.total_gex == -5_000_000
        assert result.total_dex == -200
        assert result.gex_environment == "Negative"
        assert result.call_resistance_strike == 75000
        assert result.put_support_strike == 60000
        assert result.atm_iv == 50.0
        assert result.skew_25d == 8.0
        assert result.flow_bias == "Moderate Buying"
        assert result.pc_ratio == 0.80

    def test_missing_gex_data_returns_none(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.gex_dex_structured = {}  # no GEX data

        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is None, "Should return None when GEX data is missing"

    def test_missing_instruments_returns_none(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.parsed_data = {}  # no instruments

        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is None, "Should return None when instruments are missing"

    def test_missing_flow_data_uses_defaults(self):
        """Missing flow data should not crash — use neutral defaults."""
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.buy_sell_flow_structured = {}

        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is not None
        assert result.flow_bias == "Mixed/Neutral"
        assert result.flow_trend == "Mixed/Neutral Flow"

    def test_missing_vol_surface_uses_defaults(self):
        """Missing vol surface should not crash — use zero defaults."""
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.volatility_surface_structured = {}

        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is not None
        assert result.atm_iv == 0.0
        assert result.skew_25d == 0.0


# =============================================================================
# TESTS: SynthesisMapper.build_all
# =============================================================================

class TestBuildAll:
    """Tests for SynthesisMapper.build_all()."""

    def test_returns_market_and_expiries(self):
        analyzer = make_analyzer_mock("27MAR26")
        market, expiries = SynthesisMapper.build_all(analyzer)

        assert isinstance(market, MarketWideMetrics)
        assert len(expiries) == 1
        assert expiries[0].expiry == "27MAR26"

    def test_skips_expiries_with_missing_gex(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.gex_dex_structured = {}  # remove GEX for all expiries

        market, expiries = SynthesisMapper.build_all(analyzer)
        assert len(expiries) == 0, "Expiries with no GEX should be skipped"


# =============================================================================
# TESTS: SynthesisEngine.run
# =============================================================================

class TestSynthesisEngineRun:
    """Tests for SynthesisEngine.run() with real data structures."""

    def test_run_with_example_data_no_crash(self):
        """Running the example data from build_from_current_data should succeed."""
        result = build_from_current_data()

        assert isinstance(result, str)
        assert len(result) > 100
        # Must contain regime classification
        assert any(regime in result for regime in [
            "RANGE_BOUND", "TRENDING_UP", "TRENDING_DOWN",
            "VOLATILE_BULLISH", "VOLATILE_BEARISH", "TRANSITION", "RISK_OFF",
        ])

    def test_run_returns_regime_label(self):
        """Output must contain a recognizable market regime."""
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()

        result = engine.run(market, [expiry])
        assert isinstance(result, str)
        # Header should contain regime info
        assert "Regime:" in result

    def test_run_contains_trade_recommendations(self):
        """Output must always include trade recommendations."""
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()

        result = engine.run(market, [expiry])
        assert "TRADE RECOMMENDATIONS" in result

    def test_run_contains_scoring_detail(self):
        """Output must include scoring detail section."""
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()

        result = engine.run(market, [expiry])
        assert "SCORING DETAIL" in result
        assert "Direction:" in result
        assert "Vol Regime:" in result

    def test_run_with_minimal_expiries(self):
        """Engine should handle a single expiry without crashing."""
        engine = SynthesisEngine()
        market = make_market_wide(iv_by_dte={30: 50.0})
        expiry = make_expiry_metrics(dte=30)

        result = engine.run(market, [expiry])
        assert isinstance(result, str)

    def test_run_empty_iv_by_dte_no_crash(self):
        """Header should not crash when iv_by_dte is empty."""
        engine = SynthesisEngine()
        market = make_market_wide(iv_by_dte={})
        expiry = make_expiry_metrics()

        result = engine.run(market, [expiry])
        # Should contain 0.0% for front ATM IV without crashing
        assert "ATM IV (front): ~0.0%" in result
