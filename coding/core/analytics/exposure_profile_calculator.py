"""
Per-strike vanna/charm exposure profile calculator (VEX/CEX).

institutional_metrics_spec.md section 4, Task C5. Pure: chain rows (+
optional side weights) -> per-strike VEX/CEX profiles. Does NOT re-implement
vanna/charm -- delegates to ``BlackScholesCalculator.calculate_vanna``/
``calculate_charm`` (the same closed forms Wave B's aggregate scalar in
``VolatilitySurfaceCalculator._calculate_second_order_greeks`` already uses).

Decision D7 (BINDING, established Wave B / task B2; REVISED Wave-I-D): two
sign conventions are computed from the SAME underlying vanna/charm value --
HOLDER-SIDE (every open contract counted +1, pure arithmetic, no positioning
assumption) and ASSUMED-DEALER VIEW. This module adds no third convention;
a caller may also supply externally-computed ``side_weights`` for an
INFERRED view (institutional_metrics_spec.md section 2's taker-flow-inferred
dealer positioning, when that section's gate passes) -- this calculator only
consumes whatever weights it is given for that convention, it does not
compute the gate itself (that lives in ``DealerInventoryCalculator``).

Wave-I-D (superseding D7's original ASSUMED-DEALER definition): D7 was
established in Wave B, BEFORE ``gex_dex_calculator.py``'s class docstring
(lines 50-66) later became this codebase's documented canonical SIGN
CONVENTION -- dealers long calls / short puts is a GAMMA-ONLY assumption;
delta/vanna/charm are each "short whatever customers (holders) hold" (the
negated holder-side sum). D7's original text applied the gamma-only
call/put SPLIT (+1 call, -1 put) to vanna/charm too, the same
over-generalization Wave-H-A already found and reverted in
``VolatilitySurfaceCalculator.dealer_vanna_exposure``/
``dealer_charm_exposure`` (commit e0eb59d). This module's ASSUMED-DEALER
VIEW is revised to match: -1 for every leg (call or put alike), i.e.
assumed_dealer == -holder at both the per-strike and total level -- see
``_side_weight``'s docstring for the mechanism. Unlike delta, vanna and
charm are sign-invariant between calls and puts at a given strike (put-call
parity: Delta_call - Delta_put = 1 is a spot/vol/time-independent constant,
so every higher derivative of that difference is exactly zero), so the old
SPLIT was not an always-wrong-sign bug the way delta's was -- but it still
diverged numerically from the canonical negated-sum whenever call OI != put
OI at a strike, which is the generic case for a real book.

tau recovery (spec section 4(b)): uses the INSTRUMENT NAME
(``BlackScholesCalculator.parse_instrument_name`` -> 08:00 UTC expiry), not
the gamma-inversion the aggregate scalar uses elsewhere in this codebase --
that inversion carries a measured +3.7% error from Deribit's gamma rounding
and is undefined when gamma is exactly 0. d1 is computed directly from
S, K, sigma, tau (the exact formula in spec section 4(b)), not recovered
from a stored delta -- this is the one piece of arithmetic this class does
itself (d1/d2 are a shared preliminary, not vanna/charm), matching the
existing precedent in ``VolatilitySurfaceCalculator._calculate_second_
order_greeks`` (which also computes d1/d2 itself before calling the same
two BlackScholesCalculator methods).

Idempotence (regression guard for the bug this class must not repeat):
``GexDexCalculator._aggregate_by_strike`` accumulates into ``self.
strike_data`` without resetting -- the 2x double-counting bug (bugfix_spec.md
BUG 1). This class holds NO mutable strike-keyed instance state at all --
``calculate()`` builds an entirely local dict every call and never touches
``self`` beyond the read-only constructor inputs, so repeated calls are
bit-identical by construction, not just "reset at entry."

Gate-exhaustiveness note (Task C3's 3-round lesson, applied even though this
class has no boolean "insufficient data" gate output): every way the input
data can be empty or degenerate degrades to a well-formed, non-crashing
result rather than raising --
  - ``instruments`` is an empty list -> empty ``strike_data``, zero totals,
    empty top-N lists, ``None`` peaks, ``skipped_instruments == 0``.
  - every instrument is missing/invalid (bad strike, bad option_type,
    missing/non-positive ``mark_iv``, unparseable ``instrument_name``) ->
    empty ``strike_data``, all counted in ``skipped_instruments``.
  - every strike has zero OI on both legs -> excluded from ``strike_data``
    entirely (spec 4(c) edge case: "OI = 0 -> contributes 0; keep in the
    table only if the strike has OI on the other side" -- if NEITHER side
    has OI the strike is dropped, not listed with two zero columns).
  - a single repeated strike (all instruments at the same K) -> one row,
    OI/vanna/charm accumulate correctly, no crash from dict-key collisions.
  - every remaining instrument has tau <= 0 (all expired/expiring at the
    valuation instant) -> every strike is still listed (per spec: "strike
    still listed"), with vex/cex == 0.0 for that leg -- never skipped.
"""

