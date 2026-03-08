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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from coding.core.analytics.vrp_calculator import VRPCalculator

logger = logging.getLogger(__name__)


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
        now = datetime.now()

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
    ) -> Tuple[str, Dict]:
        """
        Generate futures basis report.

        Args:
            futures_data: List of dicts with instrument_name, mark_price,
                         index_price, and expiration info.

        Returns:
            Tuple of (formatted report string, dict mapping expiry -> annualized premium).
        """
        lines = []
        sub_separator = "-" * 80
        basis_dict: Dict[str, float] = {}

        lines.append("FUTURES BASIS")
        lines.append(sub_separator)

        if not futures_data:
            lines.append("  No futures data available")
            lines.append("")
            return "\n".join(lines), {"futures_basis": basis_dict}

        lines.append(
            f"  {'Future':>20}  {'Price':>12}  {'Spot':>12}  {'Ann. Premium':>12}"
        )
        lines.append(
            f"  {'------':>20}  {'-----':>12}  {'----':>12}  {'------------':>12}"
        )

        for future in futures_data:
            name = future.get("instrument_name", "")
            price = future.get("mark_price", 0)
            spot = future.get("index_price", self.spot_price)

            if spot <= 0 or price <= 0:
                continue

            # Calculate DTE
            parts = name.split("-")
            if len(parts) >= 2:
                dte = self._calculate_dte(parts[1], datetime.now())
                expiry_label = parts[1]
            else:
                dte = None
                expiry_label = name

            # Annualized basis
            basis_pct = ((price - spot) / spot) * 100
            if dte and dte > 0:
                ann_premium = basis_pct * (365 / dte)
            else:
                ann_premium = basis_pct

            basis_dict[expiry_label] = ann_premium

            lines.append(
                f"  {name:>20}  ${price:>11,.0f}  ${spot:>11,.0f}  "
                f"{ann_premium:>11.1f}%"
            )

        lines.append("")
        return "\n".join(lines), {"futures_basis": basis_dict}

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

    def calculate_perpetual_funding_trend(
        self,
        funding_data: Dict[str, Any],
        perp_ticker: Dict[str, Any],
    ) -> Tuple[str, Dict]:
        """
        Generate perpetual funding trend report.

        Args:
            funding_data: Funding chart data from API.
            perp_ticker: Perpetual ticker data with OI and funding.

        Returns:
            Tuple of (formatted report string, dict with perp_oi, perp_funding_trend, funding_8h).
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

        if current_funding is not None:
            funding_pct = current_funding * 100

            # Determine trend from funding chart data
            trend = "N/A"
            if funding_data and "data" in funding_data:
                data_points = funding_data["data"]
                if isinstance(data_points, list) and len(data_points) >= 2:
                    # Data format varies, try to extract funding rates
                    try:
                        if isinstance(data_points[0], list):
                            recent_rates = [p[1] for p in data_points[-10:]]
                        elif isinstance(data_points[0], dict):
                            recent_rates = [
                                p.get("funding_rate", p.get("value", 0))
                                for p in data_points[-10:]
                            ]
                        else:
                            recent_rates = []

                        if len(recent_rates) >= 2:
                            avg_recent = np.mean(recent_rates[-3:])
                            avg_older = np.mean(recent_rates[:3])
                            if avg_recent > avg_older * 1.2:
                                trend = "Rising"
                            elif avg_recent < avg_older * 0.8:
                                trend = "Falling"
                            else:
                                trend = "Stable"
                    except (IndexError, TypeError):
                        trend = "N/A"

            structured["perp_funding_trend"] = trend

            lines.append(
                f"  Perp OI: {perp_oi:,.0f} USD  |  "
                f"Funding: {funding_pct:.4f}%  |  Trend: {trend}"
            )

            if funding_8h is not None:
                ann_funding = current_funding * 3 * 365 * 100
                lines.append(
                    f"  8h Funding: {funding_8h * 100:.4f}%  |  "
                    f"Annualized: {ann_funding:.1f}%"
                )
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
    def _calculate_dte(expiration: str, now: datetime) -> Optional[int]:
        """
        Calculate days to expiration from expiration string.

        Args:
            expiration: Expiration string like "28MAR26" or "27DEC24".
            now: Current datetime.

        Returns:
            Days to expiration, or None if parse fails.
        """
        try:
            exp_date = datetime.strptime(expiration, "%d%b%y")
            dte = (exp_date - now).days
            return max(dte, 0)
        except ValueError:
            return None
