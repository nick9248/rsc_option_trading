"""
Taker-flow-inferred dealer positioning calculator (Glassnode method).

institutional_metrics_spec.md section 2 / task C3. Pure: receives already-
aggregated signed-taker-flow rows plus per-instrument Greeks, never queries
the database or the API. The service layer (``OnChainAnalysisService.
_calculate_inferred_dealer_positioning``) fetches ``flow_rows`` via
``DatabaseRepository.get_signed_taker_flow_by_strike`` and builds
``greeks_by_instrument``/``oi_by_instrument`` from the SAME enriched-Greeks
instrument list already built for ``GexDexCalculator`` -- no second Greeks
pass, no second API call.

Formulas (spec §2(b)):
    signed_taker(i)  = +amount if direction == 'buy' else -amount
    taker_net(K,type) = sum of signed_taker over all trades on that instrument since T0
    dealer_net(K,type) = -taker_net(K,type)          [Glassnode: dealer mirrors taker flow]

    inferred_gex(K) = (dealer_net_c * gamma_c + dealer_net_p * gamma_p) * S^2 * 0.01
    inferred_dex(K) =  dealer_net_c * delta_c + dealer_net_p * delta_p

Structural difference from the assumed-dealer view (``GexDexCalculator.
_calculate_gex_dex``, D7/D9's sign-convention precedent -- task-C3-brief.md):
gamma is ADDED for both call and put legs here (gamma > 0 for both), and the
sign comes entirely from ``dealer_net`` -- not a call-minus-put subtraction.
``dealer_net_c``/``dealer_net_p`` are NOT OI-weighted (unlike GexDexCalculator's
call_gamma/put_gamma), because the signed-taker accumulation already IS the
position size.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_WORST_STRIKES = 5


class DealerInventoryCalculator:
    """
    Pure: signed taker flow rows + per-instrument Greeks + spot -> inferred
    dealer exposure per strike.

    Args:
        flow_rows: List of dicts with keys ``strike``, ``option_type``,
            ``taker_net`` (signed, buy positive / sell negative),
            ``gross_volume`` (optional), ``trade_count`` (optional) -- the
            shape ``DatabaseRepository.get_signed_taker_flow_by_strike``
            returns.
        greeks_by_instrument: Dict keyed ``(strike, option_type)`` ->
            per-contract ``{"gamma": ..., "delta": ...}`` (the CURRENT
            option chain -- defines which strikes are "in the chain" for the
            zero-trade-strike and stale-strike edge cases below).
        spot_price: Current underlying spot price (anchors the S^2 term).
        currency: Underlying currency symbol, for labeling only.
    """

    def __init__(
        self,
        flow_rows: List[Dict[str, Any]],
        greeks_by_instrument: Dict[Tuple[float, str], Dict[str, float]],
        spot_price: float,
        currency: str = "BTC",
    ):
        self.flow_rows = flow_rows
        self.greeks_by_instrument = greeks_by_instrument
        self.spot_price = spot_price
        self.currency = currency

    def calculate(self) -> Dict[str, Any]:
        """
        Compute per-strike inferred dealer GEX/DEX, totals, and key levels
        recomputed on the inferred sign.

        Edge cases (spec §2(c)):
        - A strike/leg in ``greeks_by_instrument`` (the current chain) with
          no matching flow row -> dealer_net = 0 for that leg, still listed.
        - A strike/leg with a flow row but absent from
          ``greeks_by_instrument`` (expired/delisted mid-window) -> dropped
          from ``strike_data``, counted in ``stale_strikes`` instead (no
          Greeks means it cannot be priced -- there is nothing safe to do
          but flag it).

        Returns:
            Dict with ``strike_data`` (strike -> per-strike fields),
            ``total_inferred_gex``, ``total_inferred_dex``, ``key_levels``,
            ``stale_strikes``, ``spot_price``, ``currency``.
        """
        taker_net_by_leg, stale_strikes = self._index_flow_rows()

        chain_strikes = sorted({strike for (strike, _opt) in self.greeks_by_instrument.keys()})

        spot_squared = (self.spot_price or 0.0) ** 2
        strike_data: Dict[float, Dict[str, Any]] = {}

        for strike in chain_strikes:
            call_greeks = self.greeks_by_instrument.get((strike, "C"), {})
            put_greeks = self.greeks_by_instrument.get((strike, "P"), {})

            call_row = taker_net_by_leg.get((strike, "C"))
            put_row = taker_net_by_leg.get((strike, "P"))

            dealer_net_c = -(call_row["taker_net"] if call_row else 0.0)
            dealer_net_p = -(put_row["taker_net"] if put_row else 0.0)

            gamma_c = call_greeks.get("gamma") or 0.0
            gamma_p = put_greeks.get("gamma") or 0.0
            delta_c = call_greeks.get("delta") or 0.0
            delta_p = put_greeks.get("delta") or 0.0

            inferred_gex = (dealer_net_c * gamma_c + dealer_net_p * gamma_p) * spot_squared * 0.01
            inferred_dex = dealer_net_c * delta_c + dealer_net_p * delta_p

            strike_data[strike] = {
                "dealer_net_c": dealer_net_c,
                "dealer_net_p": dealer_net_p,
                "inferred_gex": inferred_gex,
                "inferred_dex": inferred_dex,
                "call_gross_volume": call_row["gross_volume"] if call_row else 0.0,
                "put_gross_volume": put_row["gross_volume"] if put_row else 0.0,
                "call_trade_count": call_row["trade_count"] if call_row else 0,
                "put_trade_count": put_row["trade_count"] if put_row else 0,
            }

        key_levels = self._detect_key_levels(strike_data)

        return {
            "strike_data": strike_data,
            "total_inferred_gex": sum(d["inferred_gex"] for d in strike_data.values()),
            "total_inferred_dex": sum(d["inferred_dex"] for d in strike_data.values()),
            "key_levels": key_levels,
            "stale_strikes": stale_strikes,
            "spot_price": self.spot_price,
            "currency": self.currency,
        }

    def coverage_report(self, oi_by_instrument: Dict[Tuple[float, str], float]) -> Dict[str, Any]:
        """
        OI-bound violation check (spec §2(a)/(b)): a maker's (dealer's)
        position can never exceed total open interest, so
        ``|taker_net| > OI`` for a leg is an impossible-strike violation --
        the empirical half of decision D9's gate.

        Args:
            oi_by_instrument: Dict keyed ``(strike, option_type)`` -> open
                interest for that leg (the current chain's OI, reused from
                the same enriched-instruments list -- no second query). A
                leg with NO key in this dict (as opposed to a key present
                with an explicit ``0`` OI) means "we have no OI reference for
                this leg", not "OI is zero" -- see the exclusion below.

        Returns:
            Dict with ``n_strikes`` (legs actually considered -- flow rows
            WITH an OI reference only, matching spec T2.2's per-leg count),
            ``n_violations``, ``violation_rate`` (0.0 if ``n_strikes == 0``),
            ``worst_strikes`` (up to 5, sorted by excess descending), and
            ``legs_excluded_no_oi`` (fix round, Important #3 -- see below).
        """
        # Fix round (Important #3, bugfix_spec.md section 2(c)): a leg with
        # NO entry in oi_by_instrument (as opposed to a real 0 OI) has no OI
        # reference to check against at all -- `oi_by_instrument.get(key) or
        # 0.0` used to silently default it to 0.0, which makes ANY nonzero
        # flow on that leg look like a violation (you cannot have flow
        # exceeding zero OI without it appearing to violate the OI bound).
        # This is not hypothetical: OnChainAnalysisService drops an
        # instrument from instruments_with_greeks (and therefore from
        # oi_by_instrument) whenever its get_ticker() call raises -- an
        # ordinary, transient API failure -- which used to be able to flip
        # the binding D9 gate purely from network flakiness, never from a
        # real data-quality problem. The spec's own §2(c) edge cases say
        # stale/unpriced legs should be dropped from the calculation and
        # counted separately in a diagnostic, not folded into the violation
        # numerator/denominator -- exactly what this does now.
        violations = []
        legs_excluded_no_oi = 0
        n_strikes = 0
        for row in self.flow_rows:
            strike = row["strike"]
            option_type = (row.get("option_type") or "").upper()
            key = (strike, option_type)
            if key not in oi_by_instrument:
                legs_excluded_no_oi += 1
                continue

            n_strikes += 1
            taker_net = row.get("taker_net") or 0.0
            oi = oi_by_instrument.get(key) or 0.0
            excess = abs(taker_net) - oi
            if excess > 0:
                violations.append({
                    "strike": strike,
                    "option_type": option_type,
                    "taker_net": taker_net,
                    "open_interest": oi,
                    "excess": excess,
                })

        violations.sort(key=lambda v: v["excess"], reverse=True)

        return {
            "n_strikes": n_strikes,
            "n_violations": len(violations),
            "violation_rate": (len(violations) / n_strikes) if n_strikes else 0.0,
            "worst_strikes": tuple(violations[:_MAX_WORST_STRIKES]),
            "legs_excluded_no_oi": legs_excluded_no_oi,
        }

    def _index_flow_rows(self) -> Tuple[Dict[Tuple[float, str], Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Index ``flow_rows`` by ``(strike, option_type)``, splitting out rows
        whose strike/type is absent from ``greeks_by_instrument`` (the
        current chain) into a stale-strikes diagnostic list rather than
        silently dropping them.
        """
        indexed: Dict[Tuple[float, str], Dict[str, Any]] = {}
        stale: List[Dict[str, Any]] = []

        for row in self.flow_rows:
            strike = row.get("strike")
            option_type = (row.get("option_type") or "").upper()
            if strike is None or option_type not in ("C", "P"):
                continue

            key = (strike, option_type)
            if key not in self.greeks_by_instrument:
                stale.append({
                    "strike": strike,
                    "option_type": option_type,
                    "taker_net": row.get("taker_net") or 0.0,
                })
                continue

            indexed[key] = {
                "taker_net": row.get("taker_net") or 0.0,
                "gross_volume": row.get("gross_volume") or 0.0,
                "trade_count": row.get("trade_count") or 0,
            }

        return indexed, stale

    @staticmethod
    def _detect_key_levels(strike_data: Dict[float, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Call wall / put support / cumulative-inferred-GEX zero-crossing
        strike, recomputed on the INFERRED sign (spec §2(c)) -- independent
        strikes from the assumed view's own key levels, not a copy of them.
        Simplified relative to ``GexDexCalculator._detect_key_levels`` (no
        gamma-profile re-pricing, no net-GEX-flip fallback): this is a
        strike-axis cumulative-sign-change detector only, matching what the
        spec's acceptance tests exercise.
        """
        if not strike_data:
            return {"call_resistance": None, "put_support": None, "hvl": None}

        sorted_strikes = sorted(strike_data.keys())

        max_positive = 0.0
        call_resistance = None
        max_negative = 0.0
        put_support = None
        for strike in sorted_strikes:
            gex = strike_data[strike]["inferred_gex"]
            if gex > max_positive:
                max_positive = gex
                call_resistance = strike
            if gex < 0 and abs(gex) > max_negative:
                max_negative = abs(gex)
                put_support = strike

        hvl = None
        running = 0.0
        prev_running: Optional[float] = None
        for strike in sorted_strikes:
            running += strike_data[strike]["inferred_gex"]
            if prev_running is not None and prev_running * running < 0:
                hvl = strike
            prev_running = running

        return {
            "call_resistance": (
                {"strike": call_resistance, "inferred_gex": max_positive}
                if call_resistance is not None else None
            ),
            "put_support": (
                {"strike": put_support, "inferred_gex": -max_negative}
                if put_support is not None else None
            ),
            "hvl": hvl,
        }
