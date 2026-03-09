"""Tests for generate_flow_trend_chart optional expiration mode."""
from unittest.mock import MagicMock
import plotly.graph_objects as go
from coding.core.analytics.chart_generator import generate_flow_trend_chart


def _mock_repo(rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    repo = MagicMock()
    repo._db_cursor.return_value = mock_ctx
    return repo


def test_trend_chart_all_expirations_title():
    """When expiration=None, title contains 'All Expirations'."""
    repo = _mock_repo([])
    fig = generate_flow_trend_chart(repo, "BTC", expiration=None)
    assert isinstance(fig, go.Figure)
    assert "All Expirations" in fig.layout.title.text


def test_trend_chart_specific_expiration_title():
    """When expiration is given, title contains that expiration string."""
    repo = _mock_repo([])
    fig = generate_flow_trend_chart(repo, "BTC", expiration="27MAR26")
    assert isinstance(fig, go.Figure)
    assert "27MAR26" in fig.layout.title.text


def test_trend_chart_all_expirations_uses_fewer_query_params():
    """When expiration=None, query must NOT pass expiration as a param."""
    repo = _mock_repo([])
    generate_flow_trend_chart(repo, "BTC", expiration=None)
    # The cursor's execute call should have been called with 3 params (currency, start_ts, end_ts)
    # not 4 (which would include expiration)
    call_args = repo._db_cursor.return_value.__enter__.return_value.execute.call_args
    params = call_args[0][1]  # second positional arg to execute() is the params tuple
    assert len(params) == 3, f"Expected 3 params for all-expiration mode, got {len(params)}: {params}"


def test_trend_chart_specific_expiration_uses_four_query_params():
    """When expiration is given, query must pass 4 params including expiration."""
    repo = _mock_repo([])
    generate_flow_trend_chart(repo, "BTC", expiration="27MAR26")
    call_args = repo._db_cursor.return_value.__enter__.return_value.execute.call_args
    params = call_args[0][1]
    assert len(params) == 4, f"Expected 4 params for specific expiration, got {len(params)}: {params}"
    assert "27MAR26" in params
