"""Tests of the paper style of the report figures (pure presentation: values must stay untouched)."""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, StrikeLevelRow
from coding.core.analytics.option_flow.dealer_levels_figure import build_dealer_levels_figure
from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike
from coding.core.analytics.report.figure_style import PAPER, PUT_BUY_BLOCKED_ON_PAPER, apply_paper_style, paper_color
from coding.service.option_flow.option_flow_service import OptionFlowService, StrikeFlowReport

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
DARK_COLORS = {"#1b2540", "white", "#ffe14a"}


def strike_flow_figure():
    trades = [OptionTrade("27NOV26", 68_000.0, "C", "buy", 2.0, False), OptionTrade("27NOV26", 68_000.0, "P", "buy", 1.0, True),
              OptionTrade("27NOV26", 70_000.0, "P", "sell", 3.0, False), OptionTrade("27NOV26", 72_000.0, "C", "sell", 1.5, True)]
    report = StrikeFlowReport("BTC", NOW - timedelta(hours=24), NOW, None, aggregate_by_strike(trades), NOW, NOW - timedelta(days=30))
    return OptionFlowService.build_figure(report, "notional")


def dealer_figure():
    rows = [StrikeLevelRow(66_000.0, -3.0, 2.0, -1_000_000.0, -2_000_000.0, 10.0, 20.0),
            StrikeLevelRow(68_000.0, 4.0, -1.0, 2_500_000.0, 3_000_000.0, 30.0, 40.0)]
    levels = DealerFlowLevels(
        currency="BTC", expiry="27NOV26", index_price=67_000.0, chain_captured_at=NOW, flow_start=NOW - timedelta(days=60),
        trades_used=50, rows=rows, flow_call_wall=68_000.0, flow_put_wall=66_000.0, flow_gamma_flip=67_100.0,
        open_interest_call_wall=68_000.0, open_interest_put_wall=66_000.0, open_interest_gamma_flip=67_200.0,
        total_flow_gex=1_500_000.0, total_open_interest_gex=1_000_000.0, coverage=1.0, violation_rate=0.0, violations=0,
        options_checked=10, options_without_open_interest=0, gate_passed=True, gate_reason=None)
    return build_dealer_levels_figure(levels)


@pytest.fixture(params=["strike_flow", "dealer_levels"])
def figure(request):
    return strike_flow_figure() if request.param == "strike_flow" else dealer_figure()


def test_apply_paper_style_returns_a_new_figure_and_leaves_the_original_untouched(figure):
    original_background = figure.layout.paper_bgcolor
    original_colors = [trace.marker.color for trace in figure.data]
    styled = apply_paper_style(figure)
    assert styled is not figure
    assert figure.layout.paper_bgcolor == original_background == "#1b2540"
    assert [trace.marker.color for trace in figure.data] == original_colors


def test_paper_style_sets_white_paper_and_plot_background(figure):
    styled = apply_paper_style(figure)
    assert styled.layout.paper_bgcolor == "#FFFFFF" == PAPER
    assert styled.layout.plot_bgcolor == "#FFFFFF"


def test_paper_style_keeps_every_value_and_hover_text(figure):
    styled = apply_paper_style(figure)
    assert len(styled.data) == len(figure.data)
    for before, after in zip(figure.data, styled.data):
        assert list(after.x) == list(before.x)
        assert list(after.y) == list(before.y)
        assert after.hovertemplate == before.hovertemplate
        assert (after.customdata is None) == (before.customdata is None)
        if before.customdata is not None:
            assert [list(item) if hasattr(item, "__len__") else item for item in after.customdata] == \
                   [list(item) if hasattr(item, "__len__") else item for item in before.customdata]


def test_no_dark_theme_colour_is_left_in_the_styled_dealer_figure():
    styled = apply_paper_style(dealer_figure())
    colors = []
    for trace in styled.data:
        marker_color = trace.marker.color
        colors += list(marker_color) if isinstance(marker_color, (list, tuple)) else [marker_color]
    colors += [shape.line.color for shape in styled.layout.shapes]
    colors += [annotation.font.color for annotation in styled.layout.annotations]
    colors += [styled.layout.paper_bgcolor, styled.layout.plot_bgcolor]
    assert colors and not DARK_COLORS & {str(color).lower() for color in colors if color is not None}


def test_dealer_flip_line_becomes_ink_and_walls_use_the_paper_greens_and_reds():
    styled = apply_paper_style(dealer_figure())
    assert {shape.line.color for shape in styled.layout.shapes} >= {"#101114", "#2F7D55", "#B8433A"}


def test_put_buy_blocked_bar_gets_its_own_colour_not_ink():
    styled = apply_paper_style(strike_flow_figure())
    blocked = next(trace for trace in styled.data if trace.name == "Put Buy Blocked")
    assert blocked.marker.color == PUT_BUY_BLOCKED_ON_PAPER
    assert PUT_BUY_BLOCKED_ON_PAPER != paper_color("#ffe14a")


def test_paper_color_leaves_unknown_colours_and_none_alone():
    assert paper_color("#123456") == "#123456"
    assert paper_color(None) is None
    assert paper_color("WHITE") == "#101114"
