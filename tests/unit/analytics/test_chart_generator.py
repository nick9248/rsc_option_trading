"""Tests for chart_generator: generate_flow_trend_chart, generate_net_flow_chart,
and generate_straddle_payoff_chart."""
from unittest.mock import MagicMock
import plotly.graph_objects as go
from coding.core.analytics.chart_generator import (
    generate_flow_trend_chart,
    generate_net_flow_chart,
    generate_straddle_payoff_chart,
    inject_theme_toggle_js,
    save_chart,
)


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


# ── Straddle Payoff Chart Tests ────────────────────────────────────────────────

def _payoff_kwargs(**overrides):
    kwargs = dict(
        currency="BTC",
        expiry="25SEP26",
        dte=68.0,
        future_price=118432.50,
        atm_iv=64.8,
        strike=118000.0,
        cost_usd=4820.0,
        breakeven_down=113180.0,
        breakeven_up=122820.0,
        rv=39.4,
    )
    kwargs.update(overrides)
    return kwargs


def test_payoff_chart_returns_figure():
    fig = generate_straddle_payoff_chart(**_payoff_kwargs())
    assert isinstance(fig, go.Figure)


def test_payoff_chart_title_contains_key_facts():
    fig = generate_straddle_payoff_chart(**_payoff_kwargs())
    title = fig.layout.title.text
    assert "BTC" in title
    assert "25SEP26" in title
    assert "118,000" in title
    assert "4,820" in title


def test_payoff_chart_pnl_curve_matches_straddle_formula():
    """y = |S - K| - cost for every x on the plotted line."""
    fig = generate_straddle_payoff_chart(**_payoff_kwargs())
    pnl_trace = fig.data[0]
    strike, cost = 118000.0, 4820.0
    for x, y in zip(pnl_trace.x, pnl_trace.y):
        assert y == abs(x - strike) - cost


def test_payoff_chart_x_range_spans_future_price():
    """The plotted x range must bracket F (payoff chart must show both wings)."""
    fig = generate_straddle_payoff_chart(**_payoff_kwargs())
    pnl_trace = fig.data[0]
    assert min(pnl_trace.x) < 118432.50 < max(pnl_trace.x)


def test_payoff_chart_omits_realized_band_when_rv_none():
    """With rv=None, no crash and the figure still renders (fewer shapes)."""
    fig_with_rv = generate_straddle_payoff_chart(**_payoff_kwargs(rv=39.4))
    fig_without_rv = generate_straddle_payoff_chart(**_payoff_kwargs(rv=None))
    assert isinstance(fig_without_rv, go.Figure)
    # One fewer shaded vrect region when rv is omitted.
    assert len(fig_without_rv.layout.shapes) < len(fig_with_rv.layout.shapes)


def test_payoff_chart_no_crash_with_tiny_dte_and_iv():
    """Degenerate near-zero sigma must not produce a zero-width x-range crash."""
    fig = generate_straddle_payoff_chart(**_payoff_kwargs(dte=0.01, atm_iv=0.01, rv=0.01))
    assert isinstance(fig, go.Figure)
    pnl_trace = fig.data[0]
    assert max(pnl_trace.x) > min(pnl_trace.x)


# ── Theme Toggle: light-by-default on load ──────────────────────────────────

def test_theme_toggle_defaults_to_light_on_load(tmp_path):
    """
    User decision: the page must open in the light theme, with the button
    then offering 'Dark mode' (first click switches to dark).

    Verifies the injected JS auto-applies the light payload on load through
    the *same* applyTheme(plotDiv, isLight) call the button's onclick uses
    (isLight starts true), rather than a separate/duplicated code path.
    """
    fig = generate_straddle_payoff_chart(**_payoff_kwargs())
    html_path = tmp_path / "payoff.html"
    fig.write_html(str(html_path))
    inject_theme_toggle_js(html_path, fig)

    content = html_path.read_text(encoding="utf-8")

    # Light is the state on load.
    assert "var isLight = true;" in content

    # init() and the button's onclick both call this exact statement — the
    # auto-apply on load reuses the same path as a manual click.
    assert content.count("applyTheme(plotDiv, isLight);") == 2

    # init() applies the theme before wiring up the button, so nothing is
    # visibly dark once the button appears.
    init_body_start = content.index("function init(attempts)")
    init_body = content[init_body_start:content.index("makeButton(plotDiv);", init_body_start)]
    assert "applyTheme(plotDiv, isLight);" in init_body

    # document.body background is set synchronously (before Plotly is even
    # ready) using the light payload, so there's no post-load flash.
    assert "document.body.style.background = THEME.paper_bgcolor.light;" in content

    # Button label reflects "next action" — starts on light, so it must
    # read 'Dark mode' once rendered.
    assert "isLight ? 'Dark mode' : 'Light mode'" in content


def test_theme_toggle_survives_full_save_pipeline(tmp_path, monkeypatch):
    """End-to-end: save_chart -> inject_theme_toggle_js, as StraddleScanService
    actually calls it, still produces the light-default init wiring."""
    import coding.core.analytics.chart_generator as chart_generator_module

    monkeypatch.setattr(chart_generator_module, "CHARTS_DIR", tmp_path)

    fig = generate_straddle_payoff_chart(**_payoff_kwargs())
    path = save_chart(fig, "straddle_theme_test", subfolder="straddle", save_png=False)
    inject_theme_toggle_js(chart_generator_module.Path(path), fig)

    content = chart_generator_module.Path(path).read_text(encoding="utf-8")
    assert "var isLight = true;" in content
    assert content.count("applyTheme(plotDiv, isLight);") == 2
