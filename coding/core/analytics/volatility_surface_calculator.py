"""
Volatility surface analysis calculator.

Computes per-expiry volatility metrics:
- IV by strike (smile/skew visualization)
- 25-delta skew
- P/C ratio by moneyness bucket
- Vanna/Charm (second-order Greeks)
- VWAP IV vs mark IV
"""

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.results.vol_surface_results import (
    IvByStrikeRow,
    MoneynessBucket,
    PutCallByMoneyness,
    SecondOrderGreeks,
    SkewResult,
    VolSurfaceResult,
)
from coding.core.analytics.thresholds import (
    RISK_REVERSAL_MILD_POINTS,
    RISK_REVERSAL_STRONG_POINTS,
    interpret_put_call_ratio,
)

logger = logging.getLogger(__name__)
_bs = BlackScholesCalculator()

# bugfix_spec.md Item 3 — the aggression residual (VWAP - matched mark IV
# baseline) is now correctly ~±1 vol point (previously it conflated smile
# shape with aggression and swung ±10-15 points), so this label threshold is
# doing real work. Named per F3.3.
VWAP_AGGRESSION_THRESHOLD_POINTS = 1.0

# Below this many distinct traded instruments, the matched baseline is
# computed from too thin a sample to support a directional aggression label
# (a single traded instrument sets VWAP == baseline mix trivially). The raw
# numbers are still shown; only the label is suppressed.
# bugfix_spec.md Item 3 F3.3's illustrative code snippet shows this constant
# as 3, but its own acceptance tests (T3.2 vs T3.4, section 3.5) require 2:
# n=2 must render the normal label ("Balanced"), n=1 must suppress it. The
# acceptance tests' hand-computed expectations govern (task brief: used
# verbatim) over the snippet, so this is 2, not 3 — noted as a spec
# inconsistency in the Task A4 report.
MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION = 2

# institutional_metrics_spec.md section 3(b) -- delta-space interpolation
# for RR25/BF25. Task G2-D fix 1 (Wave G fresh audit): this REPLACES the
# nearest-delta pick that used to live in the now-deleted
# _calculate_25_delta_risk_reversal/_find_closest_delta -- that ungated
# picker had no gate on how close its "closest to +-0.25" selection
# actually was, so the per-expiry report and the market-wide report could
# (and, per the audit, did) disagree on the same expiry's RR25, sometimes
# comparing two unrelated points on the smile (e.g. a 21-delta put against
# a 36-delta call) and still printing a labelled "Balanced" reading. There
# is now exactly one risk-reversal computation
# (calculate_risk_reversal_butterfly, below) used by both report sections.
MIN_ABS_DELTA_FOR_INTERPOLATION = 0.02
MAX_ABS_DELTA_FOR_INTERPOLATION = 0.98
TARGET_ABS_DELTA_25D = 0.25
TARGET_ABS_DELTA_ATM = 0.50

# Task C4 review (Important #1): a bracket that DOES straddle the target
# delta is not automatically a genuine local read of it -- observed live on
# BTC 26JUL26 (a very short-dated, thin expiry): the put side's nearest
# quotes above/below |delta|=0.25 were at |delta|=0.18 and |delta|=0.63
# (n_quotes_used=12), a chord spanning most of the smile from near-the-money
# calls-side territory across the ATM boundary into deep ITM -- not a 25-
# delta quote by any reasonable reading, yet it satisfies "0.25 is
# bracketed" and would otherwise be persisted as a legitimate observation
# into volatility_skew_history, where Decision D10 means it can never be
# backfilled or corrected retroactively.
#
# Real desks quote delta "pillars" roughly 0.10-0.15 apart (10/25/40-delta
# conventions) -- a bracket materially wider than one pillar-spacing means
# the chain has a genuine hole around the target, not merely one sparse-but-
# legitimate neighbor. 0.20 was chosen empirically against the real BTC
# golden-master fixture (12 expirations): every legitimate row's bracket
# width is <= 0.14 (worst case: 27JUL26's calls at 0.136 / puts at 0.139,
# a genuinely thin but locally-read expiry); only 26JUL26's brackets (0.297
# call / 0.450 put) are pathological. 0.20 sits with margin above every
# legitimate observed width and well below both pathological ones, so it
# does not require an exact/fragile cutoff to separate the two populations.
MAX_ABS_DELTA_BRACKET_WIDTH = 0.20


@dataclass(frozen=True)
class InterpPoint:
    """
    One delta-space-interpolated point on the smile
    (institutional_metrics_spec.md section 3(b)): the IV and implied strike
    at a target |delta|, plus the bracketing |delta| pair that produced it.

    ``strike`` is the midpoint of the two bracketing instruments' strikes
    (per the volatility_skew_history schema comment: "strike implied by the
    interpolation bracket midpoint") -- it is NOT itself delta-interpolated,
    only the IV is.

    Wave-I-C Fix 8: ``delta`` is the |delta| actually implied AT ``strike``
    -- linearly interpolated the same way ``strike`` itself is (the same
    0.5 bracket weight, since ``strike`` is a plain midpoint), NOT the
    ``target_abs_delta`` the caller asked for. Reports must print THIS
    value alongside ``strike``, never the target -- ``strike`` is only an
    approximation of where ``target_abs_delta`` truly sits (bracket
    midpoint, not delta-solved), so the two are not, in general, exactly
    the same point on the smile. Equal to ``target_abs_delta`` only when
    the bracket happens to be symmetric around it.
    """

    iv: float
    strike: float
    delta: float
    bracket: Tuple[float, float]  # (|delta_a|, |delta_b|), ascending


