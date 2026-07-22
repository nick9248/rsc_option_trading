"""
Tests for the defined-risk payoff chart functions. Verifies the P&L curve
at a handful of settlement points matches the hand-computed payoff (same
formulas as defined_risk_candidate_builder.iron_condor_payoff/butterfly_payoff),
not just that a Figure object comes back. Also verifies the charts carry
the same visual language as generate_straddle_payoff_chart (explicit
theme colors, strike/breakeven annotations, a stats box) since that is
what makes them compatible with inject_theme_toggle_js -- a chart built
with template="plotly_dark" instead of explicit colors silently breaks
the light/dark toggle (see chart_generator.py history).
"""
from coding.core.analytics.chart_generator import (
    generate_butterfly_payoff_chart,
    generate_iron_condor_payoff_chart,
)
from coding.service.scanner.defined_risk_candidate_builder import (
    butterfly_payoff,
    iron_condor_payoff,
)


def _ic_candidate():
    return {
        "short_call": 70000.0, "long_call": 72000.0, "short_put": 58000.0, "long_put": 56000.0,
        "cost_or_credit": 500.0, "max_loss": 1500.0, "max_profit": 500.0,
        "breakeven_lo": 57500.0, "breakeven_hi": 70500.0, "prob_profit": 60.0, "ev": 10.0,
    }


def _bf_candidate():
    return {"k1": 63000.0, "k2": 65000.0, "k3": 67000.0, "cost_or_credit": 400.0,
            "max_profit": 1600.0, "breakeven_lo": 63400.0, "breakeven_hi": 66600.0,
            "prob_profit": 40.0, "ev": 5.0}


class TestIronCondorChart:
    def test_returns_figure_with_pnl_curve_matching_payoff_formula(self):
        candidate = _ic_candidate()
        fig = generate_iron_condor_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        pnl_trace = fig.data[0]
        for x, y in zip(pnl_trace.x, pnl_trace.y):
            expected = iron_condor_payoff(candidate, settlement=x)
            assert abs(y - expected) < 0.01

    def test_strike_and_breakeven_lines_are_labeled_with_annotations(self):
        # Regression test: add_vline's `label` variable was computed but
        # never passed to add_vline, so strike markers rendered with no
        # annotation text at all. Verified against the actual plotly
        # Figure structure (fig.layout.annotations), not assumed. Order is
        # not asserted -- labels are staggered in x-sorted order to avoid
        # overlap, which is an implementation detail, not a contract.
        candidate = _ic_candidate()
        fig = generate_iron_condor_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        annotation_texts = [a.text for a in fig.layout.annotations]
        for expected in ("short C", "long C", "short P", "long P", "F"):
            assert expected in annotation_texts
        assert any(t.startswith("BE↓") for t in annotation_texts)
        assert any(t.startswith("BE↑") for t in annotation_texts)

    def test_explicit_theme_colors_not_named_template(self):
        # Regression test: template="plotly_dark" stores colors inside a
        # named Plotly template, which inject_theme_toggle_js cannot
        # introspect -- it only remaps colors set explicitly on the
        # figure's own layout/annotations/shapes/traces. Pin the explicit
        # color contract so this can't silently regress back to a
        # template= shortcut.
        candidate = _ic_candidate()
        fig = generate_iron_condor_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        assert fig.layout.paper_bgcolor == "#1a1a1a"
        assert fig.layout.plot_bgcolor == "#1a1a1a"
        assert fig.data[0].line.color == "#60a5fa"

    def test_stats_box_shows_credit_and_max_loss(self):
        candidate = _ic_candidate()
        fig = generate_iron_condor_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        stats_texts = [a.text for a in fig.layout.annotations if "Credit" in (a.text or "")]
        assert len(stats_texts) == 1
        assert "$500" in stats_texts[0]
        assert "Max loss" in stats_texts[0] and "$1,500" in stats_texts[0]

    def test_polarity_fill_traces_present(self):
        # Regression test: iron condor's loss region is two disconnected
        # wings either side of a profit plateau -- one gapped trace per
        # polarity let Plotly bridge a thin sliver of red fill across the
        # gap, under the plateau (see _add_polarity_fills' docstring).
        # Each contiguous same-sign run must be its own trace: 2 loss
        # segments (the wings) + 1 profit segment (the plateau) = 3, not 2.
        candidate = _ic_candidate()
        fig = generate_iron_condor_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        fill_traces = [t for t in fig.data if getattr(t, "fill", None) == "tozeroy"]
        assert len(fill_traces) == 3
        loss_segments = [t for t in fill_traces if t.fillcolor == "rgba(239,68,68,0.12)"]
        profit_segments = [t for t in fill_traces if t.fillcolor == "rgba(34,197,94,0.15)"]
        assert len(loss_segments) == 2
        assert len(profit_segments) == 1
        for seg in loss_segments:
            assert all(y is not None and y < 0 for y in seg.y)
        assert all(y is not None and y >= 0 for y in profit_segments[0].y)


class TestButterflyChart:
    def test_returns_figure_with_pnl_curve_matching_payoff_formula(self):
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        pnl_trace = fig.data[0]
        for x, y in zip(pnl_trace.x, pnl_trace.y):
            expected = butterfly_payoff(candidate, settlement=x)
            assert abs(y - expected) < 0.01

    def test_strike_and_breakeven_lines_are_labeled_with_annotations(self):
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        annotation_texts = [a.text for a in fig.layout.annotations]
        for expected in ("K1", "K2", "K3", "F"):
            assert expected in annotation_texts
        assert any(t.startswith("BE↓") for t in annotation_texts)
        assert any(t.startswith("BE↑") for t in annotation_texts)

    def test_explicit_theme_colors_not_named_template(self):
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        assert fig.layout.paper_bgcolor == "#1a1a1a"
        assert fig.layout.plot_bgcolor == "#1a1a1a"
        assert fig.data[0].line.color == "#60a5fa"

    def test_stats_box_shows_cost_and_max_profit(self):
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        stats_texts = [a.text for a in fig.layout.annotations if "Cost" in (a.text or "")]
        assert len(stats_texts) == 1
        assert "$400" in stats_texts[0]
        assert "Max profit" in stats_texts[0] and "$1,600" in stats_texts[0]

    def test_polarity_fill_traces_present(self):
        # Same regression as the iron condor test: butterfly's loss region
        # is two disconnected wings either side of a profit peak.
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        fill_traces = [t for t in fig.data if getattr(t, "fill", None) == "tozeroy"]
        assert len(fill_traces) == 3
        loss_segments = [t for t in fill_traces if t.fillcolor == "rgba(239,68,68,0.12)"]
        profit_segments = [t for t in fill_traces if t.fillcolor == "rgba(34,197,94,0.15)"]
        assert len(loss_segments) == 2
        assert len(profit_segments) == 1
        for seg in loss_segments:
            assert all(y is not None and y < 0 for y in seg.y)
        assert all(y is not None and y >= 0 for y in profit_segments[0].y)
