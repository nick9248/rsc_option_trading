"""
Market-wide metrics calculator.

Computes metrics that span across all expirations:
- IV Term Structure
- Futures Basis
- Multi-window Realized Volatility (10d/20d/30d)
- VRP (DVOL vs 30d RV)
- Volatility Cone
- Perpetual Funding Trend
- Block Trade Detection
- Cross-Asset Correlation
"""

import logging
import math
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from coding.core.analytics.results.market_wide_results import FuturesBasisEntry, FuturesBasisResult
from coding.core.analytics.thresholds import BLOCK_TRADE_NOTIONAL_THRESHOLD_USD
from coding.core.analytics.vrp_calculator import VRPCalculator

logger = logging.getLogger(__name__)

# bugfix_spec.md Item 5 (F5.3.1/F5.3.2) — futures settle at 08:00 UTC, not
# local midnight; annualization uses simple (not compound) ACT/365, the
# crypto-market/CME convention for quoted "annualized basis"; sub-daily
# tenors are suppressed rather than annualized (multiplying noise by up to
# ~1000x is worse than showing nothing).
DERIBIT_SETTLEMENT_HOUR_UTC = 8
DAYS_PER_YEAR = 365.0
MINIMUM_DAYS_FOR_ANNUALIZATION = 1.0

# calculate_volatility_cone's own "Insufficient price history" threshold.
# Named so on_chain_analysis_service.py can gate VolatilityConeResult
# construction on the SAME condition (rather than duplicating the literal
# 35) -- see the additional finding logged alongside task A6 carried
# finding #1: the typed result was previously constructed unconditionally
# whenever price_history was truthy, even when too short for this method's
# own "Insufficient" branch, producing a fake all-zero-percentile result.
MINIMUM_PRICE_HISTORY_DAYS_FOR_VOL_CONE = 35

# bugfix_spec.md Item 11 — DVOL correlation must be computed on log CHANGES
# of the raw DVOL levels, not the levels themselves: correlating levels of
# two trending, highly persistent series measures shared trend, not
# co-movement (a textbook spurious-regression setup; live BTC/ETH, 30
# points: levels corr 0.9858 vs log-change corr 0.8922). This threshold is
# on the CHANGE series (after differencing), not the level series.
MINIMUM_CORRELATION_OBSERVATIONS = 10

# institutional_metrics_spec.md section 8 (Task C9): "anomalously high"
# forward vol vs. its immediate neighbouring calendar segments, in vol
# points (percent, not decimal) — matches the spec's own ">5.0 vol pts"
# language literally.
FORWARD_VOL_EVENT_PREMIUM_THRESHOLD_PTS = 5.0

# bugfix_spec.md Item 4 (F4.3) — the real get_funding_chart_data point shape
# is {"data": [{"timestamp":..., "index_price":..., "interest_8h":...}, ...]}.
# The old code read "funding_rate"/"value" keys that don't exist on any
# point, so recent_rates was always [0]*10 and the trend was always "Stable".
FUNDING_POINT_RATE_KEY = "interest_8h"          # Deribit 8h funding rate, decimal
FUNDING_TREND_RECENT_POINTS = 8                 # ~8h at hourly resolution
FUNDING_TREND_BASELINE_POINTS = 24              # ~24h at hourly resolution
FUNDING_TREND_MINIMUM_POINTS = 12
# 1e-5 per 8h = 0.001% per 8h = 1.10% annualized. Below this the change is noise:
# live BTC |interest_8h| median is 2.4e-5.
FUNDING_TREND_THRESHOLD_8H = 1.0e-5
FUNDING_PERIODS_PER_YEAR = 3 * 365              # 1095