import logging
import math
from typing import Any, Dict, List, Literal, Optional

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator

logger = logging.getLogger(__name__)

_VALID_SIDE_CONVENTIONS = ("holder", "assumed_dealer", "inferred")

# Scaling constants (spec section 4(b)):
#   VEX: x0.01 converts vanna from per-1.00-sigma to per-vol-point; xS
#        converts coin-delta to USD notional.
#   CEX: x(1/365) converts charm from per-year to per-day; xS converts to
#        USD notional.
_VOL_POINT_SCALE = 0.01
_DAYS_PER_YEAR = 365.0


class ExposureProfileCalculator:
    """
    Compute per-strike VEX (vanna exposure) / CEX (charm exposure) profiles
    from a chain of enriched instrument dicts, matching the same enriched
    shape ``GexDexCalculator`` consumes (strike, option_type, open_interest,
    mark_iv, instrument_name).
    """

    def __init__(
        self,
        instruments: List[Dict[str, Any]],
        spot_price: float,
        valuation_time_utc,
        currency: str = "BTC",
    ):
        """
        Args:
            instruments: Enriched instrument dicts (same list GexDexCalculator
                consumes) -- each needs strike, option_type ("C"/"P"),
                open_interest, mark_iv, instrument_name.
            spot_price: Current underlying spot price (S in the formulas).
            valuation_time_utc: Naive UTC datetime this profile is computed
                as of -- MUST be naive-UTC (matching ``BlackScholesCalculator.
                parse_instrument_name``'s 08:00-UTC-naive expiry convention),
                never naive-local. Passed explicitly (not read from
                ``datetime.now()`` internally) so this class stays pure and
                deterministic/testable.
            currency: Underlying currency symbol, for labeling only.
        """
        self.instruments = instruments
        self.spot_price = spot_price
        self.valuation_time_utc = valuation_time_utc
        self.currency = currency
        self._bs = BlackScholesCalculator()

    def calculate(
        self,
        side_convention: Literal["holder", "assumed_dealer", "inferred"] = "holder",
        side_weights: Optional[Dict[Any, float]] = None,
    ) -> Dict[str, Any]:
        """
        Compute the per-strike VEX/CEX profile for one side convention.

        Args:
            side_convention: "holder" (every open contract +1), "assumed_dealer"
                (-1 for every open contract -- dealers short whatever
                customers hold, Wave-I-D; see this module's docstring), or
                "inferred" (caller-supplied ``side_weights``, e.g. from
                section 2's taker-flow-inferred dealer positioning when its
                gate passes).
            side_weights: Required when ``side_convention == "inferred"``.
                Maps ``(strike, option_type)`` -> per-leg side weight. A leg
                not present in the mapping contributes 0 (excluded from the
                inferred view, not defaulted to holder/dealer).

        Returns:
            {
              "strike_data": {strike: {call_oi, put_oi, call_vanna,
                  put_vanna, call_charm, put_charm, vex, cex}},
              "total_vex": float, "total_cex": float,
              "top_vanna_strikes": [strike, ...] (desc by |vex|, top 5),
              "top_charm_strikes": [strike, ...] (desc by |cex|, top 5),
              "peak_vanna_strike": float or None,
              "peak_charm_strike": float or None,
              "spot_price": float,
              "skipped_instruments": int,
            }
        """
        if side_convention not in _VALID_SIDE_CONVENTIONS:
            raise ValueError(f"Unknown side_convention: {side_convention!r}")
        if side_convention == "inferred" and side_weights is None:
            raise ValueError("side_weights is required when side_convention='inferred'")

        strike_data: Dict[float, Dict[str, float]] = {}
        skipped_instruments = 0

        for item in self.instruments:
            strike = item.get("strike")
            option_type = (item.get("option_type") or "").upper()
            if strike is None or option_type not in ("C", "P"):
                skipped_instruments += 1
                continue

            mark_iv = item.get("mark_iv")
            if mark_iv is None:
                skipped_instruments += 1
                continue
            try:
                mark_iv = float(mark_iv)
            except (TypeError, ValueError):
                skipped_instruments += 1
                continue
            # Minor #2 (Task C5 review round 1) + round-2 follow-up:
            # `mark_iv <= 0` is False for NaN (any comparison against NaN
            # is False), so a NaN mark_iv would otherwise slip through and
            # propagate into d1/vanna/charm as NaN -- silently, no
            # exception raised -- all the way into a persisted NUMERIC
            # column. The round-1 fix added an explicit math.isnan() check
            # but +/-inf passes BOTH `mark_iv <= 0` and math.isnan (isnan
            # is specifically NaN, not infinite) -- inf/inf in the d1
            # calculation then produces NaN anyway, reproducing the exact
            # failure the guard was meant to close. math.isfinite() closes
            # both cases in one predicate (False for NaN AND +/-inf).
            if not math.isfinite(mark_iv) or mark_iv <= 0:
                skipped_instruments += 1
                continue

            instrument_name = item.get("instrument_name")
            tau = self._time_to_expiry(instrument_name)
            if tau is None:
                skipped_instruments += 1
                continue

            oi = item.get("open_interest") or 0.0
            sigma = float(mark_iv) / 100.0

            if tau <= 0:
                # Spec edge case: expired/expiring within the hour -> zero
                # exposure, strike still listed (never skipped).
                vanna, charm = 0.0, 0.0
            else:
                try:
                    d1 = self._calculate_d1(self.spot_price, strike, sigma, tau)
                    d2 = d1 - sigma * math.sqrt(tau)
                    vanna = self._bs.calculate_vanna(d1, d2, sigma)
                    charm = self._bs.calculate_charm(d1, d2, tau)
                except (TypeError, ValueError, ZeroDivisionError) as exc:
                    # Minor #1 (Task C5 review): TypeError must be caught
                    # here too -- a non-numeric strike (e.g. a malformed
                    # string) raises TypeError inside _calculate_d1's
                    # `spot / strike`, not ValueError. Without it in this
                    # tuple, ONE bad instrument's TypeError propagated out
                    # of calculate() entirely, aborting the WHOLE profile
                    # (every strike, not just the bad one) -- contradicting
                    # this class's own per-instrument-skip design.
                    logger.warning(
                        "Skipping vanna/charm for instrument %s: %s",
                        instrument_name or "<unknown>", exc,
                    )
                    skipped_instruments += 1
                    continue

            row = strike_data.setdefault(strike, {
                "call_oi": 0.0, "put_oi": 0.0,
                "call_vanna": 0.0, "put_vanna": 0.0,
                "call_charm": 0.0, "put_charm": 0.0,
                "vex": 0.0, "cex": 0.0,
            })

            if option_type == "C":
                row["call_oi"] += oi
                row["call_vanna"] = vanna
                row["call_charm"] = charm
            else:
                row["put_oi"] += oi
                row["put_vanna"] = vanna
                row["put_charm"] = charm

            side = self._side_weight(side_convention, side_weights, strike, option_type)
            row["vex"] += side * oi * vanna * _VOL_POINT_SCALE * self.spot_price
            row["cex"] += side * oi * charm * (1.0 / _DAYS_PER_YEAR) * self.spot_price

        # Spec 4(c) edge case: a strike with zero OI on BOTH legs contributes
        # nothing and is not a real observed strike -- drop it. A strike with
        # OI on only one side is kept (the zero-OI leg already contributed 0).
        strike_data = {
            strike: row for strike, row in strike_data.items()
            if row["call_oi"] > 0 or row["put_oi"] > 0
        }

        total_vex = sum(row["vex"] for row in strike_data.values())
        total_cex = sum(row["cex"] for row in strike_data.values())

        top_vanna_strikes = sorted(
            strike_data.keys(), key=lambda k: abs(strike_data[k]["vex"]), reverse=True
        )[:5]
        top_charm_strikes = sorted(
            strike_data.keys(), key=lambda k: abs(strike_data[k]["cex"]), reverse=True
        )[:5]

        return {
            "strike_data": strike_data,
            "total_vex": total_vex,
            "total_cex": total_cex,
            "top_vanna_strikes": top_vanna_strikes,
            "top_charm_strikes": top_charm_strikes,
            "peak_vanna_strike": top_vanna_strikes[0] if top_vanna_strikes else None,
            "peak_charm_strike": top_charm_strikes[0] if top_charm_strikes else None,
            "spot_price": self.spot_price,
            "skipped_instruments": skipped_instruments,
        }

    def _time_to_expiry(self, instrument_name: Optional[str]) -> Optional[float]:
        """
        Recover time-to-expiry in years from the instrument name (spec 4(b):
        NOT the gamma-inversion the aggregate scalar elsewhere uses -- that
        carries a measured +3.7% error and is undefined at gamma == 0).

        Returns None if the name is missing or unparseable -- the caller
        counts this in ``skipped_instruments`` (no tau, no way to price).
        """
        if not instrument_name:
            return None
        parsed = self._bs.parse_instrument_name(instrument_name)
        if parsed is None:
            return None
        return self._bs.calculate_time_to_expiry(self.valuation_time_utc, parsed["expiry_time"])

    @staticmethod
    def _calculate_d1(spot: float, strike: float, sigma: float, tau: float) -> float:
        """
        d1 = [ln(S/K) + 0.5*sigma^2*tau] / (sigma*sqrt(tau)), r=q=0
        (institutional_metrics_spec.md section 4(b)).

        Computed directly here (not recovered from a stored delta) since,
        unlike the aggregate scalar elsewhere, this class has true tau from
        the instrument name and does not need the delta-inversion shortcut.
        This is shared d1/d2 scaffolding, not a reimplementation of vanna/
        charm themselves (those come from BlackScholesCalculator).
        """
        numerator = math.log(spot / strike) + 0.5 * sigma ** 2 * tau
        denominator = sigma * math.sqrt(tau)
        return numerator / denominator

    @staticmethod
    def _side_weight(
        side_convention: str,
        side_weights: Optional[Dict[Any, float]],
        strike: float,
        option_type: str,
    ) -> float:
        """
        Decision D7, REVISED (Wave-I-D, superseding the call/put-SPLIT
        convention established Wave B/task B2): side_holder = +1 for every
        open contract; side_assumed_dealer = -1 for every open contract
        (dealers short whatever customers/holders hold), matching
        GexDexCalculator's canonical SIGN CONVENTION (gex_dex_calculator.py
        lines 50-66: the long-calls/short-puts call/put-SPLIT applies to
        GAMMA ONLY -- delta/vanna/charm are each "short whatever customers
        hold") and Wave-H-A's precedent revert of
        VolatilitySurfaceCalculator.dealer_vanna_exposure/
        dealer_charm_exposure to the same negated-holder-sum convention.
        Per-leg, "negate every leg" and "negate the summed total" are
        identical by linearity, so this stays a per-leg weight (no second
        pass, no change to the accumulation loop above) while producing
        assumed_dealer == -holder at both the per-strike and total level.
        side_inferred is whatever the caller supplied for this leg (missing
        key -> 0, i.e. excluded from the inferred view rather than silently
        defaulted to another convention).
        """
        if side_convention == "holder":
            return 1.0
        if side_convention == "assumed_dealer":
            return -1.0
        # inferred
        return float(side_weights.get((strike, option_type), 0.0))
