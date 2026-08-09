"""
Market-wide metrics orchestration.

refactor_design_spec.md section T11 (M1): ``OnChainAnalysisService.
_calculate_market_wide_metrics`` used to be a 222-line method doing 8
distinct jobs (IV term structure, futures basis, realized volatility, VRP,
volatility cone, perpetual funding, block trades, cross-asset correlation)
in one body, each with its own inline try/except and API-fetch logic.
``MarketWideOrchestrator`` splits that into 8 named private phase methods
on one collaborator class -- ``run()`` composes them and returns the typed
``MarketWideResult``; the service's own method becomes a thin delegator
that calls ``run()`` and writes the result into the builder.

Behavior-preserving: every phase's fetch/calculate/gate logic is moved
verbatim, only the try/except boundaries are now per-phase instead of
sharing one try across term-structure/futures-basis/RV+VRP+cone (the
original code already had per-phase try/except for phases 2, 6, and 8;
phases 3/4/5 shared one try -- this split now gives them each their own,
which only changes behavior if one of those three phases raises, a case
the recorded golden-master fixture does not exercise).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from coding.core.analytics.market_wide_calculator import (
    MINIMUM_PRICE_HISTORY_DAYS_FOR_VOL_CONE,
    MarketWideCalculator,
)
from coding.core.analytics.on_chain_analyzer import OnChainMetricsCalculator
from coding.core.analytics.results.market_wide_results import (
    Block,
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
from coding.core.analytics.thresholds import (
    BLOCK_TRADE_ID_TRACKED_SINCE,
    BLOCK_TRADE_NOTIONAL_THRESHOLD_USD,
)
from coding.service.deribit.deribit_api_service import DeribitApiService

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


class MarketWideOrchestrator:
    """
    Collaborator that computes every market-wide (cross-expiration) metric
    for one currency and assembles the typed ``MarketWideResult``.

    Each phase is an independent, individually-testable private method
    (refactor_design_spec.md T11 / M1). ``run()`` is the only public entry
    point — it owns phase ordering and the few cross-phase data
    dependencies (the funding/term-structure/block-trades phases are fully
    independent; RV, VRP, volatility cone, and cross-asset correlation all
    consume the same fetched ``price_history``).
    """

    def __init__(self, api: DeribitApiService):
        self.api = api

    def run(
        self,
        analyzer: OnChainMetricsCalculator,
        currency: str,
        progress_callback,
        aggregate_gex_dex_result: Optional[Any] = None,
    ) -> MarketWideResult:
        """
        Run all 8 market-wide phases and assemble the ``MarketWideResult``.

        Args:
            analyzer: OnChainMetricsCalculator with per-expiration state
                already populated (``_atm_ivs``, ``_recent_trades``,
                ``market_metrics``, ``underlying_price``).
            currency: Currency symbol.
            progress_callback: Callback for progress updates.
            aggregate_gex_dex_result: Typed aggregate GexDexResult from a
                different phase (``_fetch_greeks_and_store_gex_dex``) but a
                field of this same result.

        Returns:
            The typed ``MarketWideResult``. Task G2-B Finding 3:
            ``failed_sections`` now genuinely reflects which phases raised
            during this call, instead of being hardcoded to ``()``
            unconditionally. Each of the 7 phases below that has its own
            try/except (every phase except term-structure, which has none
            and so cannot fail this way -- an unhandled exception there
            propagates out of ``run()`` entirely, a separate, larger
            failure mode not in scope here) appends its own section name
            to ``failed`` in its except block, right where the exception
            is already being caught and logged -- this never shares a
            try/except with a pre-existing call whose failure it could
            suppress, it only records a failure that block was already
            about to swallow.
        """
        failed: List[str] = []

        dvol = analyzer.market_metrics.get("dvol")
        # bugfix_spec.md Item 7 anchor table: these are all cross-expiration,
        # market-wide metrics (RV/VRP/vol cone/correlation/block-trade
        # notional) -- index-anchored, not any one expiry's future.
        calc = MarketWideCalculator(
            currency=currency, spot_price=analyzer.index_price, dvol=dvol,
        )

        term_structure_result = self._calculate_term_structure(analyzer, calc, progress_callback)
        basis_result = self._calculate_futures_basis(currency, analyzer, calc, progress_callback, failed)

        price_history = self._fetch_price_history(currency, progress_callback, failed)
        realized_volatility_result, rv_values = self._calculate_realized_volatility(calc, price_history, failed)
        vrp_result = self._calculate_vrp(calc, dvol, rv_values, failed)
        volatility_cone_result = self._calculate_volatility_cone(calc, price_history, failed)

        funding_result = self._calculate_perpetual_funding(currency, calc, progress_callback, failed)
        block_trades_result = self._calculate_block_trades(analyzer, calc, progress_callback, failed)
        correlation_result = self._calculate_cross_asset_correlation(
            currency, calc, price_history, progress_callback, failed
        )

        return MarketWideResult(
            spot_price=analyzer.index_price,
            currency=currency,
            dvol=dvol,
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
            failed_sections=tuple(failed),
        )

    # -- Phase 1: IV term structure ---------------------------------------

    def _calculate_term_structure(
        self, analyzer: OnChainMetricsCalculator, calc: MarketWideCalculator,
        progress_callback,
    ) -> Optional[TermStructureResult]:
        """Per-expiry ATM IVs collected during the vol-surface phase."""
        atm_ivs = analyzer._atm_ivs
        if not atm_ivs:
            return None

        # task A7 review: this progress message was dropped during the T11
        # split (the other 5 phases' messages survived the move) -- restored.
        progress_callback("Calculating IV term structure...")

        # Text return value is unused -- render_market_wide_from_result
        # renders this section from the typed result now.
        _, term_struct = calc.calculate_iv_term_structure(atm_ivs)

        now = datetime.now(timezone.utc)
        entries = []
        for exp, iv in sorted(atm_ivs.items()):
            dte = MarketWideCalculator._calculate_dte(exp, now)
            if dte is not None:
                entries.append(TermStructureEntry(expiration=exp, dte=dte, atm_iv=iv))
        entries.sort(key=lambda e: e.dte)

        return TermStructureResult(
            entries=tuple(entries),
            shape=term_struct.get("shape", "FLAT"),
            spread=term_struct.get("spread", 0.0),
            spread_signed=term_struct.get("spread_signed", term_struct.get("spread", 0.0)),
            iv_by_dte=dict(term_struct.get("iv_by_dte", {})),
        )

    # -- Phase 2: futures basis --------------------------------------------

    def _calculate_futures_basis(
        self, currency: str, analyzer: OnChainMetricsCalculator, calc: MarketWideCalculator,
        progress_callback, failed: List[str],
    ):
        """Fetch dated futures and compute annualized basis."""
        try:
            progress_callback("Fetching futures for basis calculation...")
            futures_instruments = self.api.get_instruments(
                currency=currency, kind="future", expired=False
            )

            futures_data = []
            for fut in futures_instruments:
                name = fut.get("instrument_name", "")
                if "PERPETUAL" in name:
                    continue
                try:
                    ticker = self.api.get_ticker(name)
                    futures_data.append({
                        "instrument_name": name,
                        "mark_price": ticker.get("mark_price", 0),
                        # independent review round 4 sweep: index_price is
                        # nullable per deribit_schemas.py's TICKER schema
                        # -- .get(key, default) only applies the fallback
                        # when the key is ABSENT. Currently benign only
                        # because calculate_futures_basis's own extraction
                        # (Important #5) already applies `or self.spot_price`
                        # downstream -- fixed here too for consistency/
                        # defense-in-depth, not because it's exploitable
                        # today.
                        "index_price": ticker.get("index_price") or analyzer.index_price,
                    })
                except Exception as e:
                    logger.warning(f"Failed to fetch future ticker {name}: {e}")

            if futures_data:
                # calculate_futures_basis returns the typed FuturesBasisResult;
                # market_wide_formatter's format_futures_basis_section renders
                # it directly from render_market_wide_from_result.
                return calc.calculate_futures_basis(futures_data)
            return None

        except Exception as e:
            logger.warning(f"Failed to calculate futures basis: {e}")
            failed.append("futures_basis")
            return None

    # -- Shared fetch for phases 3/4/5/8: daily price history --------------

    def _fetch_price_history(
        self, currency: str, progress_callback, failed: List[str],
    ) -> List[Dict[str, Any]]:
        """180 days of daily close prices for this currency's perpetual --
        feeds realized volatility, VRP, volatility cone, and (sliced to the
        last 35 days) cross-asset correlation."""
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

            price_history: List[Dict[str, Any]] = []
            if chart_data and "ticks" in chart_data:
                timestamps = chart_data["ticks"]
                closes = chart_data.get("close", [])
                for i, ts in enumerate(timestamps):
                    if i < len(closes):
                        price_history.append({"timestamp": ts / 1000, "close": closes[i]})
            return price_history

        except Exception as e:
            logger.warning(f"Failed to fetch price history for RV/VRP/Vol Cone: {e}")
            failed.append("price_history")
            return []

    # -- Phase 3: realized volatility (multi-window) ------------------------

    def _calculate_realized_volatility(
        self, calc: MarketWideCalculator, price_history, failed: List[str],
    ):
        """Returns (RealizedVolatilityResult | None, rv_values dict) — the
        dict is threaded into the VRP phase for its rv_30d input.

        Task G2-C: resolves its own ``now_utc`` here (this module is in
        tests/conftest.py's frozen-clock list), same per-phase-local pattern
        ``_calculate_term_structure`` above already uses -- rather than
        letting ``calculate_realized_volatility_multi_window`` default to a
        naive ``datetime.now()`` internally (the confirmed non-determinism
        bug: the RV window boundary used to depend on the calling machine's
        local timezone and wall-clock hour)."""
        if not price_history:
            return None, {}
        try:
            now_utc = datetime.now(timezone.utc)
            _, rv_values = calc.calculate_realized_volatility_multi_window(price_history, now_utc)
            result = RealizedVolatilityResult(rv_by_window=dict(rv_values)) if rv_values else None
            return result, rv_values
        except Exception as e:
            logger.warning(f"Failed to calculate realized volatility: {e}")
            failed.append("realized_volatility")
            return None, {}

    # -- Phase 4: VRP ---------------------------------------------------------

    def _calculate_vrp(
        self, calc: MarketWideCalculator, dvol: Optional[float], rv_values: Dict[int, float],
        failed: List[str],
    ) -> Optional[VarianceRiskPremiumResult]:
        """
        dvol is Optional on VarianceRiskPremiumResult and the calculator's
        own text branch already renders "DVOL not available" when dvol is
        None -- gating construction on dvol being available too would drop
        the section entirely on a dvol-unavailable-but-rv_30d>0 run (A6
        carried-finding-adjacent bug, fixed pre-T11). Always construct when
        rv_30d > 0; dvol=None flows straight through.
        """
        rv_30d = rv_values.get(30, 0)
        if rv_30d <= 0:
            return None
        try:
            _, vrp_data = calc.calculate_vrp(rv_30d)
            return VarianceRiskPremiumResult(
                vrp=vrp_data["vrp"], signal=vrp_data["signal"], dvol=dvol, rv_30d=rv_30d,
            )
        except Exception as e:
            logger.warning(f"Failed to calculate VRP: {e}")
            failed.append("variance_risk_premium")
            return None

    # -- Phase 5: volatility cone -----------------------------------------

    def _calculate_volatility_cone(
        self, calc: MarketWideCalculator, price_history, failed: List[str],
    ) -> Optional[VolatilityConeResult]:
        """
        Mirrors calculate_volatility_cone's own "Insufficient price history"
        threshold (the shared constant, not a re-declared literal) so the
        typed result is None exactly when the calculator's own text says
        "Insufficient" -- constructing unconditionally whenever
        price_history was truthy produced a fake all-zero-percentile result
        on the calculator's own insufficient-data path.
        """
        if len(price_history) < MINIMUM_PRICE_HISTORY_DAYS_FOR_VOL_CONE:
            return None
        try:
            _, cone_data = calc.calculate_volatility_cone(price_history)
            # Also carries the full per-window row (current RV, 25th/median/
            # 75th, percentile) the legacy 6-column table shows -- percentile
            # alone can only reproduce a 2-column table.
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
            return VolatilityConeResult(
                percentile_by_window={
                    10: cone_data.get("cone_10d_pctile", 0.0),
                    20: cone_data.get("cone_20d_pctile", 0.0),
                    30: cone_data.get("cone_30d_pctile", 0.0),
                },
                stats_by_window=stats_by_window,
            )
        except Exception as e:
            logger.warning(f"Failed to calculate volatility cone: {e}")
            failed.append("volatility_cone")
            return None

    # -- Phase 6: perpetual funding trend -----------------------------------

    def _calculate_perpetual_funding(
        self, currency: str, calc: MarketWideCalculator, progress_callback, failed: List[str],
    ) -> Optional[PerpetualFundingResult]:
        """
        Reads the ticker's raw Optional funding values directly and gates
        construction on their presence, so an unavailable reading produces
        ``funding_8h=None`` instead of the calculator's own dict pre-seed of
        0.0 (which the typed result must not inherit -- A5-review finding).
        """
        try:
            progress_callback("Fetching perpetual funding trend...")
            funding_data = self.api.get_funding_chart_data(
                instrument_name=f"{currency}-PERPETUAL", length="1m",
            )
            # bugfix_spec.md Item 4 (F4.3): "1m" is expected to return hourly
            # points; assert that resolution rather than assuming it.
            _warn_if_funding_resolution_unexpected(funding_data)
            perp_ticker = self.api.get_ticker(f"{currency}-PERPETUAL")

            _, funding_data_struct = calc.calculate_perpetual_funding_trend(
                funding_data, perp_ticker
            )

            ticker_funding_8h = perp_ticker.get("funding_8h")
            ticker_current_funding = perp_ticker.get("current_funding")
            if ticker_funding_8h is None and ticker_current_funding is None:
                return None

            return PerpetualFundingResult(
                perp_open_interest=funding_data_struct.get("perp_oi", 0.0),
                funding_rate=ticker_current_funding,
                funding_8h=ticker_funding_8h,
                funding_trend=funding_data_struct.get("perp_funding_trend", "Stable"),
                history_points=len(calc._extract_funding_rates(funding_data)),
            )

        except Exception as e:
            logger.warning(f"Failed to calculate perpetual funding trend: {e}")
            failed.append("perpetual_funding")
            return None

    # -- Phase 7: block trades -----------------------------------------------

    def _calculate_block_trades(
        self, analyzer: OnChainMetricsCalculator, calc: MarketWideCalculator, progress_callback,
        failed: List[str],
    ) -> Optional[BlockTradesResult]:
        """
        Reuses trade data already fetched during the VWAP IV phase.

        Independent review round 3 (Important #5): this phase previously
        had no try/except -- unlike _calculate_perpetual_funding (Phase
        6), a single malformed trade (e.g. a null index_price, the exact
        shape Important #5 also fixed in the calculator) could raise and
        abort the ENTIRE market-wide phase, not just this section. Isolated
        per bugfix_spec.md/refactor_design_spec.md's per-phase try/except
        convention (see module docstring) -- this except is scoped to only
        this phase's own calculator call, never a pre-existing call whose
        failure it could suppress.
        """
        recent_trades = analyzer._recent_trades
        if not recent_trades:
            return None

        try:
            progress_callback("Detecting block trades...")
            _, block_data = calc.detect_block_trades(recent_trades)
            # institutional_metrics_spec.md section 9 / Migration M2 (Task
            # D1): "large_prints" already excludes any trade with a
            # block_trade_id (see MarketWideCalculator.detect_block_trades)
            # -- no double counting between this tuple and `blocks` below.
            large_prints_tuple = tuple(
                BlockTrade(
                    timestamp=bt.get("timestamp"),
                    instrument_name=bt.get("instrument", ""),
                    amount=bt.get("amount", 0.0),
                    direction=bt.get("direction", ""),
                    notional=bt.get("notional", 0.0),
                    implied_volatility=bt.get("iv"),
                )
                for bt in block_data.get("large_prints", [])
            )
            blocks_tuple = tuple(
                Block(
                    block_trade_id=b["block_trade_id"],
                    leg_count=b["leg_count"],
                    observed_leg_count=b["observed_leg_count"],
                    combo_id=b.get("combo_id"),
                    combined_premium_usd=b.get("combined_premium_usd", 0.0),
                    total_amount=b.get("total_amount", 0.0),
                    instruments=b.get("instruments", ()),
                    timestamp=b.get("timestamp"),
                )
                for b in block_data.get("blocks", [])
            )
            # notional_threshold matches detect_block_trades' own default;
            # total_detected approximates the (already top-10-truncated)
            # displayed count -- the calculator does not expose the
            # pre-truncation total externally.
            return BlockTradesResult(
                trades=large_prints_tuple,
                notional_threshold=BLOCK_TRADE_NOTIONAL_THRESHOLD_USD,
                total_detected=len(large_prints_tuple),
                blocks=blocks_tuple,
                tracked_since=BLOCK_TRADE_ID_TRACKED_SINCE,
            )
        except Exception as e:
            logger.warning(f"Failed to calculate block trades: {e}")
            failed.append("block_trades")
            return None

    # -- Phase 8: cross-asset correlation ------------------------------------

    def _calculate_cross_asset_correlation(
        self, currency: str, calc: MarketWideCalculator, price_history, progress_callback,
        failed: List[str],
    ) -> Optional[CrossAssetCorrelationResult]:
        """Price + DVOL correlation vs. the other major currency."""
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
                        other_prices.append({"timestamp": ts / 1000, "close": closes[i]})

            # Own prices (reuse from the shared price-history fetch, last 35 days)
            own_prices_30d = price_history[-35:] if price_history else []

            own_dvol_history: List[float] = []
            other_dvol_history: List[float] = []
            try:
                for ccy, target_list in [
                    (currency, own_dvol_history),
                    (other_currency, other_dvol_history),
                ]:
                    dvol_data = self.api.get_volatility_index_data(
                        currency=ccy, resolution=86400,
                        start_timestamp=start_ts, end_timestamp=end_ts,
                    )
                    if dvol_data and "data" in dvol_data:
                        for point in dvol_data["data"]:
                            if len(point) > 4:
                                target_list.append(point[4])
            except Exception as e:
                logger.warning(f"Failed to fetch DVOL for correlation: {e}")

            _, corr_data = calc.calculate_cross_asset_correlation(
                own_prices=own_prices_30d,
                other_prices=other_prices,
                own_dvol_history=own_dvol_history,
                other_dvol_history=other_dvol_history,
                other_currency=other_currency,
            )
            # calculate_cross_asset_correlation pre-seeds both correlation
            # keys at None (not 0.0), so .get() here correctly yields None
            # on insufficient data instead of a fabricated zero.
            return CrossAssetCorrelationResult(
                other_currency=other_currency,
                price_correlation=corr_data.get("btc_eth_price_corr"),
                dvol_correlation=corr_data.get("btc_eth_dvol_corr"),
                sample_size=min(len(own_prices_30d), len(other_prices)),
                dvol_correlation_observations=corr_data.get("btc_eth_dvol_corr_n"),
            )

        except Exception as e:
            logger.warning(f"Failed to calculate cross-asset correlation: {e}")
            failed.append("cross_asset_correlation")
            return None
