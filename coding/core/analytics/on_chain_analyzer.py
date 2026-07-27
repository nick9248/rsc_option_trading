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
from typing import Any, Dict, List, Optional

from coding.core.analytics.results.analysis_result import MarketMetricsResult, TrendSnapshot
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


def _to_trend_snapshot(prev: Optional[Dict[str, Any]]) -> Optional[TrendSnapshot]:
    """Adapt a legacy trend_data dict entry into a TrendSnapshot.

    Returns None when there is no prior record (None or empty dict) —
    matches the legacy ``if prev:`` truthiness gate. Missing keys map to
    None fields, identical to the legacy dict's ``.get(key)`` behavior.
    """
    if not prev:
        return None
    return TrendSnapshot(
        max_pain_strike=prev.get("max_pain_strike"),
        call_oi=prev.get("call_oi"),
        put_oi=prev.get("put_oi"),
        pc_ratio=prev.get("pc_ratio"),
        total_volume=prev.get("total_volume"),
        volume_ratio=prev.get("volume_ratio"),
    )


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

        Args:
            data: List of book summary items from Deribit API.
            currency: Currency symbol (ETH, BTC).
        """
        self.raw_data = data
        self.currency = currency
        self.underlying_price: float = 0.0
        self.parsed_data: Dict[str, List[Dict]] = {}

        # Cross-phase state written directly by OnChainAnalysisService (no
        # setter methods -- these are real data dependencies between
        # pipeline phases, not report-text bookkeeping):
        self.enriched_instruments: Dict[str, List[Dict]] = {}  # Instruments with full Greeks/IV
        self.market_metrics: Dict[str, Any] = {}  # DVOL, funding rate, IV rank/percentile
        self._recent_trades: List[Dict[str, Any]] = []  # For block trade detection
        self._atm_ivs: Dict[str, float] = {}  # ATM IV per expiration (for term structure)

        # Extract underlying price using most common value (mode)
        # Different instruments may have slightly different underlying_price values
        # depending on when their data was last updated. The mode gives us
        # the most current price since most instruments share it.
        if data:
            self.underlying_price = self._extract_underlying_price(data)

        logger.info(f"Initialized OnChainMetricsCalculator with {len(data)} instruments")

    def _extract_underlying_price(self, data: List[Dict[str, Any]]) -> float:
        """
        Extract the most accurate underlying price from data.

        Uses the underlying_price from the highest volume instrument,
        as actively traded instruments have the most recently updated
        price data. The book_summary endpoint caches underlying_price
        per instrument, so stale instruments may have outdated values.

        Args:
            data: List of book summary items.

        Returns:
            Underlying price from highest volume instrument, or 0 if none found.
        """
        # Filter to instruments with volume and valid price
        active_instruments = [
            item for item in data
            if (item.get("volume") or 0) > 0 and item.get("underlying_price")
        ]

        if not active_instruments:
            # Fallback: use any instrument with a price
            for item in data:
                if item.get("underlying_price"):
                    return item.get("underlying_price")
            return 0.0

        # Get the instrument with highest volume (most recently active)
        highest_volume_item = max(active_instruments, key=lambda x: x.get("volume", 0))
        price = highest_volume_item.get("underlying_price", 0)

        logger.debug(
            f"Underlying price: {price} "
            f"(from {highest_volume_item.get('instrument_name')} "
            f"with volume {highest_volume_item.get('volume')})"
        )

        return price

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

        Formula:
        For each candidate strike K:
          - Call loss at strike S: max(0, K - S) * call_OI
          - Put loss at strike S: max(0, S - K) * put_OI
          - Total pain = sum of all call + put losses
        Max Pain = K with minimum total pain

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

        # Calculate analytics
        max_pain = self.calculate_max_pain(strike_data)
        put_call_ratio = self.calculate_put_call_ratio(strike_data)
        volume_stats = self.calculate_volume_stats(strike_data)
        moneyness = self.analyze_moneyness(instruments, self.underlying_price)
        support_resistance = self.find_support_resistance(
            strike_data, self.underlying_price
        )

        analysis = {
            "expiration": expiration,
            "underlying_price": self.underlying_price,
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
