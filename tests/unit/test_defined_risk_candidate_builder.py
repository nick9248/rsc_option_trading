"""
Tests for defined_risk_candidate_builder. Hand-verified numbers -- see
comments for the manual arithmetic each assertion checks.
"""
import math

from coding.service.scanner.defined_risk_candidate_builder import (
    build_butterfly_candidates,
    build_iron_condor_candidates,
    butterfly_payoff,
    iron_condor_payoff,
    passes_liquidity,
)


class TestPassesLiquidity:
    def test_missing_bid_fails(self):
        assert passes_liquidity(None, 100.0, 50) is False

    def test_wide_spread_fails(self):
        # (100-50)/75 = 0.667 > 0.15
        assert passes_liquidity(50.0, 100.0, 50) is False

    def test_low_oi_fails(self):
        assert passes_liquidity(99.0, 101.0, 10) is False

    def test_tight_spread_and_oi_passes(self):
        # (101-99)/100 = 0.02 <= 0.15
        assert passes_liquidity(99.0, 101.0, 50) is True


def _liquid_chain():
    """
    Small synthetic chain around F=65000. Strikes 1000 apart, ask = strike-scaled
    placeholder premiums (not realistic pricing -- just enough structure to
    exercise the candidate-construction grid deterministically).
    """
    strikes = [58000, 60000, 61000, 62000, 63000, 64000, 65000, 66000, 67000, 68000, 70000, 72000, 74000, 76000, 78000]
    liquid = {}
    for k in strikes:
        # Symmetric-ish decreasing premium as strikes move away from 65000, both sides liquid.
        dist = abs(k - 65000)
        premium = max(50.0, 5000.0 - dist * 0.5)
        liquid[k] = {
            "call_bid": premium * 0.98, "call_ask": premium * 1.02, "call_ok": True,
            "put_bid": premium * 0.98, "put_ask": premium * 1.02, "put_ok": True,
        }
    return liquid


class TestBuildIronCondorCandidates:
    def test_produces_candidates_for_synthetic_chain(self):
        liquid = _liquid_chain()
        F = 65000.0
        sigma_sqrt_t = 0.30 * math.sqrt(40 / 365.0)
        candidates = build_iron_condor_candidates(liquid, F, sigma_sqrt_t)
        assert len(candidates) > 0
        for c in candidates:
            assert c["short_call"] > F > c["short_put"]
            assert c["long_call"] > c["short_call"]
            assert c["long_put"] < c["short_put"]
            assert c["cost_or_credit"] > 0
            assert c["max_loss"] > 0
            # max_loss must equal wing_width - credit (hand-check the invariant)
            wing_width = max(c["long_call"] - c["short_call"], c["short_put"] - c["long_put"])
            assert abs(c["max_loss"] - (wing_width - c["cost_or_credit"])) < 0.01

    def test_no_candidates_when_chain_too_thin(self):
        liquid = {65000: {"call_bid": 100, "call_ask": 105, "call_ok": True,
                           "put_bid": 100, "put_ask": 105, "put_ok": True}}
        candidates = build_iron_condor_candidates(liquid, 65000.0, 0.1)
        assert candidates == []


class TestBuildButterflyCandidates:
    def test_produces_candidates_for_synthetic_chain(self):
        liquid = _liquid_chain()
        F = 65000.0
        sigma_sqrt_t = 0.30 * math.sqrt(40 / 365.0)
        candidates = build_butterfly_candidates(liquid, F, sigma_sqrt_t)
        assert len(candidates) > 0
        for c in candidates:
            assert c["k1"] < c["k2"] < c["k3"]
            assert c["cost_or_credit"] > 0
            assert c["max_profit"] > 0
            # max_profit must equal min(wing widths) - cost (hand-check the invariant)
            w_lo, w_hi = c["k2"] - c["k1"], c["k3"] - c["k2"]
            assert abs(c["max_profit"] - (min(w_lo, w_hi) - c["cost_or_credit"])) < 0.01


class TestPayoffs:
    def test_iron_condor_payoff_full_win_between_short_strikes(self):
        # Price lands between the short strikes -> both spreads worthless -> keep full credit.
        candidate = {"short_call": 70000, "long_call": 72000, "short_put": 58000, "long_put": 56000, "cost_or_credit": 500.0}
        assert iron_condor_payoff(candidate, settlement=65000.0) == 500.0

    def test_iron_condor_payoff_full_loss_beyond_long_call(self):
        # Price beyond the long call -> call spread owed = full wing width (2000).
        candidate = {"short_call": 70000, "long_call": 72000, "short_put": 58000, "long_put": 56000, "cost_or_credit": 500.0}
        pnl = iron_condor_payoff(candidate, settlement=80000.0)
        assert pnl == 500.0 - 2000.0  # = -1500.0, i.e. max_loss = wing_width - credit

    def test_butterfly_payoff_max_at_mid_strike(self):
        candidate = {"k1": 63000, "k2": 65000, "k3": 67000, "cost_or_credit": 400.0}
        pnl = butterfly_payoff(candidate, settlement=65000.0)
        assert pnl == (65000 - 63000) - 400.0  # = 1600.0, payout=width_lo at the mid strike

    def test_butterfly_payoff_full_loss_below_k1(self):
        candidate = {"k1": 63000, "k2": 65000, "k3": 67000, "cost_or_credit": 400.0}
        pnl = butterfly_payoff(candidate, settlement=60000.0)
        assert pnl == -400.0  # all legs worthless -> lose the full debit
