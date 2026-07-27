"""
On-chain analysis service.

Orchestrates fetching and analyzing on-chain option data.
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.chart_generator import (
    generate_flow_distribution_chart,
    generate_flow_trend_chart,
    generate_net_flow_chart,
    inject_hover_js,
    save_chart,
)
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.on_chain_analyzer import (
    OnChainMetricsCalculator,
    _to_market_metrics,
    _to_trend_snapshot,
)
from coding.core.analytics.market_wide_calculator import (
    MINIMUM_PRICE_HISTORY_DAYS_FOR_VOL_CONE,
    MarketWideCalculator,
)
from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter
from coding.core.analytics.results.analysis_result import (
    IvPercentileResult,
    OiChangeRow,
    OiChangesResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.market_wide_results import (
    BlockTrade,
    BlockTradesResult,
    CrossAssetCorrelationResult,
    MarketWideResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    TermStructureEntry,
    TermStructureResult,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
    VolatilityConeWindowStats,
)
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.analysis_builder import OnChainAnalysisBuilder

logger = logging.getLogger(__name__)

_EXPECTED_FUNDING_RESOLUTION_MS = 3_600_000  # 1 hour


def _warn_if_funding_resolution_unexpected(funding_data: Dict[str, Any]) -> None:
    """
    bugfix_spec.md Item 4 (F4.3): ``get_funding_chart_data(length="1m")`` is
    expected to return hourly points. Log a warning (do not raise — the
    trend classifier degrades gracefully to "N/A" on too few points) if the
    median timestamp delta is not ~3,600,000 ms, so a future API resolution
    change is loud rather than silently corrupting the trend window sizes.
    """
    if not isinstance(funding_data, dict):
        return
    points = funding_data.get("data")
    if not isinstance(points, list) or len(points) < 2:
        return
    try:
        timestamps = sorted(p["timestamp"] for p in points if isinstance(p, dict) and "timestamp" in p)
    except (TypeError, KeyError):
        return
    if len(timestamps) < 2:
        return
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]
    deltas.sort()
    median_delta = deltas[len(deltas) // 2]
    if median_delta != _EXPECTED_FUNDING_RESOLUTION_MS:
        logger.warning(
            "Funding chart data median timestamp delta is %d ms, expected %d ms "
            "(hourly) — trend window sizing assumes hourly resolution.",
            median_delta, _EXPECTED_FUNDING_RESOLUTION_MS,
        )


class OnChainAnalysisService:
    """
    Service for fetching and analyzing on-chain option data.

    Handles data fetching, Greek fetching, GEX/DEX calculation,
    and report generation.
    """

    def __init__(
        self,
        api_service: Optional[DeribitApiService] = None,
        repository: Optional[DatabaseRepository] = None
    ):
        """
        Initialize service with API service and optional database repository.

        Args:
            api_service: Deribit API service instance (optional for read-only DB operations).
            repository: Database repository for querying trade data (optional).
        """
        self.api = api_service
        self.repository = repository

    @classmethod
    def create_default(cls) -> "OnChainAnalysisService":
        """
        Construct with a freshly-built ``DatabaseRepository`` and no API
        service (read-only DB operations only).

        T9 review fix (refactor_design_spec.md): the GUI must not construct
        ``DatabaseRepository`` itself ("zero business logic, zero direct
        repository/API access" is the review bar) -- this factory lives in
        the service layer instead, the same lazy-construction pattern
        ``OnChainWorkflowService.run`` already uses for its own
        dependencies, so a caller like ``OnChainAnalysisTab._open_flow_charts``
        can get a fully-wired service with zero repository import of its own.
        """
        return cls(repository=DatabaseRepository())

    def fetch_and_analyze(
        self,
        currency: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        return_analyzer: bool = False,
        return_result: bool = False,
    ):
        """
        Fetch and analyze on-chain data for a currency.

        Always includes GEX/DEX and buy/sell flow analysis.

        T6 (refactor_design_spec.md): every phase now dual-writes — the
        analyzer (the live report/synthesis/persistence path, unchanged)
        and an ``OnChainAnalysisBuilder`` (the new typed aggregate path,
        additive and currently unconsumed by anything but
        ``return_result=True`` callers and its own parity test).

        Args:
            currency: Currency symbol (BTC, ETH).
            progress_callback: Optional callback for progress updates.
            return_analyzer: If True, include the analyzer in the return value.
            return_result: If True, include the typed ``OnChainAnalysisResult``
                in the return value (temporary — T6).

        Returns:
            Analysis report text string; or a tuple growing with
            ``return_analyzer``/``return_result``, in that order
            (``(report, analyzer)``, ``(report, result)``, or
            ``(report, analyzer, result)`` when both are set).
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
        analyzer = OnChainMetricsCalculator(all_data, currency)
        analyzer.parse_instruments()

        expirations = analyzer.get_expirations()
        progress(f"Found {len(expirations)} expirations")

        builder = OnChainAnalysisBuilder(currency, analyzer.underlying_price, analyzer.parsed_data)
        for expiration in expirations:
            # T10 (refactor_design_spec.md): analyze_expiration() now
            # returns the typed ExpirationAnalysisResult directly (the
            # adapter that used to live here is now inside the method
            # itself) -- Optional[...], not the legacy dict, so the
            # emptiness check is `is not None`, not truthiness.
            analysis = analyzer.analyze_expiration(expiration)
            if analysis is not None:
                builder.set_expiration_analysis(expiration, analysis)

        # Fetch market metrics (DVOL, funding rate)
        self._fetch_market_metrics(analyzer, progress, builder)

        # Always fetch Greeks for GEX/DEX
        aggregate_gex_dex_result = self._fetch_greeks_and_store_gex_dex(analyzer, progress, builder)

        # Always fetch buy/sell flow
        self._calculate_buy_sell_flow(analyzer, progress, builder)

        # Calculate volatility surface metrics (uses enriched instruments)
        self._calculate_volatility_surface(analyzer, progress, builder)

        # Calculate DB-dependent metrics (OI changes, IV percentile per expiry)
        self._calculate_oi_changes_and_iv_percentile(analyzer, progress, builder)

        # Calculate market-wide metrics (term structure, basis, RV, VRP, etc.)
        self._calculate_market_wide_metrics(
            analyzer, currency, progress, builder, aggregate_gex_dex_result
        )

        # Fetch previous DB snapshots for trend comparison
        self._fetch_trend_data(analyzer, progress, builder)

        result = builder.build()

        # Generate report (includes GEX/DEX and flow) — rendered directly
        # from the typed result (T10). analyzer.generate_report() (a pure
        # delegator to OnChainReportFormatter.render_full as of T3) is
        # deleted; render_full_from_result is the sole full-report render
        # path now, and the first live call site for
        # render_market_wide_from_result (dead code before this task).
        progress("Generating analysis report...")
        report = OnChainReportFormatter().render_full_from_result(result)

        # Save reports per expiration — rendered from the typed result (T8),
        # not the report text (no string scanning).
        self._save_reports_per_expiration(result, currency)

        progress("Analysis complete")
        if return_analyzer and return_result:
            return report, analyzer, result
        if return_analyzer:
            return report, analyzer
        if return_result:
            return report, result
        return report

    def _fetch_greeks_and_store_gex_dex(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> Optional[Any]:
        """
        Fetch Greeks for all instruments and store GEX/DEX data in analyzer.

        Args:
            analyzer: OnChainMetricsCalculator with parsed data.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).

        Returns:
            The typed aggregate ``GexDexResult`` (or ``None`` if no
            expiration produced enriched instruments) — the caller threads
            it into ``_calculate_market_wide_metrics``, which needs it for
            ``MarketWideResult.aggregate_gex_dex``.
        """
        gex_dex_typed_by_expiry: Dict[str, Any] = {}

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
                if builder is not None:
                    builder.set_enriched_instruments(expiration, instruments_with_greeks)

                # Calculate GEX/DEX and store in analyzer
                progress_callback(f"Calculating GEX/DEX for {expiration}...")
                calculator = GexDexCalculator(
                    instruments_with_greeks,
                    analyzer.underlying_price,
                    currency=analyzer.currency
                )
                # T4 (refactor_design_spec.md): calculate() returns the typed
                # GexDexResult, kept only long enough to feed
                # aggregate_across_expirations (also typed, T4) below and
                # the builder. T10: the analyzer's own gex_dex_structured/
                # gex_dex_data text-and-dict bookkeeping is deleted --
                # rendering is render_full_from_result's job now
                # (format_gex_dex_section, operating on the typed result).
                gex_result = calculator.calculate()
                gex_dex_typed_by_expiry[expiration] = gex_result
                if builder is not None:
                    builder.set_gex_dex(expiration, gex_result)

        # Aggregate GEX/DEX across all expirations after per-expiry loop
        aggregate_result = None
        if gex_dex_typed_by_expiry:
            progress_callback("Calculating market-wide aggregate GEX/DEX...")
            aggregate_result = GexDexCalculator.aggregate_across_expirations(
                gex_dex_typed_by_expiry, analyzer.underlying_price, analyzer.currency
            )

        return aggregate_result

    def _calculate_buy_sell_flow(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> None:
        """
        Calculate buy/sell flow for all expirations and store in analyzer.

        Also saves flow metrics to database for chart generation.

        Args:
            analyzer: OnChainMetricsCalculator with parsed data.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
        """
        if self.repository is None:
            logger.warning("Repository not available - skipping buy/sell flow analysis")
            progress_callback("Warning: Repository not available for buy/sell flow")
            return

        for expiration in analyzer.get_expirations():
            progress_callback(f"Calculating buy/sell flow for {expiration}...")

            try:
                # T5 (refactor_design_spec.md): a SINGLE fetch here, with an
                # explicit window, replaces the analyzer's old self-fetching
                # calculate() + generate_report_section() pair — closes
                # bugfix_spec.md Item 6a (double DB query, two independently
                # computed "now" instants).
                window_end = datetime.now()
                window_start = window_end - timedelta(hours=24)
                window_start_ms = int(window_start.timestamp() * 1000)
                window_end_ms = int(window_end.timestamp() * 1000)

                trades = self.repository.get_trades_for_flow_analysis(
                    currency=analyzer.currency,
                    expiration=expiration,
                    start_ts=window_start_ms,
                    end_ts=window_end_ms,
                    trade_filter="all",
                )

                flow_analyzer = BuySellFlowAnalyzer(
                    trades=trades,
                    currency=analyzer.currency,
                    expiration=expiration,
                    spot_price=analyzer.underlying_price,
                    window_start_ms=window_start_ms,
                    window_end_ms=window_end_ms,
                )

                # Calculate once — pass result to storage, DB save, charts,
                # and the report formatter, so all four describe one instant.
                flow_result = flow_analyzer.calculate()
                flow_result_dict = flow_result.to_dict()
                # bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review):
                # to_dict()'s legacy shim doesn't carry the data-sufficiency
                # gate. Add it so generate_report() can read sufficient_data
                # per expiration for the EVIDENCE line — a one-line lookup,
                # not a refactor.
                flow_result_dict["sufficient_data"] = flow_result.sufficient_data
                flow_result_dict["low_confidence"] = flow_result.low_confidence
                flow_result_dict["lookback_hours"] = flow_result.lookback_hours

                # Save to database for chart queries
                try:
                    self.repository.save_flow_metrics(
                        currency=analyzer.currency,
                        expiration=expiration,
                        flow_data=flow_result_dict["flow_data"],
                        underlying_price=analyzer.underlying_price,
                        window_hours=24
                    )
                    logger.info(f"Saved flow metrics to database for {expiration}")
                except Exception as save_error:
                    logger.warning(f"Failed to save flow metrics for {expiration}: {save_error}")

                # Save flow charts to output/charts/flow_analysis/<expiration>/
                chart_paths: Dict[str, str] = {}
                try:
                    subfolder = f"flow_analysis/{expiration}"

                    fig_dist = generate_flow_distribution_chart(
                        flow_data=flow_result_dict,
                        spot_price=analyzer.underlying_price,
                        currency=analyzer.currency,
                        expiration=expiration,
                    )
                    dist_path = save_chart(
                        fig_dist, f"flow_distribution_{analyzer.currency}_{expiration}",
                        subfolder=subfolder, save_png=False,
                    )
                    inject_hover_js(Path(dist_path))
                    chart_paths["distribution"] = dist_path

                    fig_net = generate_net_flow_chart(
                        flow_data=flow_result_dict,
                        spot_price=analyzer.underlying_price,
                        currency=analyzer.currency,
                        expiration=expiration,
                    )
                    net_path = save_chart(
                        fig_net, f"net_flow_{analyzer.currency}_{expiration}",
                        subfolder=subfolder, save_png=False,
                    )
                    inject_hover_js(Path(net_path))
                    chart_paths["net_flow"] = net_path

                    fig_trend = generate_flow_trend_chart(
                        repository=self.repository,
                        currency=analyzer.currency,
                        expiration=expiration,
                        lookback_days=7,
                    )
                    trend_path = save_chart(
                        fig_trend, f"flow_trend_{analyzer.currency}_{expiration}",
                        subfolder=subfolder, save_png=False,
                    )
                    inject_hover_js(Path(trend_path))
                    chart_paths["trend"] = trend_path

                    logger.info(f"Saved flow charts to output/charts/{subfolder}/")
                except Exception as chart_error:
                    logger.warning(f"Failed to save flow charts for {expiration}: {chart_error}")

                # T10: the analyzer's own buy_sell_flow_structured/
                # buy_sell_flow_data text-and-dict bookkeeping is deleted --
                # rendering is render_full_from_result's job now
                # (format_flow_section, operating on the typed flow_result
                # the builder already receives below).
                if builder is not None:
                    builder.set_flow(expiration, flow_result)
                    builder.set_flow_charts(expiration, chart_paths)

            except Exception as e:
                logger.warning(f"Failed to calculate buy/sell flow for {expiration}: {e}")
                progress_callback(f"Warning: Failed to calculate flow for {expiration}")

    def get_flow_metrics(self, currency: str, expiration: str) -> Dict[str, Any]:
        """
        T9 (refactor_design_spec.md): passthrough so GUI callers (e.g.
        ``FlowChartsWindow``) go through this service instead of holding a
        raw ``DatabaseRepository`` reference directly.

        Args:
            currency: Currency symbol (BTC, ETH).
            expiration: Expiration date string.

        Returns:
            Dict with flow_data structure and metadata (see
            ``DatabaseRepository.get_flow_metrics``), or an empty-shaped
            dict when no repository is configured.
        """
        if self.repository is None:
            logger.warning("Repository not available for get_flow_metrics")
            return {"flow_data": {}, "spot_price": 0.0}
        return self.repository.get_flow_metrics(currency, expiration)

    def get_aggregated_flow_metrics(self, currency: str) -> Dict[str, Any]:
        """
        T9 (refactor_design_spec.md): passthrough so GUI callers (e.g.
        ``FlowChartsWindow``) go through this service instead of holding a
        raw ``DatabaseRepository`` reference directly.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            Dict with flow_data structure and metadata (see
            ``DatabaseRepository.get_aggregated_flow_metrics``), or an
            empty-shaped dict when no repository is configured.
        """
        if self.repository is None:
            logger.warning("Repository not available for get_aggregated_flow_metrics")
            return {"flow_data": {}, "spot_price": 0.0}
        return self.repository.get_aggregated_flow_metrics(currency)

    def get_active_expirations_with_flow(self, currency: str) -> List[Dict[str, Any]]:
        """
        Review fix (task A6, Important #2): passthrough so
        ``FlowChartsWindow`` goes through this service instead of reaching
        through to its own ``.repository`` attribute directly.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            List of active-expiration dicts (see
            ``DatabaseRepository.get_active_expirations_with_flow``), or an
            empty list when no repository is configured.
        """
        if self.repository is None:
            logger.warning("Repository not available for get_active_expirations_with_flow")
            return []
        return self.repository.get_active_expirations_with_flow(currency)

    def generate_flow_trend_chart_figure(
        self,
        currency: str,
        expiration: Optional[str] = None,
        lookback_days: int = 7,
        trade_filter: str = "all",
    ):
        """
        Review fix (task A6, Important #2): passthrough so
        ``FlowChartsWindow`` goes through this service for chart generation
        instead of passing its own ``.repository`` attribute directly into
        ``chart_generator.generate_flow_trend_chart``.

        Args:
            currency: Currency symbol (BTC, ETH).
            expiration: Expiration date string, or None to aggregate across
                all expirations.
            lookback_days: Number of days to look back.
            trade_filter: "all", "block", or "non_block".

        Returns:
            Plotly figure, or None when no repository is configured.
        """
        if self.repository is None:
            logger.warning("Repository not available for generate_flow_trend_chart_figure")
            return None
        return generate_flow_trend_chart(
            repository=self.repository,
            currency=currency,
            expiration=expiration,
            lookback_days=lookback_days,
            trade_filter=trade_filter,
        )

    def get_filtered_aggregate_flow(
        self,
        currency: str,
        trade_filter: str,
    ) -> Dict[str, Any]:
        """
        Aggregate flow data across all expirations with block trade filtering.

        Used when a Block or Non-Block filter is active with All Expirations view.
        Bypasses the pre-aggregated buy_sell_flow_metrics table (which lacks raw
        amount/index_price columns) by re-running BuySellFlowAnalyzer per expiration.

        Args:
            currency: Currency symbol (BTC, ETH).
            trade_filter: "block" or "non_block" — applied to raw historical_trades.

        Returns:
            Dict with "flow_data" and "spot_price" keys matching get_aggregated_flow_metrics format.
        """
        if self.repository is None:
            logger.warning("Repository not available for filtered aggregate flow")
            return {"flow_data": {}, "spot_price": 0.0}

        expirations = self.repository.get_active_expirations_with_flow(currency)
        if not expirations:
            logger.warning(f"No active expirations found for {currency}")
            return {"flow_data": {}, "spot_price": 0.0}

        # Fetch real spot price from stored metrics for the first expiration
        first_exp = expirations[0]["expiration"]
        first_metrics = self.repository.get_flow_metrics(currency, first_exp)
        spot_price = first_metrics.get("spot_price", 0.0)

        agg_flow: dict = defaultdict(lambda: {
            "C": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                  "buy_notional": 0.0, "sell_notional": 0.0},
            "P": {"buy_count": 0, "sell_count": 0, "buy_volume": 0.0, "sell_volume": 0.0,
                  "buy_notional": 0.0, "sell_notional": 0.0},
        })

        # T5: this service method now owns the fetch (compatibility-map
        # consumer row #16) — trades + window are injected into the
        # analyzer instead of it querying the repository itself.
        window_end = datetime.now()
        window_start = window_end - timedelta(hours=24)
        window_start_ms = int(window_start.timestamp() * 1000)
        window_end_ms = int(window_end.timestamp() * 1000)

        for exp_info in expirations:
            exp = exp_info["expiration"]
            try:
                trades = self.repository.get_trades_for_flow_analysis(
                    currency=currency,
                    expiration=exp,
                    start_ts=window_start_ms,
                    end_ts=window_end_ms,
                    trade_filter=trade_filter,
                )
                analyzer = BuySellFlowAnalyzer(
                    trades=trades,
                    currency=currency,
                    expiration=exp,
                    spot_price=spot_price,
                    window_start_ms=window_start_ms,
                    window_end_ms=window_end_ms,
                )
                result = analyzer.calculate().to_dict()
                for strike, type_data in result.get("flow_data", {}).items():
                    for opt_type, vals in type_data.items():
                        target = agg_flow[strike][opt_type]
                        for field in ("buy_count", "sell_count", "buy_volume",
                                      "sell_volume", "buy_notional", "sell_notional"):
                            target[field] += vals.get(field, 0.0)
            except Exception as exp_err:
                logger.warning(f"Skipping {exp} during filtered aggregation: {exp_err}")

        # Recompute derived fields from aggregated values
        for strike_data in agg_flow.values():
            for opt_data in strike_data.values():
                opt_data["net_flow"] = opt_data["buy_volume"] - opt_data["sell_volume"]
                sv = opt_data["sell_volume"]
                opt_data["buy_sell_ratio"] = opt_data["buy_volume"] / sv if sv > 0 else None

        return {"flow_data": dict(agg_flow), "spot_price": spot_price}

    def _calculate_volatility_surface(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> None:
        """
        Calculate volatility surface metrics for all expirations.

        Uses enriched instruments (already fetched during GEX/DEX phase).
        Also fetches recent trades for VWAP IV calculation.

        Args:
            analyzer: OnChainMetricsCalculator with enriched_instruments populated.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
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

            # Store all trades on analyzer for block trade detection in
            # Phase 5. T10: set_recent_trades() setter deleted; _recent_trades
            # is real cross-phase data (not report bookkeeping), so it stays
            # as a direct attribute, matching enriched_instruments'
            # existing direct-write convention.
            analyzer._recent_trades = trades
            if builder is not None:
                builder.set_recent_trades(trades)

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
            if builder is not None:
                builder.set_recent_trades([])

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
                vwap_iv, mark_iv_baseline, traded_count = self._calculate_vwap_iv(exp_trades, instruments)
                calculator.set_vwap_iv_data(vwap_iv, mark_iv_baseline, traded_count)

                # T4: calculate() returns the typed VolSurfaceResult. T10:
                # the analyzer's own volatility_surface_structured/
                # volatility_surface_data text-and-dict bookkeeping and the
                # set_atm_iv() setter are deleted -- rendering is
                # render_full_from_result's job now
                # (format_vol_surface_section), and _atm_ivs is real
                # cross-phase data (term structure reads it later), so it
                # stays as a direct dict write, matching
                # enriched_instruments' existing direct-write convention.
                result = calculator.calculate()
                if result.atm_iv is not None:
                    analyzer._atm_ivs[expiration] = result.atm_iv
                if builder is not None:
                    builder.set_vol_surface(expiration, result)

            except Exception as e:
                logger.warning(f"Failed to calculate volatility surface for {expiration}: {e}")

    def _calculate_vwap_iv(
        self,
        trades: List[Dict[str, Any]],
        instruments: List[Dict[str, Any]],
    ) -> Tuple[Optional[float], Optional[float], int]:
        """
        Volume-weighted traded IV vs. the volume-weighted MARK IV of the SAME
        instruments, weighted by the SAME traded volumes (bugfix_spec.md
        Item 3 — the "matched baseline" fix).

        The prior implementation compared VWAP against an unweighted
        arithmetic mean of every instrument's mark_iv in the whole chain,
        which measures the smile (ATM vs wings), not trading aggression.
        This compares like with like: the same instruments, the same
        weights, so the residual is attributable to execution side.

        Args:
            trades: Recent trade records for one expiration.
            instruments: Enriched instrument data for the same expiration.

        Returns:
            (vwap_iv, matched_mark_iv_baseline, traded_instrument_count),
            all None/0 when there is no usable trade data.
        """
        if not trades:
            return None, None, 0

        mark_iv_by_instrument = {
            i["instrument_name"]: i["mark_iv"]
            for i in instruments
            if i.get("instrument_name") and i.get("mark_iv") is not None and i["mark_iv"] > 0
        }

        weighted_traded_iv = 0.0
        volume_by_instrument: Dict[str, float] = defaultdict(float)

        for trade in trades:
            iv = trade.get("iv")
            amount = trade.get("amount") or 0.0
            name = trade.get("instrument_name")
            if iv is None or iv <= 0 or amount <= 0:
                continue
            if name not in mark_iv_by_instrument:  # no mark IV -> excluded from BOTH legs
                continue
            weighted_traded_iv += iv * amount
            volume_by_instrument[name] += amount

        total_volume = sum(volume_by_instrument.values())
        if total_volume <= 0:
            return None, None, 0

        vwap_iv = weighted_traded_iv / total_volume
        baseline = sum(
            mark_iv_by_instrument[name] * volume
            for name, volume in volume_by_instrument.items()
        ) / total_volume

        return vwap_iv, baseline, len(volume_by_instrument)

    def _calculate_market_wide_metrics(
        self,
        analyzer: OnChainMetricsCalculator,
        currency: str,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
        aggregate_gex_dex_result: Optional[Any] = None,
    ) -> None:
        """
        Calculate all market-wide metrics and store in analyzer.

        Args:
            analyzer: OnChainMetricsCalculator to store results in.
            currency: Currency symbol.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
            aggregate_gex_dex_result: Typed aggregate GexDexResult from
                ``_fetch_greeks_and_store_gex_dex`` — computed in a different
                phase but a field of the same ``MarketWideResult`` this
                method assembles.
        """
        dvol = analyzer.market_metrics.get("dvol")
        calc = MarketWideCalculator(
            currency=currency,
            spot_price=analyzer.underlying_price,
            dvol=dvol,
        )

        # T10: the analyzer's own market_wide_structured/market_wide_sections
        # dict-and-text bookkeeping is deleted -- SynthesisMapper (T7) and
        # render_full_from_result (T10) both read the typed sub-results
        # below directly.
        term_structure_result: Optional[TermStructureResult] = None
        basis_result = None
        realized_volatility_result: Optional[RealizedVolatilityResult] = None
        vrp_result: Optional[VarianceRiskPremiumResult] = None
        volatility_cone_result: Optional[VolatilityConeResult] = None
        funding_result: Optional[PerpetualFundingResult] = None
        block_trades_result: Optional[BlockTradesResult] = None
        correlation_result: Optional[CrossAssetCorrelationResult] = None

        # 1. IV Term Structure (uses ATM IVs collected during vol surface phase)
        atm_ivs = analyzer._atm_ivs
        if atm_ivs:
            progress_callback("Calculating IV term structure...")
            # Text return value is unused -- render_market_wide_from_result
            # renders this section from the typed result now.
            _, term_struct = calc.calculate_iv_term_structure(atm_ivs)

            # calculate_iv_term_structure is not migrated to a typed result in
            # this task (out of A5 scope — only calculate_futures_basis was
            # called out); reconstruct entries here to feed MarketWideResult.
            # Not itself the source of truth for the report text above.
            _now_for_term_structure = datetime.now(timezone.utc)
            _ts_entries = []
            for exp, iv in sorted(atm_ivs.items()):
                dte = MarketWideCalculator._calculate_dte(exp, _now_for_term_structure)
                if dte is not None:
                    _ts_entries.append(TermStructureEntry(expiration=exp, dte=dte, atm_iv=iv))
            _ts_entries.sort(key=lambda e: e.dte)
            term_structure_result = TermStructureResult(
                entries=tuple(_ts_entries),
                shape=term_struct.get("shape", "FLAT"),
                spread=term_struct.get("spread", 0.0),
                spread_signed=term_struct.get("spread_signed", term_struct.get("spread", 0.0)),
                iv_by_dte=dict(term_struct.get("iv_by_dte", {})),
            )

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
                # calculate_futures_basis returns the typed
                # FuturesBasisResult; market_wide_formatter's
                # format_futures_basis_section renders it directly from
                # render_market_wide_from_result now.
                basis_result = calc.calculate_futures_basis(futures_data)

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
                # RV -- text return value unused, rendered from the typed
                # result by render_market_wide_from_result now.
                _, rv_values = calc.calculate_realized_volatility_multi_window(
                    price_history
                )
                if rv_values:
                    realized_volatility_result = RealizedVolatilityResult(rv_by_window=dict(rv_values))

                # VRP
                rv_30d = rv_values.get(30, 0)
                if rv_30d > 0:
                    _, vrp_data = calc.calculate_vrp(rv_30d)
                    # Additional finding (same bug class as carried finding
                    # #1, found verifying the render-path flip): dvol is
                    # Optional on VarianceRiskPremiumResult and
                    # calc.calculate_vrp's own text branch already renders
                    # "DVOL not available" when dvol is None -- gating
                    # construction on dvol being available too meant a
                    # dvol-unavailable-but-rv_30d>0 run silently DROPPED the
                    # VRP section from the typed path while the legacy text
                    # path still showed it (with its own "not available"
                    # message). Always construct when rv_30d > 0; dvol=None
                    # flows straight through the same way it already does
                    # for the calculator's own text.
                    vrp_result = VarianceRiskPremiumResult(
                        vrp=vrp_data["vrp"], signal=vrp_data["signal"],
                        dvol=dvol, rv_30d=rv_30d,
                    )

                # Vol Cone -- text return value unused, rendered from the
                # typed result (including the full 6-column table via
                # stats_by_window) by render_market_wide_from_result now.
                _, cone_data = calc.calculate_volatility_cone(price_history)
                # Additional finding (same bug class as carried finding #1):
                # cone_data pre-seeds all three percentile keys at 0.0, so
                # constructing unconditionally whenever price_history was
                # truthy produced a fake all-zero-percentile
                # VolatilityConeResult on the calculator's own "Insufficient
                # price history" path. Mirror that method's exact threshold
                # (the shared constant, not a re-declared literal) so the
                # typed result is None exactly when the legacy text says
                # "Insufficient".
                if len(price_history) >= MINIMUM_PRICE_HISTORY_DAYS_FOR_VOL_CONE:
                    # T10: also carry the full per-window row (current RV,
                    # 25th/median/75th, percentile) the legacy 6-column
                    # table shows -- percentile_by_window alone can only
                    # reproduce a 2-column table (see
                    # format_volatility_cone_section), which is what a live
                    # characterization run against the recorded fixture
                    # caught once render_market_wide_from_result went live.
                    stats_by_window = {
                        window: VolatilityConeWindowStats(
                            current_rv=cone_data[f"cone_{window}d_current_rv"],
                            p25=cone_data[f"cone_{window}d_p25"],
                            p50=cone_data[f"cone_{window}d_p50"],
                            p75=cone_data[f"cone_{window}d_p75"],
                            percentile=cone_data.get(f"cone_{window}d_pctile", 0.0),
                        )
                        for window in (10, 20, 30)
                        if f"cone_{window}d_current_rv" in cone_data
                    }
                    volatility_cone_result = VolatilityConeResult(
                        percentile_by_window={
                            10: cone_data.get("cone_10d_pctile", 0.0),
                            20: cone_data.get("cone_20d_pctile", 0.0),
                            30: cone_data.get("cone_30d_pctile", 0.0),
                        },
                        stats_by_window=stats_by_window,
                    )

        except Exception as e:
            logger.warning(f"Failed to calculate RV/VRP/Vol Cone: {e}")

        # 6. Perpetual Funding Trend
        try:
            progress_callback("Fetching perpetual funding trend...")
            funding_data = self.api.get_funding_chart_data(
                instrument_name=f"{currency}-PERPETUAL",
                length="1m",
            )
            # bugfix_spec.md Item 4 (F4.3): "1m" is expected to return hourly
            # points; assert that resolution rather than assuming it, so a
            # future API change is a loud warning, not a silently-wrong trend.
            _warn_if_funding_resolution_unexpected(funding_data)
            perp_ticker = self.api.get_ticker(f"{currency}-PERPETUAL")

            # Text return value unused, rendered from the typed result by
            # render_market_wide_from_result now.
            _, funding_data_struct = calc.calculate_perpetual_funding_trend(
                funding_data, perp_ticker
            )

            # CARRIED FINDING #1 (A5 review, gates the render-path flip):
            # funding_data_struct pre-seeds "funding_8h": 0.0 /
            # "perp_funding_trend": "Stable" (market_wide_calculator.py's
            # own structured-dict defaults, read by the legacy text path
            # too), so funding_data_struct.get("funding_8h") is never None
            # even when funding is genuinely unavailable -- the typed
            # result never represented "unavailable". Read the ticker's raw
            # Optional values directly and gate construction on their
            # presence, so an unavailable reading produces funding_8h=None
            # (matching the "not available" case the formatter already
            # handles) instead of a fabricated zero-value result.
            ticker_funding_8h = perp_ticker.get("funding_8h")
            ticker_current_funding = perp_ticker.get("current_funding")
            if ticker_funding_8h is not None or ticker_current_funding is not None:
                funding_result = PerpetualFundingResult(
                    perp_open_interest=funding_data_struct.get("perp_oi", 0.0),
                    funding_rate=ticker_current_funding,
                    funding_8h=ticker_funding_8h,
                    funding_trend=funding_data_struct.get("perp_funding_trend", "Stable"),
                    history_points=len(calc._extract_funding_rates(funding_data)),
                )

        except Exception as e:
            logger.warning(f"Failed to calculate perpetual funding trend: {e}")

        # 7. Block Trades (reuse trade data from VWAP IV phase)
        recent_trades = analyzer._recent_trades
        if recent_trades:
            progress_callback("Detecting block trades...")
            # Text return value unused, rendered from the typed result by
            # render_market_wide_from_result now.
            _, block_data = calc.detect_block_trades(recent_trades)
            block_trades_tuple = tuple(
                BlockTrade(
                    timestamp=bt.get("timestamp"),
                    instrument_name=bt.get("instrument", ""),
                    amount=bt.get("amount", 0.0),
                    direction=bt.get("direction", ""),
                    notional=bt.get("notional", 0.0),
                    implied_volatility=bt.get("iv"),
                )
                for bt in block_data.get("block_trades", [])
            )
            # notional_threshold matches detect_block_trades' default; total_detected
            # approximates the (already top-10-truncated) displayed count — the
            # calculator does not expose the pre-truncation total externally.
            block_trades_result = BlockTradesResult(
                trades=block_trades_tuple, notional_threshold=100_000.0,
                total_detected=len(block_trades_tuple),
            )

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

            # Text return value unused, rendered from the typed result by
            # render_market_wide_from_result now.
            _, corr_data = calc.calculate_cross_asset_correlation(
                own_prices=own_prices_30d,
                other_prices=other_prices,
                own_dvol_history=own_dvol_history,
                other_dvol_history=other_dvol_history,
                other_currency=other_currency,
            )
            # calculate_cross_asset_correlation now pre-seeds both
            # correlation keys at None (fixed alongside task A6 carried
            # finding #1 -- previously 0.0, indistinguishable from a
            # genuine zero correlation), so .get() here correctly yields
            # None on insufficient data instead of a fabricated zero.
            correlation_result = CrossAssetCorrelationResult(
                other_currency=other_currency,
                price_correlation=corr_data.get("btc_eth_price_corr"),
                dvol_correlation=corr_data.get("btc_eth_dvol_corr"),
                sample_size=min(len(own_prices_30d), len(other_prices)),
            )

        except Exception as e:
            logger.warning(f"Failed to calculate cross-asset correlation: {e}")

        if builder is not None:
            builder.set_market_wide(
                MarketWideResult(
                    spot_price=analyzer.underlying_price,
                    currency=currency,
                    dvol=analyzer.market_metrics.get("dvol"),
                    iv_percentile_365d=analyzer.market_metrics.get("iv_percentile"),
                    aggregate_gex_dex=aggregate_gex_dex_result,
                    term_structure=term_structure_result,
                    futures_basis=basis_result,
                    realized_volatility=realized_volatility_result,
                    variance_risk_premium=vrp_result,
                    volatility_cone=volatility_cone_result,
                    perpetual_funding=funding_result,
                    block_trades=block_trades_result,
                    cross_asset_correlation=correlation_result,
                    failed_sections=(),
                )
            )

    def _calculate_oi_changes_and_iv_percentile(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> None:
        """
        Calculate OI day-over-day changes and IV percentile per expiry.

        Saves current day's OI snapshot and compares with previous day.
        Requires database repository.

        Args:
            analyzer: OnChainMetricsCalculator with enriched_instruments populated.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
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

                # Calculate OI changes. T10: the text return value is
                # unused now (analyzer.oi_changes_data bookkeeping is
                # deleted; format_oi_changes_section renders this from the
                # typed OiChangesResult in render_full_from_result).
                _, oi_changes_result = self._format_oi_changes(
                    instruments, prev_oi, expiration
                )
                if builder is not None:
                    builder.set_oi_changes(expiration, oi_changes_result)

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

                        # T10: text generation (previously appended to
                        # analyzer.oi_changes_data) deleted --
                        # format_iv_percentile_section renders this from
                        # the typed IvPercentileResult below.
                        if builder is not None:
                            builder.set_iv_percentile(
                                expiration,
                                IvPercentileResult(
                                    atm_strike=atm_strike, current_iv=current_iv,
                                    percentile=percentile, history_days=len(historical_ivs),
                                ),
                            )

            except Exception as e:
                logger.warning(f"Failed to calculate OI changes for {expiration}: {e}")

    def _format_oi_changes(
        self,
        instruments: List[Dict[str, Any]],
        prev_oi: Dict,
        expiration: str
    ) -> Tuple[Optional[str], OiChangesResult]:
        """
        Calculate OI day-over-day changes.

        T10 (refactor_design_spec.md): this method used to also build the
        report text (returned as the first tuple element); that text fed
        only ``analyzer.oi_changes_data``, deleted along with the analyzer's
        other report-text bookkeeping now that
        ``format_oi_changes_section`` renders this section from the typed
        ``OiChangesResult`` directly. The first tuple element is kept as
        ``None`` (not removed) to avoid changing this method's call-site
        signature/unpacking for a return value every caller already
        discards.

        Args:
            instruments: Current enriched instruments.
            prev_oi: Previous day's OI mapping {(strike, type): oi}.
            expiration: Expiration date string.

        Returns:
            (None, the typed OiChangesResult).
        """
        if not prev_oi:
            return None, OiChangesResult(rows=(), total_significant=0, has_previous_snapshot=False)

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
            return None, OiChangesResult(rows=(), total_significant=0, has_previous_snapshot=True)

        # Sort by absolute change percentage
        significant_changes.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

        rows = tuple(
            OiChangeRow(
                strike=c["strike"], option_type=c["type"],
                previous_oi=c["prev_oi"], current_oi=c["current_oi"],
                change=c["change"], change_pct=c["change_pct"],
            )
            for c in significant_changes[:15]
        )
        result = OiChangesResult(
            rows=rows, total_significant=len(significant_changes), has_previous_snapshot=True,
        )
        return None, result

    def _fetch_market_metrics(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> None:
        """
        Fetch market-wide metrics (DVOL, funding rate) and store in analyzer.

        Args:
            analyzer: OnChainMetricsCalculator to store metrics in.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
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

        # T10: set_market_metrics() setter deleted. market_metrics is real
        # cross-phase data (dvol/funding are read by _calculate_market_wide_
        # metrics in several places), so it stays as a direct attribute,
        # matching enriched_instruments' existing direct-write convention.
        analyzer.market_metrics = {
            "dvol": dvol,
            "iv_percentile": iv_percentile,
            "current_funding": current_funding,
            "funding_8h": funding_8h,
            "iv_rank": iv_rank,
        }
        if builder is not None:
            builder.set_market_metrics(_to_market_metrics(analyzer.market_metrics))

    def _fetch_trend_data(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> None:
        """
        Fetch previous DB snapshots per expiration for trend comparison.

        Requires repository. Silently skipped when repository is None.
        Each expiration gets the oldest of the 2 most-recent hourly
        onchain_analysis_snapshots as its "previous" value to compare
        against live API data.

        Args:
            analyzer: OnChainMetricsCalculator with parsed data.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
        """
        if self.repository is None:
            return

        progress_callback("Fetching trend data for report comparison...")

        for expiration in analyzer.get_expirations():
            try:
                history = self.repository.get_onchain_snapshot_history(
                    analyzer.currency, expiration, limit=2
                )
                prev = history[0] if history else None

                if prev is None:
                    if builder is not None:
                        builder.set_trend(expiration, None)
                    continue

                trend: Dict[str, Any] = {}
                if prev["max_pain_strike"] is not None:
                    trend["max_pain_strike"] = float(prev["max_pain_strike"])
                if prev["total_call_oi"] is not None:
                    trend["call_oi"] = float(prev["total_call_oi"])
                if prev["total_put_oi"] is not None:
                    trend["put_oi"] = float(prev["total_put_oi"])
                pc = prev["put_call_ratio_oi"]
                trend["pc_ratio"] = float(pc) if pc is not None else None
                if prev["total_volume"] is not None:
                    trend["total_volume"] = float(prev["total_volume"])
                vr = prev["put_call_ratio_volume"]
                trend["volume_ratio"] = float(vr) if vr is not None else None

                trend_or_none = trend if trend else None
                if builder is not None:
                    builder.set_trend(expiration, _to_trend_snapshot(trend_or_none))

            except Exception as e:
                logger.warning(f"Failed to fetch trend data for {expiration}: {e}")
                if builder is not None:
                    builder.set_trend(expiration, None)

    def _save_reports_per_expiration(
        self,
        result: OnChainAnalysisResult,
        currency: str,
    ) -> None:
        """
        Render and save one report file per expiration, directly from the
        typed ``OnChainAnalysisResult`` (refactor_design_spec.md section T8
        — kills the report-text splitter; fixes M2).

        Each expiration folder gets header + that expiration's own section,
        rendered via ``OnChainReportFormatter`` — no string scanning of a
        full report text; the on-disk file is built from the same typed
        aggregate the rest of the pipeline (T6/T7) already produced.

        Directory: output/data/onchain_analysis/{currency}/{expiration}/
        Filename: report_{timestamp}.txt

        Args:
            result: The typed aggregate from ``OnChainAnalysisBuilder.build()``.
            currency: Currency symbol (BTC, ETH).
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Get project root (3 levels up from this file)
            project_root = Path(__file__).parent.parent.parent.parent

            formatter = OnChainReportFormatter()
            header = formatter.render_header_from_result(result)

            saved = 0
            for expiration in result.expiration_names():
                section_content = formatter.render_expiration_from_result(result, expiration)
                if not section_content:
                    continue

                output_dir = project_root / "output" / "data" / "onchain_analysis" / currency / expiration
                output_dir.mkdir(parents=True, exist_ok=True)

                report_path = output_dir / f"report_{timestamp}.txt"
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(header)
                    if header and not header.endswith('\n'):
                        f.write('\n')
                    f.write('\n')
                    f.write(section_content)

                logger.info(f"Saved report for {expiration} to {report_path}")
                saved += 1

            logger.info(f"Saved {saved} per-expiration report(s)")

        except Exception as e:
            logger.error(f"Failed to save per-expiration reports: {e}", exc_info=True)