class MarketWideCalculator:
    """
    Calculate market-wide metrics using API data and existing calculators.

    These metrics are appended at the END of the report, after all
    per-expiry sections.
    """

    def __init__(
        self,
        currency: str,
        spot_price: float,
        dvol: Optional[float] = None,
    ):
        """
        Initialize market-wide calculator.

        Args:
            currency: Currency symbol (BTC, ETH).
            spot_price: Current underlying spot price.
            dvol: Current DVOL value (Deribit Volatility Index).
        """
        self.currency = currency
        self.spot_price = spot_price
        self.dvol = dvol
        self.vrp_calculator = VRPCalculator(currency=currency, lookback_days=30)

    def calculate_iv_term_structure(
        self,
        atm_ivs: Dict[str, float],
    ) -> Tuple[str, Dict]:
        """
        Generate IV Term Structure report from per-expiry ATM IVs.

        Args:
            atm_ivs: Dict mapping expiration -> ATM IV (percentage).

        Returns:
            Tuple of (formatted report string, structured dict with shape/spread/iv_by_dte).
        """
        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"shape": "FLAT", "spread": 0.0, "iv_by_dte": {}}

        lines.append("IV TERM STRUCTURE")
        lines.append(sub_separator)

        if not atm_ivs:
            lines.append("  No ATM IV data available")
            lines.append("")
            return "\n".join(lines), structured

        # Calculate DTE for each expiration
        entries = []
        now = datetime.now(timezone.utc)

        for exp, iv in sorted(atm_ivs.items()):
            dte = self._calculate_dte(exp, now)
            if dte is not None:
                entries.append({"expiration": exp, "dte": dte, "atm_iv": iv})

        entries.sort(key=lambda x: x["dte"])

        # Build iv_by_dte structured dict
        structured["iv_by_dte"] = {e["dte"]: e["atm_iv"] for e in entries}

        lines.append(f"  {'Expiration':>12}  {'DTE':>5}  {'ATM IV':>8}")
        lines.append(f"  {'----------':>12}  {'---':>5}  {'------':>8}")

        for entry in entries:
            lines.append(
                f"  {entry['expiration']:>12}  {entry['dte']:>5}  "
                f"{entry['atm_iv']:>7.1f}%"
            )

        # Determine structure shape
        if len(entries) >= 2:
            front_iv = entries[0]["atm_iv"]
            back_iv = entries[-1]["atm_iv"]
            diff = back_iv - front_iv

            if diff > 2:
                shape_key = "CONTANGO"
                shape_label = f"CONTANGO (+{diff:.1f} pts)"
            elif diff < -2:
                shape_key = "BACKWARDATION"
                shape_label = f"BACKWARDATED ({diff:.1f} pts)"
            else:
                shape_key = "FLAT"
                shape_label = f"FLAT ({diff:+.1f} pts)"

            structured["shape"] = shape_key
            structured["spread"] = abs(diff)       # unsigned — used by score_term_structure
            structured["spread_signed"] = diff     # signed — used by header display
            lines.append(f"  Structure: {shape_label}")

        lines.append("")
        return "\n".join(lines), structured

    @staticmethod
    def calculate_forward_vol_curve(
        atm_by_expiry: Dict[str, Tuple[Optional[float], Optional[float]]],
    ) -> Dict[str, Any]:
        """
        Forward/event vol between adjacent expiries
        (institutional_metrics_spec.md section 8, Task C9): variance
        additivity along the term structure (r = q = 0):

            sigma_fwd^2(T1->T2) = (sigma2^2 * T2 - sigma1^2 * T1) / (T2 - T1)

        Pure function -- reuses the caller's already-computed per-expiry
        ATM IV (section 3's ``atm_iv_interp``) and time-to-expiry. Touches
        no clock and recomputes no ATM IV itself, so it carries none of
        this campaign's repeated naive-local-vs-UTC risk; the caller is
        responsible for producing ``dte_days`` with a single, UTC-explicit
        ``datetime.now(timezone.utc)`` read (see
        ``OnChainAnalysisService._build_forward_vol_curve``), the same
        clock convention ``_build_skew_term_structure`` already uses for
        this same per-expiry data (so the two sections' DTE for a shared
        expiry can never desync).

        Args:
            atm_by_expiry: ``{expiration_label: (atm_iv_pct, dte_days)}``.
                ``atm_iv_pct``: ATM IV in percent (e.g. ``40.0`` for 40%),
                    or ``None``/non-positive when unavailable for that
                    expiry (thin/no-quote chain).
                ``dte_days``: time-to-expiry in days, or any consistently-
                    used unit -- the ratio in the formula cancels units
                    (T8.3 unit-invariance acceptance test). ``None``/
                    non-positive means unusable (already expired, or a
                    parse failure upstream).

        Judgment calls made here (not spelled out verbatim in the spec;
        flagged for review in the task report):
          - An expiry with a missing/non-positive ATM IV or DTE is
            EXCLUDED from candidacy entirely -- it can never be either leg
            of any bucket -- rather than only dropping the one pair that
            touches it. A thin/stale middle expiry therefore does not
            sever the term structure into two disconnected halves; the
            two flanking valid expiries still form a bucket bridging the
            gap. This is a narrower, per-PAIR concern for exactly-tied
            DTE (T2 == T1): only THAT pair is skipped, since the tied
            entry itself is still valid and can pair with its other
            neighbour.
          - "Event premium" compares a bucket only against its immediate
            neighbour bucket(s) (previous/next in the output list, not
            transitively further out), using whichever of those has a
            non-None ``fwd_vol_pct`` (a neighbour with negative variance
            is excluded from the median, never treated as 0). ``None``
            when no neighbour has a usable value -- covers both the
            explicit "only 2 expiries" spec case and the "both neighbours
            negative-variance" case the spec does not enumerate directly.
          - The >5.0 vol-pt threshold is applied to the signed
            (bucket - median(neighbours)) difference, i.e. only
            anomalously HIGH forward vol is flagged, matching the spec's
            "anomalously high" framing -- not its absolute value.

        Returns:
            ``{"buckets": [...]}`` where each bucket dict has keys
            ``from_expiry``, ``to_expiry``, ``t1_days``, ``t2_days``,
            ``sigma1_pct``, ``sigma2_pct``, ``fwd_var`` (decimal²,
            negative when the calendar-spread arb/data-error flag is
            set), ``fwd_vol_pct`` (``None`` exactly when
            ``negative_variance`` is ``True``), ``negative_variance``
            (bool), ``event_premium`` (``Optional[float]``, vol pts), and
            ``flags`` (``list[str]``, any of ``"NEGATIVE_VARIANCE"`` /
            ``"EVENT_PREMIUM"``). Buckets are ordered by ``t1_days``
            ascending. ``[]`` when fewer than 2 usable expiries remain
            after filtering, or every adjacent pair among the survivors
            ties on DTE.
        """
        valid: List[Tuple[str, float, float]] = []
        for expiration, pair in atm_by_expiry.items():
            atm_iv_pct, dte_days = pair
            if atm_iv_pct is None or dte_days is None:
                continue
            if atm_iv_pct <= 0 or dte_days <= 0:
                continue
            valid.append((expiration, float(atm_iv_pct), float(dte_days)))
        valid.sort(key=lambda row: row[2])

        if len(valid) < 2:
            return {"buckets": []}

        buckets: List[Dict[str, Any]] = []
        for (exp1, sigma1_pct, t1), (exp2, sigma2_pct, t2) in zip(valid, valid[1:]):
            if t2 == t1:
                # Edge case: division by zero — skip only THIS pair, the
                # tied entry can still pair with its other neighbour.
                continue

            sigma1 = sigma1_pct / 100.0
            sigma2 = sigma2_pct / 100.0
            fwd_var = (sigma2 ** 2 * t2 - sigma1 ** 2 * t1) / (t2 - t1)
            negative_variance = fwd_var < 0
            fwd_vol_pct = None if negative_variance else math.sqrt(fwd_var) * 100.0

            buckets.append({
                "from_expiry": exp1,
                "to_expiry": exp2,
                "t1_days": t1,
                "t2_days": t2,
                "sigma1_pct": sigma1_pct,
                "sigma2_pct": sigma2_pct,
                "fwd_var": fwd_var,
                "fwd_vol_pct": fwd_vol_pct,
                "negative_variance": negative_variance,
                "event_premium": None,  # filled below once the full bucket list exists
                "flags": ["NEGATIVE_VARIANCE"] if negative_variance else [],
            })

        for i, bucket in enumerate(buckets):
            if bucket["fwd_vol_pct"] is None:
                continue
            neighbour_vols = [
                buckets[j]["fwd_vol_pct"]
                for j in (i - 1, i + 1)
                if 0 <= j < len(buckets) and buckets[j]["fwd_vol_pct"] is not None
            ]
            if not neighbour_vols:
                continue
            median_neighbour = statistics.median(neighbour_vols)
            premium = bucket["fwd_vol_pct"] - median_neighbour
            bucket["event_premium"] = premium
            if premium > FORWARD_VOL_EVENT_PREMIUM_THRESHOLD_PTS:
                bucket["flags"].append("EVENT_PREMIUM")

        return {"buckets": buckets}

    def calculate_futures_basis(
        self,
        futures_data: List[Dict[str, Any]],
        now_utc: Optional[datetime] = None,
    ) -> FuturesBasisResult:
        """
        Calculate futures basis (annualized simple basis on ACT/365 to
        08:00 UTC settlement — bugfix_spec.md Item 5).

        T6 (refactor_design_spec.md, carried from A4 review): returns the
        typed ``FuturesBasisResult`` instead of a ``(text, dict)`` tuple.
        Rendering moved to
        ``coding.core.analytics.reporting.market_wide_formatter.format_futures_basis_section``
        (the dormant T3 formatter, now the live path for this section) —
        this method no longer builds report text itself.

        Simple annualization is the crypto-market/CME convention for a
        quoted "annualized basis" and is what a cash-and-carry desk can
        actually lock on a single trade; empirically it differs from
        compound by <=0.11pt at every observed tenor, so the choice is
        immaterial next to the ~1-2pt bug being fixed here (F5.2
        confirmation evidence).

        Sub-daily tenors (< 1 day to settlement) suppress annualization
        entirely (``None``) rather than showing a number scaled by up to
        ~1000x noise; the raw basis is still computable from
        ``mark_price``/``index_price`` on the entry.

        Args:
            futures_data: List of dicts with instrument_name, mark_price,
                         index_price, and expiration info.
            now_utc: Current instant, timezone-aware UTC. Defaults to
                ``datetime.now(timezone.utc)`` — pass explicitly in tests for
                a frozen, reproducible clock.

        Returns:
            ``FuturesBasisResult`` — ``futures_basis`` maps expiry ->
            ``Optional[float]`` annualized premium; ``None`` when
            annualization is suppressed (Decision D12: weight-zero in every
            downstream consumer, never "neutral").
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        entries: List[FuturesBasisEntry] = []
        basis_dict: Dict[str, Optional[float]] = {}

        for future in futures_data:
            name = future.get("instrument_name", "")
            price = future.get("mark_price", 0)
            spot = future.get("index_price", self.spot_price)

            if spot <= 0 or price <= 0:
                continue

            # Expiry label + exact fractional days to 08:00 UTC settlement
            parts = name.split("-")
            if len(parts) >= 2:
                expiry_label = parts[1]
            else:
                expiry_label = name
            days = self._calculate_days_to_expiry(expiry_label, now_utc)

            basis_pct = ((price - spot) / spot) * 100.0

            if days is None or days <= 0:
                annualized: Optional[float] = None
            elif days < MINIMUM_DAYS_FOR_ANNUALIZATION:
                annualized = None
            else:
                annualized = basis_pct * (DAYS_PER_YEAR / days)

            dte = None if days is None else int(math.floor(days))
            basis_dict[expiry_label] = annualized

            entries.append(FuturesBasisEntry(
                instrument_name=name,
                dte=dte,
                mark_price=price,
                index_price=spot,
                annualized_premium_pct=annualized,
            ))

        return FuturesBasisResult(entries=tuple(entries), futures_basis=basis_dict)

    def calculate_realized_volatility_multi_window(
        self,
        price_history: List[Dict[str, float]],
    ) -> Tuple[str, Dict[int, float]]:
        """
        Calculate realized volatility for 10d, 20d, 30d windows.

        Args:
            price_history: List of dicts with 'timestamp' and 'close' keys.

        Returns:
            Tuple of (formatted report string, dict of window -> rv_value).
        """
        lines = []
        sub_separator = "-" * 80

        lines.append("REALIZED VOLATILITY")
        lines.append(sub_separator)

        rv_values = {}

        if not price_history or len(price_history) < 11:
            lines.append("  Insufficient price history")
            lines.append("")
            return "\n".join(lines), rv_values

        for window in [10, 20, 30]:
            rv = self.vrp_calculator.calculate_realized_volatility(
                price_history, window_days=window
            )
            rv_values[window] = rv

        rv_strs = []
        for window, rv in rv_values.items():
            rv_strs.append(f"{window}d: {rv * 100:.1f}%")

        lines.append(f"  {' | '.join(rv_strs)}")
        lines.append("")

        return "\n".join(lines), rv_values

    def calculate_vrp(
        self,
        rv_30d: float,
    ) -> Tuple[str, Dict]:
        """
        Calculate VRP using DVOL (IV proxy) minus 30d RV.

        Args:
            rv_30d: 30-day realized volatility as decimal.

        Returns:
            Tuple of (formatted report string, dict with vrp and signal).
        """
        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"vrp": 0.0, "signal": "FAIR"}

        lines.append("VOLATILITY RISK PREMIUM (VRP)")
        lines.append(sub_separator)

        if self.dvol is None:
            lines.append("  DVOL not available")
            lines.append("")
            return "\n".join(lines), structured

        # DVOL is already in percentage (e.g., 65.0 for 65%)
        dvol_decimal = self.dvol / 100
        vrp_result = self.vrp_calculator.calculate_vrp(dvol_decimal, rv_30d)

        vrp_pts = vrp_result["vrp_absolute"] * 100
        signal = vrp_result["signal"]
        structured["vrp"] = vrp_pts
        structured["signal"] = signal

        if signal in ("VERY_EXPENSIVE", "EXPENSIVE"):
            advice = "Sell vol"
        elif signal in ("VERY_CHEAP", "CHEAP"):
            advice = "Buy vol"
        else:
            advice = "Neutral"

        lines.append(
            f"  DVOL: {self.dvol:.1f}%  |  30d RV: {rv_30d * 100:.1f}%  |  "
            f"VRP: {vrp_pts:+.1f} pts ({signal} - {advice})"
        )
        lines.append("")

        return "\n".join(lines), structured

    def calculate_volatility_cone(
        self,
        price_history: List[Dict[str, float]],
    ) -> Tuple[str, Dict]:
        """
        Calculate volatility cone (percentile of current RV vs historical range).

        Args:
            price_history: Full price history (180+ days ideal).

        Returns:
            Tuple of (formatted report string, dict with cone percentiles per window).
        """
        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"cone_10d_pctile": 0.0, "cone_20d_pctile": 0.0, "cone_30d_pctile": 0.0}

        lines.append("VOLATILITY CONE")
        lines.append(sub_separator)

        if not price_history or len(price_history) < MINIMUM_PRICE_HISTORY_DAYS_FOR_VOL_CONE:
            lines.append("  Insufficient price history for vol cone")
            lines.append("")
            return "\n".join(lines), structured

        lines.append(
            f"  {'Window':>8}  {'Current':>8}  {'25th':>8}  "
            f"{'Median':>8}  {'75th':>8}  {'Pctile':>8}"
        )
        lines.append(
            f"  {'------':>8}  {'-------':>8}  {'----':>8}  "
            f"{'------':>8}  {'----':>8}  {'------':>8}"
        )

        prices = [float(p["close"]) for p in price_history]

        for window in [10, 20, 30]:
            if len(prices) < window + 1:
                continue

            # Calculate rolling RV for all available windows
            rolling_rvs = []
            for i in range(window, len(prices)):
                segment = prices[i - window:i + 1]
                log_returns = [
                    math.log(segment[j] / segment[j - 1])
                    for j in range(1, len(segment))
                ]
                if log_returns:
                    std = np.std(log_returns)
                    rv = std * math.sqrt(365) * 100
                    rolling_rvs.append(rv)

            if not rolling_rvs:
                continue

            current_rv = rolling_rvs[-1]
            p25 = np.percentile(rolling_rvs, 25)
            p50 = np.percentile(rolling_rvs, 50)
            p75 = np.percentile(rolling_rvs, 75)

            # Calculate percentile of current RV
            below = sum(1 for rv in rolling_rvs if rv < current_rv)
            percentile = (below / len(rolling_rvs)) * 100

            structured[f"cone_{window}d_pctile"] = percentile
            # T10 (refactor_design_spec.md): the other four columns of this
            # window's table row, so on_chain_analysis_service.py can build
            # a VolatilityConeWindowStats and the typed render path
            # (render_market_wide_from_result) can reproduce the full table
            # -- previously only the percentile was exposed here.
            structured[f"cone_{window}d_current_rv"] = current_rv
            structured[f"cone_{window}d_p25"] = p25
            structured[f"cone_{window}d_p50"] = p50
            structured[f"cone_{window}d_p75"] = p75

            lines.append(
                f"  {window:>6}d  {current_rv:>7.1f}%  {p25:>7.1f}%  "
                f"{p50:>7.1f}%  {p75:>7.1f}%  {percentile:>6.0f}th"
            )

        lines.append("")
        return "\n".join(lines), structured

    def _extract_funding_rates(self, funding_data: Dict[str, Any]) -> List[float]:
        """
        Extract the 8h funding rate series from a get_funding_chart_data response.

        Deribit points are dicts keyed
        ['timestamp', 'index_price', 'interest_8h'] — interest_8h is the 8-hour
        funding rate as a decimal (4.118e-05 == 0.004118% per 8h).

        bugfix_spec.md Item 4 (F4.3): the old extraction read
        ``funding_rate``/``value`` keys that don't exist on any point, so the
        series was always ``[0]*10`` and the trend was always "Stable".
        """
        if not isinstance(funding_data, dict):
            return []
        points = funding_data.get("data")
        if not isinstance(points, list):
            return []

        rates: List[float] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            rate = point.get(FUNDING_POINT_RATE_KEY)
            if rate is None:
                continue
            try:
                rates.append(float(rate))
            except (TypeError, ValueError):
                continue

        if not rates and points:
            logger.warning(
                "Funding chart data carried %d points but no '%s' key; "
                "point keys were %s",
                len(points), FUNDING_POINT_RATE_KEY,
                list(points[0].keys()) if isinstance(points[0], dict) else type(points[0]).__name__,
            )
        return rates

    def _classify_funding_trend(self, rates: List[float]) -> str:
        """
        Additive (not multiplicative) trend classification.

        Funding crosses zero and sits near zero, so a ratio test is undefined
        there. Compare the mean of the most recent window against the mean of
        the preceding baseline window and require the difference to exceed an
        absolute threshold expressed in 8h-rate units (bugfix_spec.md Item 4).
        """
        if len(rates) < FUNDING_TREND_MINIMUM_POINTS:
            return "N/A"

        recent = rates[-FUNDING_TREND_RECENT_POINTS:]
        baseline = rates[-(FUNDING_TREND_RECENT_POINTS + FUNDING_TREND_BASELINE_POINTS):
                          -FUNDING_TREND_RECENT_POINTS]
        if not baseline:
            return "N/A"

        change = float(np.mean(recent)) - float(np.mean(baseline))
        if change > FUNDING_TREND_THRESHOLD_8H:
            return "Rising"
        if change < -FUNDING_TREND_THRESHOLD_8H:
            return "Falling"
        return "Stable"

    def calculate_perpetual_funding_trend(
        self,
        funding_data: Dict[str, Any],
        perp_ticker: Dict[str, Any],
    ) -> Tuple[str, Dict]:
        """
        Generate perpetual funding trend report.

        bugfix_spec.md Item 4 (F4.3): trend is classified from the real
        ``interest_8h`` series (via ``_extract_funding_rates`` /
        ``_classify_funding_trend``), and annualization uses ``funding_8h``
        (the realised 8h rate), never ``current_funding`` (the instantaneous
        accruing rate) — a 61x divergence was observed live between the two.

        Args:
            funding_data: Funding chart data from API.
            perp_ticker: Perpetual ticker data with OI and funding.

        Returns:
            Tuple of (formatted report string, dict with perp_oi,
            perp_funding_trend, funding_8h, funding_annualized_pct).
        """
        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"perp_oi": 0.0, "perp_funding_trend": "Stable", "funding_8h": 0.0}

        lines.append("PERPETUAL FUNDING & OI")
        lines.append(sub_separator)

        # Extract current data from ticker
        perp_oi = perp_ticker.get("open_interest", 0)
        current_funding = perp_ticker.get("current_funding")
        funding_8h = perp_ticker.get("funding_8h")

        structured["perp_oi"] = perp_oi
        if current_funding is not None:
            structured["funding_rate"] = current_funding  # same API call as funding_8h below
        if funding_8h is not None:
            structured["funding_8h"] = funding_8h

        # bugfix_spec.md Item 4 F4.3: gate on funding_8h OR current_funding —
        # a None current_funding must not suppress the whole section
        # (including OI and the trend) when funding_8h is available.
        if funding_8h is not None or current_funding is not None:
            rates = self._extract_funding_rates(funding_data)
            trend = self._classify_funding_trend(rates)
            structured["perp_funding_trend"] = trend
            structured["funding_annualized_pct"] = (
                funding_8h * FUNDING_PERIODS_PER_YEAR * 100 if funding_8h is not None else None
            )

            lines.append(f"  Perp OI: {perp_oi:,.0f} USD")
            if funding_8h is not None:
                lines.append(
                    f"  Funding (8h): {funding_8h * 100:.4f}%  |  "
                    f"Annualized: {funding_8h * FUNDING_PERIODS_PER_YEAR * 100:.2f}%  |  "
                    f"Trend: {trend}"
                )
            else:
                lines.append("  Funding (8h): not available")
            if current_funding is not None:
                lines.append(f"  Instantaneous funding: {current_funding * 100:.4f}%")
        else:
            lines.append("  Funding data not available")

        lines.append("")
        return "\n".join(lines), structured

    def detect_block_trades(
        self,
        trades: List[Dict[str, Any]],
        notional_threshold: float = BLOCK_TRADE_NOTIONAL_THRESHOLD_USD,
    ) -> Tuple[str, Dict]:
        """
        Detect and report block trades, grouped by ``block_trade_id``
        (institutional_metrics_spec.md section 9 / Migration M2, Task D1),
        plus a separately-labelled "large prints" list of large single-leg
        screen prints (the old notional-filter heuristic).

        A trade carrying a ``block_trade_id`` is a genuine block/combo leg
        and is grouped into ``structured["blocks"]`` (one row per block,
        not per leg); it is EXCLUDED from ``structured["large_prints"]``
        even if its own notional clears the threshold, so the two lists
        never double-count the same trade.

        History is not backfillable: block_trade_id was never persisted to
        historical_trades before migration 022
        (``BLOCK_TRADE_ID_TRACKED_SINCE``). This method only ever sees
        trades from the current live-fetched window, so it states that
        date as the section's start date rather than implying no data
        exists.

        Args:
            trades: Recent trade records from API.
            notional_threshold: Minimum notional value in USD for the
                large-prints list.

        Returns:
            Tuple of (formatted report string, dict with "blocks" and
            "large_prints" lists).
        """
        from coding.core.analytics.thresholds import BLOCK_TRADE_ID_TRACKED_SINCE

        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"blocks": [], "large_prints": []}

        lines.append("BLOCK TRADES")
        lines.append(sub_separator)
        lines.append(
            f"  Tracked since {BLOCK_TRADE_ID_TRACKED_SINCE} "
            "(block_trade_id was not captured before this date; history is not backfillable)"
        )

        if not trades:
            lines.append("  No recent trade data available")
            lines.append("")
            lines.append(
                "LARGE PRINTS (screen prints, not blocks; >${:,.0f} notional)".format(
                    notional_threshold
                )
            )
            lines.append(sub_separator)
            lines.append("  No recent trade data available")
            lines.append("")
            return "\n".join(lines), structured

        # -- Group legs by block_trade_id -----------------------------------
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for trade in trades:
            block_id = trade.get("block_trade_id")
            if block_id:
                groups.setdefault(block_id, []).append(trade)

        blocks = []
        for block_id, legs in groups.items():
            observed_leg_count = len(legs)
            # gate exhaustiveness: block_trade_leg_count can be null/missing
            # on a leg -- fall back to the observed count rather than
            # crashing or reporting 0.
            declared_leg_count = next(
                (leg.get("block_trade_leg_count") for leg in legs
                 if leg.get("block_trade_leg_count")),
                None,
            )
            leg_count = declared_leg_count if declared_leg_count else observed_leg_count

            combo_id = next((leg.get("combo_id") for leg in legs if leg.get("combo_id")), None)

            # independent review round 2 (M1): leg.get("index_price",
            # self.spot_price) only applies the spot_price default when the
            # key is ABSENT -- a leg with the key present but null fell
            # through to `or 0`, silently zeroing that leg's premium
            # contribution instead of using spot_price. `.get("index_price")
            # or self.spot_price` applies the fallback for both cases.
            combined_premium_usd = sum(
                (leg.get("price") or 0) * (leg.get("amount") or 0)
                * (leg.get("index_price") or self.spot_price)
                for leg in legs
            )
            total_amount = sum(leg.get("amount") or 0 for leg in legs)

            timestamps = [leg.get("timestamp") for leg in legs if leg.get("timestamp")]

            blocks.append({
                "block_trade_id": block_id,
                "leg_count": leg_count,
                "observed_leg_count": observed_leg_count,
                "combo_id": combo_id,
                "combined_premium_usd": combined_premium_usd,
                "total_amount": total_amount,
                "instruments": tuple(leg.get("instrument_name", "") for leg in legs),
                "timestamp": min(timestamps) if timestamps else None,
            })

        # Sort by combined premium descending
        blocks.sort(key=lambda b: b["combined_premium_usd"], reverse=True)
        structured["blocks"] = blocks

        if not blocks:
            lines.append("  No blocks detected in recent activity")
        else:
            lines.append(
                f"  {'Block ID':>16}  {'Legs':>4}  {'Structure':>24}  "
                f"{'Premium (USD)':>16}  {'Time':>12}"
            )
            lines.append(
                f"  {'--------':>16}  {'----':>4}  {'---------':>24}  "
                f"{'-------------':>16}  {'----':>12}"
            )
            for b in blocks:
                ts = b["timestamp"]
                # independent review round 2 (Important #2): naive-local
                # datetime is the exact banned bug class this campaign has
                # already spent 5 fix rounds on -- explicit UTC always.
                time_str = (
                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S")
                    if ts else "N/A"
                )
                structure = b["combo_id"] or "N/A"
                leg_str = (
                    str(b["leg_count"])
                    if b["leg_count"] == b["observed_leg_count"]
                    else f"{b['observed_leg_count']}/{b['leg_count']}"
                )
                lines.append(
                    f"  {b['block_trade_id']:>16}  {leg_str:>4}  {structure:>24}  "
                    f"${b['combined_premium_usd']:>15,.0f}  {time_str:>12}"
                )
        lines.append("")

        # -- Large prints: old notional filter, excluding block legs --------
        lines.append(
            "LARGE PRINTS (screen prints, not blocks; >${:,.0f} notional)".format(
                notional_threshold
            )
        )
        lines.append(sub_separator)

        large_prints = []
        for trade in trades:
            if trade.get("block_trade_id"):
                # already counted in `blocks` above -- never double-count.
                continue

            amount = trade.get("amount", 0)
            index_price = trade.get("index_price", self.spot_price)

            # Notional = amount × underlying price
            notional = amount * index_price

            if notional >= notional_threshold:
                large_prints.append({
                    "timestamp": trade.get("timestamp"),
                    "instrument": trade.get("instrument_name", ""),
                    "size": amount,
                    "amount": amount,
                    "direction": trade.get("direction", ""),
                    "notional": notional,
                    "iv": trade.get("iv"),
                })

        if not large_prints:
            lines.append("  No large prints detected in recent activity")
            lines.append("")
            return "\n".join(lines), structured

        # Sort by notional descending
        large_prints.sort(key=lambda x: x["notional"], reverse=True)
        structured["large_prints"] = large_prints[:10]

        lines.append(
            f"  {'Time':>12}  {'Instrument':>25}  {'Size':>8}  "
            f"{'Dir':>5}  {'Notional':>14}  {'IV':>6}"
        )
        lines.append(
            f"  {'----':>12}  {'----------':>25}  {'----':>8}  "
            f"{'---':>5}  {'--------':>14}  {'--':>6}"
        )

        for lp in large_prints[:10]:
            ts = lp["timestamp"]
            if ts:
                time_str = datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S")
            else:
                time_str = "N/A"

            iv_str = f"{lp['iv']:.1f}%" if lp["iv"] else "N/A"

            lines.append(
                f"  {time_str:>12}  {lp['instrument']:>25}  "
                f"{lp['amount']:>8.1f}  {lp['direction']:>5}  "
                f"${lp['notional']:>13,.0f}  {iv_str:>6}"
            )

        lines.append("")
        return "\n".join(lines), structured

    def calculate_cross_asset_correlation(
        self,
        own_prices: List[Dict[str, float]],
        other_prices: List[Dict[str, float]],
        own_dvol_history: List[float],
        other_dvol_history: List[float],
        other_currency: str,
    ) -> Tuple[str, Dict]:
        """
        Calculate cross-asset correlation.

        Args:
            own_prices: Price history for this currency.
            other_prices: Price history for comparison currency.
            own_dvol_history: DVOL close values for this currency.
            other_dvol_history: DVOL close values for comparison currency.
            other_currency: Name of comparison currency.

        Returns:
            Tuple of (formatted report string, dict with btc_eth_price_corr,
            btc_eth_dvol_corr, and -- only when btc_eth_dvol_corr was
            actually computed (bugfix_spec.md Item 11) -- btc_eth_dvol_corr_n,
            the aligned log-change observation count the "(log changes,
            Nd)" report label shows).
        """
        lines = []
        sub_separator = "-" * 80
        # Additional finding (same bug class as task A6 carried finding
        # #1): these two keys used to pre-seed at 0.0, indistinguishable
        # from a genuine zero correlation once a typed CrossAssetCorrelationResult
        # (whose fields are Optional[float]) was built from this dict via
        # plain .get() -- an insufficient-data run silently looked like a
        # measured zero correlation. Pre-seed None instead. The price-
        # correlation branch below always overwrites this with the real
        # (possibly-None) value; the DVOL-correlation branch only
        # overwrites it when there is enough aligned data to compute a
        # real number -- on the "Insufficient data"/"N/A" paths the None
        # pre-seed is what ships (task A7 review: the previous comment
        # claimed both branches always assign, which was only true for
        # price correlation).
        structured: Dict = {"btc_eth_price_corr": None, "btc_eth_dvol_corr": None}

        lines.append(f"CROSS-ASSET CORRELATION (30d, {self.currency}/{other_currency})")
        lines.append(sub_separator)

        # Price correlation
        price_corr = self._calculate_return_correlation(own_prices, other_prices)
        structured["btc_eth_price_corr"] = price_corr
        if price_corr is not None:
            lines.append(f"  Price Correlation: {price_corr:.2f}")
        else:
            lines.append("  Price Correlation: Insufficient data")

        # DVOL correlation (bugfix_spec.md Item 11): log CHANGES of the raw
        # DVOL levels, not the levels themselves -- matches
        # _calculate_return_correlation's log-return convention above, so
        # the report has one consistent co-movement basis instead of two
        # incompatible ones.
        if own_dvol_history and other_dvol_history:
            if len(own_dvol_history) != len(other_dvol_history):
                logger.warning(
                    "DVOL history length mismatch (%s=%d, %s=%d); aligning "
                    "from the most recent point on each side.",
                    self.currency, len(own_dvol_history),
                    other_currency, len(other_dvol_history),
                )
            # 31 levels -> 30 changes, matching the price correlation's own
            # `window + 1` convention above so the reported n lines up.
            # Both level windows are sliced to the SAME length from the
            # right (same underlying dates), then their changes are built
            # JOINTLY over aligned pairs (bugfix_spec.md section 11.4 edge
            # case) -- a non-positive value at a given step drops that step
            # from BOTH series, not just the one that had it, so the two
            # change series can never desynchronize by index.
            window = min(len(own_dvol_history), len(other_dvol_history), 31)
            own_level_window = own_dvol_history[-window:]
            other_level_window = other_dvol_history[-window:]
            own_changes, other_changes = self._aligned_log_changes(
                own_level_window, other_level_window
            )
            n = len(own_changes)
            if n >= MINIMUM_CORRELATION_OBSERVATIONS:
                own_window = own_changes
                other_window = other_changes
                # Zero (or float64-noise-level) variance in either series --
                # e.g. a perfectly steady log-linear trend -- makes the
                # correlation mathematically undefined (0/0), not a
                # meaningful number; corrcoef can return nan OR, when both
                # series happen to share identical rounding noise, an
                # extreme value that looks meaningful but isn't. Guard on
                # variance directly rather than trusting corrcoef's output.
                if np.var(own_window) < 1e-10 or np.var(other_window) < 1e-10:
                    lines.append("  DVOL Correlation: Insufficient data")
                else:
                    corr = float(np.corrcoef(own_window, other_window)[0, 1])
                    if math.isnan(corr):
                        lines.append("  DVOL Correlation: Insufficient data")
                    else:
                        structured["btc_eth_dvol_corr"] = corr
                        structured["btc_eth_dvol_corr_n"] = n
                        lines.append(f"  DVOL Correlation (log changes, {n}d): {corr:.2f}")
            else:
                lines.append("  DVOL Correlation: Insufficient data")
        else:
            lines.append("  DVOL Correlation: N/A")

        lines.append("")
        return "\n".join(lines), structured

    @staticmethod
    def _aligned_log_changes(
        series_a: List[float], series_b: List[float],
    ) -> Tuple[List[float], List[float]]:
        """
        Log changes (first differences of ln) of two SAME-LENGTH,
        date-aligned series, built JOINTLY over aligned pairs
        (bugfix_spec.md Item 11, section 11.4 edge case table).

        A non-positive value (or ``None``) on EITHER side at a given step
        drops that step from BOTH output series. Computing each series'
        changes independently (dropping a step from only the side that had
        the bad value) would desynchronize the two change series by index
        -- the correlation would then silently pair up changes from
        different dates. Building both series jointly here, rather than
        pushing "pass pre-aligned slices" responsibility onto the caller,
        is what task A7 review caught as unimplemented in the first cut of
        this fix (the caller never actually honored that contract).

        Args:
            series_a: Raw levels for one asset, index-aligned with series_b
                (same length, same underlying dates).
            series_b: Raw levels for the other asset.

        Returns:
            ``(changes_a, changes_b)`` -- same length as each other,
            possibly shorter than ``len(series_a) - 1`` if any steps were
            dropped.
        """
        if len(series_a) != len(series_b):
            raise ValueError(
                f"_aligned_log_changes requires same-length series, got "
                f"{len(series_a)} vs {len(series_b)}"
            )
        changes_a: List[float] = []
        changes_b: List[float] = []
        for i in range(1, len(series_a)):
            a_prev, a_curr = series_a[i - 1], series_a[i]
            b_prev, b_curr = series_b[i - 1], series_b[i]
            if (
                a_prev is not None and a_curr is not None
                and b_prev is not None and b_curr is not None
                and a_prev > 0 and a_curr > 0 and b_prev > 0 and b_curr > 0
            ):
                changes_a.append(math.log(a_curr / a_prev))
                changes_b.append(math.log(b_curr / b_prev))
        return changes_a, changes_b

    def _calculate_return_correlation(
        self,
        prices_a: List[Dict[str, float]],
        prices_b: List[Dict[str, float]],
        window: int = 30,
    ) -> Optional[float]:
        """Calculate correlation of log returns between two price series."""
        if not prices_a or not prices_b:
            return None

        # Align by taking last N entries
        a_closes = [float(p["close"]) for p in prices_a]
        b_closes = [float(p["close"]) for p in prices_b]

        min_len = min(len(a_closes), len(b_closes), window + 1)
        if min_len < 11:
            return None

        a_closes = a_closes[-min_len:]
        b_closes = b_closes[-min_len:]

        # Log returns
        a_returns = [
            math.log(a_closes[i] / a_closes[i - 1])
            for i in range(1, len(a_closes))
        ]
        b_returns = [
            math.log(b_closes[i] / b_closes[i - 1])
            for i in range(1, len(b_closes))
        ]

        if len(a_returns) < 5:
            return None

        return float(np.corrcoef(a_returns, b_returns)[0, 1])

    @staticmethod
    def _parse_expiry_datetime(expiration: str) -> Optional[datetime]:
        """
        Parse a Deribit expiry label ('28MAR26') to its settlement instant:
        08:00 UTC on that date, timezone-aware.

        bugfix_spec.md Item 5 (F5.3.1): Deribit options and dated futures
        settle at 08:00 UTC, not local midnight.
        """
        try:
            return datetime.strptime(expiration, "%d%b%y").replace(
                hour=DERIBIT_SETTLEMENT_HOUR_UTC, minute=0, second=0,
                microsecond=0, tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    @classmethod
    def _calculate_days_to_expiry(cls, expiration: str, now_utc: datetime) -> Optional[float]:
        """
        Exact fractional days to 08:00 UTC settlement. May be negative
        (already expired). ``now_utc`` must be timezone-aware.

        bugfix_spec.md Item 5 (F5.3.1) — replaces integer, local-midnight,
        truncated-toward-negative-infinity DTE with an exact value.
        """
        expiry = cls._parse_expiry_datetime(expiration)
        if expiry is None:
            return None
        return (expiry - now_utc).total_seconds() / 86400.0

    @classmethod
    def _calculate_dte(cls, expiration: str, now: datetime) -> Optional[int]:
        """
        Calculate (non-negative) integer days to expiration from expiration
        string, for the DTE display column only
        (calculate_iv_term_structure).

        Args:
            expiration: Expiration string like "28MAR26" or "27DEC24".
            now: Current datetime. Must be timezone-aware UTC
                (bugfix_spec.md Item 5 F5.3.1) — callers must pass
                ``datetime.now(timezone.utc)``, never ``datetime.now()``.

        Returns:
            Days to expiration (floor of the exact fractional value, clamped
            to 0 for past expirations), or None if parse fails.
        """
        exact = cls._calculate_days_to_expiry(expiration, now)
        if exact is None:
            return None
        return max(int(math.floor(exact)), 0)
