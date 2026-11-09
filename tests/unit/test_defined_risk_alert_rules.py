"""Tests for format_defined_risk_alert."""
from datetime import datetime, timezone
from typing import Optional

from coding.service.scanner.defined_risk_alert_rules import format_defined_risk_alert


def _candidate(ev=10.0, gate_pass=True):
    return {
        "structure_type": "iron_condor", "short_call": 70000.0, "long_call": 72000.0,
        "short_put": 58000.0, "long_put": 56000.0,
        "cost_or_credit": 500.0, "max_loss": 1500.0, "max_profit": 500.0,
        "breakeven_lo": 57500.0, "breakeven_hi": 70500.0,
        "prob_profit": 60.0, "ev": ev, "reward_risk": 0.333,
        "deribit_url": "https://www.deribit.com/options/BTC",
    }


def _scan_result(ev: Optional[float], expiry="1SEP26", gate_pass=True):
    if ev is None:
        return {"currency": "BTC", "as_of": datetime.now(timezone.utc), "index_price": 65000.0, "expiries": []}
    candidate = _candidate(ev=ev)
    return {
        "currency": "BTC", "as_of": datetime.now(timezone.utc), "index_price": 65000.0,
        "expiries": [{
            "expiry": expiry, "dte": 39.0, "F": 65000.0,
            "regime": {"net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.888, "gate_pass": gate_pass},
            "best": candidate, "candidates": [candidate],
        }],
    }


def _butterfly_candidate(ev=10.0):
    """Shape matches defined_risk_candidate_builder.build_butterfly_candidates rows -- k1/k2/k3,
    cost_or_credit, max_profit (NOT the iron-condor 4-leg short_call/long_call/short_put/long_put)."""
    return {
        "structure_type": "butterfly", "k1": 60000.0, "k2": 65000.0, "k3": 70000.0,
        "cost_or_credit": 800.0, "max_loss": 800.0, "max_profit": 4200.0,
        "breakeven_lo": 60800.0, "breakeven_hi": 69200.0,
        "prob_profit": 45.0, "ev": ev, "reward_risk": 5.25,
        "deribit_url": "https://www.deribit.com/options/BTC",
    }


def _butterfly_scan_result(ev: Optional[float], expiry="1SEP26", gate_pass=True):
    if ev is None:
        return {"currency": "BTC", "as_of": datetime.now(timezone.utc), "index_price": 65000.0, "expiries": []}
    candidate = _butterfly_candidate(ev=ev)
    return {
        "currency": "BTC", "as_of": datetime.now(timezone.utc), "index_price": 65000.0,
        "expiries": [{
            "expiry": expiry, "dte": 39.0, "F": 65000.0,
            "regime": {"net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.888, "gate_pass": gate_pass},
            "best": candidate, "candidates": [candidate],
        }],
    }


class TestFormatAlert:
    def test_gate_pass_label_in_header(self):
        message = format_defined_risk_alert(_scan_result(10.0, gate_pass=True), "iron_condor")
        assert "IRON CONDOR SCANNER" in message
        assert "CALM-REGIME MATCH" in message

    def test_gate_fail_plain_header(self):
        message = format_defined_risk_alert(_scan_result(10.0, gate_pass=False), "iron_condor")
        assert "IRON CONDOR SCANNER" in message
        assert "CALM-REGIME MATCH" not in message

    def test_no_expiries_no_candidates_message(self):
        message = format_defined_risk_alert(_scan_result(None), "butterfly")
        assert "No qualifying" in message

    def test_butterfly_candidate_renders_wings_not_iron_condor_legs(self):
        """Guards the butterfly body-render branch (k1/k2/k3/cost_or_credit/max_profit) against
        the iron-condor field set (short_call/long_call/short_put/long_put/max_loss wording) --
        with an empty-expiries scan_result this would early-return before ever reaching that
        branch, so this test uses a real butterfly-shaped candidate to actually exercise it."""
        message = format_defined_risk_alert(_butterfly_scan_result(10.0), "butterfly")

        assert "Wings: 60000 / 65000 / 70000" in message
        assert "Cost: $800.00" in message
        assert "max profit $4,200.00" in message
        assert "Credit:" not in message
        assert "max loss" not in message
