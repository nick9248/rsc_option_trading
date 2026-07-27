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
from typing import Any, Dict, List, Optional

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
    SKEW_MILD_THRESHOLD_POINTS,
    SKEW_STRONG_THRESHOLD_POINTS,
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
        skew_dict = self._calculate_25_delta_skew()
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
                skew=skew_dict["skew"],
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

    def _calculate_25_delta_skew(self) -> Dict[str, Any]:
        """
        Calculate 25-delta skew: 25d Put IV - 25d Call IV.

        Positive skew = puts more expensive (hedging demand).
        Negative skew = calls more expensive (upside speculation).

        Returns:
            Dict with put_25d_iv, call_25d_iv, skew, interpretation.
        """
        # Find instruments closest to ±0.25 delta
        puts = [i for i in self.instruments if i["option_type"] == "P" and i.get("delta") is not None]
        calls = [i for i in self.instruments if i["option_type"] == "C" and i.get("delta") is not None]

        put_25d = self._find_closest_delta(puts, -0.25)
        call_25d = self._find_closest_delta(calls, 0.25)

        if put_25d is None or call_25d is None:
            return {
                "put_25d_iv": None,
                "call_25d_iv": None,
                "skew": None,
                "interpretation": "Insufficient data",
            }

        put_iv = put_25d.get("mark_iv", 0)
        call_iv = call_25d.get("mark_iv", 0)
        skew = put_iv - call_iv

        if skew > SKEW_STRONG_THRESHOLD_POINTS:
            interpretation = "Puts More Expensive - Strong Hedging Demand"
        elif skew > SKEW_MILD_THRESHOLD_POINTS:
            interpretation = "Puts More Expensive - Hedging Demand"
        elif skew > -SKEW_MILD_THRESHOLD_POINTS:
            interpretation = "Balanced"
        elif skew > -SKEW_STRONG_THRESHOLD_POINTS:
            interpretation = "Calls More Expensive - Upside Speculation"
        else:
            interpretation = "Calls More Expensive - Strong Upside Speculation"

        return {
            "put_25d_iv": put_iv,
            "call_25d_iv": call_iv,
            "put_25d_strike": put_25d["strike"],
            "call_25d_strike": call_25d["strike"],
            "skew": skew,
            "interpretation": interpretation,
        }

    def _find_closest_delta(
        self,
        instruments: List[Dict],
        target_delta: float
    ) -> Optional[Dict]:
        """Find instrument with delta closest to target."""
        if not instruments:
            return None

        valid = [i for i in instruments if i.get("delta") is not None and i.get("mark_iv") is not None]
        if not valid:
            return None

        return min(valid, key=lambda i: abs(i["delta"] - target_delta))

    def _calculate_pc_by_moneyness(self) -> Dict[str, Any]:
        """
        Calculate P/C ratio split by moneyness buckets.

        Buckets:
        - ATM: within ±5% of spot
        - Near-OTM: 5-15% from spot
        - Far-OTM: >15% from spot

        Returns:
            Dict with per-bucket call_oi, put_oi, ratio, bias.
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
            for bucket_data in buckets.values():
                bucket_data["ratio"] = 0.0
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
            else:
                ratio = float("inf") if put_oi > 0 else 0

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

                net_vanna += _bs.calculate_vanna(d1, d2, sigma) * float(oi)
                net_charm += _bs.calculate_charm(d1, d2, tau) * float(oi)

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

        # bugfix_spec.md Item 8: net_vanna/net_charm above are the HOLDER-side
        # raw sums (Sigma over ALL instruments, no call/put positioning
        # split) -- pure arithmetic, no assumption. The assumed-dealer view
        # (SqueezeMetrics heuristic: dealers are short whatever holders
        # hold) is the negation. The narrative below describes the DEALER's
        # action, so it must be derived from the dealer-side (negated)
        # value -- using the holder sum directly here was the pre-Item-8
        # defect (GexDexCalculator's docstring states the one convention
        # this whole module follows).
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
            "vanna_exposure_holder": net_vanna,
            "charm_exposure_holder": net_charm,
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

