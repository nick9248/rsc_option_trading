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

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.chart_generator import (
    generate_flow_distribution_chart,
    generate_flow_trend_chart,
    generate_net_flow_chart,
    inject_hover_js,
    save_chart,
)
from coding.core.analytics.dealer_inventory_calculator import DealerInventoryCalculator
from coding.core.analytics.exposure_profile_calculator import ExposureProfileCalculator
from coding.core.analytics.fixed_strike_vol_calculator import (
    FixedStrikeVolCalculator,
    compute_nearest_strike_atm_iv,
)
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.results.market_wide_results import (
    ForwardVolBucket,
    ForwardVolResult,
    GammaRolloffResult,
    GammaRolloffRow,
    SkewTermStructureEntry,
    SkewTermStructureResult,
)
from coding.core.analytics.historical_normalizer import (
    HistoricalNormalizer,
    MetricSpec,
    NormalizedMetric,
)
from coding.core.analytics.on_chain_analyzer import (
    OnChainMetricsCalculator,
    _to_market_metrics,
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
from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.core.analytics.results.exposure_profile_results import (
    ExposureProfileResult,
    ExposureStrikeRow,
)
from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult
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

# Task G2-A (Wave G re-review, Minor): _compute_bs_gamma's own docstring
# claims it "never fabricates a 0.0" -- but BlackScholesCalculator.
# calculate_greeks has a blanket `except Exception` that returns all-zero
# greeks on any internal failure (e.g. math.log/math.exp domain/overflow
# errors), and the reviewer directly reproduced that fallback firing --
# silently -- via a negative strike (the old `not strike`/`not mark_iv`
# guards below are the M1/#5/#6 truthiness bug: `not -50000` is False, so
# a NEGATIVE strike/mark_iv sailed straight past them) and via an "absurd"
# mark_iv large enough to overflow a float squaring inside the d1/d2 math.
# The guarantee was only true by accident (upstream input shape, not this
# function's own contract). Closed at the source with explicit numeric
# range checks instead of truthiness checks, plus a sane upper ceiling on
# mark_iv: 1000% annualized IV is already far beyond anything Deribit's
# real book has shown even during extreme crypto vol spikes (typical peak
# is low hundreds of percent) -- a documented, generous ceiling, not a
# tuned statistical bound.
_MAX_SANE_MARK_IV_PCT = 1000.0


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
            # Wave H Task H-F, Fix 3: nearest_expiry_median_underlying_price
            # now returns None (not a fabricated 0.0) when no instrument has
            # a priced underlying_price either -- both the primary fetch and
            # this fallback have failed, so there is no real price to anchor
            # spot-scaled metrics (notional, moneyness, GEX's S^2 term,
            # max-pain distance) on. Raise loudly instead of silently
            # proceeding with a 0.0 that would poison every downstream
            # calculation without any in-band marker.
            index_price = analyzer.nearest_expiry_median_underlying_price()
            if index_price is None:
                raise RuntimeError(
                    f"No index price available for {currency}: primary "
                    f"get_index_price fetch failed ({e}) and the "
                    f"nearest-expiry median underlying_price fallback found "
                    f"no priced instrument either -- refusing to analyze "
                    f"with an unknown spot price"
                ) from e
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
        aggregate_gex_dex_result, gamma_rolloff_result = self._fetch_greeks_and_store_gex_dex(
            analyzer, progress, builder
        )

        # Always fetch buy/sell flow
        self._calculate_buy_sell_flow(analyzer, progress, builder)

        # Calculate volatility surface metrics (uses enriched instruments)
        self._calculate_volatility_surface(analyzer, progress, builder)

        # Calculate DB-dependent metrics (OI changes, IV percentile per expiry)
        self._calculate_oi_changes_and_iv_percentile(analyzer, progress, builder)

        # Calculate market-wide metrics (term structure, basis, RV, VRP, etc.)
        self._calculate_market_wide_metrics(
            analyzer, currency, progress, builder, aggregate_gex_dex_result,
            gamma_rolloff_result,
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

        # institutional_metrics_spec.md section 6 / task C7: signed delta-
        # weighted taker flow (HIRO analog), summed from the daemon-
        # persisted flow_delta_hourly table over the trailing 24h -- not
        # recomputed from raw trades here. Additive, same dataclasses.
        # replace pattern as normalized_metrics above.
        progress("Calculating delta-adjusted flow summary...")
        delta_flow_buckets, delta_flow_lookback_hours, delta_flow_hours_present, delta_flow_stale_since = (
            self._build_delta_flow_summary(currency)
        )
        if delta_flow_buckets:
            result = dataclasses.replace(
                result,
                delta_flow_buckets=delta_flow_buckets,
                delta_flow_lookback_hours=delta_flow_lookback_hours,
                delta_flow_hours_present=delta_flow_hours_present,
                delta_flow_stale_since=delta_flow_stale_since,
            )

        # Generate report (includes GEX/DEX and flow) — rendered directly
        # from the typed result (T10). analyzer.generate_report() (a pure
        # delegator to OnChainReportFormatter.render_full as of T3) is
        # deleted; render_full_from_result is the sole full-report render
        # path now, and the first live call site for
        # render_market_wide_from_result (dead code before this task).
        #
        # institutional_metrics_spec.md section 9 (Task D2 independent
        # review round 2, Important #1): report_formatter's per-expiry
        # CONTEXT rendering needs a "now" reference for its Max Pain
        # expiry-week gate. Computed HERE (this module IS in
        # tests/conftest.py's _FROZEN_CLOCK_MODULES freeze list) and
        # threaded explicitly through render_full_from_result ->
        # render_expiration_from_result -> render_expiration ->
        # format_context_section, rather than any of those read the clock
        # themselves -- a formatter-layer clock read would not be frozen
        # by the characterization suite (that module list is the only
        # place the freeze applies), silently making the golden master
        # depend on which real day the suite executes.
        progress("Generating analysis report...")
        report_now_utc = datetime.now(timezone.utc)
        report = OnChainReportFormatter().render_full_from_result(result, report_now_utc)

        # Save reports per expiration — rendered from the typed result (T8),
        # not the report text (no string scanning). Same now_utc reused
        # (not a second, independently-computed "now") so both renders of
        # the same report describe the identical instant.
        self._save_reports_per_expiration(result, currency, report_now_utc)

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
        convention already used by ``MarketWideCalculator.calculate_dte`` and
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
        # Don't derive the comparison clock from most_stale.tzinfo -- when the
        # DB column backing it is `timestamp without time zone`, tzinfo is
        # None, and datetime.now(None) silently returns naive-LOCAL time (its
        # documented behavior), not UTC, even though this codebase's naive
        # datetime columns are always UTC-valued (see collect_hour's default
        # and _DELTA_FLOW_STALENESS_THRESHOLD_HOURS's now_utc_naive pattern
        # just below in this file). On a non-UTC host that silently shifts
        # the staleness threshold by the host's UTC offset. Use an explicit
        # UTC-valued naive clock instead, matching that established pattern.
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        threshold = now_utc_naive - timedelta(hours=self._STALENESS_THRESHOLD_HOURS)
        return most_stale if most_stale < threshold else None

    def _oldest_observation_age_days(
        self,
        table: str,
        column: str,
        currency: str,
        lookback_hours: int,
        expiration: Optional[str] = None,
        time_column: Optional[str] = None,
    ) -> Optional[float]:
        """
        Age in days of the oldest observation
        ``repository.get_metric_history`` (same args) actually returned, or
        None if unavailable. Feeds ``HistoricalNormalizer``'s calendar-span
        gate (Task G2-E) via ``MetricSpec.oldest_age_days_30d``/``_90d`` --
        the gate that catches a per-expiry series racking up n >= MIN_OBS
        observations while spanning only a handful of calendar days (a
        front-month expiry, by definition, has not existed -- and so has
        not been observed by the hourly collector -- 30 or 90 days before
        its own expiration).

        Deliberately isolated in its OWN try/except, separate from the
        try/except already wrapping each metric's ``get_metric_history``
        call below: a failure here must never be attributed to, or
        suppress visibility of, a pre-existing history-fetch failure (this
        task's isolation constraint). A failure here degrades gracefully
        to "span unknown" (None) for just this one window --
        ``HistoricalNormalizer.has_sufficient_span`` treats None as exempt
        from the gate (falls back to the prior count-only behavior for
        that window) rather than discarding an otherwise-successful
        history fetch over a span-lookup problem.
        """
        try:
            oldest_ts = self.repository.get_metric_history_oldest_timestamp(
                table=table, column=column, currency=currency,
                lookback_hours=lookback_hours, expiration=expiration,
                time_column=time_column,
            )
        except ValueError as exc:
            logger.error(
                "Oldest-timestamp lookup for %s.%s hit a whitelist violation "
                "(programming bug, not a data issue): %s", table, column, exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Failed to fetch oldest timestamp for %s.%s (%s): %s",
                table, column, currency, exc,
            )
            return None

        if oldest_ts is None:
            return None
        # Same tz-defensive pattern _compute_historical_context_staleness
        # already uses below (datetime.now(most_stale.tzinfo)): the DB
        # driver may return a naive or aware datetime depending on the
        # column type, so match "now"'s tz-awareness to what was actually
        # returned rather than assuming UTC-aware.
        now = datetime.now(oldest_ts.tzinfo)
        return (now - oldest_ts).total_seconds() / 86400.0

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
                    oldest_age_days_30d=self._oldest_observation_age_days(
                        table="onchain_analysis_snapshots", column="total_net_gex",
                        currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                    ),
                    oldest_age_days_90d=self._oldest_observation_age_days(
                        table="onchain_analysis_snapshots", column="total_net_gex",
                        currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                    ),
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
                    # total_oi's history is the element-wise sum of
                    # total_call_oi + total_put_oi, both fetched with
                    # identical WHERE/ORDER BY clauses against the same
                    # table (_sum_paired_history's own row-alignment
                    # assumption) -- total_call_oi's oldest timestamp is
                    # therefore representative of the summed series' span
                    # too.
                    oldest_age_days_30d=self._oldest_observation_age_days(
                        table="onchain_analysis_snapshots", column="total_call_oi",
                        currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                    ),
                    oldest_age_days_90d=self._oldest_observation_age_days(
                        table="onchain_analysis_snapshots", column="total_call_oi",
                        currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                    ),
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
                        oldest_age_days_30d=self._oldest_observation_age_days(
                            table="onchain_analysis_snapshots", column="put_call_ratio_oi",
                            currency=currency, lookback_hours=lookback_30d, expiration=front_month,
                        ),
                        oldest_age_days_90d=self._oldest_observation_age_days(
                            table="onchain_analysis_snapshots", column="put_call_ratio_oi",
                            currency=currency, lookback_hours=lookback_90d, expiration=front_month,
                        ),
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
                    oldest_age_days_30d=self._oldest_observation_age_days(
                        table="volatility_index_history", column="dvol",
                        currency=currency, lookback_hours=lookback_30d, time_column="date",
                    ),
                    oldest_age_days_90d=self._oldest_observation_age_days(
                        table="volatility_index_history", column="dvol",
                        currency=currency, lookback_hours=lookback_90d, time_column="date",
                    ),
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
                    oldest_age_days_30d=self._oldest_observation_age_days(
                        table="funding_rate_history", column="funding_rate",
                        currency=currency, lookback_hours=lookback_30d, time_column="date",
                    ),
                    oldest_age_days_90d=self._oldest_observation_age_days(
                        table="funding_rate_history", column="funding_rate",
                        currency=currency, lookback_hours=lookback_90d, time_column="date",
                    ),
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

    # institutional_metrics_spec.md section 6 / task C7: report window for
    # the DELTA-ADJUSTED FLOW section, matching BuySellFlowAnalyzer's own
    # 24h flow window (on_chain_analysis_service.py's
    # _calculate_buy_sell_flow) so the two flow sections describe the same
    # lookback even though they read different tables.
    _DELTA_FLOW_LOOKBACK_HOURS = 24.0

    # Review fix (Important #4): same threshold value as
    # _STALENESS_THRESHOLD_HOURS (Task C1's historical-context pattern) --
    # a currency whose most recently persisted flow_delta_hourly row is
    # more than this many hours behind "now" gets an explicit staleness
    # note instead of a confident-looking total silently computed over an
    # incomplete window (e.g. a daemon down for 12h).
    _DELTA_FLOW_STALENESS_THRESHOLD_HOURS = 3

    def _build_delta_flow_summary(
        self, currency: str
    ) -> Tuple[Tuple[FlowBucket, ...], float, int, Optional[datetime]]:
        """
        Sum the persisted ``flow_delta_hourly`` rows over the trailing
        ``_DELTA_FLOW_LOOKBACK_HOURS`` for ``currency`` (institutional_
        metrics_spec.md section 6 / task C7). Reads pre-aggregated data via
        ``DatabaseRepository.get_delta_flow_summary`` -- never recomputes
        BS delta from raw trades at report time (the daemon already did
        that once, hourly, per ``ProspectiveCollector._persist_delta_flow``).

        Review fix (Important #4): also returns ``hours_present`` and
        ``stale_since`` (via ``DatabaseRepository.get_delta_flow_coverage``)
        so the report can disclose a stale/lagging daemon -- task-C7-
        brief.md explicitly named this case, and the SUM alone cannot
        surface it: a daemon down for 12h still produces a confident-
        looking total over whatever rows DID land, with the header still
        claiming the full lookback window. Mirrors ``_compute_historical_
        context_staleness``'s pattern (Task C1): ``stale_since`` is the
        most recently persisted hour, but ONLY when it is more than
        ``_DELTA_FLOW_STALENESS_THRESHOLD_HOURS`` behind "now" -- ``None``
        when fresh (or when there are no rows at all, since the empty-
        buckets case already suppresses the whole section).

        Returns ``((), _DELTA_FLOW_LOOKBACK_HOURS, 0, None)`` when there is
        no repository, or when the window has no rows yet (feature just
        shipped, or the daemon hasn't run in this window, or a real DB
        error) -- ``format_delta_flow_section`` renders "" for an empty
        buckets tuple, matching the codebase's "no data -> no section"
        convention. This is never confused with "genuine zero trading
        activity": that case still has a real ``skipped_count == 0``
        ``trade_count == 0`` "ALL" row persisted by the daemon, which DOES
        come back from ``get_delta_flow_summary`` and IS rendered.
        """
        if self.repository is None:
            return (), self._DELTA_FLOW_LOOKBACK_HOURS, 0, None

        # Review fix (Important #2): flow_delta_hourly.snapshot_hour is
        # written by ProspectiveCollector using naive-UTC hour buckets (the
        # VPS's OS and DB timezone are both confirmed UTC -- see
        # task-C7-report.md's Important #2 writeup). A naive
        # datetime.now() here is LOCAL time (this machine is Europe/Berlin,
        # UTC+1/+2) -- comparing it directly against a UTC-labeled column
        # silently shrinks the "24h" window to 22-23h, exactly the same bug
        # class c4bff4e already fixed for the BS-fallback greeks path.
        # Naive-UTC on both sides, matching that fix's pattern verbatim.
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        since = now_utc_naive - timedelta(hours=self._DELTA_FLOW_LOOKBACK_HOURS)

        try:
            rows = self.repository.get_delta_flow_summary(currency=currency, since=since)
        except Exception as exc:
            logger.warning("Failed to build delta flow summary for %s: %s", currency, exc)
            return (), self._DELTA_FLOW_LOOKBACK_HOURS, 0, None

        buckets = tuple(FlowBucket(**row) for row in rows)

        hours_present = 0
        stale_since: Optional[datetime] = None
        try:
            coverage = self.repository.get_delta_flow_coverage(currency=currency, since=since)
            hours_present = coverage.get("hours_present", 0)
            max_snapshot_hour = coverage.get("max_snapshot_hour")
            if max_snapshot_hour is not None:
                threshold = now_utc_naive - timedelta(hours=self._DELTA_FLOW_STALENESS_THRESHOLD_HOURS)
                if max_snapshot_hour < threshold:
                    stale_since = max_snapshot_hour
        except Exception as exc:
            logger.warning("Failed to build delta flow coverage for %s: %s", currency, exc)

        return buckets, self._DELTA_FLOW_LOOKBACK_HOURS, hours_present, stale_since

    def _build_skew_term_structure(
        self,
        analyzer: OnChainMetricsCalculator,
        currency: str,
    ) -> Optional[SkewTermStructureResult]:
        """
        Build the SKEW TERM STRUCTURE result (institutional_metrics_spec.md
        section 3(c), Task C4): one row per expiry with the delta-
        interpolated RR25/BF25 computed during the vol-surface phase
        (``analyzer._skew_by_expiry``), plus each metric's own 30d
        percentile/regime against ``volatility_skew_history`` -- C1's
        HistoricalNormalizer pattern, applied per-expiry (section 1's
        "normalized against the same expiry's own history when a
        per-expiry section renders it"). Only the 30d window is used here
        (unlike section 1's header block, which shows a 30d/90d pair) --
        the spec's per-row table format has one percentile per cell.

        Decision D10 (binding): ``volatility_skew_history`` starts EMPTY.
        Every percentile/regime field is expected to be None (C1's
        "insufficient history" fallback, rendered by
        ``format_skew_term_structure_section``) until the daemon has
        written >= ``HistoricalNormalizer.MIN_OBS`` hourly rows for that
        expiry. This is documented, accepted behavior, not a bug.

        Returns:
            None if there is no repository, or the vol-surface phase
            produced no skew data at all (mirrors ``_build_normalized_
            metrics``' "no repository -> skip" convention). A failure
            building ONE expiration's row is logged and that expiration is
            skipped; the rest still render (never raises).
        """
        if self.repository is None:
            return None

        skew_by_expiry = analyzer._skew_by_expiry
        if not skew_by_expiry:
            return None

        now_utc = datetime.now(timezone.utc)
        lookback_30d = self._NORMALIZER_LOOKBACK_30D_HOURS

        entries: List[SkewTermStructureEntry] = []
        for expiration, skew in skew_by_expiry.items():
            try:
                dte = MarketWideCalculator.calculate_days_to_expiry(expiration, now_utc)
                if dte is None:
                    logger.warning(
                        "Skipping skew term structure row for %s %s: "
                        "expiration string did not parse as a date",
                        currency, expiration,
                    )
                    continue

                rr_25d = skew.get("rr_25d")
                bf_25d = skew.get("bf_25d")

                rr_percentile_30d: Optional[float] = None
                rr_regime_30d: Optional[str] = None
                rr_n_30d = 0
                if rr_25d is not None:
                    rr_history_30d = self.repository.get_metric_history(
                        table="volatility_skew_history", column="rr_25d",
                        currency=currency, lookback_hours=lookback_30d,
                        expiration=expiration,
                    )
                    rr_n_30d = len(rr_history_30d)
                    if rr_n_30d >= HistoricalNormalizer.MIN_OBS:
                        rr_percentile_30d = HistoricalNormalizer.percentile(rr_25d, rr_history_30d)
                        rr_regime_30d = HistoricalNormalizer.regime_label(rr_percentile_30d)

                bf_percentile_30d: Optional[float] = None
                bf_n_30d = 0
                if bf_25d is not None:
                    bf_history_30d = self.repository.get_metric_history(
                        table="volatility_skew_history", column="bf_25d",
                        currency=currency, lookback_hours=lookback_30d,
                        expiration=expiration,
                    )
                    bf_n_30d = len(bf_history_30d)
                    if bf_n_30d >= HistoricalNormalizer.MIN_OBS:
                        bf_percentile_30d = HistoricalNormalizer.percentile(bf_25d, bf_history_30d)

                entries.append(SkewTermStructureEntry(
                    expiration=expiration, dte=dte,
                    atm_iv_interp=skew.get("atm_iv_interp"),
                    n_quotes_used=skew.get("n_quotes_used"),
                    rr_25d=rr_25d, rr_percentile_30d=rr_percentile_30d,
                    rr_regime_30d=rr_regime_30d, rr_n_30d=rr_n_30d,
                    bf_25d=bf_25d, bf_percentile_30d=bf_percentile_30d, bf_n_30d=bf_n_30d,
                ))
            except ValueError as exc:
                logger.error(
                    "skew_term_structure history hit a whitelist violation "
                    "(programming bug, not a data issue) for %s %s: %s",
                    currency, expiration, exc,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to build skew term structure row for %s %s: %s",
                    currency, expiration, exc,
                )

        if not entries:
            return None

        entries.sort(key=lambda e: e.dte)

        # RR_slope = RR25(back) - RR25(front): back is the LAST entry
        # (farthest DTE), front is the FIRST (nearest DTE) -- matches the
        # spec's own worked numeric example exactly (section 3(c): 25JUL26
        # -3.80 vs 25DEC26 -4.34 -> -0.54), not the surrounding prose
        # ("over the two nearest standard expiries"), which the example
        # contradicts if read literally. See Task C4 report.
        rr_slope: Optional[float] = None
        if len(entries) >= 2 and entries[0].rr_25d is not None and entries[-1].rr_25d is not None:
            rr_slope = entries[-1].rr_25d - entries[0].rr_25d

        return SkewTermStructureResult(entries=tuple(entries), rr_slope=rr_slope)

    def _build_forward_vol_curve(
        self,
        analyzer: OnChainMetricsCalculator,
        currency: str,
    ) -> Optional[ForwardVolResult]:
        """
        Build the FORWARD VOL result (institutional_metrics_spec.md
        section 8, Task C9): forward/event vol between adjacent
        expiries, from the SAME chain-derived per-expiry ATM IV
        (``analyzer._skew_by_expiry[...]["atm_iv_interp"]``)
        ``_build_skew_term_structure`` already reuses -- this never
        recomputes ATM IV from scratch.

        Unlike ``_build_skew_term_structure``, this needs NO repository:
        there is no percentile-history lookup, only a pure calculation
        over live per-expiry ATM IV/DTE (spec: "No new persistence -- the
        inputs are already stored by M3, so the history is derivable").
        It therefore runs even when ``self.repository`` is ``None``.

        Timezone (task brief's repeated lesson, cost fix rounds in C4,
        C5, C7, and a SECOND round in C8): ``now_utc`` is resolved ONCE
        here and reused for every expiry's DTE in this call, via
        ``MarketWideCalculator.calculate_days_to_expiry`` -- the EXACT
        same method (same clock convention, same 08:00 UTC settlement
        anchor) ``_build_skew_term_structure`` already uses for this same
        ``skew_by_expiry`` data. A shared expiry's DTE therefore uses the
        identical formula and settlement anchor in both sections. Note
        (Task C9 review, Minor #1): this method and
        ``_build_skew_term_structure`` each resolve their OWN
        ``datetime.now(timezone.utc)`` independently -- they are not
        threaded a single shared clock value -- so there is a
        theoretical sub-millisecond gap between the two reads within one
        analysis run. Immaterial today (both calls happen back-to-back in
        the same synchronous request, far below one DTE-day of drift),
        but "never desync" would overstate the guarantee; if a future
        caller needs byte-identical DTE across both sections, thread one
        ``now_utc`` through both builders instead of relying on this.

        Isolation: the try/except wraps the ENTIRE per-expiry body (DTE
        calculation, the None-DTE skip, and the ``skew.get(...)`` reads)
        in one block -- matching ``_build_skew_term_structure``'s own
        boundary exactly (Task C9 review, Important: an earlier revision
        of this method wrapped only the DTE calculation, leaving
        ``skew.get("atm_iv_interp")`` unguarded; a ``None`` ``skew`` for
        one expiry raised an uncaught ``AttributeError`` there, which
        propagated past ``builder.set_market_wide(...)`` and lost every
        market-wide report section for the run -- reproduced by the
        reviewer, fixed by widening the boundary). One bad
        expiration/label/skew-dict can never suppress the other
        expiries' rows. The pure-calculator call below is wrapped in its
        OWN separate try, so a bug in ``calculate_forward_vol_curve``
        cannot be blamed on (or hide behind) a per-expiry build failure,
        and vice versa.

        Returns:
            ``None`` when there is no skew data at all (mirrors
            ``_build_skew_term_structure``'s own gate), or when fewer
            than 2 expiries survive with both a usable ATM IV and a
            parseable DTE (spec: "Fewer than 2 expiries with ATM IV ->
            section omitted").
        """
        # getattr, not a direct attribute access: unlike
        # _build_skew_term_structure (which is gated on self.repository
        # being set BEFORE it ever touches analyzer._skew_by_expiry), this
        # method has no repository gate, so it must not assume every
        # caller's analyzer already carries this attribute (e.g. a test
        # double, or a real analyzer whose vol-surface phase never ran) --
        # isolation lesson from the task brief: this new call must not be
        # able to raise into a caller that never populated it.
        skew_by_expiry = getattr(analyzer, "_skew_by_expiry", None)
        if not skew_by_expiry:
            return None

        now_utc = datetime.now(timezone.utc)
        atm_by_expiry: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        for expiration, skew in skew_by_expiry.items():
            # Task C9 review (Important): the try boundary must wrap the
            # ENTIRE per-expiry body -- not just the DTE calculation --
            # matching _build_skew_term_structure's boundary exactly.
            # skew.get("atm_iv_interp") below can raise AttributeError if
            # `skew` is None for this expiry; that used to sit AFTER the
            # try/except and was uncaught, propagating past
            # builder.set_market_wide(...) and losing every market-wide
            # report section for the whole run -- the reviewer reproduced
            # this by execution. Fixed by widening the try to match the
            # sibling's own "wrap the whole per-expiry body" convention.
            try:
                dte = MarketWideCalculator.calculate_days_to_expiry(expiration, now_utc)
                if dte is None:
                    logger.warning(
                        "Skipping forward vol row for %s %s: expiration string "
                        "did not parse as a date",
                        currency, expiration,
                    )
                    continue
                atm_by_expiry[expiration] = (skew.get("atm_iv_interp"), dte)
            except Exception as exc:
                logger.warning(
                    "Failed to build forward vol row for %s %s: %s",
                    currency, expiration, exc,
                )

        if len(atm_by_expiry) < 2:
            return None

        try:
            curve = MarketWideCalculator.calculate_forward_vol_curve(atm_by_expiry)
        except Exception as exc:
            logger.error(
                "Failed to calculate forward vol curve for %s: %s", currency, exc, exc_info=True,
            )
            return None

        buckets = tuple(
            ForwardVolBucket(
                from_expiry=b["from_expiry"],
                to_expiry=b["to_expiry"],
                t1_days=b["t1_days"],
                t2_days=b["t2_days"],
                sigma1_pct=b["sigma1_pct"],
                sigma2_pct=b["sigma2_pct"],
                fwd_var=b["fwd_var"],
                fwd_vol_pct=b["fwd_vol_pct"],
                negative_variance=b["negative_variance"],
                event_premium=b["event_premium"],
                flags=tuple(b["flags"]),
            )
            for b in curve["buckets"]
        )
        if not buckets:
            return None
        return ForwardVolResult(buckets=buckets)

    @staticmethod
    def _compute_bs_gamma(
        bs_calculator: BlackScholesCalculator,
        item: Dict[str, Any],
        mark_iv: Optional[float],
        underlying_price: Optional[float],
        now_utc: datetime,
    ) -> Optional[float]:
        """
        Task G2-A (Wave G fresh audit, metric-verification agent): compute
        gamma from ``mark_iv`` via ``BlackScholesCalculator`` instead of
        trusting ``ticker.greeks.gamma`` directly.

        Deribit's ticker ``greeks.gamma`` is heavily quantized/rounded --
        confirmed against a live 883-ticker sample (this task's audit):
        211/533 (a separate live sample cited in the task brief) and,
        independently, 242/870 non-null tickers in this repo's own
        recorded fixture (tests/fixtures/onchain/BTC_20260725_203222/
        tickers.json.gz) all read exactly ``1e-05``, and only 31 distinct
        gamma values exist across those 870 tickers at all. This
        understated aggregate GEX by ~6.2% and, on one 321-DTE expiry, by
        up to 1.9x versus the value ``ProspectiveCollector._enrich_with_
        greeks`` (the daemon path) computes and persists from the exact
        same ``mark_iv`` -- that daemon path is structurally forced into
        BS-derived gamma (``get_book_summary`` carries no ``greeks`` key
        at all), and this method makes the report/GUI path agree with it
        for the same expiry/hour instead of disagreeing.

        delta/vega/theta are deliberately NOT recomputed here -- the same
        sample showed 803/707/750 distinct values (out of 870) for those
        three fields respectively, precise enough to trust directly from
        the ticker; only gamma is bad at ticker precision.

        Returns:
            The BS-computed gamma, or ``None`` (never a fabricated 0.0 --
            a real "could not compute" case, not "zero exposure") if
            ``mark_iv``/``strike``/``underlying_price``/the instrument
            name are missing, non-positive, unparseable, or outside a
            sane range (see ``_MAX_SANE_MARK_IV_PCT``), or the option has
            already passed its 08:00 UTC settlement (``time_to_expiry <=
            0``). A ``None`` return is exactly the "missing gamma" case
            ``GexDexCalculator._aggregate_by_strike``'s completeness
            tracking (Task G2-A, bug 2) is built to detect -- the
            instrument's open_interest still counts toward its strike,
            but its gamma contribution reads as a disclosed gap rather
            than a silent 0.0.

            Wave G re-review (Minor): every guard below is an explicit
            numeric range check (``is None or <= 0``), never a bare
            truthiness check (``not x``) -- ``not -50000`` is ``False`` in
            Python, so a truthiness guard silently lets a negative
            strike/mark_iv straight through to
            ``BlackScholesCalculator.calculate_greeks``, whose blanket
            ``except Exception`` then returns an all-zero-greeks dict
            that this method used to return unchecked -- a fabricated
            0.0 masquerading as "computed successfully", exactly what
            this method's contract promises never happens.

            Task Wave-I-E (institutional-benchmark audit, UNVERIFIED
            finding investigated and closed as NOT MATERIAL): an audit
            flagged that ``BlackScholesCalculator.calculate_greeks``
            recomputes gamma with the STANDARD linear-payoff BS formula,
            while Deribit's BTC/ETH options are coin-margined/coin-settled
            ("inverse") contracts whose true payoff is ``max(S-K,0)/S``
            (in BTC), not ``max(S-K,0)`` (in USD) -- raising the question
            of whether a genuine inverse/quanto convention adjustment is
            missing here. Investigated with three independent, converging
            lines of evidence, all pointing the same way:

            1. Math (Alexander, Chen & Imeraj, "Inverse and Quanto Inverse
               Options in a Black-Scholes World", arXiv:2107.12041v5,
               eq. 3-4 and Table A.1's "Inverse" column): converting the
               true BTC payoff to USD at the SAME instant -- S_T * (S_T -
               K)^+/S_T -- collapses EXACTLY to (S_T-K)^+, the standard
               payoff, for every path (footnote 15 in that paper: this
               holds exactly prior to expiry; only the terminal
               settlement-averaging mechanics introduce a tiny
               approximation, and only at expiry itself). Consequently
               Table A.1's own closed-form "Inverse" delta/gamma
               (``omega*Phi(omega*d1)`` / ``phi(d1)/(S*sigma*sqrt(tau))``)
               ARE the standard BS formulas -- identical to what
               ``_calculate_delta``/``_calculate_gamma`` compute here. The
               paper's genuinely different, non-monotonic-delta /
               possibly-negative-gamma formulas (its "Quanto inverse"
               column) are for a DIFFERENT, not-yet-exchange-traded
               product with a pre-fixed conversion factor -- explicitly
               NOT what Deribit lists (paper section 2.4: "these are not
               exchange-traded products at the time of writing").

            2. Deribit's own documentation (support.deribit.com's
               "Inverse Options" article -- 403s to a direct fetch, but
               readable via a text-proxy) states their own pricing model
               literally as ``C = X*N(d1) - K*N(d2)*e^(-R*T)``, i.e. the
               plain vanilla BS formula, not divided by S or otherwise
               inverse-adjusted (the one documented wrinkle is R =
               ln(F/X)/T, an implied rate from the futures basis, rather
               than an assumed r=0 -- this codebase's ``BlackScholes
               Calculator`` defaults to r=0, a separate, minor, and
               untouched-by-this-task discrepancy).

            3. Empirical (this repo's own fixture,
               tests/fixtures/onchain/BTC_20260725_203434/tickers.json.gz,
               883 tickers): comparing raw ticker gamma against this
               method's BS-recomputed gamma for the 628 non-quantized-
               floor tickers gives a ratio clustered tightly around 1.0
               (median ~0.93-1.0) with NO systematic trend across 8
               moneyness buckets (0.51x-1.67x, medians 0.985-1.16) or 8
               tenor buckets (medians 0.80-1.16) -- exactly what pure
               rounding noise around a matching convention looks like,
               and NOT the systematic S- or K-dependent multiplicative
               bias a genuine convention mismatch would produce.

            Conclusion: this method's plain-BS gamma is the mathematically
            correct convention for Deribit's actual (non-quanto) inverse
            contracts when interpreted as USD-denominated sensitivity --
            which is exactly how ``GexDexCalculator`` consumes it
            (``net_gex = net_gamma * spot_squared * 0.01``, the
            industry-standard dollar-gamma-per-1%-move convention). G2-A's
            original quantization rationale for BS-recomputing gamma
            therefore stands untouched by the inverse-settlement question
            -- there was no second bug layered underneath it. Deribit's
            "Smile Greek" delta (Delta = Delta_BS + vega * dSigma/dS,
            mentioned in Deribit Insights educational content) remains a
            separate, unconfirmed question -- no Deribit source found
            here states whether the LIVE ticker delta uses it -- but it is
            orthogonal to this method (which never recomputes delta) and
            is not a re-open of this finding.
        """
        if underlying_price is None or underlying_price <= 0:
            return None

        if (
            mark_iv is None
            or mark_iv <= 0
            or mark_iv > _MAX_SANE_MARK_IV_PCT
        ):
            return None

        strike = item.get("strike")
        name = item.get("instrument_name", "")
        if strike is None or strike <= 0 or not name:
            return None

        parsed = bs_calculator.parse_instrument_name(name)
        if parsed is None:
            return None

        # Mirrors ProspectiveCollector._enrich_with_greeks's own naive-UTC
        # convention exactly (BlackScholesCalculator.parse_instrument_name
        # always builds a naive-UTC 08:00 expiry_time) -- a naive-LOCAL
        # "now" here would reintroduce the same tau error this campaign
        # already fixed for that daemon path.
        now_utc_naive = now_utc.replace(tzinfo=None) if now_utc.tzinfo is not None else now_utc
        time_to_expiry = bs_calculator.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
        if time_to_expiry <= 0:
            return None

        calc = bs_calculator.calculate_greeks(
            spot_price=float(underlying_price),
            strike_price=float(strike),
            time_to_expiry=time_to_expiry,
            implied_volatility=float(mark_iv) / 100.0,
            option_type=parsed["option_type"],
        )
        return calc["gamma"]

    def _fetch_greeks_and_store_gex_dex(
        self,
        analyzer: OnChainMetricsCalculator,
        progress_callback: Callable[[str], None],
        builder: Optional[OnChainAnalysisBuilder] = None,
    ) -> Tuple[Optional[Any], Optional[GammaRolloffResult]]:
        """
        Fetch Greeks for all instruments and store GEX/DEX data in analyzer.

        Args:
            analyzer: OnChainMetricsCalculator with parsed data.
            progress_callback: Callback for progress updates.
            builder: T6 dual-write target (typed aggregate result).

        Returns:
            ``(aggregate_result, gamma_rolloff_result)``:
              - The typed aggregate ``GexDexResult`` (or ``None`` if no
                expiration produced enriched instruments) — the caller
                threads it into ``_calculate_market_wide_metrics``, which
                needs it for ``MarketWideResult.aggregate_gex_dex``.
              - The typed ``GammaRolloffResult`` (institutional_metrics_
                spec.md section 5, Task C6; ``None`` on the same "no data"
                condition, or if building it failed -- see
                ``_build_gamma_rolloff``) — threaded the same way for
                ``MarketWideResult.gamma_rolloff``.
        """
        gex_dex_typed_by_expiry: Dict[str, Any] = {}

        # institutional_metrics_spec.md section 7 / Task C8: resolved ONCE
        # for the whole per-expiry loop, from a single UTC-explicit clock
        # read (never this local machine's naive datetime.now(), which is
        # not UTC -- see _calculate_fixed_strike_vol_matrix's docstring and
        # this campaign's repeated day-boundary lesson). Kept as a
        # timezone-aware datetime (not just a date) so
        # _calculate_fixed_strike_vol_matrix can also use it for the
        # already-settled-expiry check (spec 7(c): "Expiry gone (settled)
        # between the two days -> skip the expiry").
        fixed_strike_vol_now_utc = datetime.now(timezone.utc)

        # Task G2-A (Wave G fresh audit, bug 1): one BlackScholesCalculator
        # instance for the whole per-expiry loop below (no per-instrument
        # construction cost; the class holds no per-instrument state) --
        # see _compute_bs_gamma's docstring for why gamma is computed here
        # instead of read from the ticker directly.
        bs_calculator = BlackScholesCalculator()

        for expiration in analyzer.get_expirations():
            instruments = analyzer.parsed_data.get(expiration, [])
            if not instruments:
                continue

            progress_callback(f"Fetching Greeks for {expiration} ({len(instruments)} instruments)...")

            # Fetch Greeks for each instrument
            instruments_with_greeks = []
            # Wave G re-review (Important #1): a ticker fetch that raises
            # (rate-limiting, API errors) drops the instrument entirely --
            # correct, you can't use data you don't have -- but the drop
            # must still feed the SAME completeness signal
            # GexDexResult.instruments_missing_gamma/oi_missing_gamma
            # carries for a null-greek-after-arrival instrument (Task
            # G2-A bug 2). Without this, the exact scenario that motivated
            # this whole task (31/830 instruments dropped to rate-
            # limiting, one expiry losing 34.49% of its OI-weighted
            # representation) still reads as a clean "full book" -- the
            # drop never reached _aggregate_by_strike, so it never
            # incremented anything. Tracked here, at the actual drop site,
            # and folded into the per-expiry GexDexResult below (and, for
            # the elif instruments: branch further down, when EVERY
            # instrument in this expiration fails).
            dropped_instrument_count = 0
            dropped_instrument_oi = 0.0
            for i, item in enumerate(instruments):
                try:
                    ticker = self.api.get_ticker(item["instrument_name"])
                    # independent review round 4 sweep: same M1/#5/#6 bug
                    # class -- a two-arg .get(key, default) only applies
                    # the default when the key is ABSENT, not when it's
                    # present-but-null. "greeks" isn't declared in
                    # deribit_schemas.py's TICKER schema at all (option-
                    # specific field the schema doesn't model), but a
                    # present-but-null value here would otherwise crash
                    # the next line's greeks.get("delta") with
                    # AttributeError -- silently dropping this instrument
                    # via the per-instrument except below rather than
                    # extracting it.
                    greeks = ticker.get("greeks") or {}

                    item_with_greeks = item.copy()
                    item_with_greeks["delta"] = greeks.get("delta")
                    item_with_greeks["theta"] = greeks.get("theta")
                    item_with_greeks["vega"] = greeks.get("vega")
                    item_with_greeks["mark_iv"] = ticker.get("mark_iv")
                    # underlying_price is nullable in practice (stale/
                    # illiquid instruments); .get(key) or fallback covers
                    # both "absent" and "present-but-null" -- currently
                    # benign only because nothing downstream crashes on a
                    # None here yet, not because the two-arg form was safe.
                    item_with_greeks["underlying_price"] = ticker.get("underlying_price") or analyzer.index_price
                    # institutional_metrics_spec.md section 3(b) step 1
                    # (Task C4): RR25/BF25's "quoted" filter needs bid/ask.
                    # Ticker's fields are best_bid_price/best_ask_price
                    # (unlike book-summary's bid_price/ask_price, which the
                    # daemon's own enrichment reads) -- normalized to the
                    # same bid_price/ask_price keys here so
                    # VolatilitySurfaceCalculator's filter works the same
                    # regardless of which enrichment path produced the
                    # instrument dict.
                    item_with_greeks["bid_price"] = ticker.get("best_bid_price")
                    item_with_greeks["ask_price"] = ticker.get("best_ask_price")
                    # Task G2-A (Wave G fresh audit, bug 1): gamma is NEVER
                    # read from ticker.greeks.gamma here (unlike delta/vega/
                    # theta above, which stay ticker-sourced) -- see
                    # _compute_bs_gamma's docstring for why. This is the one
                    # deliberate exception to this loop's "trust the
                    # ticker" convention.
                    item_with_greeks["gamma"] = self._compute_bs_gamma(
                        bs_calculator, item, item_with_greeks["mark_iv"],
                        item_with_greeks["underlying_price"], fixed_strike_vol_now_utc,
                    )
                    instruments_with_greeks.append(item_with_greeks)

                    if (i + 1) % 20 == 0:
                        progress_callback(
                            f"  Fetched {i + 1}/{len(instruments)} for {expiration}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to fetch Greeks for {item['instrument_name']}: {e}")
                    dropped_instrument_count += 1
                    dropped_instrument_oi += item.get("open_interest") or 0

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
                # Wave G re-review (Important #1): fold this expiration's
                # dropped-before-fetch instruments into the SAME
                # completeness fields _aggregate_by_strike already
                # populates for a null-greek-after-arrival instrument --
                # one combined signal, not two. GexDexResult is frozen, so
                # this composes via dataclasses.replace rather than the
                # calculator tracking something it structurally cannot
                # see (dropped instruments never reach it at all).
                if dropped_instrument_count > 0:
                    gex_result = dataclasses.replace(
                        gex_result,
                        instruments_missing_gamma=(
                            gex_result.instruments_missing_gamma + dropped_instrument_count
                        ),
                        oi_missing_gamma=gex_result.oi_missing_gamma + dropped_instrument_oi,
                    )
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

                # institutional_metrics_spec.md section 4 / task C5:
                # per-strike vanna/charm exposure profile (VEX/CEX), same
                # enriched-Greeks instruments_with_greeks list, no second
                # Greeks pass. Additive only (matches the dealer-positioning
                # call immediately above): a failure degrades to "no
                # exposure-profile section" (exposure_result is None), never
                # to a broken GEX/DEX section.
                progress_callback(f"Calculating vanna/charm exposure profile for {expiration}...")
                exposure_result = self._calculate_exposure_profile(
                    analyzer.currency, expiration, instruments_with_greeks, analyzer.index_price,
                )
                if exposure_result is not None and builder is not None:
                    builder.set_exposure_profile(expiration, exposure_result)

                # institutional_metrics_spec.md section 7 / task C8:
                # fixed-strike vol change matrix, same enriched-Greeks
                # instruments_with_greeks list ("today" is read live from
                # it), no second API call. Additive only (matches the
                # exposure-profile call immediately above): a failure
                # degrades to "no fixed-strike-vol section"
                # (fixed_strike_vol_result is None), never to a broken
                # GEX/DEX section. A present result with
                # regime == "INDETERMINATE" (insufficient/stale prior
                # history) is NOT a failure -- it is still set on the
                # builder and rendered with an explicit message.
                #
                # Independent review (Important #1): the anchor price MUST
                # be the same TYPE of price on both sides of the day-over-
                # day comparison. daily_oi_snapshots.underlying_price is
                # written elsewhere in this service
                # (_calculate_oi_changes_and_iv_percentile) as this expiry's
                # FORWARD price, not the spot index -- bugfix_spec.md Item
                # 7's settlement-space convention. Passing analyzer.
                # index_price (spot) for "today" against a forward-priced
                # "prior" would (a) print a spurious spot-move on a flat
                # day (the forward-spot basis grows with DTE) and (b) let
                # compute_nearest_strike_atm_iv pick a DIFFERENT nearest
                # strike on each side on a skewed smile, which can flip the
                # sticky-strike/sticky-delta/repriced regime label
                # entirely. Same fallback-to-index-price convention as
                # _calculate_oi_changes_and_iv_percentile when no forward
                # price is available for this expiry.
                #
                # Fix round 2 (Low #1): this resolution -- and the call
                # into _calculate_fixed_strike_vol_matrix -- is wrapped in
                # its OWN try/except, isolated from every pre-existing call
                # in this loop (gex_result, dealer_result, exposure_result
                # above): without it, an unexpected failure here (e.g. a
                # malformed analyzer stub) would propagate past this
                # expiry's remaining processing AND abort every subsequent
                # expiration in this per-expiry loop -- far wider than the
                # "additive, GEX/DEX-safe" contract this section's own
                # docstring/comments already advertise.
                fixed_strike_vol_result = None
                try:
                    fixed_strike_vol_forward_price = analyzer.forward_price_by_expiration.get(expiration)
                    # Fix round 2 (Low #3): recorded so the report can
                    # disclose which anchor was ACTUALLY used for "today"
                    # -- a logged warning alone is invisible to the report
                    # reader.
                    fixed_strike_vol_price_is_forward = fixed_strike_vol_forward_price is not None
                    if fixed_strike_vol_forward_price is None:
                        logger.warning(
                            f"No forward price for expiration {expiration} -- "
                            f"falling back to index price for the fixed-strike "
                            f"vol matrix anchor"
                        )
                        fixed_strike_vol_forward_price = analyzer.index_price

                    progress_callback(f"Calculating fixed-strike vol change matrix for {expiration}...")
                    fixed_strike_vol_result = self._calculate_fixed_strike_vol_matrix(
                        analyzer.currency, expiration, instruments_with_greeks,
                        fixed_strike_vol_forward_price, fixed_strike_vol_now_utc,
                        fixed_strike_vol_price_is_forward,
                    )
                except Exception:
                    logger.error(
                        f"OnChainAnalysisService: failed to resolve the "
                        f"fixed-strike vol matrix anchor price for "
                        f"{analyzer.currency} {expiration} -- degrading to "
                        "'no fixed-strike-vol section' rather than aborting "
                        "the remaining per-expiry loop (additive-only, "
                        "institutional_metrics_spec.md section 7 / task C8)",
                        exc_info=True,
                    )

                if fixed_strike_vol_result is not None and builder is not None:
                    builder.set_fixed_strike_vol(expiration, fixed_strike_vol_result)
            else:
                # Wave G re-review (Important #2): EVERY instrument in
                # this expiration failed its ticker fetch (100% drop) --
                # `instruments` above is guaranteed non-empty here (the
                # `if not instruments: continue` gate at the top of this
                # loop already handled "no instruments at all"), so this
                # branch is specifically "we tried, and nothing came
                # back", not "there was nothing to try". Leaving
                # bundle.gex_dex as None for this case (the old behavior)
                # let report_formatter.py fall through to its legacy
                # unconditional "OI/GEX from full book" claim -- the ONE
                # case where that claim is KNOWN false (0% represented),
                # not merely possibly incomplete. Building an explicit,
                # fully-degenerate GexDexResult (0 gamma/delta everywhere,
                # spot-only, no strikes) and stamping its completeness
                # fields to "100% missing" routes this through the exact
                # same disclosure machinery bug 2 already built --
                # instruments_missing_gamma > 0 unconditionally triggers
                # the DATA COMPLETENESS line, and the OI-weighted
                # percentage (100%, since there is zero represented OI to
                # divide by anything else) unconditionally fails the
                # EVIDENCE line's "full book" gate.
                logger.warning(
                    f"OnChainAnalysisService: all {len(instruments)} instruments "
                    f"for {analyzer.currency} {expiration} failed their ticker "
                    "fetch -- recording a fully-degenerate GEX/DEX result "
                    "(100% completeness gap) rather than leaving gex_dex as "
                    "None, so the report cannot claim 'full book' for this "
                    "expiration."
                )
                empty_gex_result = GexDexCalculator(
                    [], analyzer.index_price, currency=analyzer.currency,
                ).calculate()
                empty_gex_result = dataclasses.replace(
                    empty_gex_result,
                    instruments_missing_gamma=dropped_instrument_count,
                    oi_missing_gamma=dropped_instrument_oi,
                )
                gex_dex_typed_by_expiry[expiration] = empty_gex_result
                if builder is not None:
                    builder.set_gex_dex(expiration, empty_gex_result)

        # Aggregate GEX/DEX across all expirations after per-expiry loop
        aggregate_result = None
        if gex_dex_typed_by_expiry:
            progress_callback("Calculating market-wide aggregate GEX/DEX...")
            aggregate_result = GexDexCalculator.aggregate_across_expirations(
                gex_dex_typed_by_expiry, analyzer.index_price, analyzer.currency
            )

        # institutional_metrics_spec.md section 5 / Task C6: net GEX
        # per-expiry roll-off, from the SAME gex_dex_typed_by_expiry map
        # aggregate_across_expirations just consumed -- a separate,
        # independently-guarded call (own try/except inside
        # _build_gamma_rolloff), so a failure here can never suppress the
        # aggregate GEX/DEX result above or vice versa (task brief's
        # isolation constraint).
        progress_callback("Calculating gamma roll-off profile...")
        gamma_rolloff_result = self._build_gamma_rolloff(
            gex_dex_typed_by_expiry, datetime.now(timezone.utc),
        )

        return aggregate_result, gamma_rolloff_result

    def _build_gamma_rolloff(
        self,
        gex_dex_typed_by_expiry: Dict[str, Any],
        now_utc: datetime,
    ) -> Optional[GammaRolloffResult]:
        """
        Build the GAMMA ROLL-OFF result (institutional_metrics_spec.md
        section 5, Task C6) from the per-expiry ``GexDexResult.
        total_net_gex`` values already computed in the loop above.

        Additive only, mirrors ``_calculate_exposure_profile``'s and
        ``_calculate_inferred_dealer_positioning``'s established guard
        (task C5 review): any failure here degrades to ``None`` rather
        than propagating, so a bug in this presentation-only aggregation
        can never crash the GEX/DEX phase it runs alongside. ``None`` is
        also returned (no error) when there is no per-expiry GEX/DEX data
        at all -- mirrors ``aggregate_result``'s own ``if gex_dex_typed_
        by_expiry:`` gate immediately above.
        """
        if not gex_dex_typed_by_expiry:
            return None
        try:
            per_expiry_net_gex = {
                expiration: result.total_net_gex
                for expiration, result in gex_dex_typed_by_expiry.items()
            }
            profile = GexDexCalculator.calculate_rolloff_profile(per_expiry_net_gex, now_utc)
            rows = tuple(
                GammaRolloffRow(
                    expiration=row["expiration"],
                    dte_days=row["dte_days"],
                    net_gex=row["net_gex"],
                    share_pct=row["share_pct"],
                    cum_share_pct=row["cum_share_pct"],
                    cum_net_gex=row["cum_net_gex"],
                )
                for row in profile["rows"]
            )
            return GammaRolloffResult(
                rows=rows,
                gamma_cliff_7d=profile["gamma_cliff_7d"],
                cum_share_7d=profile["cum_share_7d"],
                cum_share_30d=profile["cum_share_30d"],
                gross_total=profile["gross_total"],
            )
        except Exception as exc:
            logger.error(f"Failed to build gamma roll-off profile: {exc}", exc_info=True)
            return None

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
                # Task Wave-H-E: preserve None here (not `item.get("gamma")
                # or 0.0`) -- this dict is what DealerInventoryCalculator.
                # calculate() checks for missing greeks (its own explicit
                # is-None guard, mirroring GexDexCalculator._aggregate_by_
                # strike's Task G2-A fix). Collapsing None to 0.0 at THIS
                # site, one layer up, would make the calculator's own
                # is-None check dead code -- it would never see a None
                # again, silently defeating the completeness disclosure
                # before it can even run.
                greeks_by_instrument[key] = {
                    "gamma": item.get("gamma"),
                    "delta": item.get("delta"),
                }
                oi_by_instrument[key] = item.get("open_interest") or 0.0

            calculator = DealerInventoryCalculator(
                flow_rows, greeks_by_instrument, spot_price, currency,
                oi_by_instrument=oi_by_instrument,
            )
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

            # Task Wave-H-E: a missing/non-positive spot_price makes every
            # strike's inferred_gex a fabricated 0.0 (the S^2 term cannot be
            # computed at all -- `calculate()` already refuses to pretend
            # otherwise, see spot_price_valid there). Folded into the SAME
            # D9 render_inferred gate as the other disclosed failure modes
            # rather than a silent None return: an explicit
            # "INFERRED DEALER VIEW UNAVAILABLE (...)" line is strictly more
            # honest than omitting the section outright, and the caller
            # already treats "dealer_result is None" and
            # "render_inferred=False" as equivalent degradations (see the
            # comment at this method's call site) -- this just picks the
            # more informative of the two equivalent outcomes.
            spot_price_valid = calc_dict["spot_price_valid"]

            render_inferred = (
                spot_price_valid
                and not no_trades_for_this_expiry
                and coverage >= _DEALER_INVENTORY_COVERAGE_GATE
                and violation_rate <= _DEALER_INVENTORY_VIOLATION_GATE
                and not insufficient_oi_reference
            )
            unavailable_reason = None
            if not render_inferred:
                if not spot_price_valid:
                    unavailable_reason = (
                        f"missing or non-positive spot price ({spot_price!r}) -- cannot "
                        "compute inferred GEX's S^2 term"
                    )
                elif no_trades_for_this_expiry:
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
                instruments_missing_gamma=calc_dict["instruments_missing_gamma"],
                oi_missing_gamma=calc_dict["oi_missing_gamma"],
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

    def _calculate_exposure_profile(
        self,
        currency: str,
        expiration: str,
        instruments_with_greeks: List[Dict[str, Any]],
        spot_price: float,
    ) -> Optional[ExposureProfileResult]:
        """
        institutional_metrics_spec.md section 4 (Task C5): per-strike
        vanna (VEX) / charm (CEX) exposure profile, holder-side raw +
        assumed-dealer view together (Decision D7, established Wave B/task
        B2 -- same convention as GexDexCalculator/DealerInventoryCalculator,
        no third convention invented here).

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "31JUL26").
            instruments_with_greeks: The SAME enriched-Greeks instrument list
                already built for GexDexCalculator/dealer positioning above
                -- no second Greeks pass, no second API call.
            spot_price: ``analyzer.index_price`` -- same anchor
                GexDexCalculator uses (bugfix_spec.md Item 7).

        Returns:
            ``ExposureProfileResult``, or ``None`` if computing it raised
            unexpectedly -- this is a purely additive computation (like
            ``_calculate_inferred_dealer_positioning``'s established guard)
            and must never abort the GEX/DEX pipeline it runs alongside.
        """
        try:
            # Report-time valuation instant -- naive-UTC to match
            # BlackScholesCalculator.parse_instrument_name's 08:00-UTC-naive
            # expiry convention (institutional_metrics_spec.md section 4(b)'s
            # known latent bug: naive-LOCAL vs naive-UTC would silently
            # misprice tau by 1-2 hours on this machine).
            valuation_time_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            calculator = ExposureProfileCalculator(
                instruments=instruments_with_greeks,
                spot_price=spot_price,
                valuation_time_utc=valuation_time_utc,
                currency=currency,
            )
            holder = calculator.calculate(side_convention="holder")
            dealer = calculator.calculate(side_convention="assumed_dealer")

            # Task C5 review fix (Important #2): skipped_instruments was
            # computed by the calculator but silently discarded here -- see
            # VolatilityReconstructionService._calculate_exposure_aggregates's
            # matching fix (the daemon persistence path) for the full
            # rationale (M5 defect class). Both calls skip the same
            # instruments; holder's count is used for the log.
            skipped = holder["skipped_instruments"]
            if skipped > 0:
                logger.warning(
                    f"Vanna/charm exposure profile for {currency} {expiration}: "
                    f"{skipped} instrument(s) skipped (missing/invalid strike, "
                    "option_type, mark_iv, or unparseable instrument name)"
                )

            # Both calls iterate the SAME instruments list -- side_convention
            # only changes the per-leg sign weight, never which instruments
            # are skipped or which strikes survive the zero-OI-both-legs
            # filter. strike_data KEYS and every non-vex/cex field (OI,
            # vanna, charm) are therefore identical between the two calls by
            # construction; only "vex"/"cex" differ.
            strike_rows = tuple(
                ExposureStrikeRow(
                    strike=strike,
                    call_oi=row["call_oi"],
                    put_oi=row["put_oi"],
                    call_vanna=row["call_vanna"],
                    put_vanna=row["put_vanna"],
                    call_charm=row["call_charm"],
                    put_charm=row["put_charm"],
                    vex_holder=row["vex"],
                    cex_holder=row["cex"],
                    vex_assumed_dealer=dealer["strike_data"][strike]["vex"],
                    cex_assumed_dealer=dealer["strike_data"][strike]["cex"],
                )
                for strike, row in sorted(holder["strike_data"].items())
            )

            return ExposureProfileResult(
                strike_rows=strike_rows,
                spot_price=spot_price,
                currency=currency,
                total_vex_holder=holder["total_vex"],
                total_cex_holder=holder["total_cex"],
                total_vex_assumed_dealer=dealer["total_vex"],
                total_cex_assumed_dealer=dealer["total_cex"],
                peak_vanna_strike=holder["peak_vanna_strike"],
                peak_charm_strike=holder["peak_charm_strike"],
                skipped_instruments=holder["skipped_instruments"],
            )
        except Exception:
            logger.error(
                f"OnChainAnalysisService: vanna/charm exposure profile "
                f"failed unexpectedly for {currency} {expiration} -- "
                "degrading to 'no exposure-profile section' rather than "
                "aborting GEX/DEX (additive-only, "
                "institutional_metrics_spec.md section 4 / task C5)",
                exc_info=True,
            )
            return None

    def _calculate_fixed_strike_vol_matrix(
        self,
        currency: str,
        expiration: str,
        instruments_with_greeks: List[Dict[str, Any]],
        spot_today: float,
        now_utc: datetime,
        spot_today_is_forward: bool = True,
    ) -> Optional[FixedStrikeVolResult]:
        """
        institutional_metrics_spec.md section 7 (Task C8): fixed-strike vol
        change matrix -- day-over-day IV change per strike vs the ATM move,
        with sticky-strike/sticky-delta/repriced attribution.

        "Today" is read LIVE from ``instruments_with_greeks`` (spec section
        7(a): "today can be computed live") -- the SAME enriched Greeks
        list already built for GEX/DEX/dealer-inventory/exposure-profile
        above, no second API call. "Prior" (exactly ``today_date_utc - 1
        day``, never the nearest available date) comes from
        ``DatabaseRepository.get_chain_iv_at``. ATM IV is computed
        identically on both sides via ``compute_nearest_strike_atm_iv``
        (neither historical source carries ``delta``, so the delta-
        interpolated ATM read used elsewhere is not available here -- see
        that function's docstring for why using the SAME method on both
        days matters).

        Additive only, mirrors ``_calculate_exposure_profile``'s/
        ``_calculate_inferred_dealer_positioning``'s established guard: an
        unexpected error (e.g. a DB failure) degrades to ``None`` -- no
        section rendered at all -- never aborting the GEX/DEX phase this
        runs alongside. This is DISTINCT from the calculator legitimately
        returning ``regime == "INDETERMINATE"`` (missing/stale prior data,
        the expected common case per section 11 judgment call #4) -- that
        is a normal, fully-formed result that DOES get rendered, with an
        explicit "insufficient history" message rather than a fabricated
        table (task-C8-brief.md's graceful-fallback requirement).

        Args:
            currency: Currency symbol (BTC or ETH).
            expiration: Expiration date string (e.g., "31JUL26").
            instruments_with_greeks: The SAME enriched-Greeks instrument
                list already built for GEX/DEX/dealer/exposure-profile
                above.
            spot_today: The SAME price anchor ``spot_prior`` (below) uses.
                Independent review (Important #1): this must be this
                expiry's FORWARD price (``analyzer.forward_price_by_
                expiration[expiration]``, falling back to
                ``analyzer.index_price`` -- resolved by the caller,
                ``_fetch_greeks_and_store_gex_dex``), NOT
                ``analyzer.index_price`` directly. ``daily_oi_snapshots.
                underlying_price`` (the source of ``spot_prior``) is
                written elsewhere in this service
                (``_calculate_oi_changes_and_iv_percentile``) as the
                per-expiry forward, per bugfix_spec.md Item 7's
                settlement-space convention -- mixing spot on one side
                against forward on the other prints a spurious "spot move"
                on a flat day (the forward-spot basis grows with DTE) and
                can make ``compute_nearest_strike_atm_iv`` pick a
                DIFFERENT nearest strike on each side on a skewed smile,
                which can flip the sticky-strike/sticky-delta/repriced
                regime label entirely.
            now_utc: Timezone-aware ``datetime`` for "now", resolved once
                by the caller from ``datetime.now(timezone.utc)`` -- never
                derived from this (non-UTC) local machine's naive clock,
                per this campaign's repeated day-boundary lesson (Tasks
                C4/C5/C7 fix rounds). ``today_date_utc = now_utc.date()``
                and the already-settled-expiry check (below) both derive
                from this single clock read.
            spot_today_is_forward: Fix round 2 (Low #3). Whether
                ``spot_today`` is the true per-expiry forward price
                (``True``) or the caller already fell back to the spot
                index because no forward price was available (``False``).
                Purely a display flag -- passed straight through to
                ``FixedStrikeVolResult.spot_today_is_forward`` so the
                report can disclose the fallback (previously only a
                logged warning, invisible to the report reader) rather
                than affecting any calculation here. Defaults to ``True``
                (the common case) so existing callers/tests that don't
                pass it keep their prior behavior.

        Returns:
            ``FixedStrikeVolResult``, or ``None`` when: there is no
            repository; ``expiration`` has already reached or passed its
            own 08:00 UTC settlement as of ``now_utc`` (spec 7(c): "Expiry
            gone (settled) between the two days -> skip the expiry" --
            a dead, cash-settled expiry has no IV smile left to compare,
            so it is skipped entirely rather than rendered as
            "insufficient history"); or building the matrix raised
            unexpectedly.
        """
        if self.repository is None:
            return None

        try:
            # Independent review (Minor #5 / spec 7(c)): an expiry at or
            # past its own settlement instant is gone -- skip it entirely,
            # before any repository call, rather than rendering an
            # "insufficient history" block for a dead contract. ``dte is
            # None`` (expiration string failed to parse) is treated the
            # same as "cannot confirm this expiry is still live" -- skip,
            # don't guess.
            #
            # Fix round 2 (Low #1): this check -- previously OUTSIDE this
            # try block -- is now inside it. An early ``return None`` from
            # within a ``try`` is ordinary control flow (does not trigger
            # ``except``); moving it in only closes the gap where an
            # unexpected exception from ``calculate_days_to_expiry``
            # (not just its documented ``None`` return) would have
            # propagated past this method's own "additive, never aborts
            # GEX/DEX" guarantee.
            dte = MarketWideCalculator.calculate_days_to_expiry(expiration, now_utc)
            if dte is None or dte <= 0:
                return None

            today_date_utc = now_utc.date()

            today_rows = [
                {
                    "strike": inst.get("strike"),
                    "option_type": inst.get("option_type"),
                    "mark_iv": inst.get("mark_iv"),
                }
                for inst in instruments_with_greeks
            ]
            atm_iv_today = compute_nearest_strike_atm_iv(today_rows, spot_today)

            prior_date = today_date_utc - timedelta(days=1)
            chain_prior = self.repository.get_chain_iv_at(currency, expiration, prior_date)
            prior_rows = chain_prior["rows"]
            spot_prior = chain_prior["underlying_price"]
            atm_iv_prior = compute_nearest_strike_atm_iv(prior_rows, spot_prior)

            if prior_rows:
                # Exact match for prior_date -- the real, reachable happy
                # path. stale_prior will be False.
                actual_prior_date = prior_date
            else:
                # Independent review (Important #3): T7.3's stale-prior
                # message must show the REAL most-recent-available date
                # (spec 7(c): "no comparable prior snapshot (last:
                # 2026-07-20)"), not silently degrade with no date at all
                # -- prior_date is always exactly yesterday by
                # construction, so passing it through unconditionally made
                # this diagnostic branch dead code in production (only
                # reachable from a synthetic unit test). Looked up here,
                # isolated in its OWN try/except (review's isolation
                # constraint) so a failure in this diagnostic-only lookup
                # degrades to "no date available" rather than suppressing
                # the main result this method already computed.
                try:
                    actual_prior_date = self.repository.get_latest_chain_iv_date(
                        currency, expiration, prior_date,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to look up latest available chain IV date "
                        "for %s %s: %s", currency, expiration, exc,
                    )
                    actual_prior_date = None

            calculator = FixedStrikeVolCalculator(
                today_rows=today_rows,
                prior_rows=prior_rows,
                spot_today=spot_today,
                spot_prior=spot_prior,
                atm_iv_today=atm_iv_today,
                atm_iv_prior=atm_iv_prior,
                today_date=today_date_utc,
                prior_date=actual_prior_date,
                expiration=expiration,
                spot_today_is_forward=spot_today_is_forward,
            )
            return calculator.calculate()
        except Exception:
            logger.error(
                f"OnChainAnalysisService: fixed-strike vol matrix failed "
                f"unexpectedly for {currency} {expiration} -- degrading to "
                "'no fixed-strike-vol section' rather than aborting GEX/DEX "
                "(additive-only, institutional_metrics_spec.md section 7 / "
                "task C8)",
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
                # Wave-I-C Fix 1: naive datetime.now() is host-local and
                # ambiguous during the DST fall-back hour (the repeated
                # local hour maps to two different UTC instants), which
                # silently produces the wrong 24h window twice a year.
                # Unlike prospective_collector.py's collect_hour (which
                # stores its UTC-valued naive result directly into a
                # `timestamp without time zone` column with no further
                # conversion), window_start/window_end here are ONLY ever
                # used to derive window_start_ms/window_end_ms via
                # .timestamp() below -- and .timestamp() on a NAIVE
                # datetime assumes host-LOCAL time. Stripping tzinfo after
                # attaching UTC (the collect_hour pattern) would make that
                # .timestamp() call reinterpret an already-UTC wall clock
                # as local, shifting the window by the host's UTC offset
                # on every single call, not just the rare DST-ambiguous
                # hour. Keeping the datetime timezone-AWARE through
                # .timestamp() is what makes the conversion unambiguous
                # and host-tz-independent (aware .timestamp() uses the
                # attached UTC offset directly, never local tz rules).
                window_end = datetime.now(timezone.utc)
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
        # Wave-I-C Fix 1: naive datetime.now() is host-local and ambiguous
        # during the DST fall-back hour -- see the twin site in
        # _calculate_buy_sell_flow above for the full explanation of why
        # this stays timezone-AWARE (not naive-UTC) through .timestamp().
        window_end = datetime.now(timezone.utc)
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
                continue

            # institutional_metrics_spec.md section 3 (Task C4): the
            # delta-interpolated RR25/BF25, computed alongside ATM IV from
            # the same calculator/instrument set -- zero additional work.
            # Task C4 review Important #2: this runs AFTER set_vol_surface
            # above (which already succeeded) and in its OWN try/except --
            # a failure here must never drop the vol-surface result that
            # was already stored, matching the daemon's own isolation
            # (ProspectiveCollector._calculate_and_save_skew). Stored the
            # same way _atm_ivs is (real cross-phase data, read later by
            # _build_skew_term_structure for the SKEW TERM STRUCTURE
            # report section).
            try:
                analyzer._skew_by_expiry[expiration] = calculator.calculate_risk_reversal_butterfly()
            except Exception as skew_exc:
                logger.warning(
                    f"Failed to calculate RR25/BF25 skew for {expiration}: {skew_exc}"
                )

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
        gamma_rolloff_result: Optional[GammaRolloffResult] = None,
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
            gamma_rolloff_result: Typed ``GammaRolloffResult``
                (institutional_metrics_spec.md section 5, Task C6) from
                ``_fetch_greeks_and_store_gex_dex`` (via
                ``_build_gamma_rolloff``) -- same "computed in a different
                phase, attached post-hoc" situation as
                ``aggregate_gex_dex_result``.
                ``MarketWideOrchestrator.run()`` has no access to the
                per-expiry ``total_net_gex`` map this needs, so it is
                attached here via ``dataclasses.replace`` after ``run()``
                returns, the same pattern ``skew_term_structure`` (below)
                already uses.
        """
        orchestrator = MarketWideOrchestrator(self.api)
        market_wide_result = orchestrator.run(
            analyzer, currency, progress_callback, aggregate_gex_dex_result,
        )

        # institutional_metrics_spec.md section 3 (Task C4): MarketWide
        # Orchestrator has no repository/DB access (it only holds self.api)
        # -- the SKEW TERM STRUCTURE section is DB-dependent (per-expiry
        # percentile history), so it is built here (which HAS
        # self.repository) and attached via dataclasses.replace, the same
        # "post-process the frozen result" pattern
        # _apply_pcr_percentile_classification already uses.
        skew_term_structure = self._build_skew_term_structure(analyzer, currency)
        if skew_term_structure is not None:
            market_wide_result = dataclasses.replace(
                market_wide_result, skew_term_structure=skew_term_structure,
            )

        # institutional_metrics_spec.md section 5 (Task C6): same post-hoc
        # attach pattern as skew_term_structure above -- gamma_rolloff_result
        # was computed in a different phase (_fetch_greeks_and_store_gex_dex)
        # that MarketWideOrchestrator.run() has no access to.
        if gamma_rolloff_result is not None:
            market_wide_result = dataclasses.replace(
                market_wide_result, gamma_rolloff=gamma_rolloff_result,
            )

        # institutional_metrics_spec.md section 8 (Task C9): same post-hoc
        # attach pattern as skew_term_structure/gamma_rolloff above, for
        # consistency -- unlike skew_term_structure, forward_vol has no
        # repository dependency (pure live-chain calculation), so it could
        # in principle live inside MarketWideOrchestrator.run() instead;
        # attaching it here anyway keeps every market-wide section that
        # depends on analyzer._skew_by_expiry built in the same place.
        forward_vol_result = self._build_forward_vol_curve(analyzer, currency)
        if forward_vol_result is not None:
            market_wide_result = dataclasses.replace(
                market_wide_result, forward_vol=forward_vol_result,
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
        iv_rank_observation_count = None
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
                    iv_rank_observation_count = len(close_values)

                    # Calculate IV percentile (% of daily closes below current)
                    values_below = sum(1 for v in close_values if v < dvol)
                    iv_percentile = (values_below / len(close_values)) * 100

                    # Calculate IV rank using true range (daily high/low) — matches Deribit website
                    # Deribit uses max(daily_high) and min(daily_low) for the 365d range
                    dvol_min = min(low_values)
                    dvol_max = max(high_values)
                    if dvol_max > dvol_min:
                        iv_rank = (dvol - dvol_min) / (dvol_max - dvol_min) * 100
                        progress_callback(
                            f"DVOL: {dvol:.2f}, IV Percentile: {iv_percentile:.1f}%, "
                            f"IV Rank: {iv_rank:.1f}% "
                            f"(based on {iv_rank_observation_count} days)"
                        )
                    else:
                        # Wave H Task H-F, Fix 4: a degenerate range (every
                        # daily high/low identical -- e.g. exactly one
                        # observation, or a genuinely flat series) has no
                        # real spread to rank against. 50.0 used to be
                        # fabricated here, rendering in the report
                        # indistinguishable from a real, computed median
                        # rank. None instead -- report_formatter already
                        # gates the "IV Rank" line on `is not None`, so this
                        # renders as "insufficient data" (line omitted)
                        # rather than a fake number.
                        iv_rank = None
                        progress_callback(
                            f"DVOL: {dvol:.2f}, IV Percentile: {iv_percentile:.1f}%, "
                            f"IV Rank: insufficient data (degenerate 365d range, "
                            f"{iv_rank_observation_count} days)"
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
            "iv_rank_observation_count": iv_rank_observation_count,
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
        institutional_metrics_spec.md section 9 (Task D2 independent review,
        Minor): trend arrows against a single prior snapshot were deleted
        from the report everywhere (removals table: "Trend arrows vs 1
        prior snapshot -> delete everywhere") -- nothing renders
        ``TrendSnapshot`` data anymore, so the per-expiration
        ``get_onchain_snapshot_history`` query this method used to run (one
        DB round trip per expiration -- 12 for a full BTC report) fetched
        data with no consumer. The query is removed; ``builder.set_trend``
        is still called (with ``None``) so the ``TrendSnapshot`` plumbing
        itself -- the dataclass, the builder method,
        ``ExpirationRenderInput.trend`` -- stays intact for a future
        consumer, exactly as before, just never populated from the DB.

        Requires repository (unchanged gate, kept for parity with the
        method's prior signature even though the body no longer queries
        it). Silently skipped when repository is None.

        Args:
            analyzer: OnChainMetricsCalculator with parsed data.
            progress_callback: Unused -- kept for signature parity with the
                caller; the misleading "Fetching trend data..." message
                this used to emit is deleted along with the query it
                described.
            builder: T6 dual-write target (typed aggregate result).
        """
        del progress_callback  # not used -- see docstring
        if self.repository is None:
            return

        for expiration in analyzer.get_expirations():
            if builder is not None:
                builder.set_trend(expiration, None)

    def _save_reports_per_expiration(
        self,
        result: OnChainAnalysisResult,
        currency: str,
        now_utc: datetime,
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
            now_utc: The report's own "now" reference (independent review
                round 2, Important #1), the SAME instant used for the main
                report render (``fetch_and_analyze`` computes this once and
                passes it to both call sites) -- threaded to
                ``render_expiration_from_result``'s CONTEXT rendering.
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Get project root (3 levels up from this file)
            project_root = Path(__file__).parent.parent.parent.parent

            formatter = OnChainReportFormatter()
            header = formatter.render_header_from_result(result)

            saved = 0
            for expiration in result.expiration_names():
                section_content = formatter.render_expiration_from_result(result, expiration, now_utc)
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
