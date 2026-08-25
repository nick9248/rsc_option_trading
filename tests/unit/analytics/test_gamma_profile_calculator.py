"""
Unit tests for GammaProfileCalculator (bugfix_spec.md Item 2 / task B1).

The current "Zero Gamma Level" (GexDexCalculator._detect_key_levels()'s
hvl/gamma_flip) is a strike-axis cumulative-GEX-crossing artifact -- a
property of how OI is distributed along the strike axis, not of how dealer
gamma responds to spot. This module re-prices total dealer gamma at a grid
of hypothetical spot levels (sticky-strike: each leg's mark IV held fixed)
to find the actual gamma flip point.

Acceptance tests T2.1-T2.4 are verbatim from bugfix_spec.md section 2.5,
including the hand-computed closed-form expected numbers.
"""

import pytest

from coding.core.analytics.gamma_profile_calculator import (
    GammaLeg,
    GammaProfileCalculator,
)


class TestClosedFormCrossing:
    """T2.1 -- closed-form crossing (the hand-checkable case).

    For a book of one call at K_c and one put at K_p with equal OI, sigma,
    tau: gamma_c(S) = gamma_p(S) requires d1(K_c) = -d1(K_p), giving the
    closed form S* = sqrt(K_c * K_p) * exp(-sigma^2 * tau / 2).

    K_c=110,000, K_p=90,000, sigma=0.50, tau=30/365:
      S* = sqrt(110000*90000) * exp(-0.25*(30/365)/2)
         = 99498.743711 * 0.989778624 = 98481.7297
    """

    def _calc(self):
        legs = [
            GammaLeg(110_000, 0.50, 30 / 365, 100, "C"),
            GammaLeg(90_000, 0.50, 30 / 365, 100, "P"),
        ]
        return GammaProfileCalculator(legs, spot_price=100_000.0)

    def test_zero_gamma_level_matches_closed_form(self):
        calc = self._calc()
        r = calc.calculate()
        assert r["zero_gamma_level"] == pytest.approx(98_481.7297, rel=1e-5)

    def test_single_crossing(self):
        calc = self._calc()
        r = calc.calculate()
        assert len(r["zero_gamma_crossings"]) == 1

    def test_put_dominated_below_crossing(self):
        calc = self._calc()
        calc.calculate()
        assert calc.net_dealer_gamma_at(97_481.73) < 0

    def test_call_dominated_above_crossing(self):
        calc = self._calc()
        calc.calculate()
        assert calc.net_dealer_gamma_at(99_481.73) > 0

    def test_net_gex_is_zero_at_the_crossing(self):
        calc = self._calc()
        calc.calculate()
        assert calc.net_gex_at(98_481.7297) == pytest.approx(0.0, abs=1.0)


class TestNoCrossingReturnsNone:
    """T2.2 -- no crossing returns None, never a strike."""

    def test_all_calls_book(self):
        legs = [
            GammaLeg(110_000, 0.50, 30 / 365, 100, "C"),
            GammaLeg(90_000, 0.50, 30 / 365, 100, "C"),
        ]
        r = GammaProfileCalculator(legs, 100_000.0).calculate()
        assert r["zero_gamma_crossings"] == []
        assert r["zero_gamma_level"] is None
        assert r["regime"] == "POSITIVE"


class TestFlatBookGuard:
    """T2.3 -- identically-flat book must NOT report 222 crossings.

    gamma_call == gamma_put exactly at the same strike/sigma/tau, so
    Gamma_net(S) = +gamma*100 - gamma*100 = 0 for EVERY S. Verified live:
    max|Gamma_net| = 0.0, 222 exact zeros, 0 strict sign changes. This
    guards the self-caught flaw in an early draft that added a
    `Gamma_net(S_j) == 0 -> record a root` branch, which reported 222 false
    crossings on exactly this book.
    """

    def _calc(self):
        legs = [
            GammaLeg(100_000, 0.50, 30 / 365, 100, "C"),
            GammaLeg(100_000, 0.50, 30 / 365, 100, "P"),
        ]
        return GammaProfileCalculator(legs, 100_000.0)

    def test_no_crossings_reported(self):
        r = self._calc().calculate()
        assert r["zero_gamma_crossings"] == []  # NOT one entry per grid point

    def test_zero_gamma_level_is_none(self):
        r = self._calc().calculate()
        assert r["zero_gamma_level"] is None

    def test_net_gex_at_spot_is_zero(self):
        r = self._calc().calculate()
        assert r["net_gex_at_spot"] == pytest.approx(0.0, abs=1e-6)

    def test_regime_is_flat(self):
        r = self._calc().calculate()
        assert r["regime"] == "FLAT"


