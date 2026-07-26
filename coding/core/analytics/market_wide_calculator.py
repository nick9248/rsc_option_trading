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
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from coding.core.analytics.results.market_wide_results import FuturesBasisEntry, FuturesBasisResult
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

        if not price_history or len(price_history) < 35:
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
        notional_threshold: float = 100_000,
    ) -> Tuple[str, Dict]:
        """
        Detect and report block trades (large notional trades).

        Args:
            trades: Recent trade records from API.
            notional_threshold: Minimum notional value in USD.

        Returns:
            Tuple of (formatted report string, dict with block_trades list).
        """
        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"block_trades": []}

        lines.append("BLOCK TRADES (>${:,.0f} notional)".format(notional_threshold))
        lines.append(sub_separator)

        if not trades:
            lines.append("  No recent trade data available")
            lines.append("")
            return "\n".join(lines), structured

        block_trades = []
        for trade in trades:
            amount = trade.get("amount", 0)
            price = trade.get("price", 0)
            index_price = trade.get("index_price", self.spot_price)

            # Notional = amount × underlying price
            notional = amount * index_price

            if notional >= notional_threshold:
                block_trades.append({
                    "timestamp": trade.get("timestamp"),
                    "instrument": trade.get("instrument_name", ""),
                    "size": amount,
                    "amount": amount,
                    "direction": trade.get("direction", ""),
                    "notional": notional,
                    "iv": trade.get("iv"),
                })

        if not block_trades:
            lines.append("  No block trades detected in recent activity")
            lines.append("")
            return "\n".join(lines), structured

        # Sort by notional descending
        block_trades.sort(key=lambda x: x["notional"], reverse=True)
        structured["block_trades"] = block_trades[:10]

        lines.append(
            f"  {'Time':>12}  {'Instrument':>25}  {'Size':>8}  "
            f"{'Dir':>5}  {'Notional':>14}  {'IV':>6}"
        )
        lines.append(
            f"  {'----':>12}  {'----------':>25}  {'----':>8}  "
            f"{'---':>5}  {'--------':>14}  {'--':>6}"
        )

        for bt in block_trades[:10]:
            ts = bt["timestamp"]
            if ts:
                time_str = datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S")
            else:
                time_str = "N/A"

            iv_str = f"{bt['iv']:.1f}%" if bt["iv"] else "N/A"

            lines.append(
                f"  {time_str:>12}  {bt['instrument']:>25}  "
                f"{bt['amount']:>8.1f}  {bt['direction']:>5}  "
                f"${bt['notional']:>13,.0f}  {iv_str:>6}"
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
            Tuple of (formatted report string, dict with btc_eth_price_corr and btc_eth_dvol_corr).
        """
        lines = []
        sub_separator = "-" * 80
        structured: Dict = {"btc_eth_price_corr": 0.0, "btc_eth_dvol_corr": 0.0}

        lines.append(f"CROSS-ASSET CORRELATION (30d, {self.currency}/{other_currency})")
        lines.append(sub_separator)

        # Price correlation
        price_corr = self._calculate_return_correlation(own_prices, other_prices)
        if price_corr is not None:
            structured["btc_eth_price_corr"] = price_corr
            lines.append(f"  Price Correlation: {price_corr:.2f}")
        else:
            lines.append("  Price Correlation: Insufficient data")

        # DVOL correlation
        dvol_corr = 0.0
        if own_dvol_history and other_dvol_history:
            min_len = min(len(own_dvol_history), len(other_dvol_history), 30)
            if min_len >= 10:
                own_slice = own_dvol_history[-min_len:]
                other_slice = other_dvol_history[-min_len:]
                dvol_corr = float(np.corrcoef(own_slice, other_slice)[0, 1])
                structured["btc_eth_dvol_corr"] = dvol_corr
                lines.append(f"  DVOL Correlation: {dvol_corr:.2f}")
            else:
                lines.append("  DVOL Correlation: Insufficient data")
        else:
            lines.append("  DVOL Correlation: N/A")

        lines.append("")
        return "\n".join(lines), structured

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
