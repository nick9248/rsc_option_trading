"""
On-chain analysis service.

Orchestrates fetching and analyzing on-chain option data.
"""

import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class OnChainAnalysisService:
    """
    Service for fetching and analyzing on-chain option data.

    Handles data fetching, Greek fetching, GEX/DEX calculation,
    and report generation.
    """

    def __init__(
        self,
        api_service: DeribitApiService,
        repository: Optional[DatabaseRepository] = None
    ):
        """
        Initialize service with API service and optional database repository.

        Args:
            api_service: Deribit API service instance.
            repository: Database repository for querying trade data (optional).
        """
        self.api = api_service
        self.repository = repository

    def fetch_and_analyze(
        self,
        currency: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        return_analyzer: bool = False,
    ):
        """
        Fetch and analyze on-chain data for a currency.

        Always includes GEX/DEX and buy/sell flow analysis.

        Args:
            currency: Currency symbol (BTC, ETH).
            progress_callback: Optional callback for progress updates.
            return_analyzer: If True, return (report, analyzer) tuple instead of just report.

        Returns:
            Analysis report text string, or (report, analyzer) tuple if return_analyzer=True.
        """
        def progress(message: str):
            """Send progress update if callback provided."""
            if progress_callback:
                progress_callback(message)
            logger.info(message)

        progress(f"Fetching book summary for {currency} options...")

        all_data = self.api.get_book_summary(
            currency=currency,
            kind="option"
        )

        progress(f"Received {len(all_data)} instruments")

        # Create analyzer and parse data
        progress("Parsing instruments and grouping by expiration...")
        analyzer = OnChainAnalyzer(all_data, currency)
        analyzer.parse_instruments()

        expirations = analyzer.get_expirations()
        progress(f"Found {len(expirations)} expirations")

        # Fetch market metrics (DVOL, funding rate)
        self._fetch_market_metrics(analyzer, progress)

        # Always fetch Greeks for GEX/DEX
        self._fetch_greeks_and_store_gex_dex(analyzer, progress)

        # Always fetch buy/sell flow
        self._calculate_buy_sell_flow(analyzer, progress)

        # Calculate volatility surface metrics (uses enriched instruments)
        self._calculate_volatility_surface(analyzer, progress)

        # Calculate DB-dependent metrics (OI changes, IV percentile per expiry)
        self._calculate_oi_changes_and_iv_percentile(analyzer, progress)

        # Calculate market-wide metrics (term structure, basis, RV, VRP, etc.)
        self._calculate_market_wide_metrics(analyzer, currency, progress)

        # Fetch previous DB snapshots for trend comparison
        self._fetch_trend_data(analyzer, progress)

        # Generate report (includes GEX/DEX and flow)
        progress("Generating analysis report...")
        report = analyzer.generate_report()

        # Save reports per expiration
        self._save_reports_per_expiration(report, currency, analyzer)

        progress("Analysis complete")
        if return_analyzer:
            return report, analyzer
        return report

    def _fetch_greeks_and_store_gex_dex(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Fetch Greeks for all instruments and store GEX/DEX data in analyzer.

        Args:
            analyzer: OnChainAnalyzer with parsed data.
            progress_callback: Callback for progress updates.
        """
        for expiration in analyzer.get_expirations():
            instruments = analyzer.parsed_data.get(expiration, [])
            if not instruments:
                continue

            progress_callback(f"Fetching Greeks for {expiration} ({len(instruments)} instruments)...")

            # Fetch Greeks for each instrument
            instruments_with_greeks = []
            for i, item in enumerate(instruments):
                try:
                    ticker = self.api.get_ticker(item["instrument_name"])
                    greeks = ticker.get("greeks", {})

                    item_with_greeks = item.copy()
                    item_with_greeks["delta"] = greeks.get("delta")
                    item_with_greeks["gamma"] = greeks.get("gamma")
                    item_with_greeks["theta"] = greeks.get("theta")
                    item_with_greeks["vega"] = greeks.get("vega")
                    item_with_greeks["mark_iv"] = ticker.get("mark_iv")
                    item_with_greeks["underlying_price"] = ticker.get("underlying_price", analyzer.underlying_price)
                    instruments_with_greeks.append(item_with_greeks)

                    if (i + 1) % 20 == 0:
                        progress_callback(
                            f"  Fetched {i + 1}/{len(instruments)} for {expiration}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to fetch Greeks for {item['instrument_name']}: {e}")

            # Store enriched instruments for downstream calculators
            if instruments_with_greeks:
                analyzer.enriched_instruments[expiration] = instruments_with_greeks

                # Calculate GEX/DEX and store in analyzer
                progress_callback(f"Calculating GEX/DEX for {expiration}...")
                calculator = GexDexCalculator(
                    instruments_with_greeks,
                    analyzer.underlying_price,
                    currency=analyzer.currency
                )
                gex_structured = calculator.calculate()
                analyzer.set_gex_dex_structured(expiration, gex_structured)
                gex_dex_report = calculator.generate_report_section()
                analyzer.set_gex_dex_data(expiration, gex_dex_report)

    def _calculate_buy_sell_flow(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Calculate buy/sell flow for all expirations and store in analyzer.

        Also saves flow metrics to database for chart generation.

        Args:
            analyzer: OnChainAnalyzer with parsed data.
            progress_callback: Callback for progress updates.
        """
        if self.repository is None:
            logger.warning("Repository not available - skipping buy/sell flow analysis")
            progress_callback("Warning: Repository not available for buy/sell flow")
            return

        for expiration in analyzer.get_expirations():
            progress_callback(f"Calculating buy/sell flow for {expiration}...")

            try:
                flow_analyzer = BuySellFlowAnalyzer(
                    repository=self.repository,
                    currency=analyzer.currency,
                    expiration=expiration,
                    spot_price=analyzer.underlying_price,
                    lookback_hours=24
                )

                # Calculate flow data
                flow_result = flow_analyzer.calculate()

                # Save to database for chart queries
                try:
                    self.repository.save_flow_metrics(
                        currency=analyzer.currency,
                        expiration=expiration,
                        flow_data=flow_result["flow_data"],
                        underlying_price=analyzer.underlying_price,
                        window_hours=24
                    )
                    logger.info(f"Saved flow metrics to database for {expiration}")
                except Exception as save_error:
                    logger.warning(f"Failed to save flow metrics for {expiration}: {save_error}")

                # Store structured flow data and generate text report
                analyzer.set_buy_sell_flow_structured(expiration, flow_result)
                flow_report = flow_analyzer.generate_report_section()
                analyzer.set_buy_sell_flow_data(expiration, flow_report)

            except Exception as e:
                logger.warning(f"Failed to calculate buy/sell flow for {expiration}: {e}")
                progress_callback(f"Warning: Failed to calculate flow for {expiration}")

    def _calculate_volatility_surface(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Calculate volatility surface metrics for all expirations.

        Uses enriched instruments (already fetched during GEX/DEX phase).
        Also fetches recent trades for VWAP IV calculation.

        Args:
            analyzer: OnChainAnalyzer with enriched_instruments populated.
            progress_callback: Callback for progress updates.
        """
        if not analyzer.enriched_instruments:
            logger.warning("No enriched instruments available - skipping volatility surface")
            return

        # Fetch recent trades for VWAP IV (reused for block trade detection later)
        trades_by_expiration = {}
        try:
            progress_callback("Fetching recent trades for VWAP IV...")
            trade_result = self.api.get_last_trades_by_currency(
                currency=analyzer.currency,
                kind="option",
                count=1000
            )
            trades = trade_result.get("trades", [])
            progress_callback(f"  Received {len(trades)} recent trades")

            # Store all trades on analyzer for block trade detection in Phase 5
            analyzer._recent_trades = trades

            # Group trades by expiration
            for trade in trades:
                inst_name = trade.get("instrument_name", "")
                parts = inst_name.split("-")
                if len(parts) >= 4:
                    exp = parts[1]
                    if exp not in trades_by_expiration:
                        trades_by_expiration[exp] = []
                    trades_by_expiration[exp].append(trade)

        except Exception as e:
            logger.warning(f"Failed to fetch recent trades for VWAP IV: {e}")
            analyzer._recent_trades = []

        # Calculate per-expiration volatility surface
        for expiration, instruments in analyzer.enriched_instruments.items():
            try:
                progress_callback(f"Calculating volatility surface for {expiration}...")

                calculator = VolatilitySurfaceCalculator(
                    instruments=instruments,
                    spot_price=analyzer.underlying_price,
                    expiration=expiration,
                )

                # Calculate VWAP IV for this expiration
                exp_trades = trades_by_expiration.get(expiration, [])
                vwap_iv, mark_iv_avg = self._calculate_vwap_iv(exp_trades, instruments)
                calculator.set_vwap_iv_data(vwap_iv, mark_iv_avg)

                # Generate report section and store in analyzer
                surface_report = calculator.generate_report_section()
                analyzer.set_volatility_surface_data(expiration, surface_report)

                # Store ATM IV for term structure (used by market-wide calculator)
                result = calculator.calculate()
                analyzer.set_volatility_surface_structured(expiration, result)
                if result["atm_iv"] is not None:
                    if not hasattr(analyzer, '_atm_ivs'):
                        analyzer._atm_ivs = {}
                    analyzer._atm_ivs[expiration] = result["atm_iv"]

            except Exception as e:
                logger.warning(f"Failed to calculate volatility surface for {expiration}: {e}")

    def _calculate_vwap_iv(
        self,
        trades: List[Dict[str, Any]],
        instruments: List[Dict[str, Any]]
    ) -> tuple:
        """
        Calculate VWAP IV from recent trades and compare with mark IV.

        Args:
            trades: Recent trade records for one expiration.
            instruments: Enriched instrument data for the same expiration.

        Returns:
            Tuple of (vwap_iv, mark_iv_avg), both as percentages or None.
        """
        if not trades:
            return None, None

        # VWAP IV = Sum(IV × volume) / Sum(volume)
        weighted_iv_sum = 0.0
        total_volume = 0.0

        for trade in trades:
            iv = trade.get("iv")
            amount = trade.get("amount", 0)
            if iv is not None and iv > 0 and amount > 0:
                weighted_iv_sum += iv * amount
                total_volume += amount

        vwap_iv = (weighted_iv_sum / total_volume) if total_volume > 0 else None

        # Average mark IV from instruments
        mark_ivs = [i["mark_iv"] for i in instruments if i.get("mark_iv") is not None and i["mark_iv"] > 0]
        mark_iv_avg = (sum(mark_ivs) / len(mark_ivs)) if mark_ivs else None

        return vwap_iv, mark_iv_avg

    def _calculate_market_wide_metrics(
        self,
        analyzer: OnChainAnalyzer,
        currency: str,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Calculate all market-wide metrics and store in analyzer.

        Args:
            analyzer: OnChainAnalyzer to store results in.
            currency: Currency symbol.
            progress_callback: Callback for progress updates.
        """
        dvol = analyzer.market_metrics.get("dvol")
        calc = MarketWideCalculator(
            currency=currency,
            spot_price=analyzer.underlying_price,
            dvol=dvol,
        )

        # Accumulate structured data across all market-wide sections
        market_wide_structured: Dict[str, Any] = {}

        # 1. IV Term Structure (uses ATM IVs collected during vol surface phase)
        atm_ivs = getattr(analyzer, '_atm_ivs', {})
        if atm_ivs:
            progress_callback("Calculating IV term structure...")
            term_structure_text, term_struct = calc.calculate_iv_term_structure(atm_ivs)
            analyzer.set_market_wide_section("iv_term_structure", term_structure_text)
            market_wide_structured.update(term_struct)

        # 2. Futures Basis
        try:
            progress_callback("Fetching futures for basis calculation...")
            futures_instruments = self.api.get_instruments(
                currency=currency, kind="future", expired=False
            )

            futures_data = []
            for fut in futures_instruments:
                name = fut.get("instrument_name", "")
                # Skip perpetual (not a dated future)
                if "PERPETUAL" in name:
                    continue
                try:
                    ticker = self.api.get_ticker(name)
                    futures_data.append({
                        "instrument_name": name,
                        "mark_price": ticker.get("mark_price", 0),
                        "index_price": ticker.get("index_price", analyzer.underlying_price),
                    })
                except Exception as e:
                    logger.warning(f"Failed to fetch future ticker {name}: {e}")

            if futures_data:
                basis_text, basis_data = calc.calculate_futures_basis(futures_data)
                analyzer.set_market_wide_section("futures_basis", basis_text)
                market_wide_structured.update(basis_data)

        except Exception as e:
            logger.warning(f"Failed to calculate futures basis: {e}")

        # 3. Realized Volatility (multi-window) + 4. VRP + 5. Vol Cone
        price_history: List[Dict[str, Any]] = []
        rv_values: Dict[int, float] = {}
        try:
            progress_callback("Fetching price history for RV/VRP/Vol Cone...")
            end_ts = int(time.time() * 1000)
            start_ts = end_ts - (180 * 24 * 60 * 60 * 1000)  # 180 days

            chart_data = self.api.get_tradingview_chart_data(
                instrument_name=f"{currency}-PERPETUAL",
                resolution="1D",
                start_timestamp=start_ts,
                end_timestamp=end_ts,
            )

            if chart_data and "ticks" in chart_data:
                timestamps = chart_data["ticks"]
                closes = chart_data.get("close", [])

                for i, ts in enumerate(timestamps):
                    if i < len(closes):
                        price_history.append({
                            "timestamp": ts / 1000,
                            "close": closes[i],
                        })

            if price_history:
                # RV
                rv_report, rv_values = calc.calculate_realized_volatility_multi_window(
                    price_history
                )
                analyzer.set_market_wide_section("realized_volatility", rv_report)
                rv_structured = {
                    "rv_10d": rv_values.get(10, 0.0),
                    "rv_20d": rv_values.get(20, 0.0),
                    "rv_30d": rv_values.get(30, 0.0),
                }
                market_wide_structured.update(rv_structured)

                # VRP
                rv_30d = rv_values.get(30, 0)
                if rv_30d > 0:
                    vrp_text, vrp_data = calc.calculate_vrp(rv_30d)
                    analyzer.set_market_wide_section("vrp", vrp_text)
                    market_wide_structured.update(vrp_data)

                # Vol Cone
                cone_text, cone_data = calc.calculate_volatility_cone(price_history)
                analyzer.set_market_wide_section("volatility_cone", cone_text)
                market_wide_structured.update(cone_data)

        except Exception as e:
            logger.warning(f"Failed to calculate RV/VRP/Vol Cone: {e}")

        # 6. Perpetual Funding Trend
        try:
            progress_callback("Fetching perpetual funding trend...")
            funding_data = self.api.get_funding_chart_data(
                instrument_name=f"{currency}-PERPETUAL",
                length="1m",
            )
            perp_ticker = self.api.get_ticker(f"{currency}-PERPETUAL")

            funding_text, funding_data_struct = calc.calculate_perpetual_funding_trend(
                funding_data, perp_ticker
            )
            analyzer.set_market_wide_section("perpetual_funding", funding_text)
            market_wide_structured.update(funding_data_struct)

        except Exception as e:
            logger.warning(f"Failed to calculate perpetual funding trend: {e}")

        # 7. Block Trades (reuse trade data from VWAP IV phase)
        recent_trades = getattr(analyzer, '_recent_trades', [])
        if recent_trades:
            progress_callback("Detecting block trades...")
            block_text, block_data = calc.detect_block_trades(recent_trades)
            analyzer.set_market_wide_section("block_trades", block_text)
            market_wide_structured.update(block_data)

        # 8. Cross-Asset Correlation
        try:
            other_currency = "ETH" if currency == "BTC" else "BTC"
            progress_callback(f"Calculating {currency}/{other_currency} correlation...")

            end_ts = int(time.time() * 1000)
            start_ts = end_ts - (35 * 24 * 60 * 60 * 1000)  # 35 days

            other_chart = self.api.get_tradingview_chart_data(
                instrument_name=f"{other_currency}-PERPETUAL",
                resolution="1D",
                start_timestamp=start_ts,
                end_timestamp=end_ts,
            )

            other_prices = []
            if other_chart and "ticks" in other_chart:
                timestamps = other_chart["ticks"]
                closes = other_chart.get("close", [])
                for i, ts in enumerate(timestamps):
                    if i < len(closes):
                        other_prices.append({
                            "timestamp": ts / 1000,
                            "close": closes[i],
                        })

            # Own prices (reuse from RV calculation above, last 35 days)
            own_prices_30d = price_history[-35:] if price_history else []

            # DVOL histories for correlation
            own_dvol_history: List[float] = []
            other_dvol_history: List[float] = []

            try:
                for ccy, target_list in [
                    (currency, own_dvol_history),
                    (other_currency, other_dvol_history)
                ]:
                    dvol_data = self.api.get_volatility_index_data(
                        currency=ccy,
                        resolution=86400,
                        start_timestamp=start_ts,
                        end_timestamp=end_ts,
                    )
                    if dvol_data and "data" in dvol_data:
                        for point in dvol_data["data"]:
                            if len(point) > 4:
                                target_list.append(point[4])
            except Exception as e:
                logger.warning(f"Failed to fetch DVOL for correlation: {e}")

            corr_text, corr_data = calc.calculate_cross_asset_correlation(
                own_prices=own_prices_30d,
                other_prices=other_prices,
                own_dvol_history=own_dvol_history,
                other_dvol_history=other_dvol_history,
                other_currency=other_currency,
            )
            analyzer.set_market_wide_section("cross_asset_correlation", corr_text)
            market_wide_structured.update(corr_data)

        except Exception as e:
            logger.warning(f"Failed to calculate cross-asset correlation: {e}")

        # Store combined market-wide structured data
        dvol = analyzer.market_metrics.get("dvol") or 0.0
        market_wide_structured.update({
            "spot_price": analyzer.underlying_price,
            "dvol": dvol,
            "iv_percentile_365d": analyzer.market_metrics.get("iv_percentile") or 0.0,
        })
        # funding_rate: prefer the value from calculate_perpetual_funding_trend (same API
        # call as funding_8h) so both fields are consistent with the report's
        # PERPETUAL FUNDING section.  Fall back to market_metrics only if the
        # calculator didn't store it (e.g. funding endpoint failed).
        if "funding_rate" not in market_wide_structured:
            market_wide_structured["funding_rate"] = (
                analyzer.market_metrics.get("current_funding") or 0.0
            )
        analyzer.set_market_wide_structured(market_wide_structured)

    def _calculate_oi_changes_and_iv_percentile(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Calculate OI day-over-day changes and IV percentile per expiry.

        Saves current day's OI snapshot and compares with previous day.
        Requires database repository.

        Args:
            analyzer: OnChainAnalyzer with enriched_instruments populated.
            progress_callback: Callback for progress updates.
        """
        if self.repository is None:
            logger.warning("Repository not available - skipping OI changes and IV percentile")
            return

        if not analyzer.enriched_instruments:
            return

        for expiration, instruments in analyzer.enriched_instruments.items():
            try:
                # Save today's OI snapshot (UPSERT, safe to call multiple times/day)
                self.repository.save_daily_oi_snapshot(
                    currency=analyzer.currency,
                    expiration=expiration,
                    instruments=instruments,
                    underlying_price=analyzer.underlying_price,
                )

                # Get previous day's snapshot for OI change detection
                prev_oi = self.repository.get_previous_oi_snapshot(
                    currency=analyzer.currency,
                    expiration=expiration,
                )

                # Calculate OI changes
                oi_changes_report = self._format_oi_changes(
                    instruments, prev_oi, expiration
                )
                if oi_changes_report:
                    analyzer.set_oi_changes_data(expiration, oi_changes_report)

                # Calculate IV percentile per expiry
                # Find ATM strike (closest to spot)
                atm_strike = min(
                    instruments,
                    key=lambda i: abs(i["strike"] - analyzer.underlying_price)
                )["strike"]

                iv_history = self.repository.get_atm_iv_history(
                    currency=analyzer.currency,
                    expiration=expiration,
                    strike=atm_strike,
                )

                if iv_history and len(iv_history) >= 5:
                    current_iv = next(
                        (i["mark_iv"] for i in instruments
                         if i["strike"] == atm_strike and i["option_type"] == "C"
                         and i.get("mark_iv") is not None),
                        None
                    )
                    if current_iv is not None:
                        historical_ivs = [
                            float(h["mark_iv"]) for h in iv_history
                            if h["mark_iv"] is not None
                        ]
                        below = sum(1 for iv in historical_ivs if iv < current_iv)
                        percentile = (below / len(historical_ivs)) * 100

                        # Append to the OI changes section
                        existing = analyzer.oi_changes_data.get(expiration, "")
                        iv_section = (
                            f"IV PERCENTILE (per-expiry, {len(historical_ivs)} days history)\n"
                            f"{'-' * 80}\n"
                            f"ATM Strike: ${atm_strike:,.0f}  |  Current IV: {current_iv:.1f}%  |  "
                            f"Percentile: {percentile:.1f}%\n"
                        )
                        if percentile >= 80:
                            iv_section += "  IV is very high relative to history - favor selling vol\n"
                        elif percentile <= 20:
                            iv_section += "  IV is very low relative to history - favor buying vol\n"
                        iv_section += "\n"

                        analyzer.set_oi_changes_data(
                            expiration,
                            existing + iv_section if existing else iv_section
                        )

            except Exception as e:
                logger.warning(f"Failed to calculate OI changes for {expiration}: {e}")

    def _format_oi_changes(
        self,
        instruments: List[Dict[str, Any]],
        prev_oi: Dict,
        expiration: str
    ) -> Optional[str]:
        """
        Format OI day-over-day changes report.

        Args:
            instruments: Current enriched instruments.
            prev_oi: Previous day's OI mapping {(strike, type): oi}.
            expiration: Expiration date string.

        Returns:
            Formatted report string, or None if no previous data.
        """
        if not prev_oi:
            return None

        lines = []
        sub_separator = "-" * 80
        lines.append("LARGE OI CHANGES (Day-over-Day)")
        lines.append(sub_separator)

        significant_changes = []
        for inst in instruments:
            strike = inst["strike"]
            opt_type = inst["option_type"]
            current_oi = inst.get("open_interest", 0)
            key = (strike, opt_type)

            if key in prev_oi:
                prev = prev_oi[key]
                if prev > 0:
                    change_pct = ((current_oi - prev) / prev) * 100
                    abs_change = current_oi - prev

                    if abs(change_pct) >= 20 and abs(abs_change) >= 10:
                        significant_changes.append({
                            "strike": strike,
                            "type": opt_type,
                            "prev_oi": prev,
                            "current_oi": current_oi,
                            "change": abs_change,
                            "change_pct": change_pct,
                        })

        if not significant_changes:
            lines.append("  No significant OI changes (>20%) detected")
            lines.append("")
            return "\n".join(lines)

        # Sort by absolute change percentage
        significant_changes.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

        lines.append(
            f"  {'Strike':>10}  {'Type':>4}  {'Prev OI':>10}  "
            f"{'Curr OI':>10}  {'Change':>10}  {'Change%':>8}"
        )
        lines.append(
            f"  {'------':>10}  {'----':>4}  {'-------':>10}  "
            f"{'-------':>10}  {'------':>10}  {'-------':>8}"
        )

        for c in significant_changes[:15]:
            type_label = "Call" if c["type"] == "C" else "Put"
            lines.append(
                f"  {c['strike']:>10,.0f}  {type_label:>4}  "
                f"{c['prev_oi']:>10,.0f}  {c['current_oi']:>10,.0f}  "
                f"{c['change']:>+10,.0f}  {c['change_pct']:>+7.1f}%"
            )

        lines.append("")
        return "\n".join(lines)

    def _fetch_market_metrics(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Fetch market-wide metrics (DVOL, funding rate) and store in analyzer.

        Args:
            analyzer: OnChainAnalyzer to store metrics in.
            progress_callback: Callback for progress updates.
        """
        dvol = None
        iv_percentile = None
        iv_rank = None
        current_funding = None
        funding_8h = None

        # Fetch DVOL data for past 365 days
        try:
            progress_callback("Fetching DVOL data for IV percentile calculation...")

            end_timestamp = int(time.time() * 1000)
            start_timestamp = end_timestamp - (365 * 24 * 60 * 60 * 1000)  # 365 days ago

            dvol_data = self.api.get_volatility_index_data(
                currency=analyzer.currency,
                resolution=86400,  # Daily resolution for 365 days
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp
            )

            if dvol_data and "data" in dvol_data and dvol_data["data"]:
                # Data format: [timestamp, open, high, low, close]
                valid_points = [point for point in dvol_data["data"] if len(point) > 4]
                close_values = [point[4] for point in valid_points]
                high_values = [point[2] for point in valid_points]
                low_values = [point[3] for point in valid_points]

                if close_values:
                    dvol = close_values[-1]  # Current DVOL (most recent close)

                    # Calculate IV percentile (% of daily closes below current)
                    values_below = sum(1 for v in close_values if v < dvol)
                    iv_percentile = (values_below / len(close_values)) * 100

                    # Calculate IV rank using true range (daily high/low) — matches Deribit website
                    # Deribit uses max(daily_high) and min(daily_low) for the 365d range
                    dvol_min = min(low_values)
                    dvol_max = max(high_values)
                    if dvol_max > dvol_min:
                        iv_rank = (dvol - dvol_min) / (dvol_max - dvol_min) * 100
                    else:
                        iv_rank = 50.0

                    progress_callback(
                        f"DVOL: {dvol:.2f}, IV Percentile: {iv_percentile:.1f}%, "
                        f"IV Rank: {iv_rank:.1f}% "
                        f"(based on {len(close_values)} days)"
                    )

        except Exception as e:
            logger.warning(f"Failed to fetch DVOL data: {e}")

        # Fetch funding rate from perpetual ticker
        try:
            progress_callback("Fetching funding rate...")

            perpetual_ticker = self.api.get_ticker(f"{analyzer.currency}-PERPETUAL")
            current_funding = perpetual_ticker.get("current_funding")
            funding_8h = perpetual_ticker.get("funding_8h")

            if current_funding is not None:
                funding_str = f"Current Funding: {current_funding * 100:.4f}%"
                if funding_8h is not None:
                    funding_str += f", 8h Funding: {funding_8h * 100:.4f}%"
                progress_callback(funding_str)

        except Exception as e:
            logger.warning(f"Failed to fetch funding rate: {e}")

        # Store in analyzer
        analyzer.set_market_metrics(
            dvol=dvol,
            iv_percentile=iv_percentile,
            iv_rank=iv_rank,
            current_funding=current_funding,
            funding_8h=funding_8h
        )

    def _fetch_trend_data(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None],
    ) -> None:
        """
        Fetch previous DB snapshots per expiration for trend comparison.

        Requires repository. Silently skipped when repository is None.
        Each expiration gets the oldest of the 2 most-recent DB records
        as its "previous" value to compare against live API data.

        Args:
            analyzer: OnChainAnalyzer with parsed data.
            progress_callback: Callback for progress updates.
        """
        if self.repository is None:
            return

        progress_callback("Fetching trend data for report comparison...")

        for expiration in analyzer.get_expirations():
            try:
                mp_history = self.repository.get_max_pain_history(
                    analyzer.currency, expiration, limit=2
                )
                oi_history = self.repository.get_open_interest_history(
                    analyzer.currency, expiration, limit=2
                )
                vol_history = self.repository.get_volume_history(
                    analyzer.currency, expiration, limit=2
                )

                prev_mp = mp_history[0] if mp_history else None
                prev_oi = oi_history[0] if oi_history else None
                prev_vol = vol_history[0] if vol_history else None

                if not any([prev_mp, prev_oi, prev_vol]):
                    analyzer.set_trend_data(expiration, None)
                    continue

                trend: Dict[str, Any] = {}
                if prev_mp:
                    trend["max_pain_strike"] = float(prev_mp["max_pain_strike"])
                if prev_oi:
                    trend["call_oi"] = float(prev_oi["total_call_oi"])
                    trend["put_oi"] = float(prev_oi["total_put_oi"])
                    pc = prev_oi.get("put_call_ratio")
                    trend["pc_ratio"] = float(pc) if pc is not None else None
                if prev_vol:
                    trend["total_volume"] = (
                        float(prev_vol["total_call_volume"])
                        + float(prev_vol["total_put_volume"])
                    )
                    vr = prev_vol.get("volume_put_call_ratio")
                    trend["volume_ratio"] = float(vr) if vr is not None else None

                analyzer.set_trend_data(expiration, trend)

            except Exception as e:
                logger.warning(f"Failed to fetch trend data for {expiration}: {e}")
                analyzer.set_trend_data(expiration, None)

    def _save_reports_per_expiration(
        self,
        full_report: str,
        currency: str,
        analyzer: OnChainAnalyzer
    ) -> None:
        """
        Parse full report and save per-expiration sections.

        Each expiration folder gets only its section (header + expiration data).
        Full report remains in GUI only.

        Directory: output/data/onchain_analysis/{currency}/{expiration}/
        Filename: report_{timestamp}.txt

        Args:
            full_report: Full analysis report text.
            currency: Currency symbol (BTC, ETH).
            analyzer: OnChainAnalyzer instance with parsed data.
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Get project root (3 levels up from this file)
            project_root = Path(__file__).parent.parent.parent.parent

            # Split report into lines for parsing
            lines = full_report.split('\n')

            # Find header (everything before first "EXPIRATION:")
            header_lines = []
            first_exp_idx = None
            for i, line in enumerate(lines):
                if line.startswith("EXPIRATION:"):
                    first_exp_idx = i
                    break
                header_lines.append(line)

            if first_exp_idx is None:
                logger.warning("No EXPIRATION sections found in report")
                return

            header = '\n'.join(header_lines)

            # Find all expiration sections
            exp_sections = {}
            current_exp = None
            current_lines = []

            for i in range(first_exp_idx, len(lines)):
                line = lines[i]

                if line.startswith("EXPIRATION:"):
                    # Save previous section if exists
                    if current_exp and current_lines:
                        exp_sections[current_exp] = '\n'.join(current_lines)

                    # Start new section
                    current_exp = line.split(":", 1)[1].strip()
                    current_lines = [line]
                elif current_exp:
                    current_lines.append(line)

            # Save last section
            if current_exp and current_lines:
                exp_sections[current_exp] = '\n'.join(current_lines)

            logger.info(f"Parsed {len(exp_sections)} expiration sections")

            # Save each section to its folder
            for expiration, section_content in exp_sections.items():
                # Create directory structure
                output_dir = project_root / "output" / "data" / "onchain_analysis" / currency / expiration
                output_dir.mkdir(parents=True, exist_ok=True)

                # Save header + section (not full report)
                report_path = output_dir / f"report_{timestamp}.txt"
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(header)
                    if header and not header.endswith('\n'):
                        f.write('\n')
                    f.write('\n')
                    f.write(section_content)

                logger.info(f"Saved report for {expiration} to {report_path}")

        except Exception as e:
            logger.error(f"Failed to save per-expiration reports: {e}", exc_info=True)
