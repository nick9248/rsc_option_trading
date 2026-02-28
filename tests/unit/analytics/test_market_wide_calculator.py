"""
Unit tests for MarketWideCalculator.
"""

import math
import time
import pytest
from datetime import datetime, timedelta

from coding.core.analytics.market_wide_calculator import MarketWideCalculator


def _make_price_history(days=60, base_price=90000):
    """Generate synthetic daily price history."""
    prices = []
    now = time.time()

    for i in range(days):
        ts = now - (days - i) * 86400
        # Add some volatility
        price = base_price * (1 + 0.01 * math.sin(i * 0.5))
        prices.append({"timestamp": ts, "close": price})

    return prices


@pytest.fixture
def calculator():
    return MarketWideCalculator(
        currency="BTC",
        spot_price=90000,
        dvol=65.0,
    )


class TestMarketWideCalculator:
    """Tests for MarketWideCalculator."""

    def test_iv_term_structure_contango(self, calculator):
        atm_ivs = {
            "28FEB26": 70.0,
            "28MAR26": 65.0,
            "27JUN26": 60.0,
        }
        report, structured = calculator.calculate_iv_term_structure(atm_ivs)

        assert "IV TERM STRUCTURE" in report
        assert "28FEB26" in report
        assert "28MAR26" in report
        # Front month (70) > back month (60) = backwardated
        assert "BACKWARDATED" in report
        assert structured["shape"] == "BACKWARDATION"
        assert isinstance(structured["iv_by_dte"], dict)

    def test_iv_term_structure_empty(self, calculator):
        report, structured = calculator.calculate_iv_term_structure({})
        assert "No ATM IV data available" in report
        assert structured["iv_by_dte"] == {}

    def test_futures_basis(self, calculator):
        futures_data = [
            {
                "instrument_name": "BTC-28MAR26",
                "mark_price": 92000,
                "index_price": 90000,
            },
        ]
        report, structured = calculator.calculate_futures_basis(futures_data)

        assert "FUTURES BASIS" in report
        assert "BTC-28MAR26" in report
        assert "92,000" in report
        assert "futures_basis" in structured

    def test_futures_basis_empty(self, calculator):
        report, structured = calculator.calculate_futures_basis([])
        assert "No futures data available" in report
        assert structured["futures_basis"] == {}

    def test_realized_volatility_multi_window(self, calculator):
        prices = _make_price_history(60)
        report, rv_values = calculator.calculate_realized_volatility_multi_window(prices)

        assert "REALIZED VOLATILITY" in report
        assert 10 in rv_values
        assert 20 in rv_values
        assert 30 in rv_values
        # RV should be positive
        for rv in rv_values.values():
            assert rv > 0

    def test_realized_volatility_insufficient_data(self, calculator):
        prices = _make_price_history(5)
        report, rv_values = calculator.calculate_realized_volatility_multi_window(prices)
        assert "Insufficient" in report

    def test_vrp(self, calculator):
        rv_30d = 0.50  # 50% realized vol
        report, structured = calculator.calculate_vrp(rv_30d)

        assert "VOLATILITY RISK PREMIUM" in report
        assert "DVOL: 65.0%" in report
        assert "30d RV: 50.0%" in report
        # VRP = 65 - 50 = +15 pts
        assert "+15.0 pts" in report
        assert "vrp" in structured
        assert structured["vrp"] == pytest.approx(15.0, abs=0.1)

    def test_vrp_no_dvol(self):
        calc = MarketWideCalculator("BTC", 90000, dvol=None)
        report, structured = calc.calculate_vrp(0.50)
        assert "DVOL not available" in report

    def test_volatility_cone(self, calculator):
        prices = _make_price_history(120)
        report, structured = calculator.calculate_volatility_cone(prices)

        assert "VOLATILITY CONE" in report
        assert "10d" in report
        assert "Current" in report
        assert "Median" in report
        assert "cone_10d_pctile" in structured
        assert "cone_30d_pctile" in structured

    def test_volatility_cone_insufficient_data(self, calculator):
        prices = _make_price_history(10)
        report, structured = calculator.calculate_volatility_cone(prices)
        assert "Insufficient" in report
        assert structured["cone_30d_pctile"] == 0.0

    def test_perpetual_funding_trend(self, calculator):
        funding_data = {
            "data": [[1, 0.0001], [2, 0.0002], [3, 0.0003],
                     [4, 0.0002], [5, 0.0001], [6, 0.0002],
                     [7, 0.0003], [8, 0.0004], [9, 0.0005], [10, 0.0006]]
        }
        perp_ticker = {
            "open_interest": 125000,
            "current_funding": 0.0001,
            "funding_8h": 0.0003,
        }

        report, structured = calculator.calculate_perpetual_funding_trend(
            funding_data, perp_ticker
        )

        assert "PERPETUAL FUNDING" in report
        assert "125,000" in report
        assert "0.0100%" in report
        assert structured["perp_oi"] == 125000
        assert structured["funding_8h"] == 0.0003

    def test_block_trade_detection(self, calculator):
        trades = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 5.0,
                "price": 0.05,
                "index_price": 90000,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
            },
            {
                "instrument_name": "BTC-28MAR26-80000-P",
                "amount": 0.5,
                "price": 0.02,
                "index_price": 90000,
                "direction": "sell",
                "timestamp": int(time.time() * 1000),
                "iv": 70.0,
            },
        ]

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert "BLOCK TRADES" in report
        # 5.0 * 90000 = 450000 > threshold
        assert "BTC-28MAR26-90000-C" in report
        # 0.5 * 90000 = 45000 < threshold - should NOT appear
        assert "BTC-28MAR26-80000-P" not in report
        assert len(structured["block_trades"]) == 1

    def test_block_trade_no_data(self, calculator):
        report, structured = calculator.detect_block_trades([])
        assert "No recent trade data" in report
        assert structured["block_trades"] == []

    def test_cross_asset_correlation(self, calculator):
        own_prices = _make_price_history(35, base_price=90000)
        # ETH prices correlated with BTC
        other_prices = _make_price_history(35, base_price=3000)

        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=own_prices,
            other_prices=other_prices,
            own_dvol_history=[60 + i * 0.1 for i in range(30)],
            other_dvol_history=[55 + i * 0.15 for i in range(30)],
            other_currency="ETH",
        )

        assert "CROSS-ASSET CORRELATION" in report
        assert "Price Correlation" in report
        assert "DVOL Correlation" in report
        assert "btc_eth_price_corr" in structured
        assert "btc_eth_dvol_corr" in structured

    def test_dte_calculation(self):
        # Test a known future date
        now = datetime(2026, 2, 26)
        dte = MarketWideCalculator._calculate_dte("28MAR26", now)
        assert dte == 30

    def test_dte_invalid(self):
        dte = MarketWideCalculator._calculate_dte("INVALID", datetime.now())
        assert dte is None

    def test_dte_past(self):
        # Past expiration should return 0
        now = datetime(2026, 4, 1)
        dte = MarketWideCalculator._calculate_dte("28MAR26", now)
        assert dte == 0
