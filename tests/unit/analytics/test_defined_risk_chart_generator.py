"""
Tests for the defined-risk payoff chart functions. Verifies the P&L curve
at a handful of settlement points matches the hand-computed payoff (same
formulas as defined_risk_candidate_builder.iron_condor_payoff/butterfly_payoff),
not just that a Figure object comes back.
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

    def test_strike_lines_are_labeled_with_annotations(self):
        # Regression test: add_vline's `label` variable was computed but
        # never passed to add_vline, so strike markers rendered with no
        # annotation text at all. Verified against the actual plotly
        # Figure structure (fig.layout.annotations), not assumed.
        candidate = _ic_candidate()
        fig = generate_iron_condor_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        annotation_texts = [a.text for a in fig.layout.annotations]
        assert annotation_texts == ["short C", "long C", "short P", "long P", "F"]


class TestButterflyChart:
    def test_returns_figure_with_pnl_curve_matching_payoff_formula(self):
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        pnl_trace = fig.data[0]
        for x, y in zip(pnl_trace.x, pnl_trace.y):
            expected = butterfly_payoff(candidate, settlement=x)
            assert abs(y - expected) < 0.01

    def test_strike_lines_are_labeled_with_annotations(self):
        candidate = _bf_candidate()
        fig = generate_butterfly_payoff_chart("BTC", "1SEP26", 39.0, 65000.0, candidate)
        annotation_texts = [a.text for a in fig.layout.annotations]
        assert annotation_texts == ["K1", "K2", "K3", "F"]