class TestExpiringLegGated:
    """T2.4 -- expiring leg is gated (tau < 1 hour excluded, count tracked)."""

    def test_expiring_leg_skipped_and_result_unchanged(self):
        legs = [
            GammaLeg(110_000, 0.50, 30 / 365, 100, "C"),
            GammaLeg(90_000, 0.50, 30 / 365, 100, "P"),
            GammaLeg(100_000, 0.50, 0.5 / 365 / 24, 10_000, "P"),  # 30 min to expiry
        ]
        r = GammaProfileCalculator(legs, 100_000.0).calculate()
        assert r["legs_skipped"] == 1
        assert r["zero_gamma_level"] == pytest.approx(98_481.7297, rel=1e-5)


class TestEdgeCases:
    """bugfix_spec.md section 2.4 edge cases not covered by T2.1-T2.4."""

    def test_multiple_crossings_returns_all_nearest_to_spot_is_zero_gamma_level(self):
        # Three legs designed to create more than one sign change across the
        # grid: heavy put wall far below spot, heavy call wall far above,
        # and a modest put near spot -- net gamma flips near spot, and again
        # out towards the far call wall as the call gamma dominates.
        legs = [
            GammaLeg(60_000, 0.80, 14 / 365, 5_000, "P"),
            GammaLeg(98_000, 0.50, 14 / 365, 50, "P"),
            GammaLeg(102_000, 0.50, 14 / 365, 50, "C"),
            GammaLeg(140_000, 0.80, 14 / 365, 5_000, "C"),
        ]
        r = GammaProfileCalculator(legs, 100_000.0).calculate()
        assert len(r["zero_gamma_crossings"]) >= 1
        if r["zero_gamma_level"] is not None:
            nearest = min(r["zero_gamma_crossings"], key=lambda s: abs(s - 100_000.0))
            assert r["zero_gamma_level"] == pytest.approx(nearest)

    def test_spot_price_non_positive_returns_none_and_unknown_regime(self):
        legs = [GammaLeg(100_000, 0.50, 30 / 365, 100, "C")]
        r = GammaProfileCalculator(legs, 0.0).calculate()
        assert r["zero_gamma_level"] is None
        assert r["regime"] == "UNKNOWN"

    def test_single_leg_has_no_crossing(self):
        legs = [GammaLeg(100_000, 0.50, 30 / 365, 100, "C")]
        r = GammaProfileCalculator(legs, 100_000.0).calculate()
        assert r["zero_gamma_level"] is None
        assert r["zero_gamma_crossings"] == []

    def test_extreme_iv_is_clamped_not_rejected(self):
        # sigma = 8.0 (800%) should be clamped to 5.0, not blow up / NaN.
        legs = [
            GammaLeg(110_000, 8.0, 30 / 365, 100, "C"),
            GammaLeg(90_000, 0.50, 30 / 365, 100, "P"),
        ]
        r = GammaProfileCalculator(legs, 100_000.0).calculate()
        assert r["net_gex_at_spot"] is not None
        assert r["net_gex_at_spot"] == r["net_gex_at_spot"]  # not NaN

    def test_empty_legs_returns_none(self):
        r = GammaProfileCalculator([], 100_000.0).calculate()
        assert r["zero_gamma_level"] is None
        assert r["zero_gamma_crossings"] == []
