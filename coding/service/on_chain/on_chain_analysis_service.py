"""
On-chain analysis service.

Orchestrates fetching and analyzing on-chain option data.
"""

import dataclasses
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.chart_generator import (
    generate_flow_distribution_chart,
    generate_flow_trend_chart,
    generate_net_flow_chart,
    inject_hover_js,
    save_chart,
)
from coding.core.analytics.dealer_inventory_calculator import DealerInventoryCalculator
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.historical_normalizer import (
    HistoricalNormalizer,
    MetricSpec,
    NormalizedMetric,
)
from coding.core.analytics.on_chain_analyzer import (
    OnChainMetricsCalculator,
    _to_market_metrics,
    _to_trend_snapshot,
)
from coding.core.analytics.put_call_ratio_interpreter import (
    interpret_put_call_ratio_percentile,
)
from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter
from coding.core.analytics.results.analysis_result import (
    IvPercentileResult,
    OiChangeRow,
    OiChangesResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.dealer_inventory_results import (
    DealerInventoryKeyLevels,
    DealerInventoryLevel,
    DealerInventoryResult,
    DealerInventoryStrikeRow,
)
from coding.core.analytics.thresholds import (
    OI_CHANGE_SIGNIFICANT_ABS_THRESHOLD,
    OI_CHANGE_SIGNIFICANT_PCT_THRESHOLD,
)
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.analysis_builder import OnChainAnalysisBuilder
from coding.service.on_chain.market_wide_orchestrator import MarketWideOrchestrator

logger = logging.getLogger(__name__)

# institutional_metrics_spec.md section 2 / task C3: T0 = max(first trade
# seen for this expiry, 2026-04-25) -- 2026-04-25 is the start of the
# >=99.9%-complete trailing trade-history window [verified: 0.05% missing
# hours over the trailing 90 days as of the spec's writing]. Decision D9
# (task-C3-brief.md, BINDING): render the inferred view only if coverage
# since T0 is >= 95% AND the OI-bound violation rate is <= 5% -- both
# boundary-inclusive per the spec's literal pseudocode
# (`coverage >= 0.95 AND violation_rate <= 0.05`).
_DEALER_INVENTORY_COVERAGE_STABLE_DATE = datetime(2026, 4, 25, tzinfo=timezone.utc)
_DEALER_INVENTORY_COVERAGE_GATE = 0.95
_DEALER_INVENTORY_VIOLATION_GATE = 0.05

# Fix round 2 (Important, orchestrator ruling): DealerInventoryCalculator.
# coverage_report excludes legs with no OI reference from its
# n_strikes/violation_rate denominator (fix round 1, Important #3) so a
# handful of transient ticker-fetch failures can't flip the D9 gate. But
# that fix left an edge case fail-OPEN: if EVERY leg on an expiry lacks an
# OI reference, n_strikes == 0 and violation_rate reads 0.0 -- "zero
# violations, clean pass" -- when the truth is "nothing was actually
# checked". Pre-fix-round-1 behavior (defaulting missing OI to 0.0) was
# accidentally the correct fail-SAFE direction for this specific
# degenerate case, even though it was wrong for the "some legs excluded"
# case fix round 1 targeted. Guarded here instead of loosening the
# calculator's own denominator logic, because "how much of the checked set
# is trustworthy" is a gating decision (D9's), not a math correction the
# pure calculator should make on its own.
#
# 20% is a deliberate, documented starting point, not a tuned constant:
# above roughly 1-in-5 legs excluded, the checked remainder is no longer a
# representative sample of the expiry's book, so a "clean" violation rate
# on it is not trustworthy enough to justify overriding the assumed
# convention -- similar in spirit to D9's own 95%/5% thresholds (round
# numbers chosen for interpretability, not derived from a statistical
# significance calculation). n_strikes == 0 with any exclusions is ALWAYS
# a hard fail regardless of this threshold (see render_inferred below) --
# this constant only governs the partial-exclusion case.
#
# Fix round 3 (Important): this constant and the n_strikes==0-with-
# exclusions floor above do NOT cover a zero-trades-for-this-expiry
# expiry (flow_rows == [] -> n_strikes_checked == 0 AND legs_excluded_no_oi
# == 0 -- no exclusions happened because there was nothing to exclude).
# That is a separate check (`no_trades_for_this_expiry` in
# _calculate_inferred_dealer_positioning below) -- see its inline comment
# for why currency-wide `coverage` cannot stand in for it.
_DEALER_INVENTORY_MAX_EXCLUSION_RATE = 0.20


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
    ) -> Union[
        str,
        Tuple[str, OnChainMetricsCalculator],
        Tuple[str, OnChainAnalysisResult],
        Tuple[str, OnChainMetricsCalculator, OnChainAnalysisResult],
    ]:
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

        # bugfix_spec.md Item 7: the spot index is fetched explicitly now --
        # no heuristic, no volume-race pick across the whole book. Falls
        # back to the nearest-expiry median underlying_price (the
        # smallest-basis proxy available without the index) if the index
        # fetch itself fails -- never silently reverts to the old global
        # highest-volume pick.
        progress(f"Fetching index price for {currency}...")
        try:
            index_price = self.api.get_index_price(currency=currency)
        except Exception as e:
            index_price = analyzer.nearest_expiry_median_underlying_price()
            logger.error(
                f"get_index_price failed for {currency}: {e} -- falling back "
                f"to nearest-expiry median underlying_price ({index_price})"
            )
        analyzer.set_index_price(index_price)

        expirations = analyzer.get_expirations()
        progress(f"Found {len(expirations)} expirations")

        builder = OnChainAnalysisBuilder(currency, analyzer.index_price, analyzer.parsed_data)
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

        # bugfix_spec.md Item 10: replace each expiration's hard-coded
        # 0.7/1.0/1.3-threshold P/C ratio bias with a percentile-vs-own-
        # 90d-history classification. Runs after build() (needs the typed
        # per-expiration bundles) and reconstructs the affected bundles via
        # dataclasses.replace since the result and its bundles are frozen.
        progress("Reclassifying put/call ratio vs own history...")
        result = self._apply_pcr_percentile_classification(analyzer, result)

        # institutional_metrics_spec.md section 1: percentile/z-score context
        # for the front-month AVAILABLE metrics. Runs after build() (needs
        # the typed result to read the front-month bundle) and attaches via
        # dataclasses.replace since OnChainAnalysisResult is frozen.
        progress("Calculating historical percentile context...")
        normalized_metrics, normalized_metrics_front_month, normalized_metrics_stale_since = (
            self._build_normalized_metrics(analyzer, result)
        )
        if normalized_metrics:
            result = dataclasses.replace(
                result,
                normalized_metrics=normalized_metrics,
                normalized_metrics_front_month=normalized_metrics_front_month,
                normalized_metrics_stale_since=normalized_metrics_stale_since,
            )

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

    # bugfix_spec.md Item 10: 90-day lookback window (F10.3.1's
    # PERCENTILE_WINDOW_DAYS), independent of section 1's dual 30d/90d
    # windows used for the header's HISTORICAL CONTEXT block.
    _PCR_CLASSIFICATION_LOOKBACK_HOURS = 2160

    def _apply_pcr_percentile_classification(
        self,
        analyzer: OnChainMetricsCalculator,
        result: OnChainAnalysisResult,
    ) -> OnChainAnalysisResult:
        """
        Replace each expiration's hard-coded-threshold P/C ratio bias with
        a percentile-vs-own-90d-history classification (bugfix_spec.md
        Item 10).

        ``OnChainMetricsCalculator.calculate_put_call_ratio`` (core, no DB
        access) already set ``bias`` via the old
        ``thresholds.interpret_put_call_ratio`` 0.7/1.0/1.3 thresholds --
        this method fetches this expiration's own trailing history (the
        one thing core is not allowed to do) and overwrites ``bias`` with
        the percentile-based label, via ``dataclasses.replace`` since
        ``PutCallRatioResult``/``ExpirationAnalysisResult``/
        ``ExpirationBundle``/``OnChainAnalysisResult`` are all frozen.

        Edge cases (bugfix_spec.md section 10.4):
        - ``ratio`` non-finite (call OI == 0): bias="N/A", no percentile
          computed (skips both the current reading and history -- N/A is
          a data-insufficiency case, not a directional claim).
        - History shorter than ``HistoricalNormalizer.MIN_OBS``:
          percentile=None, bias="Insufficient history".
        - A failed history fetch for one expiration is logged and that
          expiration falls back to "Insufficient history" (never left at
          the stale hard-coded label -- that would silently reintroduce
          the exact bug this task replaces via the error path) while every
          other expiration is still reclassified normally.

        Returns:
            ``result`` unchanged if there is no repository (matches the
            codebase's existing "no repository -> skip DB-dependent work"
            convention). Otherwise a new ``OnChainAnalysisResult`` with
            every expiration's ``put_call_ratio`` reclassified.
        """
        if self.repository is None:
            return result

        currency = analyzer.currency
        new_bundles = []
        for bundle in result.expirations:
            if bundle.analysis is None:
                new_bundles.append(bundle)
                continue

            pcr = bundle.analysis.put_call_ratio
            ratio = pcr.ratio

            if ratio is None or ratio == float("inf"):
                new_pcr = dataclasses.replace(
                    pcr, bias="N/A", percentile_90d=None, history_n_90d=0,
                )
            else:
                try:
                    history_90d = self.repository.get_metric_history(
                        table="onchain_analysis_snapshots", column="put_call_ratio_oi",
                        currency=currency,
                        lookback_hours=self._PCR_CLASSIFICATION_LOOKBACK_HOURS,
                        expiration=bundle.expiration,
                    )
                except Exception as exc:
                    logger.warning(
                        "PCR percentile classification failed for %s %s: %s",
                        currency, bundle.expiration, exc,
                    )
                    history_90d = []

                if len(history_90d) >= HistoricalNormalizer.MIN_OBS:
                    # C1 review Important #2: HistoricalNormalizer's own
                    # degenerate-series guard only covers value == the
                    # constant (mid-rank -> 50.0, "no signal"). It does
                    # NOT cover a zero-variance history paired with a
                    # DIFFERING current ratio -- that combination computes
                    # a maximally-confident (0.0 or 100.0) percentile from
                    # a history that says nothing about normal variation
                    # (a flat 90d series carries no information about
                    # whether ANY value is "unusual"). This is PCR-
                    # specific business logic (guard lives here, not in
                    # HistoricalNormalizer's core, per review guidance --
                    # the other 5 normalized metrics don't get this guard
                    # in this round).
                    if max(history_90d) == min(history_90d):
                        percentile = None
                    else:
                        percentile = HistoricalNormalizer.percentile(ratio, history_90d)
                else:
                    percentile = None

                bias = interpret_put_call_ratio_percentile(percentile)
                new_pcr = dataclasses.replace(
                    pcr, bias=bias, percentile_90d=percentile,
                    history_n_90d=len(history_90d),
                )

            new_analysis = dataclasses.replace(bundle.analysis, put_call_ratio=new_pcr)
            new_bundles.append(dataclasses.replace(bundle, analysis=new_analysis))

        return dataclasses.replace(result, expirations=tuple(new_bundles))

    # institutional_metrics_spec.md section 1: 30d/90d lookback windows.
    # Hourly sampling -> n ~= 720 (30d) and n ~= 2,160 (90d), matching the
    # spec's verified 2,133-distinct-BTC-snapshot-hours-in-90d figure.
    _NORMALIZER_LOOKBACK_30D_HOURS = 720
    _NORMALIZER_LOOKBACK_90D_HOURS = 2160

    # institutional_metrics_spec.md section 1(c): "if max(snapshot_hour) <
    # now() - 3h, prefix the whole normalization block with STALE: history
    # ends {ts}".
    _STALENESS_THRESHOLD_HOURS = 3

    # C1 review Critical #2 (confirmed, not just suspected): prospective_
    # collector.py's save_funding_rate call divides the live ticker's
    # funding_8h field by 100 before persisting it ("Convert from
    # percentage"). A live ticker call plus a DB query (avg(abs(
    # funding_rate)) by month) confirmed a ~100x level break in
    # funding_rate_history exactly at the point the daemon collector
    # became the writer -- the stored column is 100x smaller than the raw
    # ticker/market_metrics["funding_8h"] scale this service (and the
    # report header) otherwise use. Rather than dividing the live value
    # (which would then need report_formatter.py's *already-correct*
    # header display convention -- funding_8h * 100 -- special-cased for
    # this one metric too), the fetched HISTORY is multiplied back up by
    # this factor so both value and history are compared on the SAME
    # (raw-ticker) scale, and every other display convention in the report
    # stays untouched. backfill_funding_rate.py is fixed to also divide by
    # 100 going forward, so future rows stay on the "divided" stored scale
    # this factor corrects for.
    _FUNDING_RATE_STORAGE_SCALE_CORRECTION = 100.0

    @staticmethod
    def _pick_front_month_expiration(expiration_names: Tuple[str, ...]) -> Optional[str]:
        """
        Pick the true nearest-DTE (front-month) expiration.

        C1 review Important #1: ``sorted(expiration_names)[0]`` (the
        previous convention, mirrored from ``get_recent_onchain_history``'s
        subquery) picks the lexicographically-first expiration STRING, not
        the chronologically-nearest one -- "%d%b%y"-style date strings
        like "14AUG26"/"26JUL26" do not sort chronologically as plain
        strings. Confirmed in this task's own golden fixture: string-sort
        picked "14AUG26" (19 DTE) over the true front month "26JUL26"
        (0 DTE). Parses each name as a real date instead (same "%d%b%y"
        convention already used by ``SynthesisMapper._calculate_dte`` and
        ``OnChainMetricsCalculator.nearest_expiry_median_underlying_
        price``) and picks the minimum.

        Returns:
            The nearest-DTE expiration name, or ``None`` if none of
            ``expiration_names`` parses as a valid "%d%b%y" date.
        """
        dated = []
        for name in expiration_names:
            try:
                exp_date = datetime.strptime(name, "%d%b%y")
            except ValueError:
                continue
            dated.append((exp_date, name))
        if not dated:
            return None
        return min(dated, key=lambda pair: pair[0])[1]

    def _sum_paired_history(
        self,
        history_a: List[float],
        history_b: List[float],
        *,
        currency: str,
        expiration: str,
        metric_name: str,
    ) -> List[float]:
        """
        Element-wise sum of two same-query-shape history series (C1
        review Critical #1: replaces the never-whitelisted composite SQL
        expression ``"(total_call_oi + total_put_oi)"`` -- fetch each
        whitelisted column separately and sum in Python instead).

        Both series come from identical WHERE/ORDER BY clauses against the
        same table, so they are row-aligned by construction UNLESS one
        column has an independent NULL the other doesn't (both columns are
        written by the same INSERT in this schema, so this is expected to
        be rare, not structural). Guards against silently pairing values
        from different hours: a length mismatch is logged and treated as
        "no history" for this metric rather than risking a misaligned sum.
        """
        if len(history_a) != len(history_b):
            logger.warning(
                "%s history length mismatch for %s %s (call=%d, put=%d) -- "
                "treating as no history rather than risk a misaligned sum",
                metric_name, currency, expiration, len(history_a), len(history_b),
            )
            return []
        return [a + b for a, b in zip(history_a, history_b)]

    def _compute_historical_context_staleness(
        self, currency: str, front_month: str, tables_used: List[Tuple[str, Optional[str]]],
    ) -> Optional[datetime]:
        """
        Most-stale (earliest) freshness timestamp across every table the
        HISTORICAL CONTEXT block actually drew from (C1 review Important
        #4). Returns the timestamp only when it is more than
        ``_STALENESS_THRESHOLD_HOURS`` old -- the formatter treats a
        non-None return as "prefix STALE: history ends {ts}".

        Args:
            tables_used: (table, expiration_or_None) pairs actually queried
                -- e.g. [("onchain_analysis_snapshots", front_month),
                ("volatility_index_history", None)]. Deduplicated
                internally so a table isn't queried twice for the same
                staleness check.
        """
        seen = set()
        timestamps = []
        for table, expiration in tables_used:
            key = (table, expiration)
            if key in seen:
                continue
            seen.add(key)
            try:
                ts = self.repository.get_metric_freshness(
                    table=table, currency=currency, expiration=expiration,
                )
            except Exception as exc:
                logger.warning("get_metric_freshness(%s) failed for %s: %s", table, currency, exc)
                ts = None
            if ts is not None:
                timestamps.append(ts)

        if not timestamps:
            return None

        most_stale = min(timestamps)
        threshold = datetime.now(most_stale.tzinfo) - timedelta(hours=self._STALENESS_THRESHOLD_HOURS)
        return most_stale if most_stale < threshold else None

    def _build_normalized_metrics(
        self,
        analyzer: OnChainMetricsCalculator,
        result: OnChainAnalysisResult,
    ) -> Tuple[Dict[str, NormalizedMetric], Optional[str], Optional[datetime]]:
        """
        Build percentile/z-score context for the front-month AVAILABLE
        metrics (institutional_metrics_spec.md section 1), runs once per
        analysis (not per expiry).

        Wires exactly five of the six section-1(a) AVAILABLE metrics:
        net GEX, PCR-OI, and total OI (front-month -- see
        ``_pick_front_month_expiration``, C1 review Important #1) plus
        DVOL and funding (market-wide, no expiration filter).

        VRP is deliberately NOT wired here. The live report's VRP
        (MarketWideOrchestrator's DVOL-vs-realized-vol figure, see
        market_wide_orchestrator.py's ``_calculate_vrp``) and the stored
        ``onchain_volatility_snapshots.vrp_absolute`` history
        (volatility_reconstruction_service.py's ``_reconstruct_vrp`` --
        that expiration's own average ATM IV vs realized vol) are two
        different formulas for the same metric name. Feeding one as
        "value" against the other's history would silently produce a
        misleading percentile -- task-C1 brief's explicit STOP condition.
        See task-C1-report.md for the full writeup and recommended
        follow-up (either recompute VRP per-expiration to match the
        stored history's formula, or migrate the stored history to the
        DVOL-based formula).

        C1 review Critical #1: each metric's history fetch is now isolated
        in its OWN try/except (previously one broad try/except wrapped
        every metric, so a single whitelist ``ValueError`` on ONE metric --
        which is exactly what the never-whitelisted composite total_oi
        expression triggered, on every single run -- silently discarded
        ALL FIVE metrics). A ``ValueError`` (a whitelist violation, a
        programming error, not a data-availability issue) is logged at
        ERROR with the full exception and that metric is skipped; any
        other exception is logged at WARNING and that metric is skipped.
        Every other already-built metric survives.

        Returns:
            ``({}, None, None)`` if there is no repository, no
            expirations, or none of the expiration names parses as a
            valid date (``_pick_front_month_expiration`` returns None).
            Otherwise ``(metrics, front_month_expiration, stale_since)``
            -- ``stale_since`` is the most-stale queried table's max
            timestamp when it exceeds ``_STALENESS_THRESHOLD_HOURS``,
            else None. Never raises.
        """
        if self.repository is None:
            return {}, None, None

        expiration_names = result.expiration_names()
        if not expiration_names:
            return {}, None, None

        front_month = self._pick_front_month_expiration(expiration_names)
        if front_month is None:
            return {}, None, None

        bundle = result.bundle(front_month)
        if bundle is None:
            return {}, front_month, None

        currency = analyzer.currency
        lookback_30d = self._NORMALIZER_LOOKBACK_30D_HOURS
        lookback_90d = self._NORMALIZER_LOOKBACK_90D_HOURS
        specs: List[MetricSpec] = []
        tables_used: List[Tuple[str, Optional[str]]] = []

        if bundle.gex_dex is not None and bundle.gex_dex.total_net_gex is not None:
            try:
                specs.append(MetricSpec(
                    name="net_gex", value=float(bundle.gex_dex.total_net_gex),
                    history_30d=self.repository.get_metric_history(
                        table="onchain_analysis_snapshots", column="total_net_gex",
                        currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                    ),
                    history_90d=self.repository.get_metric_history(
                        table="onchain_analysis_snapshots", column="total_net_gex",
                        currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                    ),
                    unit="USD",
                ))
                tables_used.append(("onchain_analysis_snapshots", front_month))
            except ValueError as exc:
                logger.error(
                    "net_gex normalized-metric history hit a whitelist violation "
                    "(programming bug, not a data issue): %s", exc,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch net_gex history for %s %s: %s", currency, front_month, exc,
                )

        pcr = bundle.analysis.put_call_ratio if bundle.analysis is not None else None
        if pcr is not None:
            # Total OI is well-defined even when the ratio itself is inf
            # (call_oi == 0) -- do not couple its availability to the
            # ratio's finiteness. C1 review Critical #1: total_call_oi and
            # total_put_oi are fetched as two SEPARATE whitelisted queries
            # and summed in Python (see _sum_paired_history) instead of a
            # composite SQL expression that was never whitelisted.
            try:
                call_oi_30d = self.repository.get_metric_history(
                    table="onchain_analysis_snapshots", column="total_call_oi",
                    currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                )
                put_oi_30d = self.repository.get_metric_history(
                    table="onchain_analysis_snapshots", column="total_put_oi",
                    currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                )
                call_oi_90d = self.repository.get_metric_history(
                    table="onchain_analysis_snapshots", column="total_call_oi",
                    currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                )
                put_oi_90d = self.repository.get_metric_history(
                    table="onchain_analysis_snapshots", column="total_put_oi",
                    currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                )
                specs.append(MetricSpec(
                    name="total_oi", value=float(pcr.total_call_oi + pcr.total_put_oi),
                    history_30d=self._sum_paired_history(
                        call_oi_30d, put_oi_30d,
                        currency=currency, expiration=front_month, metric_name="total_oi (30d)",
                    ),
                    history_90d=self._sum_paired_history(
                        call_oi_90d, put_oi_90d,
                        currency=currency, expiration=front_month, metric_name="total_oi (90d)",
                    ),
                    unit="coins",
                ))
                tables_used.append(("onchain_analysis_snapshots", front_month))
            except ValueError as exc:
                logger.error(
                    "total_oi normalized-metric history hit a whitelist violation "
                    "(programming bug, not a data issue): %s", exc,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch total_oi history for %s %s: %s", currency, front_month, exc,
                )

            if pcr.ratio is not None and pcr.ratio != float("inf"):
                try:
                    specs.append(MetricSpec(
                        name="pcr_oi", value=float(pcr.ratio),
                        history_30d=self.repository.get_metric_history(
                            table="onchain_analysis_snapshots", column="put_call_ratio_oi",
                            currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                        ),
                        history_90d=self.repository.get_metric_history(
                            table="onchain_analysis_snapshots", column="put_call_ratio_oi",
                            currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                        ),
                        unit="ratio",
                    ))
                    tables_used.append(("onchain_analysis_snapshots", front_month))
                except ValueError as exc:
                    logger.error(
                        "pcr_oi normalized-metric history hit a whitelist violation "
                        "(programming bug, not a data issue): %s", exc,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch pcr_oi history for %s %s: %s", currency, front_month, exc,
                    )

        market_metrics = analyzer.market_metrics or {}
        dvol = market_metrics.get("dvol")
        if dvol is not None:
            try:
                specs.append(MetricSpec(
                    name="dvol", value=float(dvol),
                    history_30d=self.repository.get_metric_history(
                        table="volatility_index_history", column="dvol",
                        currency=currency, lookback_hours=lookback_30d, time_column="date",
                    ),
                    history_90d=self.repository.get_metric_history(
                        table="volatility_index_history", column="dvol",
                        currency=currency, lookback_hours=lookback_90d, time_column="date",
                    ),
                    unit="vol pts",
                ))
                tables_used.append(("volatility_index_history", None))
            except ValueError as exc:
                logger.error(
                    "dvol normalized-metric history hit a whitelist violation "
                    "(programming bug, not a data issue): %s", exc,
                )
            except Exception as exc:
                logger.warning("Failed to fetch dvol history for %s: %s", currency, exc)

        funding_8h = market_metrics.get("funding_8h")
        if funding_8h is not None:
            try:
                # C1 review Critical #2: funding_rate_history.funding_rate
                # is stored 100x smaller than the raw ticker/market_
                # metrics["funding_8h"] scale (confirmed -- see
                # _FUNDING_RATE_STORAGE_SCALE_CORRECTION's docstring).
                # Rescale the fetched history back up rather than divide
                # the live value, so ``value`` and the report header's
                # existing funding_8h * 100 display convention are
                # untouched -- only the history is corrected.
                scale = self._FUNDING_RATE_STORAGE_SCALE_CORRECTION
                raw_history_30d = self.repository.get_metric_history(
                    table="funding_rate_history", column="funding_rate",
                    currency=currency, lookback_hours=lookback_30d, time_column="date",
                )
                raw_history_90d = self.repository.get_metric_history(
                    table="funding_rate_history", column="funding_rate",
                    currency=currency, lookback_hours=lookback_90d, time_column="date",
                )
                specs.append(MetricSpec(
                    name="funding", value=float(funding_8h),
                    history_30d=[v * scale for v in raw_history_30d],
                    history_90d=[v * scale for v in raw_history_90d],
                    unit="%",
                ))
                tables_used.append(("funding_rate_history", None))
            except ValueError as exc:
                logger.error(
                    "funding normalized-metric history hit a whitelist violation "
                    "(programming bug, not a data issue): %s", exc,
                )
            except Exception as exc:
                logger.warning("Failed to fetch funding history for %s: %s", currency, exc)

        if not specs:
            return {}, front_month, None

        metrics = HistoricalNormalizer().normalize_many(specs)
        stale_since = self._compute_historical_context_staleness(currency, front_month, tables_used)
        return metrics, front_month, stale_since

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
                    item_with_greeks["underlying_price"] = ticker.get("underlying_price", analyzer.index_price)
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

                # Calculate GEX/DEX and store in analyzer. bugfix_spec.md
                # Item 7 anchor table: GEX/DEX are exposures to a move in the
                # underlying SPOT, and GEX's S² term amplifies any basis
                # error -- anchor on the index, not one expiry's future.
                progress_callback(f"Calculating GEX/DEX for {expiration}...")
                calculator = GexDexCalculator(
                    instruments_with_greeks,
                    analyzer.index_price,
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

                # institutional_metrics_spec.md section 2 / task C3: runs
                # AFTER the GEX/DEX step and reuses the SAME enriched-Greeks
                # instruments_with_greeks list built above -- no second
                # Greeks pass, no second API call. Additive only (D9): a
                # gate failure or an unexpected error both degrade to "no
                # inferred section" (dealer_result is None or
                # render_inferred=False), never to a broken GEX/DEX section.
                progress_callback(f"Calculating inferred dealer positioning for {expiration}...")
                dealer_result = self._calculate_inferred_dealer_positioning(
                    analyzer.currency, expiration, instruments_with_greeks, analyzer.index_price,
                )
                if dealer_result is not None and builder is not None:
                    builder.set_dealer_inventory(expiration, dealer_result)

        # Aggregate GEX/DEX across all expirations after per-expiry loop
        aggregate_result = None
        if gex_dex_typed_by_expiry:
            progress_callback("Calculating market-wide aggregate GEX/DEX...")
            aggregate_result = GexDexCalculator.aggregate_across_expirations(
                gex_dex_typed_by_expiry, analyzer.index_price, analyzer.currency
            )

        return aggregate_result

    def _calculate_inferred_dealer_positioning(
        self,
        currency: str,
        expiration: str,
        instruments_with_greeks: List[Dict[str, Any]],
        spot_price: float,
    ) -> Optional[DealerInventoryResult]:
        """
        institutional_metrics_spec.md section 2 (Glassnode taker-flow
        method) / task C3. Fetches signed taker flow since T0, computes the
        D9 gate (trade-history hour coverage AND OI-bound violation rate),
        and returns the typed result the report formatter renders as either
        the inferred view (gate passed) or an explicit "unavailable, falls
        back to the assumed view" line (gate failed) -- never both, never
        blended (D9, BINDING).

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "31JUL26").
            instruments_with_greeks: The SAME enriched-Greeks instrument list
                already built for ``GexDexCalculator`` in the caller -- no
                second Greeks pass, no second API call.
            spot_price: Current underlying spot price (``analyzer.
                index_price`` -- same anchor GexDexCalculator uses,
                bugfix_spec.md Item 7).

        Returns:
            ``DealerInventoryResult``, or ``None`` if computing it raised
            unexpectedly -- this is a purely additive computation (like
            ``GexDexCalculator._calculate_gamma_profile``'s established
            guard) and must never abort the GEX/DEX pipeline it runs
            alongside.
        """
        # Fix round (Minor #4): matches the established "no repository ->
        # skip DB-dependent work" convention already used throughout this
        # module (e.g. _apply_pcr_percentile_classification, line ~315).
        # Without this, running without a repository configured hit the
        # broad except-Exception guard below on every call, logging a full
        # ERROR traceback for what is an expected, gracefully-handled
        # condition, not an unexpected failure.
        if self.repository is None:
            return None

        try:
            coverage_stable_ms = int(
                _DEALER_INVENTORY_COVERAGE_STABLE_DATE.timestamp() * 1000
            )
            first_trade_ms = self.repository.get_first_trade_timestamp(currency, expiration)
            # Zero-trades-ever edge case: first_trade_ms is None -- T0 still
            # resolves to the coverage-stable date rather than leaving T0
            # undefined, so the rest of this method (and the hour-coverage
            # query below) always has a well-defined window to compute
            # against instead of crashing.
            t0_ms = (
                coverage_stable_ms if first_trade_ms is None
                else max(first_trade_ms, coverage_stable_ms)
            )

            flow_rows = self.repository.get_signed_taker_flow_by_strike(currency, expiration, t0_ms)
            # Fix round (Important #2, orchestrator ruling): table-wide
            # (currency-wide) collection completeness, NOT filtered by this
            # expiration -- matches how bugfix_spec.md section 2(a)'s own
            # empirical validation measured coverage (across ALL trades for
            # the currency, not one contract's own trading activity). The
            # original per-expiry version measured contract LIQUIDITY, which
            # inverted which expiries the spec validated as trustworthy
            # (favored high-volume contracts, penalized the short-dated,
            # fully-covered expiries the spec's own violation-rate study
            # found had 0% violations).
            present_hours, expected_hours = self.repository.get_trade_hour_coverage(currency, t0_ms)
            # expected_hours == 0 (t0 == now, or a clock skew edge case) ->
            # coverage 0.0 by convention: there is no window to have had
            # coverage over, so the honest answer is "no data", which
            # correctly fails the gate rather than dividing by zero.
            coverage = (present_hours / expected_hours) if expected_hours > 0 else 0.0

            greeks_by_instrument: Dict[Tuple[float, str], Dict[str, float]] = {}
            oi_by_instrument: Dict[Tuple[float, str], float] = {}
            for item in instruments_with_greeks:
                strike = item.get("strike")
                option_type = (item.get("option_type") or "").upper()
                if strike is None or option_type not in ("C", "P"):
                    continue
                key = (strike, option_type)
                greeks_by_instrument[key] = {
                    "gamma": item.get("gamma") or 0.0,
                    "delta": item.get("delta") or 0.0,
                }
                oi_by_instrument[key] = item.get("open_interest") or 0.0

            calculator = DealerInventoryCalculator(flow_rows, greeks_by_instrument, spot_price, currency)
            calc_dict = calculator.calculate()
            coverage_dict = calculator.coverage_report(oi_by_instrument)
            violation_rate = coverage_dict["violation_rate"]
            n_strikes_checked = coverage_dict["n_strikes"]
            legs_excluded_no_oi = coverage_dict["legs_excluded_no_oi"]

            # Fix round 3 (Important): `coverage` (fix round 1) is now
            # currency-wide collection completeness -- it answers "was the
            # collector healthy", not "did THIS expiry ever trade since T0".
            # A currently-listed expiry that has genuinely never traded
            # since T0 has `flow_rows == []`, so n_strikes_checked == 0 AND
            # legs_excluded_no_oi == 0 -- neither round-2 guard below fires
            # (the hard floor needs legs_excluded_no_oi > 0; 0/0 exclusion
            # rate is 0.0, under the threshold) -- while currency-wide
            # coverage can still read high (the collector is fine; it just
            # never saw a trade on THIS contract). That combination used to
            # render_inferred=True with zero legs actually checked -- the
            # same "validated nothing, reported clean" pattern as round 2's
            # bug, one level up. Checked directly here (this expiry's own
            # flow_rows, not any coverage proxy) rather than folded into the
            # exclusion-rate check, because "this expiry has zero trade
            # history" and "some of this expiry's legs are unpriced" are two
            # different failure reasons that deserve two different messages.
            no_trades_for_this_expiry = len(flow_rows) == 0

            total_legs_seen = n_strikes_checked + legs_excluded_no_oi
            exclusion_rate = (legs_excluded_no_oi / total_legs_seen) if total_legs_seen > 0 else 0.0
            insufficient_oi_reference = (
                (n_strikes_checked == 0 and legs_excluded_no_oi > 0)
                or exclusion_rate > _DEALER_INVENTORY_MAX_EXCLUSION_RATE
            )

            render_inferred = (
                not no_trades_for_this_expiry
                and coverage >= _DEALER_INVENTORY_COVERAGE_GATE
                and violation_rate <= _DEALER_INVENTORY_VIOLATION_GATE
                and not insufficient_oi_reference
            )
            unavailable_reason = None
            if not render_inferred:
                if no_trades_for_this_expiry:
                    unavailable_reason = (
                        f"no trade history for this expiry since T0 "
                        f"(currency-wide coverage {coverage * 100:.1f}% -- collector health, not "
                        f"this contract's own history)"
                    )
                else:
                    unavailable_reason = (
                        f"coverage {coverage * 100:.1f}%, violations {violation_rate * 100:.1f}%"
                    )
                    if insufficient_oi_reference:
                        unavailable_reason += (
                            f", insufficient OI reference ({legs_excluded_no_oi}/{total_legs_seen} "
                            f"legs excluded, {exclusion_rate * 100:.1f}%)"
                        )

            n_signed_trades = sum(row.get("trade_count", 0) for row in flow_rows)

            strike_rows = tuple(
                DealerInventoryStrikeRow(
                    strike=strike,
                    dealer_net_c=data["dealer_net_c"],
                    dealer_net_p=data["dealer_net_p"],
                    inferred_gex=data["inferred_gex"],
                    inferred_dex=data["inferred_dex"],
                    call_gross_volume=data["call_gross_volume"],
                    put_gross_volume=data["put_gross_volume"],
                    call_trade_count=data["call_trade_count"],
                    put_trade_count=data["put_trade_count"],
                )
                for strike, data in sorted(calc_dict["strike_data"].items())
            )
            cr = calc_dict["key_levels"]["call_resistance"]
            ps = calc_dict["key_levels"]["put_support"]

            return DealerInventoryResult(
                strike_rows=strike_rows,
                key_levels=DealerInventoryKeyLevels(
                    call_resistance=(
                        DealerInventoryLevel(strike=cr["strike"], inferred_gex=cr["inferred_gex"])
                        if cr else None
                    ),
                    put_support=(
                        DealerInventoryLevel(strike=ps["strike"], inferred_gex=ps["inferred_gex"])
                        if ps else None
                    ),
                    hvl=calc_dict["key_levels"]["hvl"],
                ),
                total_inferred_gex=calc_dict["total_inferred_gex"],
                total_inferred_dex=calc_dict["total_inferred_dex"],
                spot_price=spot_price,
                currency=currency,
                t0_epoch_ms=t0_ms,
                coverage=coverage,
                violation_rate=violation_rate,
                n_signed_trades=n_signed_trades,
                render_inferred=render_inferred,
                unavailable_reason=unavailable_reason,
                stale_strikes=tuple(calc_dict.get("stale_strikes", [])),
            )
        except Exception:
            logger.error(
                f"OnChainAnalysisService: inferred dealer positioning failed "
                f"unexpectedly for {currency} {expiration} -- degrading to "
                "'no inferred section' rather than aborting GEX/DEX "
                "(additive-only, institutional_metrics_spec.md section 2 / task C3)",
                exc_info=True,
            )
            return None

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
                    # bugfix_spec.md Item 7 anchor table: flow notional
                    # (amount x index_price) is a USD conversion -> index.
                    spot_price=analyzer.index_price,
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
                        underlying_price=analyzer.index_price,
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
                        spot_price=analyzer.index_price,
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
                        spot_price=analyzer.index_price,
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

                # bugfix_spec.md Item 7 anchor table: 25d skew/RR strike
                # selection and ATM IV are settlement-space (ATM is defined
                # at the forward) -- this expiry's own forward, not the
                # index. Falls back to the index (with a warning) for the
                # rare expiry with no priced instrument at all.
                forward_price = analyzer.forward_price_by_expiration.get(expiration)
                if forward_price is None:
                    logger.warning(
                        f"No forward price for expiration {expiration} -- "
                        f"falling back to index price for volatility surface"
                    )
                    forward_price = analyzer.index_price

                calculator = VolatilitySurfaceCalculator(
                    instruments=instruments,
                    spot_price=forward_price,
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
        Calculate all market-wide metrics and store in the builder.

        refactor_design_spec.md T11 (M1): the 222-line, 8-job method that
        used to live here is split into ``MarketWideOrchestrator``'s 8
        named phase methods (market_wide_orchestrator.py). This is now a
        thin delegator so existing callers/tests exercising this method's
        builder-mutation contract keep working unchanged.

        Args:
            analyzer: OnChainMetricsCalculator with per-expiration state
                already populated.
            currency: Currency symbol.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).
            aggregate_gex_dex_result: Typed aggregate GexDexResult from
                ``_fetch_greeks_and_store_gex_dex`` -- computed in a different
                phase but a field of the same ``MarketWideResult`` this
                orchestrator assembles.
        """
        orchestrator = MarketWideOrchestrator(self.api)
        market_wide_result = orchestrator.run(
            analyzer, currency, progress_callback, aggregate_gex_dex_result,
        )
        if builder is not None:
            builder.set_market_wide(market_wide_result)

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
                # bugfix_spec.md Item 7 anchor table: ATM strike selection
                # is settlement-space (ATM is defined at the forward) --
                # this expiry's own forward, not a global index/future.
                forward_price = analyzer.forward_price_by_expiration.get(expiration)
                if forward_price is None:
                    logger.warning(
                        f"No forward price for expiration {expiration} -- "
                        f"falling back to index price for ATM strike selection"
                    )
                    forward_price = analyzer.index_price

                # Save today's OI snapshot (UPSERT, safe to call multiple times/day)
                self.repository.save_daily_oi_snapshot(
                    currency=analyzer.currency,
                    expiration=expiration,
                    instruments=instruments,
                    underlying_price=forward_price,
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
                # Find ATM strike (closest to this expiry's own forward)
                atm_strike = min(
                    instruments,
                    key=lambda i: abs(i["strike"] - forward_price)
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

                    if (
                        abs(change_pct) >= OI_CHANGE_SIGNIFICANT_PCT_THRESHOLD
                        and abs(abs_change) >= OI_CHANGE_SIGNIFICANT_ABS_THRESHOLD
                    ):
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
