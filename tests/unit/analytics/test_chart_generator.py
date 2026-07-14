"""Tests for chart_generator: generate_flow_trend_chart and generate_net_flow_chart."""
from unittest.mock import MagicMock
import plotly.graph_objects as go
from coding.core.analytics.chart_generator import generate_flow_trend_chart, generate_net_flow_chart


def _mock_repo(rows):
    repo = MagicMock()
    repo.get_hourly_flow_volumes.return_value = rows
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


def test_trend_chart_delegates_to_repository():
    """The chart must fetch data via the public repository method."""
    repo = _mock_repo([])
    generate_flow_trend_chart(repo, "BTC", expiration="27MAR26", trade_filter="block")
    kwargs = repo.get_hourly_flow_volumes.call_args.kwargs
    assert kwargs["currency"] == "BTC"
    assert kwargs["expiration"] == "27MAR26"
    assert kwargs["trade_filter"] == "block"


def test_trend_chart_all_expirations_passes_none():
    """When expiration=None, None is forwarded to the repository."""
    repo = _mock_repo([])
    generate_flow_trend_chart(repo, "BTC", expiration=None)
    assert repo.get_hourly_flow_volumes.call_args.kwargs["expiration"] is None


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
    """barmode must be 'group' so call and put bars appear side by side per strike."""
    flow_data = {
        "flow_data": {80000.0: {"C": {"net_flow": 1.0}, "P": {"net_flow": -1.0}}},
        "spot_price": 82000.0,
    }
    fig = generate_net_flow_chart(flow_data, spot_price=82000.0, currency="BTC", expiration="27MAR26")
    assert fig.layout.barmode == "group"


# trade_filter SQL-clause tests live in tests/unit/test_repository_hourly_flow_volumes.py
# since the query moved into DatabaseRepository.get_hourly_flow_volumes.
