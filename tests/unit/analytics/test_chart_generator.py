"""Tests for chart_generator: generate_flow_trend_chart and generate_net_flow_chart."""
from unittest.mock import MagicMock
import plotly.graph_objects as go
from coding.core.analytics.chart_generator import generate_flow_trend_chart, generate_net_flow_chart


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


# ── Net Flow Chart Tests ──────────────────────────────────────────────────────

def test_net_flow_chart_has_exactly_two_traces():
    """Redesigned chart must have exactly 2 traces: Call Net Flow and Put Net Flow."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
            85000.0: {"C": {"net_flow": -0.3}, "P": {"net_flow": 2.1}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    assert len(fig.data) == 2


def test_net_flow_chart_trace_names():
    """Traces must be named 'Call Net Flow' and 'Put Net Flow'."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    names = [t.name for t in fig.data]
    assert "Call Net Flow" in names
    assert "Put Net Flow" in names


def test_net_flow_chart_is_horizontal():
    """All traces must have orientation='h'."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    for trace in fig.data:
        assert trace.orientation == "h", f"Trace '{trace.name}' should be horizontal"


def test_net_flow_chart_signed_values():
    """x values (net volume) must be the signed net flow values."""
    flow_data = {
        "flow_data": {
            80000.0: {"C": {"net_flow": 1.5}, "P": {"net_flow": -0.8}},
            85000.0: {"C": {"net_flow": -0.3}, "P": {"net_flow": 2.1}},
        },
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    call_trace = next(t for t in fig.data if t.name == "Call Net Flow")
    put_trace = next(t for t in fig.data if t.name == "Put Net Flow")
    # x holds the values; y holds the strike labels
    assert list(call_trace.x) == [1.5, -0.3]
    assert list(put_trace.x) == [-0.8, 2.1]


def test_net_flow_chart_colors():
    """Call Net Flow = emerald #10b981, Put Net Flow = indigo #818cf8."""
    flow_data = {
        "flow_data": {80000.0: {"C": {"net_flow": 1.0}, "P": {"net_flow": -1.0}}},
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    call_trace = next(t for t in fig.data if t.name == "Call Net Flow")
    put_trace = next(t for t in fig.data if t.name == "Put Net Flow")
    assert call_trace.marker.color == "#10b981"
    assert put_trace.marker.color == "#818cf8"


def test_net_flow_chart_empty_data():
    """Empty flow data must return a figure without crashing."""
    fig = generate_net_flow_chart({"flow_data": {}}, spot_price=0.0, currency="BTC", expiration="27MAR26")
    assert fig is not None


def test_net_flow_chart_barmode():
    """barmode must be 'relative' so signed bars extend from x=0 in opposite directions."""
    flow_data = {
        "flow_data": {80000.0: {"C": {"net_flow": 1.0}, "P": {"net_flow": -1.0}}},
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    assert fig.layout.barmode == "relative"
