"""
On-chain analytics for options market data.

Calculates max pain, put/call ratios, support/resistance levels, and
moneyness breakdowns per expiration.

refactor_design_spec.md section T10: this module was ``OnChainAnalyzer``, a
mutable accumulator that also owned 351 lines of report-text generation
(``generate_report()``) plus 14 setters and 16 mutable section attributes
for that report's bookkeeping. All of that moved out over T3 (formatters
extracted to ``core/analytics/reporting/``), T6 (``OnChainAnalysisBuilder``
replaced the setters as the typed aggregation point), T8/T10 (rendering
moved to ``OnChainReportFormatter.render_full_from_result``, fed by the
builder's typed result, not this class's dicts). This module is now
``OnChainMetricsCalculator``: pure per-expiration calculation, narrowed to
exactly the methods ``ProspectiveCollector`` (the production daemon) and
``OnChainAnalysisService`` actually call, plus the state those calls need
across phases (``enriched_instruments``, ``market_metrics``,
``_recent_trades``, ``_atm_ivs`` -- real cross-phase data, not report
bookkeeping, so they stay as plain attributes the service writes directly,
with no setter method).

``OnChainAnalyzer`` remains a back-compat alias for any caller that still
imports the old name.
"""

import logging
import statistics
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from coding.core.analytics.forward_price_utils import select_forward_price
from coding.core.analytics.results.analysis_result import MarketMetricsResult
from coding.core.analytics.thresholds import (
    OI_SKEW_ITM_HEAVY_THRESHOLD_PCT,
    OI_SKEW_OTM_HEAVY_THRESHOLD_PCT,
    interpret_put_call_ratio,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    LevelRef,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    StrikeOiRow,
    SupportResistanceResult,
    VolumeStatsResult,
)

logger = logging.getLogger(__name__)


def _to_market_metrics(market_metrics: Dict[str, Any]) -> Optional[MarketMetricsResult]:
    """Adapt the legacy market_metrics dict into a MarketMetricsResult.

    Returns None when market_metrics was never populated (empty dict) —
    matches the legacy ``if self.market_metrics:`` truthiness gate.
    """
    if not market_metrics:
        return None
    return MarketMetricsResult(**market_metrics)


def _level_ref(level: Optional[Dict[str, Any]], oi_key: str) -> Optional[LevelRef]:
    """Adapt a legacy {"strike": ..., "call_oi"|"put_oi": ...} dict into a LevelRef."""
    if not level:
        return None
    return LevelRef(strike=level["strike"], open_interest=level[oi_key])


def _to_expiration_analysis_result(analysis: Dict[str, Any]) -> ExpirationAnalysisResult:
    """
    Adapt the legacy ``analyze_expiration()`` dict shape into an
    ExpirationAnalysisResult. Used internally by ``analyze_expiration``
    (the method itself now returns the typed result directly, T10).
    """
    strike_data = analysis["strike_data"]
    strike_rows = tuple(
        StrikeOiRow(
            strike=strike,
            call_oi=strike_data[strike]["call_oi"],
            put_oi=strike_data[strike]["put_oi"],
            call_volume=strike_data[strike]["call_volume"],
            put_volume=strike_data[strike]["put_volume"],
        )
        for strike in sorted(strike_data.keys())
    )

    money = analysis["moneyness"]
    sr = analysis["support_resistance"]

    return ExpirationAnalysisResult(
        expiration=analysis["expiration"],
        underlying_price=analysis["underlying_price"],
        total_instruments=analysis["total_instruments"],
        call_count=analysis["call_count"],
        put_count=analysis["put_count"],
        strike_rows=strike_rows,
        max_pain=MaxPainResult(**analysis["max_pain"]),
        put_call_ratio=PutCallRatioResult(**analysis["put_call_ratio"]),
        volume_stats=VolumeStatsResult(**analysis["volume_stats"]),
        moneyness=MoneynessResult(
            calls=MoneynessLeg(**money["calls"]),
            puts=MoneynessLeg(**money["puts"]),
            totals=MoneynessLeg(**money["totals"]),
            oi_skew=money["oi_skew"],
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=tuple(
                _level_ref(level, "call_oi") for level in sr["resistance_levels"]
            ),
            support_levels=tuple(
                _level_ref(level, "put_oi") for level in sr["support_levels"]
            ),
            short_term_resistance=_level_ref(sr["short_term_resistance"], "call_oi"),
            short_term_support=_level_ref(sr["short_term_support"], "put_oi"),
        ),
    )


