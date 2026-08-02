"""
GEX (Gamma Exposure) and DEX (Delta Exposure) calculator.

Calculates gamma and delta exposure per strike, cumulative profiles, and
identifies key levels (Call Resistance, Put Support, Cumulative GEX Zero
Strike). Also re-prices dealer gamma across a spot grid (via
``GammaProfileCalculator``) to locate the actual Zero Gamma Level --
bugfix_spec.md Item 2. See
``coding.core.analytics.results.gex_dex_results.GexDexKeyLevels``'s
docstring for why both quantities exist and why ``hvl``/``gamma_flip`` keep
their historical, now-misleading name instead of being renamed outright.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from coding.core.analytics.gamma_profile_calculator import (
    GammaLeg,
    GammaProfileCalculator,
)
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
)

logger = logging.getLogger(__name__)

# Deribit options settle at 08:00 UTC on the expiry date, never local midnight
# (bugfix_spec.md Item 2, reusing the same convention already fixed for Item
# 5 -- see MarketWideCalculator._parse_expiry_datetime).
_DERIBIT_SETTLEMENT_HOUR_UTC = 8
_MIN_LEG_OPEN_INTEREST = 0.0
_MIN_LEG_IMPLIED_VOLATILITY = 0.0


class GexDexCalculator:
    """
    Calculate GEX and DEX from options data with Greeks.

    Formulas (Industry Standard):
    - Net GEX per strike = (Call Gamma - Put Gamma) * Spot Price² * 0.01
      * Gamma is weighted by OI during aggregation
      * Spot² accounts for notional dollar exposure
      * 0.01 scales to 1% underlying move
    - Net DEX per strike = Call Delta + Put Delta (put delta is negative)

    SIGN CONVENTION (bugfix_spec.md Item 8 -- the one place this is stated;
    every ``*_holder``/``dealer_*`` field on ``GexDexStrikeRow``/
    ``GexDexResult`` refers back to this instead of re-explaining it):
    two families of exposure are computed, from two different positioning
    models. HOLDER-SIDE (``*_holder`` fields) is pure arithmetic on
    observable open interest and Greeks -- no assumption about who holds
    what. ASSUMED-DEALER VIEW (``dealer_*`` fields, including the original
    ``net_gex``/``net_dex``) is the SqueezeMetrics/SpotGamma heuristic that
    dealers are long calls and short puts for gamma, and short whatever
    customers (i.e. holders) hold for delta/vanna/charm -- an inference, not
    a measurement. ``net_gex`` (kept, not renamed) IS the dealer-side gamma
    number -- that split is the specific published convention this figure
    already means, and redefining it would break comparability with every
    external GEX source. ``net_dex`` (kept) IS the holder-side delta number.
    See ``dealer_gamma_exposure``/``gamma_exposure_holder``/
    ``delta_exposure_holder``/``dealer_delta_exposure`` for the
    correctly-labelled, complete set of both families.

    Key Levels:
    - Call Resistance: Strike with maximum positive Net GEX
    - Put Support: Strike with maximum negative Net GEX
    - Cumulative GEX Zero Strike (internally ``hvl``/``gamma_flip`` -- kept
      unrenamed, see ``GexDexKeyLevels``'s docstring): the STRIKE where
      cumulative net GEX (summed strike-by-strike) changes sign. This is a
      property of how open interest happens to be distributed along the
      strike axis, NOT a re-priced dealer-gamma flip -- bugfix_spec.md Item
      2's live audit found it can (and did) sit on the OPPOSITE side of spot
      from the actual Zero Gamma Level. ``repository.save_onchain_snapshot``
      persists this value into the LIVE ``hvl_level`` DB column read by the
      straddle regime gate and IC/BF scanners, and ``synthesis.py``
      (morning-note rendering) also reads it directly -- that is why this
      attribute keeps its old, misleading name rather than being renamed.
    - Zero Gamma Level (the actual gamma flip, ``GexDexKeyLevels.
      zero_gamma_level``): re-prices total dealer gamma across a spot grid
      (sticky-strike Black-Scholes, ``GammaProfileCalculator``) and finds
      where it changes sign. This is the SpotGamma/SqueezeMetrics
      definition of "Zero Gamma Level" -- Cumulative GEX Zero Strike above
      is NOT it, despite historically being labeled that in this report.
    """

    def __init__(
        self,
        instruments: List[Dict[str, Any]],
        spot_price: float,
        currency: str = "BTC",
    ):
        """
        Initialize calculator with instrument data containing Greeks.

        Args:
            instruments: List of instrument dicts with gamma, delta, OI, strike, option_type.
            spot_price: Current underlying spot price.
            currency: Underlying currency symbol (e.g. "BTC", "ETH"). Used for unit labels.
                      GEX is always in USD; DEX is in this currency.
        """
        self.instruments = instruments
        self.spot_price = spot_price
        self.currency = currency
        self.strike_data: Dict[float, Dict[str, Any]] = {}

    def calculate(self) -> GexDexResult:
        """
        Calculate all GEX/DEX metrics.

        Idempotent: repeated calls on unchanged instruments/spot_price return
        equal results. strike_data is reset (reassigned, not mutated in place)
        at entry so a stale reference held by a previous caller is never
        touched by a later recalculation.

        Returns:
            GexDexResult with per-strike rows, cumulative profiles, and key
            levels. Call ``.to_dict()`` for the legacy dict shape.
        """
        self.strike_data = {}
        self._aggregate_by_strike()
        self._calculate_gex_dex()
        cumulative = self._calculate_cumulative_profiles()
        key_levels = self._detect_key_levels()
        gamma_profile = self._calculate_gamma_profile(self.instruments, self.spot_price)

        return self._build_result(
            self.strike_data, cumulative, key_levels, self.spot_price, self.currency,
            gamma_profile=gamma_profile,
        )

    @staticmethod
    def _build_gamma_legs(instruments: List[Dict[str, Any]]) -> List[GammaLeg]:
        """
        Build ``GammaLeg`` inputs for ``GammaProfileCalculator`` from the same
        instrument dicts ``GexDexCalculator`` already receives.

        bugfix_spec.md Item 2 (F2.3.2 edge cases), applied here rather than in
        the service layer (task B1 D3 keeps the daemon/service leg-building
        and persistence paths untouched): a leg is skipped if its strike/
        option_type are missing, ``open_interest <= 0``, ``mark_iv`` is
        missing or `<= 0``, or its parsed expiry is unparseable/already
        elapsed. Expiry is computed from the ``expiration`` label (e.g.
        "31JUL26") at 08:00 UTC settlement -- the same convention already
        fixed for bugfix_spec.md Item 5 (``MarketWideCalculator.
        _parse_expiry_datetime``) -- never a local-midnight/integer-days
        calculation.
        """
        now_utc = datetime.now(timezone.utc)
        legs: List[GammaLeg] = []

        for item in instruments:
            strike = item.get("strike")
            option_type = (item.get("option_type") or "").upper()
            if strike is None or option_type not in ("C", "P"):
                continue

            open_interest = item.get("open_interest") or 0
            if open_interest <= _MIN_LEG_OPEN_INTEREST:
                continue

            mark_iv = item.get("mark_iv")
            if mark_iv is None or mark_iv <= _MIN_LEG_IMPLIED_VOLATILITY:
                continue

            expiration = item.get("expiration")
            if not expiration:
                continue
            try:
                expiry_dt = datetime.strptime(expiration, "%d%b%y").replace(
                    hour=_DERIBIT_SETTLEMENT_HOUR_UTC, minute=0, second=0,
                    microsecond=0, tzinfo=timezone.utc,
                )
            except ValueError:
                continue

            time_to_expiry_years = (expiry_dt - now_utc).total_seconds() / (365.0 * 86400.0)
            if time_to_expiry_years <= 0:
                continue

            legs.append(GammaLeg(
                strike=float(strike),
                implied_volatility=float(mark_iv) / 100.0,
                time_to_expiry_years=time_to_expiry_years,
                open_interest=float(open_interest),
                option_type=option_type,
            ))

        return legs

    @staticmethod
    def _calculate_gamma_profile(
        instruments: List[Dict[str, Any]], spot_price: float
    ) -> Dict[str, Any]:
        """
        Re-price total dealer gamma across a spot grid to locate the actual
        zero-gamma level (bugfix_spec.md Item 2) via ``GammaProfileCalculator``.

        Gracefully degrades to the "nothing to price" shape (``zero_gamma_level
        = None``) when ``instruments`` lack the fields a leg needs (no
        ``mark_iv``/``expiration``) -- this is the case for
        ``aggregate_across_expirations``, which only has already strike-merged
        gamma*OI with no per-leg IV/tau left to re-price (see that method's
        docstring): concatenating raw per-expiry legs into a true cross-expiry
        re-priced profile needs service-layer leg plumbing across expirations,
        which is out of scope for task B1 (D3 restricts this task to
        ``GexDexCalculator`` alone).

        Task B1 review (Important #1): this feeds an ADDITIVE field only
        (``zero_gamma_level`` etc. on ``GexDexKeyLevels``) -- it must never be
        able to raise. ``calculate()`` is the same method that produces the
        value persisted into the live ``hvl_level`` DB column, and task A6
        made the daemon (``ProspectiveCollector._run_onchain_analysis``)
        re-raise ``TypeError``/``AttributeError`` rather than swallow them,
        specifically so real shape-mismatch bugs surface loudly instead of
        silently zeroing writes. Without a guard here, a malformed field
        feeding only this new computation (e.g. a non-numeric ``mark_iv`` or
        ``open_interest``, a non-string ``expiration``) would raise
        ``TypeError``/``ValueError`` uncaught and abort ``calculate()`` --
        and therefore the WHOLE currency's on-chain analysis, including the
        untouched ``hvl``/``key_levels`` path -- defeating D3's "purely
        additive, can't hurt the live path" guarantee. Any exception here is
        caught, logged, and degrades to the same "insufficient data" shape
        ``GammaProfileCalculator`` itself already returns for a non-positive
        spot / no legs, rather than propagating.
        """
        try:
            legs = GexDexCalculator._build_gamma_legs(instruments)
            return GammaProfileCalculator(legs, spot_price).calculate()
        except Exception:
            logger.error(
                "GexDexCalculator: gamma-profile re-pricing failed "
                "unexpectedly -- degrading to 'insufficient data' rather "
                "than aborting the GEX/DEX calculation (this computation is "
                "additive only, bugfix_spec.md Item 2 / task B1 D3)",
                exc_info=True,
            )
            return {
                "zero_gamma_level": None,
                "zero_gamma_crossings": [],
                "net_gex_at_spot": None,
                "gamma_profile": [],
                "spot_price": spot_price,
                "leg_count": 0,
                "legs_skipped": 0,
                "regime": "UNKNOWN",
            }

    @staticmethod
    def _build_result(
        strike_data: Dict[float, Dict[str, Any]],
        cumulative: Dict[str, Dict[float, float]],
        key_levels: Dict[str, Any],
        spot_price: float,
        currency: str,
        expiration_count: Optional[int] = None,
        gamma_profile: Optional[Dict[str, Any]] = None,
    ) -> GexDexResult:
        """Assemble a typed ``GexDexResult`` from the internal dict-based working state."""
        gamma_profile = gamma_profile or {}
        strike_rows = tuple(
            GexDexStrikeRow(
                strike=strike,
                call_gamma=data["call_gamma"],
                put_gamma=data["put_gamma"],
                call_delta=data["call_delta"],
                put_delta=data["put_delta"],
                call_oi=data["call_oi"],
                put_oi=data["put_oi"],
                net_gex=data["net_gex"],
                net_dex=data["net_dex"],
                net_gamma=data["net_gamma"],
                cumulative_gex=data["cumulative_gex"],
                cumulative_dex=data["cumulative_dex"],
                # bugfix_spec.md Item 8: explicit when available (correctly
                # S^2*0.01-scaled by _calculate_gex_dex); GexDexStrikeRow's
                # own __post_init__ derives a fallback otherwise.
                gamma_exposure_holder=data.get("gamma_exposure_holder"),
                delta_exposure_holder=data.get("delta_exposure_holder"),
                dealer_gamma_exposure=data.get("dealer_gamma_exposure"),
                dealer_delta_exposure=data.get("dealer_delta_exposure"),
            )
            for strike, data in sorted(strike_data.items())
        )

        cr = key_levels.get("call_resistance")
        ps = key_levels.get("put_support")

        return GexDexResult(
            strike_rows=strike_rows,
            cumulative_gex=dict(cumulative["cumulative_gex"]),
            cumulative_dex=dict(cumulative["cumulative_dex"]),
            key_levels=GexDexKeyLevels(
                call_resistance=GexDexLevel(strike=cr["strike"], net_gex=cr["net_gex"]) if cr else None,
                put_support=GexDexLevel(strike=ps["strike"], net_gex=ps["net_gex"]) if ps else None,
                hvl=key_levels.get("hvl"),
                gamma_flip=key_levels.get("gamma_flip"),
                cumulative_gex_zero_strike=key_levels.get("hvl"),
                zero_gamma_level=gamma_profile.get("zero_gamma_level"),
                zero_gamma_crossings=tuple(gamma_profile.get("zero_gamma_crossings", []) or []),
                net_gex_at_spot=gamma_profile.get("net_gex_at_spot"),
                gamma_regime=gamma_profile.get("regime"),
                legs_skipped=gamma_profile.get("legs_skipped", 0),
            ),
            spot_price=spot_price,
            total_net_gex=sum(d["net_gex"] for d in strike_data.values()),
            total_net_dex=sum(d["net_dex"] for d in strike_data.values()),
            currency=currency,
            expiration_count=expiration_count,
        )

    def _aggregate_by_strike(self) -> None:
        """Aggregate instrument data by strike price."""
        for item in self.instruments:
            strike = item.get("strike")
            if strike is None:
                continue

            # independent review round 4 sweep: aligned with
            # _aggregate_by_moneyness's `(item.get("option_type") or "")`
            # (same field, a few dozen lines above) -- option_type is
            # always parsed from the instrument_name string by this
            # pipeline (never raw-API null in practice), so this was never
            # exploitable, just an internal inconsistency between two
            # extractions of the same field in the same file.
            option_type = (item.get("option_type") or "").upper()
            gamma = item.get("gamma") or 0
            delta = item.get("delta") or 0
            oi = item.get("open_interest") or 0

            if strike not in self.strike_data:
                self.strike_data[strike] = {
                    "call_gamma": 0.0,
                    "put_gamma": 0.0,
                    "call_delta": 0.0,
                    "put_delta": 0.0,
                    "call_oi": 0.0,
                    "put_oi": 0.0,
                    "net_gex": 0.0,
                    "net_dex": 0.0,
                }

            if option_type == "C":
                self.strike_data[strike]["call_gamma"] += gamma * oi
                self.strike_data[strike]["call_delta"] += delta * oi
                self.strike_data[strike]["call_oi"] += oi
            elif option_type == "P":
                self.strike_data[strike]["put_gamma"] += gamma * oi
                self.strike_data[strike]["put_delta"] += delta * oi
                self.strike_data[strike]["put_oi"] += oi

    def _calculate_gex_dex(self) -> None:
        """
        Calculate Net GEX and Net DEX per strike.

        Net GEX = (Call Gamma - Put Gamma) * Spot Price² * 0.01
        - Spot² accounts for notional exposure to underlying moves
        - 0.01 converts to percentage-based move (1%)
        - Gamma values are already weighted by OI from aggregation

        Net DEX = Call Delta + Put Delta (put delta is already negative)

        bugfix_spec.md Item 8 (F8.3.1): also computes the holder-side raw
        exposures (no positioning assumption -- pure arithmetic on observable
        OI/Greeks) alongside the existing dealer-side ``net_gex``/``net_dex``
        (the SqueezeMetrics call-put-split heuristic for gamma; the raw
        holder sum, unlabelled, for delta). ``net_gex``/``net_dex`` keep
        their values and meaning unchanged -- ``dealer_gamma_exposure``/
        ``delta_exposure_holder`` are exact aliases of them.
        """
        spot_squared = self.spot_price ** 2
        for strike, data in self.strike_data.items():
            # Net GEX: (Call Gamma - Put Gamma) * Spot² * 0.01 (industry standard)
            # The gamma values are already weighted by OI from aggregation
            net_gamma = data["call_gamma"] - data["put_gamma"]
            data["net_gex"] = net_gamma * spot_squared * 0.01

            # Net DEX: Call Delta + Put Delta
            # Put delta is negative, so this gives net directional exposure
            data["net_dex"] = data["call_delta"] + data["put_delta"]

            # Store raw net gamma for reference
            data["net_gamma"] = net_gamma

            # Item 8: holder-side raw gamma exposure and the dealer-side
            # aliases/negation.
            data["gamma_exposure_holder"] = (
                (data["call_gamma"] + data["put_gamma"]) * spot_squared * 0.01
            )
            data["delta_exposure_holder"] = data["net_dex"]
            data["dealer_gamma_exposure"] = data["net_gex"]
            data["dealer_delta_exposure"] = -data["net_dex"]

    def _calculate_cumulative_profiles(self) -> Dict[str, Dict[float, float]]:
        """
        Calculate cumulative GEX and DEX profiles across strikes.

        Returns:
            Dict with cumulative_gex and cumulative_dex mappings.
        """
        sorted_strikes = sorted(self.strike_data.keys())

        cumulative_gex: Dict[float, float] = {}
        cumulative_dex: Dict[float, float] = {}

        running_gex = 0.0
        running_dex = 0.0

        for strike in sorted_strikes:
            running_gex += self.strike_data[strike]["net_gex"]
            running_dex += self.strike_data[strike]["net_dex"]
            cumulative_gex[strike] = running_gex
            cumulative_dex[strike] = running_dex
            # Store in strike data as well
            self.strike_data[strike]["cumulative_gex"] = running_gex
            self.strike_data[strike]["cumulative_dex"] = running_dex

        return {
            "cumulative_gex": cumulative_gex,
            "cumulative_dex": cumulative_dex,
        }

    def _detect_key_levels(self) -> Dict[str, Any]:
        """
        Detect key trading levels from GEX/DEX data.

        Returns:
            Dict with call_resistance, put_support, hvl, and gamma_flip.
            ``hvl``/``gamma_flip`` are the STRIKE where cumulative net GEX
            (summed strike-by-strike) changes sign -- a strike-axis artifact
            of how open interest is distributed, NOT a re-priced gamma flip.
            See the class docstring's "Key Levels" section and
            ``GexDexKeyLevels``'s docstring for why this attribute name is
            kept and what it actually represents; the correct gamma flip is
            ``GexDexKeyLevels.zero_gamma_level``, computed separately by
            ``GammaProfileCalculator`` (bugfix_spec.md Item 2).
        """
        if not self.strike_data:
            return {
                "call_resistance": None,
                "put_support": None,
                "hvl": None,
                "gamma_flip": None,
            }

        sorted_strikes = sorted(self.strike_data.keys())

        # Call Resistance: Strike with maximum positive Net GEX
        max_positive_gex = 0.0
        call_resistance = None
        for strike in sorted_strikes:
            gex = self.strike_data[strike]["net_gex"]
            if gex > max_positive_gex:
                max_positive_gex = gex
                call_resistance = strike

        # Put Support: Strike with maximum negative Net GEX (by absolute value)
        max_negative_gex = 0.0
        put_support = None
        for strike in sorted_strikes:
            gex = self.strike_data[strike]["net_gex"]
            if gex < 0 and abs(gex) > max_negative_gex:
                max_negative_gex = abs(gex)
                put_support = strike

        # HVL / Gamma Flip: Where cumulative GEX crosses zero
        # Find the strike where sign changes from positive to negative or vice versa
        gamma_flip = None
        hvl = None
        prev_cumulative = None
        
        # Also track Net GEX flips (local zero gamma) as fallback
        net_gex_flips = []
        prev_net_gex = None
        prev_strike = None

        for strike in sorted_strikes:
            # 1. Check Cumulative Flip
            curr_cumulative = self.strike_data[strike]["cumulative_gex"]

            if prev_cumulative is not None:
                # Check for sign change in cumulative GEX
                if prev_cumulative * curr_cumulative < 0:
                    # Sign changed - this is the global gamma flip point
                    gamma_flip = strike
                    hvl = strike
                    
            prev_cumulative = curr_cumulative
            
            # 2. Check Net GEX Flip (Local Zero Gamma)
            curr_net_gex = self.strike_data[strike]["net_gex"]
            
            if prev_net_gex is not None:
                 if (prev_net_gex > 0 and curr_net_gex < 0) or (prev_net_gex < 0 and curr_net_gex > 0):
                     # Found a local flip
                     # Store strike, and the magnitude of the flip (sum of abs values)
                     magnitude = abs(prev_net_gex) + abs(curr_net_gex)
                     net_gex_flips.append({
                         "strike": strike,
                         "prev_strike": prev_strike,
                         "magnitude": magnitude,
                         "distance_to_spot": abs(strike - self.spot_price) if self.spot_price else float('inf')
                     })
            
            prev_net_gex = curr_net_gex
            prev_strike = strike

        # If no global flip found, or if it's at the very edge (trivial), use major Net GEX flip
        # Trivial check: if hvl is the first or last strike, it's likely just an artifact of starting at 0
        is_trivial_hvl = hvl == sorted_strikes[0] or hvl == sorted_strikes[-1]
        
        if (hvl is None or is_trivial_hvl) and net_gex_flips:
            # Find the "major" flip. 
            # We prioritize flips closest to spot price to find the relevant trading level.
            # Alternatively, we could prioritize magnitude. 
            # Let's sort by distance to spot first, then magnitude.
            
            # Filter for flips within reasonable range if possible, or just take closest
            best_flip = min(net_gex_flips, key=lambda x: x["distance_to_spot"])
            
            hvl = best_flip["strike"]
            # If we didn't have a gamma flip, we can use this as a proxy or leave it None
            # Keeping gamma_flip strictly for cumulative zero crossing is more accurate to definition.

        # If still no HVL (very rare), find strike closest to zero cumulative GEX (absolute minimum)
        if hvl is None and sorted_strikes:
            min_abs_cumulative = float("inf")
            for strike in sorted_strikes:
                abs_cumulative = abs(self.strike_data[strike]["cumulative_gex"])
                if abs_cumulative < min_abs_cumulative:
                    min_abs_cumulative = abs_cumulative
                    hvl = strike
                    
        return {
            "call_resistance": {
                "strike": call_resistance,
                "net_gex": max_positive_gex,
            } if call_resistance else None,
            "put_support": {
                "strike": put_support,
                "net_gex": -max_negative_gex,
            } if put_support else None,
            "hvl": hvl,
            "gamma_flip": gamma_flip,
        }

    @staticmethod
    def aggregate_across_expirations(
        gex_dex_by_expiry: Dict[str, GexDexResult],
        spot_price: float,
        currency: str = "BTC",
    ) -> GexDexResult:
        """
        Aggregate GEX/DEX data across all expirations using equal-weight summation.

        Industry standard (SqueezeMetrics, SpotGamma, Glassnode): gamma already
        encodes time-to-expiry via the Black-Scholes formula, so DTE-weighting
        would double-count that effect.

        Args:
            gex_dex_by_expiry: Dict mapping expiry -> calculate() result
                               (typed ``GexDexResult``). Keys named "AGGREGATE"
                               are skipped.
            spot_price: Current underlying spot price (used for GEX formula).
            currency: Underlying currency symbol for unit labels.

        Returns:
            GexDexResult with same structure as calculate() plus expiration_count set.

        Note (bugfix_spec.md Item 2 / task B1): the aggregate's
        ``zero_gamma_level``/``zero_gamma_crossings``/``net_gex_at_spot`` are
        always ``None``/``()``/``None`` here (``gamma_regime`` "UNKNOWN") --
        this method only receives already strike-merged ``GexDexResult``
        rows (``call_gamma``/``put_gamma`` = sum of gamma*OI at the ORIGINAL
        spot each expiry was computed at), which discards each leg's own IV
        and time-to-expiry. There is no data here from which to re-price a
        true cross-expiry gamma profile; doing so needs the daemon/service to
        concatenate raw per-expiry legs (bugfix_spec.md F2.3.2), which is out
        of scope for task B1 (D3 restricts this task to ``GexDexCalculator``
        alone). Each expiration's own ``calculate()`` result still carries a
        correctly re-priced per-expiry ``zero_gamma_level``.
        """
        merged_strike_data: Dict[float, Dict[str, Any]] = {}
        expiration_count = 0

        for expiry, result in gex_dex_by_expiry.items():
            if expiry == "AGGREGATE":
                continue
            expiration_count += 1

            for row in result.strike_rows:
                if row.strike not in merged_strike_data:
                    merged_strike_data[row.strike] = {
                        "call_gamma": 0.0,
                        "put_gamma": 0.0,
                        "call_delta": 0.0,
                        "put_delta": 0.0,
                        "call_oi": 0.0,
                        "put_oi": 0.0,
                        "net_gex": 0.0,
                        "net_dex": 0.0,
                    }
                merged_strike_data[row.strike]["call_gamma"] += row.call_gamma
                merged_strike_data[row.strike]["put_gamma"] += row.put_gamma
                merged_strike_data[row.strike]["call_delta"] += row.call_delta
                merged_strike_data[row.strike]["put_delta"] += row.put_delta
                merged_strike_data[row.strike]["call_oi"] += row.call_oi
                merged_strike_data[row.strike]["put_oi"] += row.put_oi

        # No raw instruments are available at this level (only already
        # strike-merged GexDexResult rows) -- see the gamma-profile note
        # above. Always the "insufficient data" shape (regime "UNKNOWN").
        no_data_gamma_profile = GexDexCalculator._calculate_gamma_profile([], spot_price)

        if not merged_strike_data:
            return GexDexCalculator._build_result(
                {}, {"cumulative_gex": {}, "cumulative_dex": {}},
                {"call_resistance": None, "put_support": None, "hvl": None, "gamma_flip": None},
                spot_price, currency, expiration_count=expiration_count,
                gamma_profile=no_data_gamma_profile,
            )

        # Inject merged strike data into a temporary calculator instance and re-run formulas
        agg_calc = GexDexCalculator([], spot_price=spot_price, currency=currency)
        agg_calc.strike_data = merged_strike_data

        agg_calc._calculate_gex_dex()
        cumulative = agg_calc._calculate_cumulative_profiles()
        key_levels = agg_calc._detect_key_levels()

        return GexDexCalculator._build_result(
            agg_calc.strike_data, cumulative, key_levels, spot_price, currency,
            expiration_count=expiration_count,
            gamma_profile=no_data_gamma_profile,
        )

    @staticmethod
    def _parse_expiry_dte_days(expiration: str, now_utc: datetime) -> Optional[float]:
        """
        Exact fractional days from ``now_utc`` to this expiry's 08:00 UTC
        settlement -- same "DDMONYY" / settlement-hour convention already
        used by ``_build_gamma_legs`` in this class (bugfix_spec.md Item 5).
        May be negative if the expiry's settlement already passed.
        ``None`` if ``expiration`` does not parse.
        """
        try:
            expiry_dt = datetime.strptime(expiration, "%d%b%y").replace(
                hour=_DERIBIT_SETTLEMENT_HOUR_UTC, minute=0, second=0,
                microsecond=0, tzinfo=timezone.utc,
            )
        except ValueError:
            return None
        return (expiry_dt - now_utc).total_seconds() / 86400.0

    @staticmethod
    def calculate_rolloff_profile(
        per_expiry_net_gex: Dict[str, float], now_utc: datetime,
    ) -> Dict[str, Any]:
        """
        Net GEX per-expiry roll-off (institutional_metrics_spec.md section 5
        / Task C6): % of gamma MASS expiring at each expiry, the cumulative
        decay curve across expiries (chronological order), and a "GAMMA
        CLIFF" presentation flag when more than 30% of gamma mass rolls off
        within 7 days.

        Pure aggregation over per-expiry ``total_net_gex`` values the
        GEX/DEX calculator already produces (each individually correct as
        of the strike_data-reset fix in ``calculate()`` -- BUG 1,
        metric_verification_report.md -- so no re-computation of Greeks
        happens here). Takes a dict, returns a dict; never mutates its
        input, never raises on degenerate input.

        Sign handling (spec 5(b)): signed net GEX cannot be turned into a
        share directly (it crosses zero, so a naive ratio can exceed 100%
        or go negative) -- shares are computed on a gross MAGNITUDE
        denominator (``Sigma |net_gex(e)|``) while the cumulative signed
        USD contribution (``cum_net_gex``) is reported separately and can
        be non-monotone when signs differ across expiries. That is correct,
        not a bug -- see the T5.2 mixed-sign acceptance test.

        Args:
            per_expiry_net_gex: {expiration_label: total_net_gex} for every
                expiry with data (label must parse as "DDMONYY", e.g.
                "25JUL26" -- the same convention every other DTE-computing
                method in this codebase uses). An unparseable label is
                skipped (logged), never raised -- mirrors
                ``_build_gamma_legs``' own parse-failure handling.
            now_utc: reference instant for DTE. Must be timezone-aware UTC.

        Returns:
            dict with keys:
              ``rows``: list of ``{expiration, dte_days, net_gex, share_pct,
                cum_share_pct, cum_net_gex}``, sorted by ``dte_days``
                ascending (chronological). ``share_pct``/``cum_share_pct``
                are ``None`` for every row when ``gross_total`` is 0 (spec
                5(c) "no OI anywhere" edge case) -- never a
                ``ZeroDivisionError``.
              ``gamma_cliff_7d``: ``True`` when ``cum_share_7d > 30.0``
                (strictly greater -- spec 5(b)). Always ``False`` when
                ``gross_total`` is 0 or there are no expiries.
              ``cum_share_7d`` / ``cum_share_30d``: cumulative share (%) of
                gamma mass expiring within 7 / 30 days respectively (``dte
                <= D``). ``None`` when ``gross_total`` is 0.
              ``gross_total``: ``Sigma |net_gex(e)|`` (additive field, not
                individually named in the spec's fix-spec list but needed
                to distinguish "no gamma anywhere" from "some gamma, none
                within 7d" -- both produce ``gamma_cliff_7d=False`` but only
                the former should render "no gamma" instead of a table).

        Edge cases (spec 5(c), all covered by tests):
            - Zero expiries / all-zero net GEX -> ``gross_total == 0`` ->
              all shares ``None``, ``gamma_cliff_7d = False``, no crash.
            - An expiry already past its 08:00 UTC settlement but still
              present in the chain -> ``dte_days`` clamps to ``0.0`` (never
              negative) and is included in the 7d bucket.
            - Mixed signs -> shares still sum to 100% (computed on ``| |``)
              while ``cum_net_gex`` may be non-monotone -- correct.
        """
        parsed: List[Dict[str, Any]] = []
        for expiration, net_gex in per_expiry_net_gex.items():
            dte_exact = GexDexCalculator._parse_expiry_dte_days(expiration, now_utc)
            if dte_exact is None:
                logger.warning(
                    "calculate_rolloff_profile: skipping expiry %r -- "
                    "unparseable expiration label",
                    expiration,
                )
                continue
            # Spec 5(c): already past settlement but still in the chain ->
            # clamp to 0.0 (never negative) and keep it in the 7d bucket.
            dte_days = max(dte_exact, 0.0)
            parsed.append({"expiration": expiration, "dte_days": dte_days, "net_gex": net_gex})

        parsed.sort(key=lambda p: p["dte_days"])

        gross_total = sum(abs(p["net_gex"]) for p in parsed)

        rows: List[Dict[str, Any]] = []
        cum_share_pct = 0.0
        cum_net_gex = 0.0
        for p in parsed:
            cum_net_gex += p["net_gex"]
            if gross_total > 0:
                share_pct: Optional[float] = 100.0 * abs(p["net_gex"]) / gross_total
                cum_share_pct += share_pct
                row_cum_share_pct: Optional[float] = cum_share_pct
            else:
                share_pct = None
                row_cum_share_pct = None
            rows.append({
                "expiration": p["expiration"],
                "dte_days": p["dte_days"],
                "net_gex": p["net_gex"],
                "share_pct": share_pct,
                "cum_share_pct": row_cum_share_pct,
                "cum_net_gex": cum_net_gex,
            })

        if gross_total > 0:
            cum_share_7d: Optional[float] = sum(
                r["share_pct"] for r in rows if r["dte_days"] <= 7.0
            )
            cum_share_30d: Optional[float] = sum(
                r["share_pct"] for r in rows if r["dte_days"] <= 30.0
            )
            gamma_cliff_7d = cum_share_7d > 30.0
        else:
            cum_share_7d = None
            cum_share_30d = None
            gamma_cliff_7d = False

        return {
            "rows": rows,
            "gamma_cliff_7d": gamma_cliff_7d,
            "cum_share_7d": cum_share_7d,
            "cum_share_30d": cum_share_30d,
            "gross_total": gross_total,
        }

