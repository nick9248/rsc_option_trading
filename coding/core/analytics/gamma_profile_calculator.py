"""
Gamma profile / Zero Gamma Level (dealer gamma flip) calculator.

bugfix_spec.md Item 2 / task B1. The pre-existing "Zero Gamma Level" produced
by ``GexDexCalculator._detect_key_levels()`` (now exposed as the renamed,
documented ``cumulative_gex_zero_strike``) is a strike-axis cumulative-GEX
sign-crossing artifact: a property of how open interest happens to be
distributed along the strike axis, not of how dealer gamma actually responds
to the underlying moving. Verified live: it can sit on the OPPOSITE side of
spot from the true gamma flip (project value 66,000 above spot vs. a
re-priced flip of ~62,000 below spot).

This module instead answers "at what hypothetical spot price would total
dealer gamma flip sign" by re-pricing every leg's Black-Scholes gamma across
a grid of hypothetical spot levels and finding where the signed sum crosses
zero -- the definition used by SpotGamma ("the estimated level at which
dealers flip from long gamma to short gamma") and recommended by industry
practice to avoid "teleporting" strike-crossing artifacts.

Assumptions (documented, not hidden):
  - Sticky-strike: each leg's implied volatility is held FIXED as the
    hypothetical spot is varied. This is the standard simplification for
    dealer-gamma profiles and the only assumption consistent with re-pricing
    from observed per-strike mark IV.
  - r = q = 0 (Deribit inverse-settled crypto convention, used throughout
    this repo).

Validated against a closed-form check (bugfix_spec.md section 2.2): for a
book of one call at K_c and one put at K_p with equal OI/sigma/tau,
gamma_c(S) = gamma_p(S) requires d1(K_c) = -d1(K_p), giving
    S* = sqrt(K_c * K_p) * exp(-sigma^2 * tau / 2)
See tests/unit/analytics/test_gamma_profile_calculator.py::TestClosedFormCrossing.
"""

import logging
import math
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GammaLeg:
    """One option leg's static inputs to the gamma re-pricing grid."""

    strike: float
    implied_volatility: float  # sigma, decimal (e.g. 0.50 for 50%), held fixed (sticky-strike)
    time_to_expiry_years: float  # tau, in years, from now to 08:00 UTC expiry
    open_interest: float
    option_type: str  # "C" or "P"