class OnChainMetricsCalculator:
    """
    Calculate on-chain analytics from option book summary data.

    Analyzes open interest distribution to calculate:
    - Max pain price per expiration
    - Put/Call ratios
    - Support and resistance levels
    - Open interest by strike
    - Moneyness (ITM/OTM) breakdown

    Pure calculation (refactor_design_spec.md section T10) -- GEX/DEX, flow,
    volatility-surface, market-wide, and report-rendering concerns all live
    in their own calculator/formatter modules now; this class only knows
    about the per-expiration book-summary numbers.
    """

    def __init__(self, data: List[Dict[str, Any]], currency: str):
        """
        Initialize calculator with book summary data.

        bugfix_spec.md Item 7: this used to auto-extract a single global
        "spot" price -- the ``underlying_price`` (a FUTURE, not the index)
        of whichever instrument in the WHOLE book happened to have the
        highest 24h volume -- and apply it everywhere (GEX/DEX, moneyness,
        max-pain distance, ...), regardless of expiry. That is wrong twice
        over: (1) ``underlying_price`` is a future, not the index, so it is
        the wrong basis for spot-anchored metrics (GEX/DEX, USD conversion);
        (2) even where a future price IS correct (moneyness, breakevens --
        strike-space math), using ONE expiry's future for every OTHER
        expiry ignores the futures basis, which the audit measured at up to
        +3.9% across expiries (GEX distortion up to +7.9%, since GEX scales
        with S²).

        Fix: two prices now, each anchored correctly --
        ``index_price`` (spot index, set explicitly via ``set_index_price``,
        no heuristic -- the service supplies it from
        ``DeribitApiService.get_index_price``) and
        ``forward_price_by_expiration`` (this expiry's own future price,
        picked per-expiry from ``data`` at construction time -- no network
        call needed, it's the same book-summary rows already passed in).
        See ``DeribitApiService.get_option_chain_snapshot``'s docstring for
        the authoritative index-vs-future rule this follows.

        Args:
            data: List of book summary items from Deribit API.
            currency: Currency symbol (ETH, BTC).
        """
        self.raw_data = data
        self.currency = currency
        self.index_price: float = 0.0
        self.forward_price_by_expiration: Dict[str, Optional[float]] = {}
        self.parsed_data: Dict[str, List[Dict]] = {}

        # Cross-phase state written directly by OnChainAnalysisService (no
        # setter methods -- these are real data dependencies between
        # pipeline phases, not report-text bookkeeping):
        self.enriched_instruments: Dict[str, List[Dict]] = {}  # Instruments with full Greeks/IV
        self.market_metrics: Dict[str, Any] = {}  # DVOL, funding rate, IV rank/percentile
        self._recent_trades: List[Dict[str, Any]] = []  # For block trade detection
        self._atm_ivs: Dict[str, float] = {}  # ATM IV per expiration (for term structure)
        # institutional_metrics_spec.md section 3 (Task C4): delta-
        # interpolated RR25/BF25 dict (VolatilitySurfaceCalculator.
        # calculate_risk_reversal_butterfly()'s return shape) per
        # expiration, populated during the same vol-surface phase that
        # fills _atm_ivs -- feeds the SKEW TERM STRUCTURE report section.
        self._skew_by_expiry: Dict[str, Dict[str, Any]] = {}

        if data:
            self.forward_price_by_expiration = self._extract_forward_prices(data)

        logger.info(f"Initialized OnChainMetricsCalculator with {len(data)} instruments")

    @property
    def underlying_price(self) -> float:
        """
        DEPRECATED (bugfix_spec.md Item 7): historically the single global
        "spot" price (actually a future's price, from the highest-volume
        instrument in the whole book). Kept for one release as a read-only
        alias for ``index_price`` so existing readers (``save_onchain_snapshot``
        callers, report code that has not yet migrated) keep working.

        Use ``index_price`` for spot-anchored metrics (GEX/DEX, USD
        conversion) or ``forward_price_by_expiration[expiration]`` for
        settlement-space metrics (moneyness, max-pain distance, breakevens).
        """
        warnings.warn(
            "OnChainMetricsCalculator.underlying_price is deprecated -- use "
            "index_price (spot-anchored metrics) or "
            "forward_price_by_expiration[expiration] (settlement-space "
            "metrics). bugfix_spec.md Item 7.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.index_price

    def set_index_price(self, index_price: float) -> None:
        """
        Set the spot index price (bugfix_spec.md Item 7).

        The service supplies this from ``DeribitApiService.get_index_price``
        -- no heuristic, no volume race. Anchors every spot-anchored metric
        (GEX/DEX, USD notional conversion).

        Args:
            index_price: Current spot index price (USD per unit currency).
        """
        self.index_price = index_price

    def _extract_forward_prices(self, data: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        """
        Per-expiry forward (future) price, picked from ``data`` directly (no
        network call -- this is the same book-summary rows already passed to
        the constructor).

        Groups by expiry parsed from ``instrument_name`` (mirrors
        ``parse_instruments``'s own parsing, done separately here because
        ``parse_instruments`` drops ``underlying_price`` from its parsed
        shape) and applies the shared ``select_forward_price`` pick --
        the same highest-volume-in-group logic
        ``DeribitApiService.get_option_chain_snapshot`` uses for its own
        ``futures_by_expiry`` (bugfix_spec.md F7.3.1: shared helper, not a
        third duplicate).

        Args:
            data: List of book summary items.

        Returns:
            Dict mapping expiry -> forward price. A group with no priced
            instrument at all maps to ``None`` (resolved to ``index_price``
            with a ``logger.warning`` at the point of use, once
            ``index_price`` is actually known -- see ``analyze_expiration``).
        """
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in data:
            parts = item.get("instrument_name", "").split("-")
            if len(parts) < 2:
                continue
            grouped.setdefault(parts[1], []).append(item)

        return {expiry: select_forward_price(items) for expiry, items in grouped.items()}

    def nearest_expiry_median_underlying_price(self) -> Optional[float]:
        """
        Fallback spot price when ``get_index_price`` fails (bugfix_spec.md
        Item 7 / 7.4 edge case): the median ``underlying_price`` across the
        NEAREST expiry's instruments -- the smallest-basis proxy available
        without the index -- never the old global highest-volume pick.

        Callers must ``logger.error`` when they use this (a fallback firing
        means the primary index-price fetch failed) -- this method only
        computes the value.

        Returns:
            The nearest expiry's median underlying_price, or ``None`` if no
            instrument in ``raw_data`` has a parseable expiry with a priced
            underlying_price at all (Wave H Task H-F, Fix 3: was ``0.0``,
            which is indistinguishable from a genuine $0 price once
            persisted -- a caller with NO real price must be able to tell
            "no price" apart from "priced at zero" and refuse to write a
            poisoned snapshot).
        """
        prices_by_expiry: Dict[str, List[float]] = {}
        for item in self.raw_data:
            parts = item.get("instrument_name", "").split("-")
            if len(parts) < 2:
                continue
            price = item.get("underlying_price")
            if not price:
                continue
            prices_by_expiry.setdefault(parts[1], []).append(price)

        if not prices_by_expiry:
            return None

        now_utc = datetime.now(timezone.utc)

        def _parse_expiry(expiry: str) -> Optional[datetime]:
            try:
                return datetime.strptime(expiry, "%d%b%y").replace(
                    hour=8, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
                )
            except ValueError:
                return None

        dated_expiries = [
            (expiry, _parse_expiry(expiry)) for expiry in prices_by_expiry
        ]
        dated_expiries = [(expiry, dt) for expiry, dt in dated_expiries if dt is not None]
        if not dated_expiries:
            return None

        nearest_expiry, _ = min(
            dated_expiries, key=lambda pair: abs((pair[1] - now_utc).total_seconds())
        )
        return float(statistics.median(prices_by_expiry[nearest_expiry]))

    def parse_instruments(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Parse instrument names and group by expiration.

        Instrument format: ETH-27DEC24-3000-C
        - Parts[0]: Currency (ETH)
        - Parts[1]: Expiration (27DEC24)
        - Parts[2]: Strike price (3000)
        - Parts[3]: Option type (C=Call, P=Put)

        Returns:
            Dict mapping expiration -> list of parsed instruments.
        """
        grouped: Dict[str, List[Dict]] = {}

        for item in self.raw_data:
            instrument_name = item.get("instrument_name", "")
            parts = instrument_name.split("-")

            if len(parts) < 4:
                logger.warning(f"Skipping invalid instrument: {instrument_name}")
                continue

            expiration = parts[1]
            try:
                strike = float(parts[2])
            except ValueError:
                logger.warning(f"Invalid strike price in: {instrument_name}")
                continue

            option_type = parts[3].upper()
            if option_type not in ("C", "P"):
                logger.warning(f"Invalid option type in: {instrument_name}")
                continue

            parsed_item = {
                "instrument_name": instrument_name,
                "expiration": expiration,
                "strike": strike,
                "option_type": option_type,
                "open_interest": item.get("open_interest", 0) or 0,
                "volume": item.get("volume", 0) or 0,
                "volume_usd": item.get("volume_usd", 0) or 0,
                "mark_price": item.get("mark_price", 0) or 0,
                "mark_iv": item.get("mark_iv"),
            }

            if expiration not in grouped:
                grouped[expiration] = []
            grouped[expiration].append(parsed_item)

        self.parsed_data = grouped
        logger.info(f"Parsed {len(grouped)} expirations")
        return grouped

    def group_by_strike(
        self, instruments: List[Dict[str, Any]]
    ) -> Dict[float, Dict[str, float]]:
        """
        Group instruments by strike price.

        Args:
            instruments: List of parsed instrument dicts for one expiration.

        Returns:
            Dict mapping strike -> {call_oi, put_oi, call_volume, put_volume}.
        """
        grouped: Dict[float, Dict[str, float]] = {}

        for item in instruments:
            strike = item["strike"]
            option_type = item["option_type"]
            oi = item["open_interest"]
            volume = item["volume"]

            if strike not in grouped:
                grouped[strike] = {
                    "call_oi": 0.0,
                    "put_oi": 0.0,
                    "call_volume": 0.0,
                    "put_volume": 0.0,
                }

            if option_type == "C":
                grouped[strike]["call_oi"] += oi
                grouped[strike]["call_volume"] += volume
            else:  # P
                grouped[strike]["put_oi"] += oi
                grouped[strike]["put_volume"] += volume

        return grouped

    def calculate_max_pain(
        self, strike_data: Dict[float, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Calculate max pain strike price.

        Max pain is the strike where option writers (sellers) pay the minimum
        to option buyers. It's where the most options expire worthless.

        Formula (Wave G task G2-F fix: this docstring previously had the
        call/put terms swapped relative to the code below -- the code was
        always correct; only the docstring was inverted):
        For each candidate settlement price S:
          - Call loss at strike K: max(0, S - K) * call_OI
          - Put loss at strike K: max(0, K - S) * put_OI
          - Total pain = sum over all strikes K of call + put losses
        Max Pain = S with minimum total pain

        Args:
            strike_data: Dict mapping strike -> {call_oi, put_oi}.

        Returns:
            Dict with max_pain_strike, pain_by_strike, and min_pain_value.
        """
        if not strike_data:
            return {
                "max_pain_strike": None,
                "pain_by_strike": {},
                "min_pain_value": 0,
            }

        strikes = sorted(strike_data.keys())
        pain_by_strike: Dict[float, float] = {}

        for candidate in strikes:
            total_pain = 0.0

            for strike, oi_data in strike_data.items():
                call_oi = oi_data["call_oi"]
                put_oi = oi_data["put_oi"]

                # Call intrinsic value if underlying settles at candidate
                # Calls are ITM when underlying > strike
                call_pain = max(0, candidate - strike) * call_oi

                # Put intrinsic value if underlying settles at candidate
                # Puts are ITM when underlying < strike
                put_pain = max(0, strike - candidate) * put_oi

                total_pain += call_pain + put_pain

            pain_by_strike[candidate] = total_pain

        max_pain_strike = min(pain_by_strike.keys(), key=lambda k: pain_by_strike[k])

        return {
            "max_pain_strike": max_pain_strike,
            "pain_by_strike": pain_by_strike,
            "min_pain_value": pain_by_strike[max_pain_strike],
        }

    def calculate_put_call_ratio(
        self, strike_data: Dict[float, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Calculate put/call ratio from open interest.

        Args:
            strike_data: Dict mapping strike -> {call_oi, put_oi}.

        Returns:
            Dict with total_call_oi, total_put_oi, ratio, and bias.
        """
        total_call_oi = sum(data["call_oi"] for data in strike_data.values())
        total_put_oi = sum(data["put_oi"] for data in strike_data.values())

        if total_call_oi > 0:
            ratio = total_put_oi / total_call_oi
        else:
            ratio = float("inf") if total_put_oi > 0 else 0

        # M4 (code_quality_review.md): shared interpreter, unifying this
        # method's vocabulary with VolatilitySurfaceCalculator's per-bucket
        # P/C ratio bias (refactor_design_spec.md T12 -- planned golden
        # delta).
        bias = interpret_put_call_ratio(ratio)

        return {
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "ratio": ratio,
            "bias": bias,
        }

    def calculate_volume_stats(
        self, strike_data: Dict[float, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Calculate volume statistics.

        Args:
            strike_data: Dict mapping strike -> {call_volume, put_volume, ...}.

        Returns:
            Dict with total volumes and volume ratio.
        """
        total_call_volume = sum(data["call_volume"] for data in strike_data.values())
        total_put_volume = sum(data["put_volume"] for data in strike_data.values())
        total_volume = total_call_volume + total_put_volume

        if total_call_volume > 0:
            volume_ratio = total_put_volume / total_call_volume
        else:
            volume_ratio = float("inf") if total_put_volume > 0 else 0

        return {
            "total_call_volume": total_call_volume,
            "total_put_volume": total_put_volume,
            "total_volume": total_volume,
            "volume_ratio": volume_ratio,
        }

    def analyze_moneyness(
        self,
        instruments: List[Dict[str, Any]],
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Analyze open interest by moneyness (ITM/OTM) with notional values.

        Matches Deribit's classification (no ATM category):
        - ITM Call: strike < current_price
        - OTM Call: strike >= current_price
        - ITM Put: strike > current_price
        - OTM Put: strike <= current_price

        Notional Value = OI × underlying_price

        Args:
            instruments: List of parsed instrument dicts.
            current_price: Current underlying price.

        Returns:
            Dict with OI and notional value breakdown by moneyness.
        """
        # Initialize counters for OI
        call_itm_oi = 0.0
        call_otm_oi = 0.0
        put_itm_oi = 0.0
        put_otm_oi = 0.0

        # Initialize counters for notional value
        call_itm_notional = 0.0
        call_otm_notional = 0.0
        put_itm_notional = 0.0
        put_otm_notional = 0.0

        for item in instruments:
            strike = item["strike"]
            option_type = item["option_type"]
            oi = item["open_interest"]
            # Notional value = OI × underlying price
            notional = oi * current_price

            if option_type == "C":
                if strike < current_price:  # ITM call
                    call_itm_oi += oi
                    call_itm_notional += notional
                else:  # OTM call (includes ATM)
                    call_otm_oi += oi
                    call_otm_notional += notional
            else:  # Put
                if strike > current_price:  # ITM put
                    put_itm_oi += oi
                    put_itm_notional += notional
                else:  # OTM put (includes ATM)
                    put_otm_oi += oi
                    put_otm_notional += notional

        # Calculate call totals
        total_call_oi = call_itm_oi + call_otm_oi
        total_call_notional = call_itm_notional + call_otm_notional

        # Calculate put totals
        total_put_oi = put_itm_oi + put_otm_oi
        total_put_notional = put_itm_notional + put_otm_notional

        # Calculate overall totals
        total_itm_oi = call_itm_oi + put_itm_oi
        total_otm_oi = call_otm_oi + put_otm_oi
        total_oi = total_itm_oi + total_otm_oi

        total_itm_notional = call_itm_notional + put_itm_notional
        total_otm_notional = call_otm_notional + put_otm_notional
        total_notional = total_itm_notional + total_otm_notional

        # Calculate percentages (based on notional value like Deribit)
        call_itm_pct = (call_itm_notional / total_call_notional * 100) if total_call_notional > 0 else 0
        call_otm_pct = (call_otm_notional / total_call_notional * 100) if total_call_notional > 0 else 0
        put_itm_pct = (put_itm_notional / total_put_notional * 100) if total_put_notional > 0 else 0
        put_otm_pct = (put_otm_notional / total_put_notional * 100) if total_put_notional > 0 else 0

        total_itm_pct = (total_itm_notional / total_notional * 100) if total_notional > 0 else 0
        total_otm_pct = (total_otm_notional / total_notional * 100) if total_notional > 0 else 0

        # Determine OI skew interpretation
        if total_otm_pct > OI_SKEW_OTM_HEAVY_THRESHOLD_PCT:
            oi_skew = "Heavy OTM (Speculative)"
        elif total_itm_pct > OI_SKEW_ITM_HEAVY_THRESHOLD_PCT:
            oi_skew = "Heavy ITM (Hedging)"
        else:
            oi_skew = "Balanced"

        return {
            "calls": {
                "itm_oi": call_itm_oi,
                "otm_oi": call_otm_oi,
                "total_oi": total_call_oi,
                "itm_notional": call_itm_notional,
                "otm_notional": call_otm_notional,
                "total_notional": total_call_notional,
                "itm_pct": call_itm_pct,
                "otm_pct": call_otm_pct,
            },
            "puts": {
                "itm_oi": put_itm_oi,
                "otm_oi": put_otm_oi,
                "total_oi": total_put_oi,
                "itm_notional": put_itm_notional,
                "otm_notional": put_otm_notional,
                "total_notional": total_put_notional,
                "itm_pct": put_itm_pct,
                "otm_pct": put_otm_pct,
            },
            "totals": {
                "itm_oi": total_itm_oi,
                "otm_oi": total_otm_oi,
                "total_oi": total_oi,
                "itm_notional": total_itm_notional,
                "otm_notional": total_otm_notional,
                "total_notional": total_notional,
                "itm_pct": total_itm_pct,
                "otm_pct": total_otm_pct,
            },
            "oi_skew": oi_skew,
        }

    def find_support_resistance(
        self,
        strike_data: Dict[float, Dict[str, float]],
        current_price: float,
        top_n: int = 3,
    ) -> Dict[str, Any]:
        """
        Find support and resistance levels based on open interest.

        - Resistance: Strikes with highest Call OI (price magnets above)
        - Support: Strikes with highest Put OI (price magnets below)
        - Short-term: Nearest high-OI strikes to current price

        Args:
            strike_data: Dict mapping strike -> {call_oi, put_oi}.
            current_price: Current underlying price.
            top_n: Number of top levels to return.

        Returns:
            Dict with resistance_levels, support_levels, and short_term_levels.
        """
        if not strike_data:
            return {
                "resistance_levels": [],
                "support_levels": [],
                "short_term_resistance": None,
                "short_term_support": None,
            }

        # Sort by Call OI for resistance (descending)
        call_oi_sorted = sorted(
            [(strike, data["call_oi"]) for strike, data in strike_data.items()],
            key=lambda x: x[1],
            reverse=True,
        )
        resistance_levels = [
            {"strike": strike, "call_oi": oi}
            for strike, oi in call_oi_sorted[:top_n]
            if oi > 0
        ]

        # Sort by Put OI for support (descending)
        put_oi_sorted = sorted(
            [(strike, data["put_oi"]) for strike, data in strike_data.items()],
            key=lambda x: x[1],
            reverse=True,
        )
        support_levels = [
            {"strike": strike, "put_oi": oi}
            for strike, oi in put_oi_sorted[:top_n]
            if oi > 0
        ]

        # Find nearest high-OI strikes to current price
        # Short-term resistance: nearest strike above current price with significant call OI
        strikes_above = [
            (strike, data["call_oi"])
            for strike, data in strike_data.items()
            if strike > current_price and data["call_oi"] > 0
        ]
        if strikes_above:
            # Sort by proximity to current price, then by OI
            strikes_above.sort(key=lambda x: (x[0] - current_price, -x[1]))
            short_term_resistance = {
                "strike": strikes_above[0][0],
                "call_oi": strikes_above[0][1],
            }
        else:
            short_term_resistance = None

        # Short-term support: nearest strike below current price with significant put OI
        strikes_below = [
            (strike, data["put_oi"])
            for strike, data in strike_data.items()
            if strike < current_price and data["put_oi"] > 0
        ]
        if strikes_below:
            # Sort by proximity to current price (descending), then by OI
            strikes_below.sort(key=lambda x: (current_price - x[0], -x[1]))
            short_term_support = {
                "strike": strikes_below[0][0],
                "put_oi": strikes_below[0][1],
            }
        else:
            short_term_support = None

        return {
            "resistance_levels": resistance_levels,
            "support_levels": support_levels,
            "short_term_resistance": short_term_resistance,
            "short_term_support": short_term_support,
        }

    def analyze_expiration(self, expiration: str) -> Optional[ExpirationAnalysisResult]:
        """
        Perform full analysis for a single expiration.

        refactor_design_spec.md section T10 (compat map row #8): returns
        the typed ``ExpirationAnalysisResult`` directly now (previously a
        plain dict; the adapter that used to sit in the caller,
        ``_to_expiration_analysis_result``, now lives inside this method).
        Every caller (``OnChainAnalysisService``, ``ProspectiveCollector``)
        updated in the same commit.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            The expiration's typed analysis result, or None if the
            expiration is not in the parsed data (matches the legacy
            ``return {}`` "not found" case's falsy/absent semantics).
        """
        if expiration not in self.parsed_data:
            logger.warning(f"Expiration {expiration} not found in data")
            return None

        instruments = self.parsed_data[expiration]
        strike_data = self.group_by_strike(instruments)

        # Count calls and puts
        call_count = sum(1 for i in instruments if i["option_type"] == "C")
        put_count = sum(1 for i in instruments if i["option_type"] == "P")

        # bugfix_spec.md Item 7 / 7.2 anchor table: moneyness, max-pain
        # distance, and support/resistance are settlement-space (strike vs.
        # where THIS expiry's contract settles) -- the per-expiry forward is
        # the correct anchor, not the global index. Fall back to the index
        # (with a warning) only for the rare expiry with no priced
        # instrument at all (edge case 7.4).
        forward_price = self.forward_price_by_expiration.get(expiration)
        if forward_price is None:
            logger.warning(
                f"No forward price for expiration {expiration} -- falling "
                f"back to index price ({self.index_price})"
            )
            forward_price = self.index_price

        # Calculate analytics
        max_pain = self.calculate_max_pain(strike_data)
        put_call_ratio = self.calculate_put_call_ratio(strike_data)
        volume_stats = self.calculate_volume_stats(strike_data)
        moneyness = self.analyze_moneyness(instruments, forward_price)
        support_resistance = self.find_support_resistance(
            strike_data, forward_price
        )

        analysis = {
            "expiration": expiration,
            "underlying_price": forward_price,
            "total_instruments": len(instruments),
            "call_count": call_count,
            "put_count": put_count,
            "strike_data": strike_data,
            "max_pain": max_pain,
            "put_call_ratio": put_call_ratio,
            "volume_stats": volume_stats,
            "moneyness": moneyness,
            "support_resistance": support_resistance,
        }
        return _to_expiration_analysis_result(analysis)

    def get_expirations(self) -> List[str]:
        """
        Get list of available expirations.

        Returns:
            Sorted list of expiration date strings.
        """
        if not self.parsed_data:
            self.parse_instruments()
        return sorted(self.parsed_data.keys())


# T10 design choice (refactor_design_spec.md): back-compat alias for any
# caller that still imports the pre-T10 name. ProspectiveCollector (the
# production daemon) is the highest-risk consumer of this class -- this
# alias means an import site that was never updated still works.
#
# CHANGELOG / removal candidate (carried finding #5, A6 review; re-verified
# at task A7): this alias is NOT yet dead code. As of task A7, two in-repo
# call sites still import it directly by the old name --
# tests/unit/test_on_chain_analysis_service_flow.py::
# TestCalculateBuySellFlowSingleFetch._make_analyzer and
# scripts/record_onchain_fixture.py (the golden-master fixture recorder) --
# so a same-task removal is not free even ignoring any out-of-tree caller.
# `analyze_expiration`'s dict->typed-result flip (T10) is the one real
# behavior change that could hide behind this alias for an out-of-tree
# caller that never migrated. Flagged for removal in a future cleanup
# task, once the two in-repo call sites above are migrated to
# OnChainMetricsCalculator and the removal is treated as the
# breaking-change decision it is -- not bundled into T12's janitorial
# scope.
OnChainAnalyzer = OnChainMetricsCalculator
