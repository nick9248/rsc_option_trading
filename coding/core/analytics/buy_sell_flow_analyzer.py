"""
Buy/Sell Flow Analyzer for options trading.

Analyzes trade direction (buy/sell aggressor side) to identify conviction signals,
large trade activity, and regime changes. Complements OI-based metrics by showing
the direction of recent market activity.

Metrics:
- Per-strike buy/sell volume, notional, and counts
- Net flow and buy/sell ratios
- Multi-window trend detection (1h, 4h, full window)
- Top strikes by buying/selling pressure

Core purity (refactor_design_spec.md T5): this analyzer takes already-fetched
trades and an explicit time window — it does NOT import DatabaseRepository or
query the database. The caller (service layer) fetches once and injects the
trades and window; this closes bugfix_spec.md Item 6a (the analyzer's own
calculate() + generate_report_section() used to each issue their own DB query
with independently-computed "now" instants, so the stored and reported flow
data were provably drawn from two different windows).
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List

from coding.core.analytics.results.flow_results import (
    FlowResult,
    FlowTotals,
    StrikeFlowEntry,
    TopStrikeEntry,
)
from coding.core.analytics.thresholds import (
    FLOW_BIAS_BALANCED_LOW_THRESHOLD,
    FLOW_BIAS_HEAVY_THRESHOLD,
    FLOW_BIAS_MODERATE_THRESHOLD,
    FLOW_BIAS_SELLING_THRESHOLD,
    FLOW_TREND_ACCELERATION_FACTOR,
    FLOW_TREND_CONFIRMATION_FACTOR,
    FLOW_TREND_DECELERATION_FACTOR,
)

logger = logging.getLogger(__name__)

# bugfix_spec.md Item 6 / Decision D5 — two-tier data-sufficiency QUALITY gate
# (not a single suppress-everything-below-N gate: a thin-but-nonzero sample is
# shown with a confidence caveat, not hidden).
MINIMUM_TRADES_FOR_SECTION = 5
"""Below this trade count, the entire flow section is suppressed and replaced
with an explicit "insufficient flow data" message (Decision D5). Calibrated
2026-07-25 against 30d of production (currency, expiration, hour) windows:
<5-trade windows are ~1.9-2.0% of cases (bugfix_spec.md Item 6b confirmation
evidence) — the audit's original "26% zero-flow" claim was stale (0.0% since
April) and is not the basis for this threshold; it is a deliberately small
floor that only suppresses genuinely uninformative samples."""

MINIMUM_TRADES_FOR_CONFIDENCE = 20
"""Below this trade count (but >= MINIMUM_TRADES_FOR_SECTION), the section
renders normally but the bias/trend labels carry a LOW CONFIDENCE tag
(Decision D5) rather than being suppressed outright. Calibrated 2026-07-25:
a 20-trade floor gates 5.5% of (expiration, hour) cases over 30d of
production data (bugfix_spec.md Item 6, F6.3.2)."""

MINIMUM_TRADES_1H_FOR_TREND = 5
"""Minimum trades within the 1-hour sub-window before a trend label is
emitted. The trend calculation slices the sample further than the overall
bias (1h/4h/full-window comparison), so it needs its own, stricter floor —
independent of MINIMUM_TRADES_FOR_CONFIDENCE (bugfix_spec.md Item 6, F6.3.2)."""

INSUFFICIENT_DATA_LABEL = "Insufficient flow data"
LOW_CONFIDENCE_SUFFIX = " (LOW CONFIDENCE)"


class BuySellFlowAnalyzer:
    """
    Analyze buy/sell flow from trade direction data.

    Uses actual trade direction to identify directional conviction,
    detect regime changes, and track large participant activity.

    Trades and the analysis window are injected by the caller (single fetch,
    single source of truth) — this class does no I/O.
    """

    def __init__(
        self,
        trades: List[Dict[str, Any]],
        currency: str,
        expiration: str,
        spot_price: float,
        window_start_ms: int,
        window_end_ms: int,
    ):
        """
        Initialize buy/sell flow analyzer.

        Args:
            trades: Trade records already fetched by the caller for
                [window_start_ms, window_end_ms). Each dict shape matches
                DatabaseRepository.get_trades_for_flow_analysis's rows.
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "27MAR26").
            spot_price: Current underlying spot price.
            window_start_ms: Start of the analysis window (epoch ms).
            window_end_ms: End of the analysis window (epoch ms). Also used
                as the trend detector's "now" reference, so trend windows
                (1h/4h) anchor on the same instant the caller fetched
                against — not a fresh, independently-computed "now".
        """
        self.trades = trades
        self.currency = currency
        self.expiration = expiration
        self.spot_price = spot_price
        self.window_start_ms = window_start_ms
        self.window_end_ms = window_end_ms

        # Per-strike flow data: {strike: {option_type: {buy_count, sell_count, ...}}}
        self.flow_data: Dict[float, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {
                "buy_count": 0.0,
                "sell_count": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            })
        )

        # Expiration-level aggregates
        self.expiration_totals = {
            "call_buy_volume": 0.0,
            "call_sell_volume": 0.0,
            "put_buy_volume": 0.0,
            "put_sell_volume": 0.0,
        }

    @property
    def lookback_hours(self) -> float:
        """Window length in hours, derived from the injected window (not
        assumed to be 24h — fixes M6's hardcoded divisors)."""
        return (self.window_end_ms - self.window_start_ms) / 3_600_000.0

    def calculate(self) -> FlowResult:
        """
        Calculate all buy/sell flow metrics from the injected trades.

        Idempotent: repeated calls reset flow_data/expiration_totals before
        recomputing, so calling calculate() more than once on the same
        instance yields identical results (mirrors the GEX/DEX fix,
        bugfix_spec.md Item 1).

        Returns:
            FlowResult (coding/core/analytics/results/flow_results.py).
            Call ``.to_dict()`` for the legacy dict shape.
        """
        self.flow_data = defaultdict(
            lambda: defaultdict(lambda: {
                "buy_count": 0.0,
                "sell_count": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
            })
        )
        self.expiration_totals = {
            "call_buy_volume": 0.0,
            "call_sell_volume": 0.0,
            "put_buy_volume": 0.0,
            "put_sell_volume": 0.0,
        }

        trades = self.trades
        trade_count = len(trades)

        if trade_count == 0:
            logger.warning(f"No trades found for {self.currency} {self.expiration}")
            return self._empty_result()

        for trade in trades:
            self._process_trade(trade)

        self._calculate_derived_metrics()

        flow_trend = self._detect_flow_trend(trades)
        top_buy_strikes = self._find_top_strikes_by_buying()
        top_sell_strikes = self._find_top_strikes_by_selling()

        sufficient_data = trade_count >= MINIMUM_TRADES_FOR_SECTION
        low_confidence = sufficient_data and trade_count < MINIMUM_TRADES_FOR_CONFIDENCE
        bias_interpretation = self._interpret_flow_bias() if sufficient_data else INSUFFICIENT_DATA_LABEL

        return self._build_result(
            bias_interpretation=bias_interpretation,
            flow_trend=flow_trend,
            top_buy_strikes=top_buy_strikes,
            top_sell_strikes=top_sell_strikes,
            trade_count=trade_count,
            sufficient_data=sufficient_data,
            low_confidence=low_confidence,
        )

    def _build_result(
        self,
        bias_interpretation: str,
        flow_trend: str,
        top_buy_strikes: List[Dict[str, Any]],
        top_sell_strikes: List[Dict[str, Any]],
        trade_count: int,
        sufficient_data: bool,
        low_confidence: bool,
    ) -> FlowResult:
        """Wrap the dict-based working state (self.flow_data, self.expiration_totals) into FlowResult."""
        flow_data: Dict[float, Dict[str, StrikeFlowEntry]] = {
            strike: {
                option_type: StrikeFlowEntry(
                    buy_count=data["buy_count"],
                    sell_count=data["sell_count"],
                    buy_volume=data["buy_volume"],
                    sell_volume=data["sell_volume"],
                    buy_notional=data["buy_notional"],
                    sell_notional=data["sell_notional"],
                    net_flow=data["net_flow"],
                    buy_sell_ratio=data["buy_sell_ratio"],
                )
                for option_type, data in by_type.items()
            }
            for strike, by_type in self.flow_data.items()
        }

        return FlowResult(
            flow_data=flow_data,
            expiration_totals=FlowTotals(**self.expiration_totals),
            bias_interpretation=bias_interpretation,
            flow_trend=flow_trend,
            top_buy_strikes=tuple(
                TopStrikeEntry(
                    strike=e["strike"], option_type=e["option_type"], net_flow=e["net_flow"],
                    volume=e["buy_volume"], notional=e["buy_notional"],
                )
                for e in top_buy_strikes
            ),
            top_sell_strikes=tuple(
                TopStrikeEntry(
                    strike=e["strike"], option_type=e["option_type"], net_flow=e["net_flow"],
                    volume=e["sell_volume"], notional=e["sell_notional"],
                )
                for e in top_sell_strikes
            ),
            trade_count=trade_count,
            spot_price=self.spot_price,
            window_start_ms=self.window_start_ms,
            window_end_ms=self.window_end_ms,
            lookback_hours=self.lookback_hours,
            sufficient_data=sufficient_data,
            low_confidence=low_confidence,
        )

    def _process_trade(self, trade: Dict[str, Any]) -> None:
        """
        Process a single trade and update flow data.

        Args:
            trade: Trade dictionary from database.
        """
        # Convert Decimal types from database to float
        strike = float(trade["strike"]) if trade["strike"] is not None else 0.0
        option_type = trade["option_type"]  # "C" or "P"
        amount = float(trade["amount"]) if trade["amount"] is not None else 0.0
        direction = trade["direction"]  # "buy" or "sell"
        index_price = float(trade.get("index_price")) if trade.get("index_price") is not None else self.spot_price

        # Calculate notional value
        notional = amount * index_price

        # Get or initialize strike data
        strike_data = self.flow_data[strike][option_type]

        # Update counts and volumes based on direction
        if direction == "buy":
            strike_data["buy_count"] += 1
            strike_data["buy_volume"] += amount
            strike_data["buy_notional"] += notional

            # Update expiration totals
            if option_type == "C":
                self.expiration_totals["call_buy_volume"] += amount
            else:
                self.expiration_totals["put_buy_volume"] += amount
        else:  # direction == "sell"
            strike_data["sell_count"] += 1
            strike_data["sell_volume"] += amount
            strike_data["sell_notional"] += notional

            # Update expiration totals
            if option_type == "C":
                self.expiration_totals["call_sell_volume"] += amount
            else:
                self.expiration_totals["put_sell_volume"] += amount

    def _calculate_derived_metrics(self) -> None:
        """
        Calculate derived metrics (net flow, buy/sell ratio) for each strike.
        """
        for strike, option_types in self.flow_data.items():
            for option_type, data in option_types.items():
                buy_vol = data["buy_volume"]
                sell_vol = data["sell_volume"]

                # Net flow: positive = net buying, negative = net selling
                data["net_flow"] = buy_vol - sell_vol

                # Buy/sell ratio: a single None sentinel for "undefined"
                # (M7 — replaces the legacy float("inf"), which could not be
                # persisted to a DECIMAL column and had two competing
                # "undefined" values (inf vs 0.0) depending on buy_vol).
                data["buy_sell_ratio"] = (buy_vol / sell_vol) if sell_vol > 0 else None

    def _detect_flow_trend(self, all_trades: List[Dict[str, Any]]) -> str:
        """
        Detect flow trend by comparing rates across multiple time windows.

        Compares 1h, 4h, and the full injected window to identify
        acceleration/deceleration. Anchors "now" on window_end_ms (the same
        instant the caller fetched against), not a fresh datetime.now() call
        — closes bugfix_spec.md Item 6a's two-instants defect for the trend
        calculation specifically.

        Args:
            all_trades: Trades for the full window (already injected).

        Returns:
            Trend label string, or INSUFFICIENT_DATA_LABEL when the 1h
            sub-window or the overall sample is too thin to support a trend
            claim (bugfix_spec.md Item 6, F6.3.2).
        """
        now_ms = self.window_end_ms
        cutoff_1h = now_ms - 1 * 3600 * 1000
        # Clamp the 4h lookback to the actual window start so a window
        # shorter than 4h (lookback_hours < 4) can never make trades_4h
        # exceed all_trades (bugfix_spec.md Item 6, F6.3.2 edge case).
        cutoff_4h = max(self.window_start_ms, now_ms - 4 * 3600 * 1000)

        trades_1h = [t for t in all_trades if t["trade_timestamp"] >= cutoff_1h]
        trades_4h = [t for t in all_trades if t["trade_timestamp"] >= cutoff_4h]

        if len(trades_1h) < MINIMUM_TRADES_1H_FOR_TREND or len(all_trades) < MINIMUM_TRADES_FOR_SECTION:
            return INSUFFICIENT_DATA_LABEL

        # Calculate net flow for each window
        def calc_net_flow(trades: List[Dict[str, Any]]) -> float:
            """Calculate net flow (buy - sell volume) from trades."""
            net = 0.0
            for trade in trades:
                amount = float(trade["amount"]) if trade["amount"] is not None else 0.0
                direction = trade["direction"]
                if direction == "buy":
                    net += amount
                else:
                    net -= amount
            return net

        net_1h = calc_net_flow(trades_1h)
        net_4h = calc_net_flow(trades_4h)
        net_full = calc_net_flow(all_trades)

        # Normalize to per-hour rates. rate_full divides by the ACTUAL
        # window length (M6 fix — was a hardcoded /24).
        lookback_hours = self.lookback_hours
        rate_1h = net_1h / 1.0
        rate_4h = net_4h / 4.0 if trades_4h else 0.0
        rate_full = (net_full / lookback_hours) if lookback_hours > 0 else 0.0

        # Detect trend patterns
        # Accelerating buy: 1h >> 4h >> full (all positive, increasing rate)
        if rate_1h > 0 and rate_4h > 0 and rate_full > 0:
            if (
                rate_1h > rate_4h * FLOW_TREND_ACCELERATION_FACTOR
                and rate_4h > rate_full * FLOW_TREND_CONFIRMATION_FACTOR
            ):
                return "Accelerating Buy Pressure"
            elif rate_1h < rate_4h * FLOW_TREND_DECELERATION_FACTOR:
                return "Decelerating Buy Pressure"
            else:
                return "Steady Buy Pressure"

        # Accelerating sell: 1h << 4h << full (all negative, increasing magnitude)
        elif rate_1h < 0 and rate_4h < 0 and rate_full < 0:
            if (
                abs(rate_1h) > abs(rate_4h) * FLOW_TREND_ACCELERATION_FACTOR
                and abs(rate_4h) > abs(rate_full) * FLOW_TREND_CONFIRMATION_FACTOR
            ):
                return "Accelerating Sell Pressure"
            elif abs(rate_1h) < abs(rate_4h) * FLOW_TREND_DECELERATION_FACTOR:
                return "Decelerating Sell Pressure"
            else:
                return "Steady Sell Pressure"

        # Reversing to sell: 1h negative but full-window positive
        elif rate_1h < 0 < rate_full:
            return "Reversing to Sell Pressure"

        # Reversing to buy: 1h positive but full-window negative
        elif rate_1h > 0 > rate_full:
            return "Reversing to Buy Pressure"

        # Mixed or neutral
        else:
            return "Mixed/Neutral Flow"

    def _find_top_strikes_by_buying(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Find top strikes by buying pressure (net buying volume).

        Args:
            top_n: Number of top strikes to return.

        Returns:
            List of dicts with strike, option_type, net_flow, buy_volume.
        """
        all_strikes = []
        for strike, option_types in self.flow_data.items():
            for option_type, data in option_types.items():
                if data["net_flow"] > 0:  # Only positive net flow (net buying)
                    all_strikes.append({
                        "strike": strike,
                        "option_type": option_type,
                        "net_flow": data["net_flow"],
                        "buy_volume": data["buy_volume"],
                        "buy_notional": data["buy_notional"],
                    })

        # Sort by net flow descending
        all_strikes.sort(key=lambda x: x["net_flow"], reverse=True)
        return all_strikes[:top_n]

    def _find_top_strikes_by_selling(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Find top strikes by selling pressure (net selling volume).

        Args:
            top_n: Number of top strikes to return.

        Returns:
            List of dicts with strike, option_type, net_flow, sell_volume.
        """
        all_strikes = []
        for strike, option_types in self.flow_data.items():
            for option_type, data in option_types.items():
                if data["net_flow"] < 0:  # Only negative net flow (net selling)
                    all_strikes.append({
                        "strike": strike,
                        "option_type": option_type,
                        "net_flow": data["net_flow"],
                        "sell_volume": data["sell_volume"],
                        "sell_notional": data["sell_notional"],
                    })

        # Sort by net flow ascending (most negative first)
        all_strikes.sort(key=lambda x: x["net_flow"])
        return all_strikes[:top_n]

    def _interpret_flow_bias(self) -> str:
        """
        Interpret expiration-level flow bias.

        Only called when the data-sufficiency gate has already passed
        (calculate() gates this before calling); the ratio's own
        inf-vs-1.0 tie-breaking is a local implementation detail of the
        classification, unrelated to the storage-level None sentinel (M7).

        Returns:
            Bias interpretation string.
        """
        total_buy = (
            self.expiration_totals["call_buy_volume"] +
            self.expiration_totals["put_buy_volume"]
        )
        total_sell = (
            self.expiration_totals["call_sell_volume"] +
            self.expiration_totals["put_sell_volume"]
        )

        # Thresholds based on ratio
        if total_sell > 0:
            buy_sell_ratio = total_buy / total_sell
        else:
            buy_sell_ratio = float("inf") if total_buy > 0 else 1.0

        if buy_sell_ratio > FLOW_BIAS_HEAVY_THRESHOLD:
            return "Heavy Buying"
        elif buy_sell_ratio > FLOW_BIAS_MODERATE_THRESHOLD:
            return "Moderate Buying"
        elif buy_sell_ratio > FLOW_BIAS_BALANCED_LOW_THRESHOLD:
            return "Balanced"
        elif buy_sell_ratio > FLOW_BIAS_SELLING_THRESHOLD:
            return "Moderate Selling"
        else:
            return "Heavy Selling"

    def _empty_result(self) -> FlowResult:
        """Return the result for zero injected trades (one sentinel, not two — M7/D5)."""
        return FlowResult(
            flow_data={},
            expiration_totals=FlowTotals(
                call_buy_volume=0.0, call_sell_volume=0.0, put_buy_volume=0.0, put_sell_volume=0.0,
            ),
            bias_interpretation=INSUFFICIENT_DATA_LABEL,
            flow_trend=INSUFFICIENT_DATA_LABEL,
            top_buy_strikes=(),
            top_sell_strikes=(),
            trade_count=0,
            spot_price=self.spot_price,
            window_start_ms=self.window_start_ms,
            window_end_ms=self.window_end_ms,
            lookback_hours=self.lookback_hours,
            sufficient_data=False,
            low_confidence=False,
        )

