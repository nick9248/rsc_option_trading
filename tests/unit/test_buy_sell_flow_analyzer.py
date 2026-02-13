"""
Unit tests for BuySellFlowAnalyzer.

Tests buy/sell flow metrics calculation from trade direction data.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer


@pytest.fixture
def mock_repository():
    """Create a mock DatabaseRepository."""
    return MagicMock()


@pytest.fixture
def analyzer(mock_repository):
    """Create a BuySellFlowAnalyzer instance."""
    return BuySellFlowAnalyzer(
        repository=mock_repository,
        currency="BTC",
        expiration="27MAR26",
        spot_price=100000.0,
        lookback_hours=24
    )


def test_empty_trades(analyzer, mock_repository):
    """Test analyzer with no trades."""
    # Mock empty trade list
    with patch.object(analyzer, '_fetch_trades', return_value=[]):
        result = analyzer.calculate()

    assert result["trade_count"] == 0
    assert result["flow_data"] == {}
    assert result["bias_interpretation"] == "No Data"
    assert result["flow_trend"] == "No Data"
    assert result["top_buy_strikes"] == []
    assert result["top_sell_strikes"] == []


def test_single_buy_trade(analyzer):
    """Test analyzer with a single buy trade."""
    trades = [{
        "trade_id": "1",
        "trade_timestamp": int(datetime.now().timestamp() * 1000),
        "instrument_name": "BTC-27MAR26-100000-C",
        "strike": 100000.0,
        "option_type": "C",
        "price": 5000.0,
        "amount": 10.0,
        "direction": "buy",
        "index_price": 100000.0
    }]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    assert result["trade_count"] == 1

    # Check per-strike data
    flow_data = result["flow_data"]
    assert 100000.0 in flow_data
    assert "C" in flow_data[100000.0]

    call_data = flow_data[100000.0]["C"]
    assert call_data["buy_count"] == 1
    assert call_data["sell_count"] == 0
    assert call_data["buy_volume"] == 10.0
    assert call_data["sell_volume"] == 0.0
    assert call_data["net_flow"] == 10.0
    assert call_data["buy_sell_ratio"] == float("inf")

    # Check expiration totals
    totals = result["expiration_totals"]
    assert totals["call_buy_volume"] == 10.0
    assert totals["call_sell_volume"] == 0.0


def test_single_sell_trade(analyzer):
    """Test analyzer with a single sell trade."""
    trades = [{
        "trade_id": "1",
        "trade_timestamp": int(datetime.now().timestamp() * 1000),
        "instrument_name": "BTC-27MAR26-95000-P",
        "strike": 95000.0,
        "option_type": "P",
        "price": 3000.0,
        "amount": 5.0,
        "direction": "sell",
        "index_price": 100000.0
    }]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    assert result["trade_count"] == 1

    # Check per-strike data
    flow_data = result["flow_data"]
    assert 95000.0 in flow_data
    assert "P" in flow_data[95000.0]

    put_data = flow_data[95000.0]["P"]
    assert put_data["buy_count"] == 0
    assert put_data["sell_count"] == 1
    assert put_data["buy_volume"] == 0.0
    assert put_data["sell_volume"] == 5.0
    assert put_data["net_flow"] == -5.0
    assert put_data["buy_sell_ratio"] == 0.0

    # Check expiration totals
    totals = result["expiration_totals"]
    assert totals["put_buy_volume"] == 0.0
    assert totals["put_sell_volume"] == 5.0


def test_mixed_trades_same_strike(analyzer):
    """Test analyzer with mixed buy/sell at same strike."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        },
        {
            "trade_id": "2",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 4900.0,
            "amount": 3.0,
            "direction": "sell",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    assert result["trade_count"] == 2

    # Check per-strike data
    call_data = result["flow_data"][100000.0]["C"]
    assert call_data["buy_count"] == 1
    assert call_data["sell_count"] == 1
    assert call_data["buy_volume"] == 10.0
    assert call_data["sell_volume"] == 3.0
    assert call_data["net_flow"] == 7.0
    assert abs(call_data["buy_sell_ratio"] - (10.0 / 3.0)) < 0.01


def test_multiple_strikes(analyzer):
    """Test analyzer with trades across multiple strikes."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        },
        {
            "trade_id": "2",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-95000-P",
            "strike": 95000.0,
            "option_type": "P",
            "price": 3000.0,
            "amount": 5.0,
            "direction": "sell",
            "index_price": 100000.0
        },
        {
            "trade_id": "3",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-105000-C",
            "strike": 105000.0,
            "option_type": "C",
            "price": 2000.0,
            "amount": 8.0,
            "direction": "buy",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    assert result["trade_count"] == 3
    assert len(result["flow_data"]) == 3

    # Verify all strikes are present
    assert 100000.0 in result["flow_data"]
    assert 95000.0 in result["flow_data"]
    assert 105000.0 in result["flow_data"]


def test_top_buy_strikes(analyzer):
    """Test finding top strikes by buying pressure."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 20.0,
            "direction": "buy",
            "index_price": 100000.0
        },
        {
            "trade_id": "2",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-95000-P",
            "strike": 95000.0,
            "option_type": "P",
            "price": 3000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        },
        {
            "trade_id": "3",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 4900.0,
            "amount": 5.0,
            "direction": "sell",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    top_buy = result["top_buy_strikes"]
    assert len(top_buy) == 2  # Two strikes with net buying

    # First should be 100000 C (net flow = 15)
    assert top_buy[0]["strike"] == 100000.0
    assert top_buy[0]["option_type"] == "C"
    assert top_buy[0]["net_flow"] == 15.0

    # Second should be 95000 P (net flow = 10)
    assert top_buy[1]["strike"] == 95000.0
    assert top_buy[1]["option_type"] == "P"
    assert top_buy[1]["net_flow"] == 10.0


def test_top_sell_strikes(analyzer):
    """Test finding top strikes by selling pressure."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 5.0,
            "direction": "buy",
            "index_price": 100000.0
        },
        {
            "trade_id": "2",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 4900.0,
            "amount": 20.0,
            "direction": "sell",
            "index_price": 100000.0
        },
        {
            "trade_id": "3",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-95000-P",
            "strike": 95000.0,
            "option_type": "P",
            "price": 3000.0,
            "amount": 10.0,
            "direction": "sell",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    top_sell = result["top_sell_strikes"]
    assert len(top_sell) == 2  # Two strikes with net selling

    # First should be 100000 C (net flow = -15, most negative)
    assert top_sell[0]["strike"] == 100000.0
    assert top_sell[0]["option_type"] == "C"
    assert top_sell[0]["net_flow"] == -15.0

    # Second should be 95000 P (net flow = -10)
    assert top_sell[1]["strike"] == 95000.0
    assert top_sell[1]["option_type"] == "P"
    assert top_sell[1]["net_flow"] == -10.0


def test_bias_interpretation_heavy_buying(analyzer):
    """Test bias interpretation with heavy buying."""
    trades = [
        {
            "trade_id": str(i),
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": f"BTC-27MAR26-{100000 + i * 1000}-C",
            "strike": 100000.0 + i * 1000,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        }
        for i in range(10)
    ] + [
        {
            "trade_id": "100",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-95000-P",
            "strike": 95000.0,
            "option_type": "P",
            "price": 3000.0,
            "amount": 5.0,
            "direction": "sell",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    # Buy/sell ratio should be > 1.3 for heavy buying
    assert result["bias_interpretation"] == "Heavy Buying"


def test_bias_interpretation_heavy_selling(analyzer):
    """Test bias interpretation with heavy selling."""
    trades = [
        {
            "trade_id": str(i),
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": f"BTC-27MAR26-{100000 + i * 1000}-C",
            "strike": 100000.0 + i * 1000,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "sell",
            "index_price": 100000.0
        }
        for i in range(10)
    ] + [
        {
            "trade_id": "100",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-95000-P",
            "strike": 95000.0,
            "option_type": "P",
            "price": 3000.0,
            "amount": 5.0,
            "direction": "buy",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    # Buy/sell ratio should be < 0.7 for heavy selling
    assert result["bias_interpretation"] == "Heavy Selling"


def test_bias_interpretation_balanced(analyzer):
    """Test bias interpretation with balanced flow."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        },
        {
            "trade_id": "2",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-95000-P",
            "strike": 95000.0,
            "option_type": "P",
            "price": 3000.0,
            "amount": 10.0,
            "direction": "sell",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    # Buy/sell ratio should be close to 1.0 for balanced
    assert result["bias_interpretation"] == "Balanced"


def test_report_generation(analyzer):
    """Test that report generation doesn't crash."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        report = analyzer.generate_report_section()

    assert isinstance(report, str)
    assert len(report) > 0
    assert "BUY/SELL FLOW ANALYSIS" in report
    assert "EXPIRATION-LEVEL FLOW:" in report
    assert "TOP 5 STRIKES BY BUYING PRESSURE:" in report


def test_division_by_zero_safety(analyzer):
    """Test that division by zero is handled safely."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    # Buy/sell ratio should be inf when sell volume is 0
    call_data = result["flow_data"][100000.0]["C"]
    assert call_data["buy_sell_ratio"] == float("inf")


def test_notional_calculation(analyzer):
    """Test that notional values are calculated correctly."""
    trades = [
        {
            "trade_id": "1",
            "trade_timestamp": int(datetime.now().timestamp() * 1000),
            "instrument_name": "BTC-27MAR26-100000-C",
            "strike": 100000.0,
            "option_type": "C",
            "price": 5000.0,
            "amount": 10.0,
            "direction": "buy",
            "index_price": 100000.0
        }
    ]

    with patch.object(analyzer, '_fetch_trades', return_value=trades):
        result = analyzer.calculate()

    call_data = result["flow_data"][100000.0]["C"]
    # Notional = amount * index_price = 10 * 100000 = 1,000,000
    assert call_data["buy_notional"] == 1000000.0
    assert call_data["sell_notional"] == 0.0
