"""
On-chain analytics for options market data.

Calculates max pain, put/call ratios, support/resistance levels,
and generates formatted text reports per expiration.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.reporting.report_formatter import (
    ExpirationRenderInput,
    OnChainReportFormatter,
)
from coding.core.analytics.results.analysis_result import MarketMetricsResult, TrendSnapshot
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

    Returns None when set_market_metrics() was never called (empty dict) —
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
    Adapt the legacy ``analyze_expiration()`` dict into an
    ExpirationAnalysisResult. Temporary adapter (refactor_design_spec.md
    section T3) — analyze_expiration() itself keeps returning the legacy
    dict shape until a later task wires the calculators to produce typed
    results directly.
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


class OnChainAnalyzer:
    """
    Calculate on-chain analytics from option book summary data.

    Analyzes open interest distribution to calculate:
    - Max pain price per expiration
    - Put/Call ratios
    - Support and resistance levels
    - Open interest by strike
    - GEX/DEX exposure (when Greeks data is provided)
    """

    def __init__(self, data: List[Dict[str, Any]], currency: str):
        """
        Initialize analyzer with book summary data.

        Args:
            data: List of book summary items from Deribit API.
            currency: Currency symbol (ETH, BTC).
        """
        self.raw_data = data
        self.currency = currency
        self.underlying_price: float = 0.0
        self.parsed_data: Dict[str, List[Dict]] = {}
        self.gex_dex_data: Dict[str, str] = {}  # Stores GEX/DEX report per expiration
        self.buy_sell_flow_data: Dict[str, str] = {}  # Stores buy/sell flow report per expiration
        self.buy_sell_flow_charts: Dict[str, Dict[str, str]] = {}  # Stores chart paths per expiration
        self.market_metrics: Dict[str, Any] = {}  # Stores DVOL, funding rate, etc.
        self.enriched_instruments: Dict[str, List[Dict]] = {}  # Instruments with full Greeks/IV
        self.volatility_surface_data: Dict[str, str] = {}  # Vol surface report per expiration
        self.oi_changes_data: Dict[str, str] = {}  # OI changes report per expiration
        self.market_wide_sections: Dict[str, str] = {}  # Market-wide report sections
        self.gex_dex_structured: Dict[str, Dict] = {}           # Raw GEX/DEX data per expiry
        self.buy_sell_flow_structured: Dict[str, Dict] = {}     # Raw flow data per expiry
        self.volatility_surface_structured: Dict[str, Dict] = {}  # Raw vol surface per expiry
        self.market_wide_structured: Dict[str, Any] = {}        # Raw market-wide metrics
        self.trend_data: Dict[str, Optional[Dict]] = {}         # Previous DB snapshot per expiry
        self._recent_trades: List[Dict[str, Any]] = []          # Recent trades for block trade detection
        self._atm_ivs: Dict[str, float] = {}                    # ATM IV per expiration (for term structure)

        # Extract underlying price using most common value (mode)
        # Different instruments may have slightly different underlying_price values
        # depending on when their data was last updated. The mode gives us
        # the most current price since most instruments share it.
        if data:
            self.underlying_price = self._extract_underlying_price(data)

        logger.info(f"Initialized OnChainAnalyzer with {len(data)} instruments")

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

        # Determine bias
        if ratio < 0.7:
            bias = "Strong Bullish"
        elif ratio < 1.0:
            bias = "Bullish"
        elif ratio == 1.0:
            bias = "Neutral"
        elif ratio < 1.3:
            bias = "Bearish"
        else:
            bias = "Strong Bearish"

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
        if total_otm_pct > 70:
            oi_skew = "Heavy OTM (Speculative)"
        elif total_itm_pct > 40:
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

    def analyze_expiration(self, expiration: str) -> Dict[str, Any]:
        """
        Perform full analysis for a single expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").

        Returns:
            Dict with all analysis results for this expiration.
        """
        if expiration not in self.parsed_data:
            logger.warning(f"Expiration {expiration} not found in data")
            return {}

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

        return {
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

    def generate_report(self) -> str:
        """
        Generate a formatted text report for all expirations.

        Pure delegator (refactor_design_spec.md section T3): builds a
        temporary ExpirationRenderInput per expiration (adapting this
        analyzer's own dicts into the typed ExpirationAnalysisResult /
        TrendSnapshot models, plus whatever pre-rendered extra section text
        the other calculators already produced) and hands everything to
        OnChainReportFormatter, which owns all the actual text formatting.

        Returns:
            Formatted text report string.
        """
        if not self.parsed_data:
            self.parse_instruments()

        generated_at = datetime.now()
        market_metrics = _to_market_metrics(self.market_metrics)

        render_inputs = []
        for expiration in sorted(self.parsed_data.keys()):
            analysis = self.analyze_expiration(expiration)
            if not analysis:
                continue

            render_inputs.append(
                ExpirationRenderInput(
                    expiration=expiration,
                    analysis=_to_expiration_analysis_result(analysis),
                    trend=_to_trend_snapshot(self.trend_data.get(expiration)),
                    extra_sections=tuple(self._raw_extra_sections(expiration)),
                    evidence_line=self._build_evidence_line(expiration),
                )
            )

        return OnChainReportFormatter().render_full(
            currency=self.currency,
            underlying_price=self.underlying_price,
            generated_at=generated_at,
            market_metrics=market_metrics,
            expirations=tuple(render_inputs),
            market_wide_sections=self.market_wide_sections,
        )

    def _raw_extra_sections(self, expiration: str) -> List[str]:
        """
        Collect this expiration's already-formatted GEX/DEX, buy/sell flow,
        volatility surface, and OI-changes text, in that fixed order —
        matches the legacy inline appends in generate_report(). These
        calculators still produce plain text (not typed results) until
        T4/T5/T8, so generate_report() passes their output through verbatim.
        """
        texts = []
        if expiration in self.gex_dex_data:
            texts.append(self.gex_dex_data[expiration])
        if expiration in self.buy_sell_flow_data:
            texts.append(self.buy_sell_flow_data[expiration])
        if expiration in self.volatility_surface_data:
            texts.append(self.volatility_surface_data[expiration])
        if expiration in self.oi_changes_data:
            texts.append(self.oi_changes_data[expiration])
        return texts

    def _build_evidence_line(self, expiration: str) -> Optional[str]:
        """
        bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review): propagate
        the flow data-sufficiency gate to the report's directional
        conclusions. The complaint that "the surrounding report still
        asserts PCR bias and GEX environment labels around an empty flow
        section" is fixed here, at the report level — PCR is an OI metric
        and does not need trades, so it is not silenced; instead the
        per-expiration header carries an explicit evidence caveat.

        Returns None when there is no flow bookkeeping at all for this
        expiration (e.g. the repository was unavailable and the whole flow
        phase was skipped) — the header omits the line entirely rather than
        guessing.
        """
        flow_data = self.buy_sell_flow_structured.get(expiration)
        if not flow_data:
            return "EVIDENCE: OI/GEX from full book | Flow: NOT ANALYZED"

        sufficient = flow_data.get("sufficient_data")
        if sufficient is None:
            # Pre-fix bookkeeping absent (flow_result_dict without the
            # sufficient_data key) — omit rather than assert a status we
            # cannot actually verify.
            return None

        trade_count = flow_data.get("trade_count", 0)
        lookback_hours = flow_data.get("lookback_hours", 24)
        status = "OK" if sufficient else "INSUFFICIENT"
        return (
            f"EVIDENCE: OI/GEX from full book | "
            f"Flow: {status} ({trade_count} trades in {lookback_hours:.0f}h)"
        )

    def get_expirations(self) -> List[str]:
        """
        Get list of available expirations.

        Returns:
            Sorted list of expiration date strings.
        """
        if not self.parsed_data:
            self.parse_instruments()
        return sorted(self.parsed_data.keys())

    def set_gex_dex_data(self, expiration: str, report_text: str) -> None:
        """
        Store GEX/DEX report text for an expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").
            report_text: Formatted GEX/DEX report section text.
        """
        self.gex_dex_data[expiration] = report_text

    def set_buy_sell_flow_data(self, expiration: str, report_text: str) -> None:
        """
        Store buy/sell flow report text for an expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").
            report_text: Formatted buy/sell flow report section text.
        """
        self.buy_sell_flow_data[expiration] = report_text

    def set_buy_sell_flow_charts(self, expiration: str, chart_paths: Dict[str, str]) -> None:
        """
        Store buy/sell flow chart paths for an expiration.

        Args:
            expiration: Expiration date string (e.g., "27DEC24").
            chart_paths: Dict with keys: distribution, net_flow, trend (values are file paths).
        """
        self.buy_sell_flow_charts[expiration] = chart_paths

    def set_volatility_surface_data(self, expiration: str, report_text: str) -> None:
        """Store volatility surface report text for an expiration."""
        self.volatility_surface_data[expiration] = report_text

    def set_oi_changes_data(self, expiration: str, report_text: str) -> None:
        """Store OI changes report text for an expiration."""
        self.oi_changes_data[expiration] = report_text

    def set_market_wide_section(self, section_name: str, report_text: str) -> None:
        """Store a market-wide report section."""
        self.market_wide_sections[section_name] = report_text

    def set_gex_dex_structured(self, expiration: str, data: Dict) -> None:
        """Store raw GEX/DEX structured data for an expiration."""
        self.gex_dex_structured[expiration] = data

    def set_buy_sell_flow_structured(self, expiration: str, data: Dict) -> None:
        """Store raw buy/sell flow structured data for an expiration."""
        self.buy_sell_flow_structured[expiration] = data

    def set_volatility_surface_structured(self, expiration: str, data: Dict) -> None:
        """Store raw volatility surface structured data for an expiration."""
        self.volatility_surface_structured[expiration] = data

    def set_market_wide_structured(self, data: Dict) -> None:
        """Store raw market-wide structured metrics."""
        self.market_wide_structured = data

    def set_recent_trades(self, trades: List[Dict[str, Any]]) -> None:
        """
        Store recent trades fetched from API for block trade detection.

        Args:
            trades: List of recent trade dicts from get_last_trades_by_currency.
        """
        self._recent_trades = trades

    def set_atm_iv(self, expiration: str, atm_iv: float) -> None:
        """
        Store ATM IV for a specific expiration (used for term structure in market-wide analysis).

        Args:
            expiration: Expiration string (e.g. '27MAR26').
            atm_iv: ATM implied volatility as a percentage (e.g. 55.3).
        """
        self._atm_ivs[expiration] = atm_iv

    def set_trend_data(self, expiration: str, data: Optional[Dict]) -> None:
        """
        Store previous DB snapshot for trend comparison in report.

        Args:
            expiration: Expiration string (e.g. '10MAR26').
            data: Dict with prev values, or None if no prior record.
                  Keys: max_pain_strike, call_oi, put_oi, pc_ratio,
                        total_volume, volume_ratio.
        """
        self.trend_data[expiration] = data

    def _format_trend(
        self, current: float, previous: Optional[float], is_ratio: bool = False
    ) -> str:
        """
        Format trend vs previous value.

        Args:
            current: Current value.
            previous: Previous value, or None if unavailable.
            is_ratio: If True, format as ratio (2 decimal places); otherwise as integer.

        Returns:
            Formatted trend string, or empty string if no previous value.
        """
        if previous is None:
            return ""
        delta = current - previous
        if delta == 0:
            return "  [→ unchanged]"
        arrow = "↑" if delta > 0 else "↓"
        if is_ratio:
            return f"  [{arrow} from {previous:.2f}, {delta:+.2f}]"
        return f"  [{arrow} from {previous:,.0f}, {delta:+,.0f}]"

    def set_market_metrics(
        self,
        dvol: Optional[float] = None,
        iv_percentile: Optional[float] = None,
        current_funding: Optional[float] = None,
        funding_8h: Optional[float] = None,
        iv_rank: Optional[float] = None,
    ) -> None:
        """
        Store market-wide metrics (DVOL, funding rate, IV rank).

        These metrics are currency-wide, not from the book summary data.

        Args:
            dvol: Current DVOL (Deribit Volatility Index) value.
            iv_percentile: IV percentile based on past 365 days.
            current_funding: Current funding rate from perpetual.
            funding_8h: 8-hour funding rate from perpetual.
            iv_rank: IV rank over 52 weeks (0-100 scale).
        """
        self.market_metrics = {
            "dvol": dvol,
            "iv_percentile": iv_percentile,
            "current_funding": current_funding,
            "funding_8h": funding_8h,
            "iv_rank": iv_rank,
        }
