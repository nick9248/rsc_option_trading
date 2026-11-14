"""Tests of the interactive MenthorQ figures: the numbers drawn equal the ``replicate_menthorq`` numbers (pure, no database)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.menthorq.report_figures import (
    HVL_NOTE, _scaled_text, expiry_iv_times_open_interest_figure, iv_times_open_interest_figure, net_dex_figure, net_gex_panels_figure,
    replication_iv_times_open_interest_figure)
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_ALL, compute_expiry_iv_times_open_interest, replicate_menthorq, select_drawn_levels)

AS_OF = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc) - timedelta(days=365)
SPOT = 100.0
MARK_IV_PERCENT = 50.0


def make_row(expiration, strike, option_type, open_interest):
    return {"instrument_name": f"BTC-{expiration}-{strike}-{option_type}", "expiration": expiration, "strike": float(strike),
            "option_type": option_type, "open_interest": open_interest, "mark_iv": MARK_IV_PERCENT, "underlying_price": SPOT,
            "mark_price": None}


ROWS = [make_row("27NOV26", 90, "P", 30.0), make_row("27NOV26", 100, "C", 10.0), make_row("25DEC26", 100, "C", 4.0),
        make_row("25DEC26", 110, "C", 5.0), make_row("25DEC26", 95, "P", 8.0)]


@pytest.fixture(scope="module")
def replication():
    return replicate_menthorq(ROWS, SPOT, AS_OF, EXPIRY_UNIVERSE_ALL)


def lines_by_name(figure):
    """The dashed level lines (scatter traces named after the level) as {name: y value}."""
    return {trace.name: trace.y[0] for trace in figure.data if trace.type == "scatter" and trace.name in
            ("Call resistance", "Put support", "HVL", "Spot")}


def bar_values(figure, name):
    return dict(zip(figure.data[[trace.name for trace in figure.data].index(name)].y,
                    figure.data[[trace.name for trace in figure.data].index(name)].x))


def test_iv_times_open_interest_bars_are_iv_decimal_times_open_interest_summed_over_expiries(replication):
    figure = replication_iv_times_open_interest_figure(replication, "BTC")
    assert bar_values(figure, "IV x OI calls") == {100.0: pytest.approx(0.5 * (10.0 + 4.0)), 110.0: pytest.approx(0.5 * 5.0)}
    assert bar_values(figure, "IV x OI puts") == {90.0: pytest.approx(0.5 * 30.0), 95.0: pytest.approx(0.5 * 8.0)}
    calculator = replication["iv_times_open_interest"]["by_candidate"]["decimal_sum"]
    for strike, value in bar_values(figure, "IV x OI calls").items():
        assert value == pytest.approx(calculator[strike]["call"])


def test_iv_times_open_interest_hover_has_strike_call_put_total_and_units(replication):
    figure = replication_iv_times_open_interest_figure(replication, "BTC")
    calls = next(trace for trace in figure.data if trace.name == "IV x OI calls")
    text = calls.hovertext[list(calls.y).index(100.0)]
    assert "Strike 100 USD" in text and "Calls 7.00" in text and "Puts not computed" in text and "Total not computed" in text
    both = iv_times_open_interest_figure({100.0: {"call": 7.0, "put": 2.5}}, {}, SPOT, "BTC")
    assert "Total 9.50" in both.data[0].hovertext[0]
    assert "IV decimal x OI in BTC" in text
    assert "IV x OI" in figure.layout.xaxis.title.text and "BTC" in figure.layout.xaxis.title.text


def test_level_lines_and_spot_are_drawn_at_the_levels_of_the_replication(replication):
    figure = replication_iv_times_open_interest_figure(replication, "BTC")
    levels = select_drawn_levels(replication["iv_times_open_interest"]["levels"])
    lines = lines_by_name(figure)
    for key, name in (("call_resistance", "Call resistance"), ("put_support", "Put support"), ("hvl", "HVL")):
        assert (name in lines) == (levels[key] is not None)
        if levels[key] is not None:
            assert lines[name] == levels[key]
    assert lines["Spot"] == SPOT
    assert any(trace.type == "scatter" and trace.line.dash == "dash" for trace in figure.data)


def test_a_level_that_is_not_computed_is_not_drawn():
    by_strike = {100.0: {"call": 1.0, "put": None}}
    figure = iv_times_open_interest_figure(by_strike, {"call_resistance": 105.0, "put_support": None, "hvl": None}, SPOT, "BTC")
    lines = lines_by_name(figure)
    assert set(lines) == {"Call resistance", "Spot"}
    assert all("Put support" not in annotation.text and "HVL" not in annotation.text for annotation in figure.layout.annotations)


def test_hvl_hover_says_it_is_a_strike_axis_point_not_a_gamma_flip():
    figure = iv_times_open_interest_figure({100.0: {"call": 1.0, "put": 1.0}}, {"hvl": 100.0}, SPOT, "BTC")
    hvl = next(trace for trace in figure.data if trace.name == "HVL")
    assert HVL_NOTE in hvl.hovertext
    assert "not a gamma flip" in HVL_NOTE and "re-priced" in HVL_NOTE


def test_a_missing_part_is_not_drawn_as_zero():
    figure = iv_times_open_interest_figure({100.0: {"call": 2.0, "put": None}}, {}, SPOT, "BTC")
    assert list(next(trace for trace in figure.data if trace.name == "IV x OI puts").x) == []


def test_net_gex_panels_bars_are_net_gex_over_spot_times_one_percent(replication):
    figure = net_gex_panels_figure(replication, "BTC")
    panels = replication["panels"]["absolute_net_gex"]
    first = panels["first_expiration"]
    assert first == "27NOV26"
    bars = [trace for trace in figure.data if trace.type == "bar"]
    assert len(bars) == 2                                  # four panel names, two expiries in the chain: two panels have one
    first_panel = bars[0]
    expected = {row["strike"]: row["net_gex"] / (SPOT * 0.01) for row in replication["gex_by_expiration"][first]["strikes"]}
    assert dict(zip(first_panel.y, first_panel.x)) == {strike: pytest.approx(value) for strike, value in expected.items()}
    assert "Call part" in first_panel.hovertext[0] and "per 1 % move" in first_panel.hovertext[0]
    assert "net = call part - put part" in first_panel.hovertext[0]
    assert "-3 USD" not in " ".join(first_panel.hovertext) and _scaled_text(-3.14159) == "-3.14" and _scaled_text(12345.6) == "12,346"
    subplot_titles = [annotation.text for annotation in figure.layout.annotations if "expiration" in annotation.text.lower()]
    assert len(subplot_titles) == 4 and "27NOV26" in subplot_titles[0] and "GEX expiring" in subplot_titles[0]
    assert "no expiry" in subplot_titles[2]


def test_net_gex_panel_levels_are_the_levels_of_that_expiry(replication):
    figure = net_gex_panels_figure(replication, "BTC")
    levels = select_drawn_levels(replication["gex_by_expiration"]["27NOV26"]["levels"])
    first_panel_lines = [trace for trace in figure.data if trace.type == "scatter" and trace.xaxis == "x" and trace.name]
    by_name = {trace.name: trace.y[0] for trace in first_panel_lines}
    for key, name in (("call_resistance", "Call resistance"), ("put_support", "Put support"), ("hvl", "HVL")):
        assert (name in by_name) == (levels[key] is not None)
        if levels[key] is not None:
            assert by_name[name] == levels[key]


def test_net_dex_bars_equal_the_replication_net_dex(replication):
    figure = net_dex_figure(replication, "BTC")
    bars = figure.data[0]
    expected = {row["strike"]: row["net_dex"] for row in replication["net_dex_all_expiries"]["strikes"] if row["net_dex"] is not None}
    assert dict(zip(bars.y, bars.x)) == {strike: pytest.approx(value) for strike, value in expected.items()}
    assert "Net DEX" in bars.hovertext[0] and "USD" in bars.hovertext[0]
    assert {trace.name for trace in figure.data} >= {"Net DEX", "DEX profile", "Spot"}
    profile = next(trace for trace in figure.data if trace.name == "DEX profile")
    assert "drawn value" in profile.hovertext[0] and "rescaled, no unit" in profile.hovertext[0]


def test_per_expiry_figure_uses_the_levels_of_that_expiry(replication):
    result = compute_expiry_iv_times_open_interest(ROWS, "25DEC26", replication["gex_by_expiration"]["25DEC26"])
    figure = expiry_iv_times_open_interest_figure(result, SPOT, "BTC")
    assert bar_values(figure, "IV x OI calls") == {100.0: pytest.approx(0.5 * 4.0), 110.0: pytest.approx(0.5 * 5.0)}
    assert lines_by_name(figure)["Spot"] == SPOT
    result["levels"] = None                                  # an expiry whose GEX was not computed
    assert set(lines_by_name(expiry_iv_times_open_interest_figure(result, SPOT, "BTC"))) == {"Spot"}


def test_no_dollar_sign_anywhere_and_no_title(replication):
    figures = [replication_iv_times_open_interest_figure(replication, "BTC"), net_gex_panels_figure(replication, "BTC"),
               net_dex_figure(replication, "BTC")]
    for figure in figures:
        assert "$" not in json.dumps(figure.to_plotly_json())
        assert not figure.layout.title.text