class VolatilitySurfaceCalculator:
    """
    Calculate volatility surface metrics from enriched instrument data.

    Uses instrument-level mark_iv, delta, gamma, theta, vega
    (already fetched during GEX/DEX phase).
    """

    def __init__(
        self,
        instruments: List[Dict[str, Any]],
        spot_price: float,
        expiration: str,
    ):
        """
        Initialize with enriched instruments for a single expiration.

        Args:
            instruments: List of instrument dicts with mark_iv, delta, gamma,
                        theta, vega, strike, option_type, open_interest.
            spot_price: Current underlying spot price.
            expiration: Expiration date string.
        """
        self.instruments = instruments
        self.spot_price = spot_price
        self.expiration = expiration
        self._vwap_iv: Optional[float] = None
        self._mark_iv_baseline: Optional[float] = None
        self._traded_instrument_count: int = 0

    def calculate(self) -> VolSurfaceResult:
        """
        Run all volatility surface calculations.

        Returns:
            VolSurfaceResult (coding/core/analytics/results/vol_surface_results.py).
            Call ``.to_dict()`` for the legacy dict shape.
        """
        iv_by_strike_rows = self._calculate_iv_by_strike_rows()
        skew_dict = self._skew_dict_from_risk_reversal_butterfly()
        pc_dict = self._calculate_pc_by_moneyness()
        second_order_dict = self._calculate_second_order_greeks()
        atm_iv = self._calculate_atm_iv()

        def _bucket(name: str) -> MoneynessBucket:
            b = pc_dict[name]
            return MoneynessBucket(
                call_oi=b["call_oi"], put_oi=b["put_oi"], range_label=b["range"],
                ratio=b["ratio"], bias=b["bias"],
            )

        return VolSurfaceResult(
            expiration=self.expiration,
            spot_price=self.spot_price,
            iv_by_strike=tuple(iv_by_strike_rows),
            skew_25d=SkewResult(
                put_25d_iv=skew_dict["put_25d_iv"],
                call_25d_iv=skew_dict["call_25d_iv"],
                put_25d_strike=skew_dict.get("put_25d_strike"),
                call_25d_strike=skew_dict.get("call_25d_strike"),
                put_25d_delta=skew_dict.get("put_25d_delta"),
                call_25d_delta=skew_dict.get("call_25d_delta"),
                risk_reversal_25d=skew_dict["risk_reversal_25d"],
                put_over_call_skew_25d=skew_dict["put_over_call_skew_25d"],
                interpretation=skew_dict["interpretation"],
            ),
            pc_by_moneyness=PutCallByMoneyness(
                atm=_bucket("atm"), near_otm=_bucket("near_otm"), far_otm=_bucket("far_otm"),
            ),
            second_order_greeks=SecondOrderGreeks(
                vanna_exposure_holder=second_order_dict["vanna_exposure_holder"],
                charm_exposure_holder=second_order_dict["charm_exposure_holder"],
                dealer_vanna_exposure=second_order_dict["dealer_vanna_exposure"],
                dealer_charm_exposure=second_order_dict["dealer_charm_exposure"],
                vanna_signal=second_order_dict["vanna_signal"],
                charm_signal=second_order_dict["charm_signal"],
                skipped_instruments=second_order_dict["skipped_instruments"],
            ),
            atm_iv=atm_iv,
            vwap_iv=self._vwap_iv,
            mark_iv_average=self._mark_iv_baseline,
            traded_instrument_count=self._traded_instrument_count,
        )

    def _calculate_iv_by_strike_rows(self) -> List[IvByStrikeRow]:
        """
        Build one IvByStrikeRow per instrument with a usable mark_iv.

        The legacy calculate() dict merged call_iv/put_iv per strike; that
        merge is now owned by ``VolSurfaceResult.merged_iv_by_strike()``
        (moved out of the formatter — A3-review carried finding) and by
        ``VolSurfaceResult.to_dict()`` for the legacy shim.

        Returns:
            List sorted by strike ascending, matching the legacy table order.
        """
        rows: List[IvByStrikeRow] = []
        for inst in self.instruments:
            mark_iv = inst.get("mark_iv")
            if mark_iv is None:
                continue

            strike = inst["strike"]
            moneyness_pct = (
                abs(strike - self.spot_price) / self.spot_price * 100.0
                if self.spot_price > 0 else 0.0
            )
            rows.append(IvByStrikeRow(
                strike=strike,
                option_type=inst["option_type"],
                mark_iv=mark_iv,
                delta=inst.get("delta"),
                moneyness_pct=moneyness_pct,
            ))

        rows.sort(key=lambda r: (r.strike, r.option_type))
        return rows

    def _skew_dict_from_risk_reversal_butterfly(self) -> Dict[str, Any]:
        """
        Build the per-expiry SkewResult dict shape from
        ``calculate_risk_reversal_butterfly()`` -- the SAME bracket-gated
        delta-interpolation the market-wide SKEW TERM STRUCTURE section
        uses (Task G2-D fix 1). There is exactly one risk-reversal
        computation; this method only adapts its shape/adds the
        per-expiry interpretation label, it does not recompute anything.

        Deletes the old ``_calculate_25_delta_risk_reversal``/
        ``_find_closest_delta`` (the ungated nearest-delta picker): that
        picker had no gate on how close its ±0.25 pick actually was, so it
        could (and, per an independent audit, did) disagree with the
        market-wide method on the same expiry -- one live example compared
        a 21-delta put against a 36-delta call and still labelled the
        result "Balanced".

        Returns:
            Dict with put_25d_iv, call_25d_iv, put_25d_strike,
            call_25d_strike, put_25d_delta, call_25d_delta (Wave-I-C
            Fix 8: the |delta| actually implied at *_25d_strike, NOT
            necessarily exactly ±TARGET_ABS_DELTA_25D -- *_25d_strike is
            a bracket midpoint, not delta-solved, so the two are not, in
            general, the same point on the smile; None together with the
            corresponding IV when that side isn't bracketed),
            risk_reversal_25d (PRIMARY, market convention),
            put_over_call_skew_25d (legacy sign, explicitly named), and
            interpretation ("insufficient chain" -- the same fallback
            string market_wide_formatter.py's _format_rr25_cell/
            _format_bf25_cell already use -- when the chain doesn't
            bracket the target tightly enough on either side, OR when
            calculate_risk_reversal_butterfly() raised unexpectedly).

        Isolation (matches ``_calculate_gamma_profile``'s guard on
        ``GammaProfileCalculator.calculate()`` just above): ``calculate()``
        also builds ``iv_by_strike``/``pc_by_moneyness``/
        ``second_order_greeks``/``atm_iv`` for this SAME expiry, and
        ``OnChainAnalysisService._calculate_volatility_surface`` calls
        ``calculate_risk_reversal_butterfly()`` a SECOND time right after
        (to persist the full bf_25d/n_quotes_used dict into
        ``volatility_skew_history``) in its own isolated try/except (Task
        C4 review Important #2) so a failure there never drops the
        already-stored vol-surface result. Before this method existed,
        that second call was the ONLY caller, so a bug in the
        interpolation logic could only ever take out the SKEW TERM
        STRUCTURE section. Now that this method also calls it, an
        unguarded exception here would propagate out of ``calculate()``
        and take out the ENTIRE per-expiry volatility-surface result
        (including the unrelated fields above) too -- a strictly worse
        blast radius than before this task. Catching it here and
        degrading to the same "insufficient chain" shape used for a
        genuinely-unbracketed chain preserves the pre-existing isolation
        guarantee.
        """
        try:
            rr_bf = self.calculate_risk_reversal_butterfly()
        except Exception:
            logger.error(
                "VolatilitySurfaceCalculator: calculate_risk_reversal_butterfly "
                "failed unexpectedly for expiration %s -- degrading skew_25d to "
                "'insufficient chain' rather than aborting the whole volatility "
                "surface calculation (Task G2-D fix 1 isolation)",
                self.expiration, exc_info=True,
            )
            return {
                "put_25d_iv": None,
                "call_25d_iv": None,
                "put_25d_strike": None,
                "call_25d_strike": None,
                "put_25d_delta": None,
                "call_25d_delta": None,
                "risk_reversal_25d": None,
                "put_over_call_skew_25d": None,
                "interpretation": "insufficient chain",
            }

        call_iv = rr_bf["call_25d_iv"]
        put_iv = rr_bf["put_25d_iv"]
        risk_reversal = rr_bf["rr_25d"]

        if risk_reversal is None or call_iv is None or put_iv is None:
            return {
                "put_25d_iv": put_iv,
                "call_25d_iv": call_iv,
                "put_25d_strike": rr_bf["put_25d_strike"],
                "call_25d_strike": rr_bf["call_25d_strike"],
                "put_25d_delta": None,
                "call_25d_delta": None,
                "risk_reversal_25d": None,
                "put_over_call_skew_25d": None,
                "interpretation": "insufficient chain",
            }

        if risk_reversal < -RISK_REVERSAL_STRONG_POINTS:
            interpretation = "Puts Much Richer - Strong Downside Hedging Demand"
        elif risk_reversal < -RISK_REVERSAL_MILD_POINTS:
            interpretation = "Puts Richer - Downside Hedging Demand"
        elif risk_reversal <= RISK_REVERSAL_MILD_POINTS:
            interpretation = "Balanced"
        elif risk_reversal <= RISK_REVERSAL_STRONG_POINTS:
            interpretation = "Calls Richer - Upside Speculation"
        else:
            interpretation = "Calls Much Richer - Strong Upside Speculation"

        return {
            "put_25d_iv": put_iv,
            "call_25d_iv": call_iv,
            "put_25d_strike": rr_bf["put_25d_strike"],
            "call_25d_strike": rr_bf["call_25d_strike"],
            # Wave-I-C Fix 8: the |delta| actually implied at *_25d_strike
            # (InterpPoint.delta, via calculate_risk_reversal_butterfly) --
            # NOT the hardcoded ±TARGET_ABS_DELTA_25D this used to be. The
            # strike printed alongside this is a bracket midpoint, not
            # delta-solved, so it is not in general exactly the target-
            # delta strike -- printing the target here as if it were
            # precisely measured AT that strike overclaimed precision the
            # interpolation doesn't have. This is the honest value.
            "put_25d_delta": rr_bf["put_25d_delta"],
            "call_25d_delta": rr_bf["call_25d_delta"],
            "risk_reversal_25d": risk_reversal,
            "put_over_call_skew_25d": -risk_reversal,
            "interpretation": interpretation,
        }

    def _build_delta_points(self, option_type: str) -> List[Dict[str, float]]:
        """
        Build the filtered, deduplicated ``(|delta|, iv, strike)`` point set
        for one option side, per institutional_metrics_spec.md section
        3(b) steps 1-2:

        1. Keep instruments of this ``option_type`` with a ``mark_iv`` and
           ``delta``, that are QUOTED (``bid_price > 0 or ask_price > 0``
           -- missing/None treated as 0), and with
           ``0.02 <= |delta| <= 0.98``.

           Task Wave-J-E Fix 2: a side counts as quoted only when it is
           ALSO not flagged estimated (``bid_is_estimated``/
           ``ask_is_estimated`` -- see migration 025 and
           HourlyAggregationService._aggregate_instrument). Historical
           instruments sourced from hourly_snapshots (DatabaseRepository.
           get_hourly_snapshots_for_hour) never carry a real order-book
           quote -- bid_price/ask_price there are always trade-derived,
           and the estimated flag is True specifically when there was NO
           trade on that side at all that hour (a pure vwap+/-0.5% guess,
           not a genuine market reading). Missing flag keys (the live
           on-chain path, which always carries real ticker-sourced bid/ask
           and never sets these keys) default to "not estimated" so live
           behavior is unchanged.
        2. Sort by |delta| ascending; average IV (and strike) at equal
           |delta| rather than keeping duplicates as separate points.

        Returns:
            List of ``{"abs_delta", "iv", "strike"}`` dicts, sorted
            ascending by ``abs_delta``, one per distinct |delta| value.
        """
        points: List[Dict[str, float]] = []
        for inst in self.instruments:
            if inst.get("option_type") != option_type:
                continue
            delta = inst.get("delta")
            mark_iv = inst.get("mark_iv")
            strike = inst.get("strike")
            # Task C4 review (Important #2 root cause): a missing/None
            # strike must be skipped like a missing delta/mark_iv, not
            # reach ``float(inst["strike"])`` below -- that raised KeyError
            # (key absent) or TypeError (``float(None)``) on real-world
            # instrument dicts that don't carry a strike, which propagated
            # out of calculate_risk_reversal_butterfly() uncaught.
            if delta is None or mark_iv is None or strike is None:
                continue

            bid_price = inst.get("bid_price") or 0
            ask_price = inst.get("ask_price") or 0
            # Task Wave-J-E Fix 2: a fallback (no-trade-evidence) value on
            # a side must not count as "quoted" for that side -- only a
            # positive price that is NOT flagged estimated does. Missing
            # keys (live path) default to False (not estimated), matching
            # the live path's always-real bid/ask and preserving prior
            # behavior there exactly.
            bid_quoted = bid_price > 0 and not inst.get("bid_is_estimated", False)
            ask_quoted = ask_price > 0 and not inst.get("ask_is_estimated", False)
            if not (bid_quoted or ask_quoted):
                continue

            abs_delta = abs(delta)
            if not (MIN_ABS_DELTA_FOR_INTERPOLATION <= abs_delta <= MAX_ABS_DELTA_FOR_INTERPOLATION):
                continue

            points.append({
                "abs_delta": abs_delta,
                "iv": float(mark_iv),
                "strike": float(strike),
            })

        points.sort(key=lambda p: p["abs_delta"])

        # Average IV/strike at equal |delta| (spec step 2) instead of
        # keeping duplicate points, which would make bracket selection
        # ambiguous.
        merged: List[Dict[str, float]] = []
        for point in points:
            if merged and merged[-1]["abs_delta"] == point["abs_delta"]:
                prev = merged[-1]
                n = prev.pop("_n", 1) + 1
                prev["iv"] = prev["iv"] + (point["iv"] - prev["iv"]) / n
                prev["strike"] = prev["strike"] + (point["strike"] - prev["strike"]) / n
                prev["_n"] = n
            else:
                merged.append(dict(point))

        for point in merged:
            point.pop("_n", None)

        return merged

    def interpolate_iv_at_delta(
        self, option_type: str, target_abs_delta: float
    ) -> Optional[InterpPoint]:
        """
        Interpolate IV (and implied strike) at ``target_abs_delta`` for one
        option side, linear in delta space
        (institutional_metrics_spec.md section 3(b) steps 3-5).

        Never extrapolates: if the full chain does not reach
        ``target_abs_delta`` on this side (no quote with |delta| <= target,
        or none with |delta| >= target), returns None. This is the
        replacement for the deleted ``_find_closest_delta``'s "closest,
        however far" pick (Task G2-D fix 1: the last consumer of that
        picker, ``_calculate_25_delta_risk_reversal``, is also deleted --
        both are gone, not "unchanged and still used by other consumers"
        as this docstring previously (incorrectly) scoped them).

        Args:
            option_type: "C" or "P".
            target_abs_delta: Target |delta|, e.g. 0.25 for 25-delta, 0.50
                for the ATM point.

        Returns:
            ``InterpPoint`` with the interpolated IV, the bracket-midpoint
            strike, and the bracket |delta| pair used. None if the target
            is not bracketed by this side's quoted chain, OR if it IS
            bracketed but the bracket is wider than
            ``MAX_ABS_DELTA_BRACKET_WIDTH`` (Task C4 review Important #1):
            a wide bracket means the chain has a hole around
            ``target_abs_delta``, not a genuine local quote to interpolate
            from -- treated identically to "not bracketed at all" so it is
            never persisted to ``volatility_skew_history`` (Decision D10:
            that history cannot be corrected retroactively) and never
            silently rendered as a real 25-delta/ATM read.
        """
        points = self._build_delta_points(option_type)

        below = [p for p in points if p["abs_delta"] <= target_abs_delta]
        above = [p for p in points if p["abs_delta"] >= target_abs_delta]
        if not below or not above:
            return None

        a = max(below, key=lambda p: p["abs_delta"])
        b = min(above, key=lambda p: p["abs_delta"])

        if (b["abs_delta"] - a["abs_delta"]) > MAX_ABS_DELTA_BRACKET_WIDTH:
            return None

        if a["abs_delta"] == b["abs_delta"]:
            # Exact match (or a single point straddling both lists) --
            # return it directly, avoiding a division by zero below.
            return InterpPoint(
                iv=a["iv"], strike=a["strike"], delta=a["abs_delta"],
                bracket=(a["abs_delta"], b["abs_delta"]),
            )

        weight = (target_abs_delta - a["abs_delta"]) / (b["abs_delta"] - a["abs_delta"])
        iv = a["iv"] + weight * (b["iv"] - a["iv"])
        strike = (a["strike"] + b["strike"]) / 2.0  # bracket midpoint, not delta-weighted
        # Wave-I-C Fix 8: the |delta| actually implied AT `strike` -- since
        # `strike` is a plain 0.5-weight midpoint (not `weight`-weighted
        # like `iv` above), the delta consistent with it is the matching
        # 0.5-weight average of the bracket |delta|s, NOT target_abs_delta.
        # Cheap to compute (reuses a/b already fetched above) and honest:
        # `strike` generally is NOT exactly where target_abs_delta sits.
        delta_at_strike = (a["abs_delta"] + b["abs_delta"]) / 2.0

        return InterpPoint(
            iv=iv, strike=strike, delta=delta_at_strike,
            bracket=(a["abs_delta"], b["abs_delta"]),
        )

    def calculate_risk_reversal_butterfly(self) -> Dict[str, Any]:
        """
        Calculate the full-chain, delta-interpolated 25-delta risk reversal
        and butterfly (institutional_metrics_spec.md section 3(b)/(c),
        Migration M3 / Task C4).

        Task G2-D fix 1: this IS now the single risk-reversal computation
        for both report paths -- ``calculate()``'s ``skew_25d`` (per-
        expiry) derives from this method's output via
        ``_skew_dict_from_risk_reversal_butterfly``, and the market-wide
        SKEW TERM STRUCTURE section already called this method directly.
        Previously additive alongside a separate ungated nearest-delta
        picker (``_calculate_25_delta_risk_reversal``); that picker is
        deleted -- the two report sections could disagree on the same
        expiry's RR25 before this fix.

        RR25 = IV(25d call) - IV(25d put)  (call - put; negative = puts
            bid = downside skew).
        BF25 = (IV(25d call) + IV(25d put)) / 2 - ATM_IV, where ATM_IV is
            interpolated at |delta|=0.50, averaged across call and put
            sides (removes the "ATM = mean of the two 25d picks"
            degeneracy of the old nearest-delta code, F2).

        Either quantity is None when its required interpolation(s) are not
        bracketed by the chain (never extrapolated), OR when a bracket
        exists but is wider than ``MAX_ABS_DELTA_BRACKET_WIDTH`` (Task C4
        review Important #1 -- a wide bracket is a hole in the chain, not
        a genuine local read) -- see ``interpolate_iv_at_delta``.

        Returns:
            Dict with ``rr_25d``, ``bf_25d``, ``call_25d_iv``,
            ``put_25d_iv``, ``call_25d_strike``, ``put_25d_strike``,
            ``atm_iv_interp``, ``call_bracket``, ``put_bracket``,
            ``n_quotes_used`` (count of quoted, delta-range-filtered
            instruments across BOTH sides that fed the interpolation --
            the chain breadth ``volatility_skew_history.n_quotes_used``
            persists, per spec: filter thin rows, ``n_quotes_used >= 8``,
            out of percentile windows), and ``method`` ("linear_delta").
        """
        call_pt = self.interpolate_iv_at_delta("C", TARGET_ABS_DELTA_25D)
        put_pt = self.interpolate_iv_at_delta("P", TARGET_ABS_DELTA_25D)
        call_atm_pt = self.interpolate_iv_at_delta("C", TARGET_ABS_DELTA_ATM)
        put_atm_pt = self.interpolate_iv_at_delta("P", TARGET_ABS_DELTA_ATM)

        atm_iv_interp: Optional[float] = None
        if call_atm_pt is not None and put_atm_pt is not None:
            atm_iv_interp = (call_atm_pt.iv + put_atm_pt.iv) / 2.0

        call_25d_iv = call_pt.iv if call_pt is not None else None
        put_25d_iv = put_pt.iv if put_pt is not None else None

        rr_25d: Optional[float] = None
        if call_25d_iv is not None and put_25d_iv is not None:
            rr_25d = call_25d_iv - put_25d_iv

        bf_25d: Optional[float] = None
        if call_25d_iv is not None and put_25d_iv is not None and atm_iv_interp is not None:
            bf_25d = (call_25d_iv + put_25d_iv) / 2.0 - atm_iv_interp

        n_quotes_used = len(self._build_delta_points("C")) + len(self._build_delta_points("P"))

        return {
            "rr_25d": rr_25d,
            "bf_25d": bf_25d,
            "call_25d_iv": call_25d_iv,
            "put_25d_iv": put_25d_iv,
            "call_25d_strike": call_pt.strike if call_pt is not None else None,
            "put_25d_strike": put_pt.strike if put_pt is not None else None,
            # Wave-I-C Fix 8: the |delta| actually implied at *_25d_strike
            # (InterpPoint.delta) -- NOT TARGET_ABS_DELTA_25D. *_25d_strike
            # is a bracket midpoint, not delta-solved, so it is not in
            # general exactly the target-delta strike; this is the delta
            # value honestly consistent with the strike printed alongside
            # it. Signed here (put negative, call positive) to match this
            # module's |delta| convention everywhere else.
            "call_25d_delta": call_pt.delta if call_pt is not None else None,
            "put_25d_delta": -put_pt.delta if put_pt is not None else None,
            "atm_iv_interp": atm_iv_interp,
            "call_bracket": call_pt.bracket if call_pt is not None else None,
            "put_bracket": put_pt.bracket if put_pt is not None else None,
            "n_quotes_used": n_quotes_used,
            "method": "linear_delta",
        }

    def _calculate_pc_by_moneyness(self) -> Dict[str, Any]:
        """
        Calculate P/C ratio split by moneyness buckets.

        Buckets:
        - ATM: within ±5% of spot
        - Near-OTM: 5-15% from spot
        - Far-OTM: >15% from spot

        Wave-H-A (Task 5): a bucket's ``ratio`` is ``None`` when the bucket
        has ZERO instruments in it (``call_oi == 0 and put_oi == 0``) --
        distinct from a genuinely-measured ratio of 0.0 (``call_oi > 0``,
        ``put_oi == 0`` -- some calls, no puts, a real reading). Before
        this fix, an empty bucket silently fell into the ``call_oi <= 0``
        branch and got ``ratio = 0.0`` (this dataclass's field docstring
        used to describe that as an intentional "H2 fix" convention --
        it was not; see ``MoneynessBucket.ratio``'s corrected docstring).
        That fabricated 0.0 was then indistinguishable from a real zero
        AND fed ``interpret_put_call_ratio(0.0)`` (0 < the "Strong
        Bullish" threshold), producing a "Strong Bullish" ``bias`` label
        for a bucket that measured nothing at all -- not rendered in the
        text report today (vol_surface_formatter only prints raw ratio),
        but reachable via ``to_dict()``/structured output.
        ``interpret_put_call_ratio`` already treats ``None`` the same as
        ``float('inf')`` (both "undefined" -> "N/A"), so no change to that
        function was needed -- only to what this method feeds it.

        Returns:
            Dict with per-bucket call_oi, put_oi, ratio (Optional[float]),
            bias.
        """
        buckets = {
            "atm": {"call_oi": 0, "put_oi": 0, "range": "±5%"},
            "near_otm": {"call_oi": 0, "put_oi": 0, "range": "5-15%"},
            "far_otm": {"call_oi": 0, "put_oi": 0, "range": "15%+"},
        }

        if self.spot_price <= 0:
            # Distance-to-spot is undefined with no spot price, so no OI can be
            # bucketed. Still populate ratio/bias on every bucket -- calculate()'s
            # _bucket() closure below unconditionally reads both keys to build
            # the typed MoneynessBucket (results/vol_surface_results.py) and
            # would KeyError otherwise (task A7 review: this comment previously
            # named the deleted generate_report_section as the reason -- the
            # real reason is calculate()'s typed-model construction).
            #
            # Wave-H-A (Task 5): ratio is None here, not a fabricated 0.0 --
            # None is "no data", not "a measured ratio of exactly zero". See
            # this method's docstring below for the full distinction.
            for bucket_data in buckets.values():
                bucket_data["ratio"] = None
                bucket_data["bias"] = "N/A"
            return buckets

        for inst in self.instruments:
            strike = inst["strike"]
            oi = inst.get("open_interest", 0)
            option_type = inst["option_type"]

            distance_pct = abs(strike - self.spot_price) / self.spot_price * 100

            if distance_pct <= 5:
                bucket = "atm"
            elif distance_pct <= 15:
                bucket = "near_otm"
            else:
                bucket = "far_otm"

            if option_type == "C":
                buckets[bucket]["call_oi"] += oi
            else:
                buckets[bucket]["put_oi"] += oi

        # Calculate ratios and bias per bucket
        for bucket_data in buckets.values():
            call_oi = bucket_data["call_oi"]
            put_oi = bucket_data["put_oi"]

            if call_oi > 0:
                ratio = put_oi / call_oi
            elif put_oi > 0:
                ratio = float("inf")
            else:
                # Wave-H-A (Task 5): ZERO instruments in this bucket at
                # all -- not a measured ratio. Distinct from call_oi > 0,
                # put_oi == 0 above (a real reading of 0.0, no puts but
                # some calls).
                ratio = None

            bucket_data["ratio"] = ratio
            # M4 (code_quality_review.md): shared interpreter, unifying
            # this bucket-level bias with OnChainMetricsCalculator.
            # calculate_put_call_ratio's whole-expiration bias
            # (refactor_design_spec.md T12 -- planned golden delta).
            bucket_data["bias"] = interpret_put_call_ratio(ratio)

        return buckets

    def _calculate_second_order_greeks(self) -> Dict[str, Any]:
        """
        Calculate aggregated second-order Greeks (Vanna, Charm).

        Closed-form Black-Scholes formulas (r=q=0, standard for crypto):
        - Vanna = −φ(d1) × d2 / σ  (∂Δ/∂σ, sensitivity of delta to vol)
        - Charm = φ(d1) × d2 / (2τ)  (∂Δ/∂t -- delta drift per unit of
          ELAPSING calendar time, equivalently −∂Δ/∂τ where τ is time
          REMAINING; bugfix_spec.md Item 12 -- the sign LABEL here was
          previously wrong, the value was always correct. Per YEAR; divide
          by 365 for the per-day drift.)

        d1 is recovered from stored delta via inverse normal CDF.
        τ is derived from stored gamma and vega without needing expiry date:
          gamma = φ(d1)/(S·σ·√τ),  vega = S·φ(d1)·√τ/100
          → τ = (vega × 100) / (S² × gamma × sigma)

        Results are aggregated across all instruments, weighted by OI.

        Wave-H-A (reverting a regression, Task C5 review fix round 1 /
        commit b6d483e): the ASSUMED-DEALER view for vanna/charm is
        ``-(holder-side sum)`` -- dealers short whatever holders hold --
        per GexDexCalculator's own canonical SIGN CONVENTION (its class
        docstring, gex_dex_calculator.py lines 50-66: the long-calls/
        short-puts call/put-SPLIT convention applies to GAMMA ONLY;
        delta/vanna/charm are each "short whatever customers (i.e.
        holders) hold"). Commit b6d483e changed this to the call/put SPLIT
        (+1 call, -1 put), reasoning it should match
        ``GexDexCalculator``'s own convention -- but that convention is
        gamma-only, so the fix over-generalized it. Unlike delta, the
        split is mathematically well-formed here too (Vanna_call =
        Vanna_put and Charm_call = Charm_put always, by put-call parity --
        Delta_call - Delta_put = 1 is a spot/vol/time-independent constant,
        so every higher derivative of that difference is exactly zero),
        so this was not a smoking-gun always-one-sign bug like delta's --
        but it is still a confirmed deviation from the documented
        convention: the split and the negated sum diverge numerically
        whenever call OI != put OI at a given vanna/charm value, which is
        the generic case for a real book.

        Returns:
            Dict with net_vanna, net_charm, vanna_signal, charm_signal,
            skipped_instruments (count of instruments with oi > 0 that were
            excluded from the sum for any reason — missing greeks, invalid
            derived tau, or a raised exception; VolSurfaceResult.
            second_order_greeks.skipped_instruments, M5).
        """
        net_vanna = 0.0
        net_charm = 0.0
        skipped_instruments = 0
        # Wave-H-A (Task 4, None-vs-zero): counts instruments that actually
        # contributed a vanna/charm value. When this stays 0 -- either no
        # instrument had positive OI at all, or every one that did was
        # skipped -- net_vanna/net_charm/dealer_vanna/dealer_charm below
        # must come out as None (nothing measured), not a fabricated 0.0
        # that reads identically to a genuinely-balanced book.
        computed_instruments = 0

        for inst in self.instruments:
            delta = inst.get("delta")
            gamma = inst.get("gamma")
            vega = inst.get("vega")
            mark_iv = inst.get("mark_iv")
            option_type = inst.get("option_type")
            oi = inst.get("open_interest", 0)

            if oi <= 0:
                continue
            if None in (delta, gamma, vega, mark_iv, option_type):
                skipped_instruments += 1
                continue

            try:
                sigma = float(mark_iv) / 100.0
                gamma_f = float(gamma)
                vega_f = float(vega)
                if sigma <= 0 or gamma_f <= 0 or vega_f <= 0 or self.spot_price <= 0:
                    skipped_instruments += 1
                    continue

                # Derive time-to-expiry from stored greeks (no expiry date needed)
                raw_vega = vega_f * 100.0  # S·φ(d1)·√τ (undo /100 convention)
                tau = raw_vega / (self.spot_price ** 2 * gamma_f * sigma)
                if not (0 < tau <= 2.0):
                    skipped_instruments += 1
                    continue

                d1 = _bs.d1_from_delta(float(delta), option_type)
                d2 = d1 - sigma * math.sqrt(tau)

                vanna_i = _bs.calculate_vanna(d1, d2, sigma)
                charm_i = _bs.calculate_charm(d1, d2, tau)

                net_vanna += vanna_i * float(oi)
                net_charm += charm_i * float(oi)
                computed_instruments += 1

            except Exception as e:
                # M5 (code_quality_review.md): this used to be a bare
                # `except Exception: continue` with zero logging -- a
                # systematic input problem (e.g. every mark_iv malformed)
                # yielded a plausible-looking net vanna of 0.0 with no
                # diagnostics. Name the failing instrument so a real outage
                # is loud, not silent.
                skipped_instruments += 1
                logger.warning(
                    "Skipping vanna/charm for instrument %s: %s",
                    inst.get("instrument_name", "<unknown>"), e,
                )
                continue

        # Wave-H-A (Task 4, None-vs-zero): computed_instruments == 0 means
        # nothing contributed to net_vanna/net_charm -- either no
        # instrument had positive OI at all, or every one that did was
        # skipped (missing greeks, invalid derived tau, or an exception).
        # That is NOT the same as a genuinely-measured zero (e.g. a
        # balanced call/put book with computed_instruments > 0 and
        # net_vanna == 0.0 exactly) -- see synthesis.py's score_vanna_charm
        # None branch, which already distinguishes the two and was unable
        # to be exercised for this failure path before this fix (the
        # calculator always returned a measured-looking 0.0 here).
        if computed_instruments == 0:
            vanna_exposure_holder: Optional[float] = None
            charm_exposure_holder: Optional[float] = None
            dealer_vanna: Optional[float] = None
            dealer_charm: Optional[float] = None
            vanna_signal = "Insufficient data (no instrument contributed a vanna/charm reading)"
            charm_signal = "Insufficient data (no instrument contributed a vanna/charm reading)"
        else:
            vanna_exposure_holder = net_vanna
            charm_exposure_holder = net_charm
            # bugfix_spec.md Item 8: net_vanna/net_charm are the HOLDER-side
            # raw sums (Sigma over ALL instruments, no call/put positioning
            # split) -- pure arithmetic, no assumption. Wave-H-A (reverting
            # Task C5 review fix round 1 / commit b6d483e): dealer_vanna/
            # dealer_charm are -(holder sum), per GexDexCalculator's
            # canonical SIGN CONVENTION (dealers short whatever holders
            # hold for vanna/charm, same as delta -- the call/put SPLIT is
            # gamma-only) -- see this method's docstring. The narrative
            # below describes the DEALER's action, so it is derived from
            # the dealer-side value, matching Item 8's original intent.
            dealer_vanna = -net_vanna
            dealer_charm = -net_charm

            if dealer_vanna > 0:
                vanna_signal = "IV drop → dealers buy underlying (bullish)"
            else:
                vanna_signal = "IV drop → dealers sell underlying (bearish)"

            if dealer_charm > 0:
                charm_signal = "Time decay pushing delta positive (bullish drift)"
            else:
                charm_signal = "Time decay pushing delta negative (bearish drift)"

        return {
            "vanna_exposure_holder": vanna_exposure_holder,
            "charm_exposure_holder": charm_exposure_holder,
            "dealer_vanna_exposure": dealer_vanna,
            "dealer_charm_exposure": dealer_charm,
            "vanna_signal": vanna_signal,
            "charm_signal": charm_signal,
            "skipped_instruments": skipped_instruments,
        }

    def _calculate_atm_iv(self) -> Optional[float]:
        """
        Calculate ATM IV as the average of the closest call and put IVs to spot.

        Returns:
            ATM IV as percentage, or None if insufficient data.
        """
        calls = [i for i in self.instruments if i["option_type"] == "C" and i.get("mark_iv") is not None]
        puts = [i for i in self.instruments if i["option_type"] == "P" and i.get("mark_iv") is not None]

        if not calls and not puts:
            return None

        atm_ivs = []
        for group in [calls, puts]:
            if group:
                closest = min(group, key=lambda i: abs(i["strike"] - self.spot_price))
                atm_ivs.append(closest["mark_iv"])

        return sum(atm_ivs) / len(atm_ivs) if atm_ivs else None

    def set_vwap_iv_data(
        self,
        vwap_iv: Optional[float],
        mark_iv_baseline: Optional[float],
        traded_instrument_count: int = 0,
    ) -> None:
        """
        Store VWAP IV data for inclusion in the next calculate().

        Args:
            vwap_iv: Volume-weighted IV of actual trades.
            mark_iv_baseline: Volume-weighted MARK IV of the SAME instruments
                that traded, weighted by the SAME traded volumes (the
                "matched baseline" — bugfix_spec.md Item 3). Historically
                named ``mark_iv_avg``; callers that have not yet been updated
                to compute the matched baseline may still pass a chain-wide
                average here, but the report gates on
                ``traded_instrument_count`` either way.
            traded_instrument_count: Number of distinct instruments that
                contributed to both legs above. 0 (the default) means the
                aggression signal is suppressed in the report.
        """
        self._vwap_iv = vwap_iv
        self._mark_iv_baseline = mark_iv_baseline
        self._traded_instrument_count = traded_instrument_count