class GammaProfileCalculator:
    """
    Re-price total dealer gamma at hypothetical spot levels to locate the
    zero-gamma level (gamma flip).

    Sticky-strike: each leg's mark_iv is held fixed as spot is varied.
    r = q = 0 (Deribit inverse-settled convention).
    """

    GRID_LOW_MULTIPLIER = 0.5
    GRID_HIGH_MULTIPLIER = 1.5
    GRID_RELATIVE_STEP = 0.005
    BISECTION_TOLERANCE = 1e-6
    BISECTION_MAX_ITERATIONS = 80

    # A book whose net dealer gamma is identically zero at every grid point
    # (e.g. an equal-size straddle at one strike, where gamma_call ==
    # gamma_put) must NOT be treated as 222 strict sign changes -- see the
    # module docstring and T2.3.
    FLAT_GAMMA_TOLERANCE = 1e-12

    # Expiry-day gate: as tau -> 0, gamma -> infinity at the money and -> 0
    # elsewhere, turning the profile into a meaningless spike train.
    MIN_TIME_TO_EXPIRY_YEARS = 1.0 / 365.0 / 24.0  # 1 hour

    # sigma > 500% is kept (gamma is still well-defined) but clamped to
    # avoid exp() underflow/overflow noise in d1.
    MAX_IMPLIED_VOLATILITY = 5.0

    def __init__(self, legs: List[GammaLeg], spot_price: float):
        self.spot_price = spot_price
        self._input_leg_count = len(legs)
        self.legs, self.legs_skipped = self._filter_legs(legs)

    @classmethod
    def _filter_legs(cls, legs: List[GammaLeg]) -> Tuple[List[GammaLeg], int]:
        """Gate expiring legs (tau < 1 hour) and clamp extreme IV."""
        filtered: List[GammaLeg] = []
        skipped = 0
        for leg in legs:
            if leg.time_to_expiry_years < cls.MIN_TIME_TO_EXPIRY_YEARS:
                skipped += 1
                continue
            if leg.implied_volatility > cls.MAX_IMPLIED_VOLATILITY:
                logger.warning(
                    "GammaProfileCalculator: clamping extreme implied "
                    f"volatility {leg.implied_volatility:.2f} to "
                    f"{cls.MAX_IMPLIED_VOLATILITY} for strike {leg.strike}"
                )
                leg = replace(leg, implied_volatility=cls.MAX_IMPLIED_VOLATILITY)
            filtered.append(leg)

        total = len(legs)
        if total > 0 and (skipped / total) > 0.2:
            logger.warning(
                f"GammaProfileCalculator: {skipped}/{total} legs skipped "
                "(>20%) building the gamma profile (expiring or otherwise gated)"
            )
        return filtered, skipped

    @staticmethod
    def _leg_gamma(leg: GammaLeg, spot: float) -> float:
        """Black-Scholes gamma at a hypothetical spot, r = q = 0."""
        sigma = leg.implied_volatility
        tau = leg.time_to_expiry_years
        if sigma <= 0 or tau <= 0 or spot <= 0 or leg.strike <= 0:
            return 0.0
        sqrt_t = math.sqrt(tau)
        d1 = (math.log(spot / leg.strike) + 0.5 * sigma * sigma * tau) / (sigma * sqrt_t)
        phi_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
        return phi_d1 / (spot * sigma * sqrt_t)

    def net_dealer_gamma_at(self, spot: float) -> float:
        """
        Net dealer gamma at a hypothetical spot: sum of sign_i * gamma_i(spot)
        * OI_i across all (post-gate) legs. Dealers are long calls, short
        puts -- same convention as the existing GEX formula.
        """
        total = 0.0
        for leg in self.legs:
            sign = 1.0 if leg.option_type.upper() == "C" else -1.0
            total += sign * self._leg_gamma(leg, spot) * leg.open_interest
        return total

    def net_gex_at(self, spot: float) -> float:
        """Net dollar gamma exposure at a hypothetical spot: Gamma_net(S) * S^2 * 0.01."""
        return self.net_dealer_gamma_at(spot) * (spot ** 2) * 0.01

    def _build_grid(self) -> List[float]:
        """
        Geometric (log-uniform) grid, because gamma is a function of
        ln(S/K): S_j = S0 * 0.5 * (1.005)^j, j = 0..n,
        n = ceil(ln(3)/ln(1.005)) = 221 -> 222 points spanning
        [0.5*S0, 1.5*S0] at 0.5% relative steps.
        """
        low = self.spot_price * self.GRID_LOW_MULTIPLIER
        ratio = self.GRID_HIGH_MULTIPLIER / self.GRID_LOW_MULTIPLIER
        step = 1.0 + self.GRID_RELATIVE_STEP
        n = math.ceil(math.log(ratio) / math.log(step))
        return [low * (step ** j) for j in range(n + 1)]

    def _bisect(self, a: float, b: float, fa: float) -> float:
        """Bisection to relative tolerance on a bracket with a strict sign change."""
        for _ in range(self.BISECTION_MAX_ITERATIONS):
            m = (a + b) / 2.0
            fm = self.net_dealer_gamma_at(m)
            if (b - a) / m < self.BISECTION_TOLERANCE:
                return m
            if (fa < 0) == (fm < 0):
                a, fa = m, fm
            else:
                b = m
        return (a + b) / 2.0

    def calculate(self) -> Dict[str, Any]:
        """
        Returns:
            {
              "zero_gamma_level": Optional[float],     # nearest crossing to spot
              "zero_gamma_crossings": List[float],     # all crossings, ascending
              "net_gex_at_spot": Optional[float],
              "gamma_profile": List[Tuple[float, float]],   # (spot, net_gex) for charts
              "spot_price": float,
              "leg_count": int,
              "legs_skipped": int,
              "regime": str,                           # "POSITIVE" | "NEGATIVE" | "FLAT" | "UNKNOWN"
            }
        """
        base_result = {
            "spot_price": self.spot_price,
            "leg_count": len(self.legs),
            "legs_skipped": self.legs_skipped,
        }

        # Never divide by zero / take log of a non-positive spot.
        if self.spot_price is None or self.spot_price <= 0:
            return {
                **base_result,
                "zero_gamma_level": None,
                "zero_gamma_crossings": [],
                "net_gex_at_spot": None,
                "gamma_profile": [],
                "regime": "UNKNOWN",
            }

        # No legs survived gating (or none were passed in) -- nothing to
        # price. Distinct from the FLAT regime below (which means "we DID
        # price a book and its net gamma is identically zero everywhere") --
        # this means there is no gamma data to compute a regime from at all.
        if not self.legs:
            return {
                **base_result,
                "zero_gamma_level": None,
                "zero_gamma_crossings": [],
                "net_gex_at_spot": None,
                "gamma_profile": [],
                "regime": "UNKNOWN",
            }

        grid = self._build_grid()
        grid_values = [self.net_dealer_gamma_at(s) for s in grid]
        net_gex_at_spot = self.net_gex_at(self.spot_price)

        # Guard against the self-caught flaw in an early draft: a book whose
        # net dealer gamma is identically zero at every grid point (e.g. an
        # equal-size straddle at one strike) must be detected as FLAT, never
        # treated as one strict sign change per grid point.
        if max(abs(v) for v in grid_values) < self.FLAT_GAMMA_TOLERANCE:
            return {
                **base_result,
                "zero_gamma_level": None,
                "zero_gamma_crossings": [],
                "net_gex_at_spot": 0.0,
                "gamma_profile": list(zip(grid, grid_values)),
                "regime": "FLAT",
            }

        # Only STRICT sign changes count -- an exact-zero grid point is not
        # treated as a crossing on its own (it would double-count with the
        # adjacent bracket and, on a degenerate book, produce one "root" per
        # grid point instead of the FLAT regime detected above).
        crossings: List[float] = []
        for j in range(1, len(grid)):
            fa, fb = grid_values[j - 1], grid_values[j]
            if fa * fb < 0:
                crossings.append(self._bisect(grid[j - 1], grid[j], fa))

        crossings.sort()

        if net_gex_at_spot > 0:
            regime = "POSITIVE"
        elif net_gex_at_spot < 0:
            regime = "NEGATIVE"
        else:
            regime = "FLAT"

        zero_gamma_level = (
            min(crossings, key=lambda s: abs(s - self.spot_price)) if crossings else None
        )

        return {
            **base_result,
            "zero_gamma_level": zero_gamma_level,
            "zero_gamma_crossings": crossings,
            "net_gex_at_spot": net_gex_at_spot,
            "gamma_profile": list(zip(grid, grid_values)),
            "regime": regime,
        }
