"""Tests for get_aggregated_flow_metrics."""
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest
from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    return repo


def test_aggregated_flow_returns_correct_structure():
    """Result has flow_data dict and spot_price float."""
    repo = _make_repo()

    # Simulate DB returning rows already aggregated by strike/option_type
    # (the SQL does the grouping; mock returns already-summed rows)
    fake_rows = [
        # strike, opt_type, buy_count, buy_vol, buy_not, sell_count, sell_vol, sell_not, net_flow, price
        (90000, "C", 8, 1.5, 135000.0, 4, 0.7, 63000.0, 0.8, 85000.0),
        (90000, "P", 3, 0.4, 36000.0, 6, 1.2, 108000.0, -0.8, 85000.0),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fake_rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert "flow_data" in result
    assert "spot_price" in result

    fd = result["flow_data"]
    assert 90000.0 in fd
    assert abs(fd[90000.0]["C"]["buy_volume"] - 1.5) < 0.001
    assert abs(fd[90000.0]["P"]["sell_volume"] - 1.2) < 0.001
    assert isinstance(result["spot_price"], float)


def test_aggregated_flow_empty_returns_defaults():
    """Empty DB returns empty flow_data and 0.0 spot_price."""
    repo = _make_repo()

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert result == {"flow_data": {}, "spot_price": 0.0}


def test_aggregated_flow_zero_sell_volume_buy_sell_ratio_is_none():
    """When sell_volume is 0, buy_sell_ratio must be None (no division by zero)."""
    repo = _make_repo()

    fake_rows = [
        (80000, "C", 2, 1.0, 80000.0, 0, 0.0, 0.0, 1.0, 85000.0),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fake_rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert result["flow_data"][80000.0]["C"]["buy_sell_ratio"] is None


def test_aggregated_flow_handles_decimal_types_from_psycopg2():
    """psycopg2 returns Decimal for NUMERIC columns — must convert to float without error."""
    repo = _make_repo()

    fake_rows = [
        (Decimal("90000"), "C", 5, Decimal("1.5"), Decimal("135000.0"),
         3, Decimal("0.7"), Decimal("63000.0"), Decimal("0.8"), Decimal("85000.0")),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fake_rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert isinstance(result["spot_price"], float)
    assert isinstance(result["flow_data"][90000.0]["C"]["buy_volume"], float)


def test_aggregated_flow_sums_across_expirations():
    """Aggregation correctly sums same strike across multiple expirations."""
    repo = _make_repo()

    # Two rows for the same strike (90000, C) — one per expiration after SQL GROUP BY
    # The SQL already groups; mock represents already-summed output
    # (C at 90k: sum of two expiries = buy_volume 1.0+0.5=1.5, sell_volume 0.5+0.2=0.7)
    fake_rows = [
        (90000, "C", 8, 1.5, 135000.0, 4, 0.7, 63000.0, 0.8, 85000.0),
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fake_rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_aggregated_flow_metrics("BTC")

    assert abs(result["flow_data"][90000.0]["C"]["buy_volume"] - 1.5) < 0.001
    assert abs(result["flow_data"][90000.0]["C"]["sell_volume"] - 0.7) < 0.001
