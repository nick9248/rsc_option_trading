"""
Volatility Risk Premium (VRP) calculator.

Computes VRP = IV - RV where:
- IV (Implied Volatility): Forward-looking volatility priced into options
- RV (Realized Volatility): Actual historical volatility of the underlying

VRP indicates whether options are expensive (high IV) or cheap (low IV) relative
to realized volatility. Positive VRP suggests options are expensive, negative VRP
suggests options are cheap.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np

from coding.core.analytics.historical_normalizer import MIN_OBS

logger = logging.getLogger(__name__)


class VRPCalculator:
    """
    Calculate Volatility Risk Premium from options and price data.

    VRP = IV - RV
    - Positive VRP: Options overpriced relative to realized vol (sell vol)
    - Negative VRP: Options underpriced relative to realized vol (buy vol)
    """

    def __init__(
        self,
        currency: str,
        lookback_days: int = 30
    ):
        """
        Initialize VRP calculator.

        Args:
            currency: Currency symbol (BTC or ETH).
            lookback_days: Days to look back for realized volatility calculation.
        """
        self.currency = currency
        self.lookback_days = lookback_days

    def calculate_realized_volatility(
        self,
        price_history: List[Dict[str, float]],
        window_days: Optional[int] = None,
        *,
        reference_time: datetime,
    ) -> Optional[float]:
        """
        Calculate realized volatility from price history.

        Uses log returns and annualized standard deviation.

        Args:
            price_history: List of dicts with 'timestamp' and 'close' keys
                (timestamp is a Unix epoch in seconds, UTC — unambiguous).
            window_days: Optional window in days (uses lookback_days if not specified).
            reference_time: Anchor for the lookback window. Required,
                timezone-aware UTC (``reference_time.tzinfo`` must be set) —
                passed explicitly by the caller (e.g. ``datetime.now(timezone.
                utc)`` for live use, or an already-resolved historical
                snapshot instant when reconstructing RV for a past hour) so
                this Core class never reads the wall clock itself, matching
                ``ExposureProfileCalculator``'s convention. A naive
                ``reference_time`` compared against the timezone-aware bar
                timestamps below raises ``TypeError`` rather than silently
                producing a wrong, machine-local-timezone-dependent window
                boundary (the confirmed bug this parameter's requiredness
                closes — see institutional_metrics_spec.md Wave G Task G2-C).

        Returns:
            Annualized realized volatility as a decimal (e.g., 0.80 for
            80%), or ``None`` (never a fabricated ``0.0`` — a real "could
            not compute" case, not "zero volatility") when fewer than 2
            price bars are available at all, or fewer than 2 survive the
            window filter (Wave H Task H-D: ``0.0`` here previously flowed
            straight into ``calculate_vrp`` as ``VRP = IV - 0``, a
            maximum-conviction ``VERY_EXPENSIVE`` signal manufactured from
            missing data, not a measured one).
        """
        if not price_history or len(price_history) < 2:
            logger.warning("Insufficient price history for RV calculation")
            return None

        window_days = window_days or self.lookback_days

        # Filter to window. Bar timestamps are converted with an explicit
        # tz=timezone.utc (never bare datetime.fromtimestamp(), which
        # interprets the epoch in the machine's LOCAL timezone) so the
        # comparison against reference_time is always apples-to-apples UTC,
        # regardless of what timezone the calling machine is configured
        # with or what hour-of-day reference_time happens to carry.
        cutoff_time = reference_time - timedelta(days=window_days)
        filtered_prices = [
            p for p in price_history
            if datetime.fromtimestamp(p.get("timestamp", 0), tz=timezone.utc) >= cutoff_time
        ]

        if len(filtered_prices) < 2:
            logger.warning(f"Only {len(filtered_prices)} prices in window, need at least 2")
            return None

        # Calculate log returns
        prices = [float(p["close"]) for p in filtered_prices]
        log_returns = [
            math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
        ]

        if not log_returns:
            return None

        # Calculate standard deviation
        std_dev = np.std(log_returns)

        # Annualize (assuming daily data)
        # RV = std_dev * sqrt(365)
        annualized_rv = std_dev * math.sqrt(365)

        return annualized_rv

    def calculate_average_iv(
        self,
        options_data: List[Dict[str, any]],
        moneyness_filter: Optional[Tuple[float, float]] = (0.9, 1.1)
    ) -> Optional[float]:
        """
        Calculate average implied volatility from options data.

        Args:
            options_data: List of option dicts with 'mark_iv', 'strike', 'underlying_price'.
            moneyness_filter: Optional tuple (min, max) for moneyness filtering.
                            Moneyness = strike / spot. Default: (0.9, 1.1) for ±10% ATM.

        Returns:
            Average IV as a decimal (e.g., 0.80 for 80%), or ``None``
            (never a fabricated ``0.0`` — a real "could not compute" case,
            not "options are free") when no options data is supplied or
            nothing passes the moneyness/liquidity filter (Wave H Task
            H-D: ``0.0`` here previously flowed straight into
            ``calculate_vrp`` as ``VRP = 0 - RV``, a maximum-conviction
            ``VERY_CHEAP`` signal manufactured from missing data, not a
            measured one).
        """
        if not options_data:
            logger.warning("No options data provided for IV calculation")
            return None

        # Filter by moneyness if specified
        if moneyness_filter:
            min_moneyness, max_moneyness = moneyness_filter
            filtered_options = []

            for opt in options_data:
                strike = opt.get("strike")
                spot = opt.get("underlying_price")
                iv = opt.get("mark_iv")

                if strike and spot and iv and spot > 0:
                    moneyness = strike / spot
                    if min_moneyness <= moneyness <= max_moneyness:
                        filtered_options.append(iv)
        else:
            filtered_options = [
                opt.get("mark_iv")
                for opt in options_data
                if opt.get("mark_iv") is not None
            ]

        if not filtered_options:
            logger.warning("No options passed moneyness filter")
            return None

        # Calculate average IV
        avg_iv = np.mean(filtered_options)

        return avg_iv

    def calculate_vrp(
        self,
        implied_vol: Optional[float],
        realized_vol: Optional[float]
    ) -> Optional[Dict[str, float]]:
        """
        Calculate VRP metrics.

        Args:
            implied_vol: Implied volatility as decimal (e.g., 0.80 for
                80%), or ``None`` when the caller could not compute it
                (e.g. ``calculate_average_iv`` returned ``None``).
            realized_vol: Realized volatility as decimal, or ``None`` when
                the caller could not compute it (e.g.
                ``calculate_realized_volatility`` returned ``None``).

        Returns:
            Dict with vrp_absolute, vrp_percentage, implied_volatility,
            realized_volatility, signal -- or ``None`` (Wave H Task H-D)
            when either input is ``None``, or ``realized_vol`` is
            non-positive. The non-positive-``realized_vol`` case is
            deliberately folded into the SAME "insufficient" outcome as
            the ``None`` case, not handled by falling back to a
            ``vrp_percentage`` of ``0.0``: dividing by a non-positive RV
            has no honest percentage answer, and the old fallback produced
            a reproducible self-contradiction -- a real, non-zero
            ``vrp_absolute`` (``IV - RV`` with ``RV <= 0`` is just ``IV``)
            printed alongside a ``vrp_percentage``/``signal`` of exactly
            ``NEUTRAL`` for the same reading, i.e. "fairly priced" and "a
            large VRP" reported in the same breath. Returning ``None``
            here means the whole result is "could not compute", never a
            fabricated neutral reading with a contradicting absolute VRP
            next to it.
        """
        if implied_vol is None or realized_vol is None or realized_vol <= 0:
            return None

        vrp_absolute = implied_vol - realized_vol
        vrp_percentage = (vrp_absolute / realized_vol) * 100

        return {
            "vrp_absolute": vrp_absolute,
            "vrp_percentage": vrp_percentage,
            "implied_volatility": implied_vol,
            "realized_volatility": realized_vol,
            "signal": self._interpret_vrp(vrp_absolute, vrp_percentage)
        }

    def _interpret_vrp(self, vrp_abs: float, vrp_pct: float) -> str:
        """
        Interpret VRP signal.

        Args:
            vrp_abs: Absolute VRP (IV - RV).
            vrp_pct: VRP as percentage of RV.

        Returns:
            Signal string.
        """
        if vrp_pct > 50:
            return "VERY_EXPENSIVE"  # Options significantly overpriced
        elif vrp_pct > 20:
            return "EXPENSIVE"  # Options moderately overpriced
        elif vrp_pct > -10:
            return "NEUTRAL"  # Fair pricing
        elif vrp_pct > -30:
            return "CHEAP"  # Options moderately underpriced
        else:
            return "VERY_CHEAP"  # Options significantly underpriced

    def calculate_iv_percentile(
        self,
        current_iv: float,
        iv_history: List[float],
        lookback_days: int = 30
    ) -> Optional[float]:
        """
        Calculate IV percentile (rank).

        Shows where current IV sits relative to historical range.

        Args:
            current_iv: Current implied volatility.
            iv_history: Historical IV values.
            lookback_days: Days to look back (not used if history provided).

        Returns:
            Percentile as 0-100 (e.g., 75 means current IV is higher than
            75% of historical IVs), or ``None`` (Wave H Task H-D) when
            ``iv_history`` has fewer than ``MIN_OBS`` observations --
            reusing the same sufficiency threshold
            ``HistoricalNormalizer`` (institutional_metrics_spec.md
            section 1) requires before trusting a percentile against
            trailing history, rather than inventing a separate number for
            this calculator. Below that count there is no honest "where
            does this sit" answer: this method used to return ``50.0``
            ("Default to median") for ZERO observations, and had no gate
            at all otherwise, so even a single historical reading yielded
            a 0th or 100th percentile presented with full confidence.
        """
        if len(iv_history) < MIN_OBS:
            return None

        # Count how many historical IVs are below current IV
        below_count = sum(1 for iv in iv_history if iv < current_iv)

        # Percentile = (count below / total count) * 100
        percentile = (below_count / len(iv_history)) * 100

        return percentile

    def generate_report_section(
        self,
        vrp_data: Optional[Dict[str, float]],
        iv_percentile: Optional[float] = None
    ) -> str:
        """
        Generate formatted VRP report section.

        Args:
            vrp_data: VRP calculation results from calculate_vrp(), or
                ``None``/a dict with ``None`` numeric fields when VRP
                could not be computed (insufficient IV or RV input --
                Wave H Task H-D). Either shape renders an explicit
                "insufficient data" message instead of crashing or
                silently defaulting.
            iv_percentile: IV percentile rank, or ``None`` when not
                supplied or when ``calculate_iv_percentile`` had
                insufficient history -- renders an explicit "insufficient
                data" line rather than silently omitting the section.

        Returns:
            Formatted string for inclusion in analysis report.
        """
        lines = []
        separator = "-" * 80

        lines.append("VOLATILITY RISK PREMIUM (VRP) ANALYSIS")
        lines.append(separator)

        # Wave H Task H-D: vrp_data itself may be None (calculate_vrp's own
        # "insufficient" return), or a dict whose numeric fields are None
        # (a caller's own insufficient-data placeholder, e.g.
        # VRPService._empty_result) -- both mean the same thing here and
        # get the same honest message, never a crash on `None * 100`.
        if (
            vrp_data is None
            or vrp_data.get("implied_volatility") is None
            or vrp_data.get("realized_volatility") is None
        ):
            lines.append(
                "  Insufficient data to compute VRP (missing or unusable "
                "implied/realized volatility input)"
            )
            lines.append("")
            return "\n".join(lines)

        # Core metrics
        iv = vrp_data["implied_volatility"] * 100  # Convert to percentage
        rv = vrp_data["realized_volatility"] * 100
        vrp_abs = vrp_data["vrp_absolute"] * 100
        vrp_pct = vrp_data["vrp_percentage"]
        signal = vrp_data["signal"]

        lines.append(f"Implied Volatility (IV): {iv:.2f}%")
        lines.append(f"Realized Volatility (RV): {rv:.2f}%")
        lines.append(f"VRP (IV - RV): {vrp_abs:+.2f}% ({vrp_pct:+.1f}%)")
        lines.append("")

        # Signal interpretation
        lines.append(f"Signal: {signal}")

        if signal == "VERY_EXPENSIVE":
            lines.append("  - Options are significantly overpriced relative to realized vol")
            lines.append("  - Consider selling volatility (spreads, iron condors)")
        elif signal == "EXPENSIVE":
            lines.append("  - Options are moderately overpriced")
            lines.append("  - Favor selling strategies over buying")
        elif signal == "NEUTRAL":
            lines.append("  - Options are fairly priced")
            lines.append("  - No strong bias toward buying or selling vol")
        elif signal == "CHEAP":
            lines.append("  - Options are moderately underpriced")
            lines.append("  - Favor buying strategies (long calls/puts, debit spreads)")
        elif signal == "VERY_CHEAP":
            lines.append("  - Options are significantly underpriced")
            lines.append("  - Strong buying opportunity for directional trades")

        lines.append("")

        # IV Percentile. Wave H Task H-D: an explicit "insufficient data"
        # line rather than silently omitting the section -- calculate_iv_
        # percentile now returns None below MIN_OBS history, and a reader
        # seeing no percentile line at all cannot tell "not requested"
        # apart from "requested but insufficient history".
        if iv_percentile is not None:
            lines.append(f"IV Percentile (30-day): {iv_percentile:.1f}%")

            if iv_percentile >= 80:
                lines.append("  - IV is in the top 20% of recent range (very high)")
            elif iv_percentile >= 60:
                lines.append("  - IV is above average")
            elif iv_percentile >= 40:
                lines.append("  - IV is around average")
            elif iv_percentile >= 20:
                lines.append("  - IV is below average")
            else:
                lines.append("  - IV is in the bottom 20% of recent range (very low)")
        else:
            lines.append(
                f"IV Percentile (30-day): insufficient history "
                f"(need >= {MIN_OBS} observations)"
            )

        lines.append("")
        return "\n".join(lines)

