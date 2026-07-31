"""
Prospective Data Collection Service

Collects hourly market data for ML training:
- Recent trades (with IV)
- Book summary (with OI)
- Hourly aggregated snapshots via HourlyAggregationService
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.on_chain_analyzer import OnChainMetricsCalculator
from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator
from coding.core.config import SUPPORTED_CURRENCIES
from coding.core.database.repository import DatabaseRepository
from coding.service.data_collection.hourly_aggregation_service import HourlyAggregationService
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.forward_testing_harness import ForwardTestingHarness
from coding.service.on_chain.volatility_reconstruction_service import VolatilityReconstructionService
from coding.service.scanner.butterfly_scan_service import ButterflyScanService
from coding.service.scanner.defined_risk_alert_rules import DefinedRiskAlertRule, format_defined_risk_alert
from coding.service.scanner.defined_risk_forward_test_harness import DefinedRiskForwardTestHarness
from coding.service.scanner.iron_condor_scan_service import IronCondorScanService
from coding.service.scanner.regime_gate_service import RegimeGateService
from coding.service.scanner.straddle_alert_rules import StraddleAlertRule
from coding.service.scanner.straddle_forward_test_harness import StraddleForwardTestHarness
from coding.service.scanner.straddle_scan_service import StraddleScanService
from coding.service.scanner.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)


class ProspectiveCollector:
    """
    Collects prospective market data for ML training.

    Responsibilities:
    - Fetch recent trades
    - Fetch book summary (current state)
    - Store in database
    - Delegate hourly aggregation to HourlyAggregationService
    - Handle errors gracefully
    """

    def __init__(
        self,
        api_service: Optional[DeribitApiService] = None,
        repository: Optional[DatabaseRepository] = None
    ):
        """
        Initialize collector.

        Args:
            api_service: Deribit API service (creates new if None)
            repository: Database repository (creates new if None)
        """
        self.api = api_service or DeribitApiService()
        self.repo = repository or DatabaseRepository()
        self.aggregation_service = HourlyAggregationService(repository=self.repo)
        self._forward_harness = ForwardTestingHarness(repository=self.repo)
        self._volatility_reconstruction = VolatilityReconstructionService(repository=self.repo)
        # Straddle scanner (increment 2): reuses the same live api connection
        # and repository as the rest of this collector (THE ONE DATA SOURCE
        # RULE — see straddle_scan_service.py module docstring).
        self._straddle_scan_service = StraddleScanService(api_service=self.api, repository=self.repo)
        self._straddle_harness = StraddleForwardTestHarness(repository=self.repo)
        self._straddle_alert_rule = StraddleAlertRule(repository=self.repo)
        self._straddle_telegram = TelegramAlertService()
        # Defined-risk scanners (iron condor + long butterfly): same THE ONE
        # DATA SOURCE RULE as the straddle scanner above, sharing this
        # collector's live api connection and repository.
        self._regime_gate_service = RegimeGateService(repository=self.repo)
        self._iron_condor_scan_service = IronCondorScanService(
            api_service=self.api, repository=self.repo, regime_gate_service=self._regime_gate_service)
        self._butterfly_scan_service = ButterflyScanService(
            api_service=self.api, repository=self.repo, regime_gate_service=self._regime_gate_service)
        self._defined_risk_harness = DefinedRiskForwardTestHarness(repository=self.repo)
        self._iron_condor_alert_rule = DefinedRiskAlertRule("iron_condor", repository=self.repo)
        self._butterfly_alert_rule = DefinedRiskAlertRule("butterfly", repository=self.repo)
        self._defined_risk_telegram = TelegramAlertService()

        logger.info("ProspectiveCollector initialized")

    def collect_hour(
        self,
        currencies: List[str] = None,
        hour: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Collect data for a specific hour.

        After collecting trades and book summaries, aggregates ALL
        unaggregated hours (not just the current one) to fill gaps.

        Args:
            currencies: List of currencies to collect (default: ['BTC', 'ETH'])
            hour: Hour to collect (default: current hour)

        Returns:
            Collection result with status and counts
        """
        currencies = currencies or SUPPORTED_CURRENCIES
        hour = hour or datetime.now().replace(minute=0, second=0, microsecond=0)

        logger.info("=" * 60)
        logger.info(f"Starting collection for hour: {hour}")
        logger.info(f"Currencies: {currencies}")
        logger.info("=" * 60)

        start_time = time.time()
        trades_collected = 0
        instruments_collected = 0
        errors = []
        details = {}

        # Collect for each currency
        for currency in currencies:
            logger.info(f"\nCollecting {currency} data...")

            try:
                result = self._collect_currency(currency, hour)
                trades_collected += result.get("trades", 0)
                instruments_collected += result.get("instruments", 0)
                details[currency] = result

                logger.info(f"  {currency} collection complete:")
                logger.info(f"   Trades: {result.get('trades', 0)}")
                logger.info(f"   Instruments: {result.get('instruments', 0)}")

            except Exception as e:
                logger.error(f"  Error collecting {currency}: {e}")
                errors.append(f"{currency}: {str(e)}")
                details[currency] = {"error": str(e)}

        # Calculate duration
        duration = time.time() - start_time

        # Determine status
        if not errors:
            status = "success"
        elif trades_collected > 0:
            status = "partial"
        else:
            status = "failed"

        result = {
            "status": status,
            "trades_collected": trades_collected,
            "instruments_collected": instruments_collected,
            "duration_seconds": round(duration, 2),
            "errors": errors,
            "details": details
        }

        logger.info(f"\n{'='*60}")
        logger.info(f"Collection complete: {status}")
        logger.info(f"  Total trades: {trades_collected}")
        logger.info(f"  Total instruments: {instruments_collected}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info("=" * 60)

        # Run hourly aggregation for ALL unaggregated hours
        if status in ["success", "partial"] and trades_collected > 0:
            logger.info(f"\nRunning hourly aggregation (all unaggregated hours)...")
            total_snapshots = 0
            try:
                for currency in currencies:
                    agg_result = self.aggregation_service.aggregate_unaggregated_hours(currency)
                    total_snapshots += agg_result.get("snapshots_created", 0)

                result["aggregation"] = {"snapshots_created": total_snapshots}
                logger.info(f"  Aggregation complete: {total_snapshots} snapshots created")
            except Exception as e:
                logger.error(f"  Aggregation failed: {e}")
                result["aggregation"] = {"error": str(e)}

            # Reconstruct volatility-surface/VRP/percentile metrics for this hour so
            # onchain_volatility_snapshots stays current going forward (previously
            # only populated by the one-off scripts/backfill_volatility_reconstruction.py).
            # Depends on hourly_snapshots for this hour, hence runs after aggregation above.
            # Wrapped per-currency so one failure never breaks collection or another currency.
            logger.info(f"\nRunning volatility reconstruction for hour {hour}...")
            reconstruction_summary = {}
            for currency in currencies:
                try:
                    recon_result = self._volatility_reconstruction.reconstruct_range(
                        currency=currency,
                        start=hour,
                        end=hour,
                    )
                    reconstruction_summary[currency] = recon_result
                    logger.info(f"  {currency} volatility reconstruction: {recon_result}")
                except Exception as e:
                    logger.warning(f"  Volatility reconstruction failed for {currency}: {e}")
                    reconstruction_summary[currency] = {"error": str(e)}
            result["volatility_reconstruction"] = reconstruction_summary

            # Straddle scanner (increment 2): rank expiries, record scan
            # history for forward-testing, resolve settled expiries, and
            # send a rate-limited Telegram alert when the rule fires.
            # Wrapped in an outer try/except AND a per-currency try/except
            # (same isolation pattern as ForwardTestingHarness/
            # VolatilityReconstructionService above) so a scanner failure
            # -- at any level -- can never break the collection cycle.
            logger.info(f"\nRunning straddle scanner for hour {hour}...")
            scanner_summary = {}
            try:
                for currency in currencies:
                    try:
                        scan_result = self._straddle_scan_service.scan(currency)
                        inserted = self._straddle_harness.record_scan(scan_result, scan_time=hour)
                        self._straddle_harness.resolve_due(currency)

                        should_send, top_entry, reason = self._straddle_alert_rule.should_alert(scan_result)
                        alert_sent = False
                        if should_send:
                            message = self._straddle_scan_service.format_alert(scan_result)
                            alert_sent = self._straddle_telegram.send(message)
                            if alert_sent:
                                self.repo.mark_straddle_scan_alert_sent(
                                    currency=currency,
                                    expiration=top_entry["expiry"],
                                    scan_time=hour,
                                )

                        scanner_summary[currency] = {
                            "inserted": inserted, "alert_sent": alert_sent, "reason": reason,
                        }
                        logger.info(f"  {currency} straddle scan: {scanner_summary[currency]}")
                    except Exception as e:
                        logger.warning(f"  Straddle scanner failed for {currency}: {e}")
                        scanner_summary[currency] = {"error": str(e)}
            except Exception as e:
                logger.warning(f"  Straddle scanner block failed: {e}")
            result["straddle_scanner"] = scanner_summary

            self._run_defined_risk_scanners(currencies, hour, result)

        return result

    def _run_defined_risk_scanners(self, currencies, hour, result: dict) -> None:
        """
        Iron condor + long butterfly scanners (defined-risk complement to
        the straddle scanner). Every candidate is recorded regardless of
        gate_pass -- the gate only affects the alert's header label (see
        docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md).
        Same per-currency try/except isolation as the straddle scanner and
        ForwardTestingHarness above: a failure here -- including a regime
        gate compute() failure -- can never break the collection cycle or
        skip a later currency in the same cycle. The regime is computed
        exactly once per currency and shared across both structure-type
        scans below.
        """
        logger.info(f"\nRunning defined-risk scanners for hour {hour}...")
        scanner_summary: dict = {}
        try:
            for currency in currencies:
                scanner_summary[currency] = {}

                try:
                    regime = self._regime_gate_service.compute(currency)
                except Exception as e:
                    logger.warning(f"  Regime gate compute failed for {currency}: {e}")
                    scanner_summary[currency]["regime_gate"] = {"error": str(e)}
                    continue

                for structure_type, scan_service, alert_rule in (
                    ("iron_condor", self._iron_condor_scan_service, self._iron_condor_alert_rule),
                    ("butterfly", self._butterfly_scan_service, self._butterfly_alert_rule),
                ):
                    try:
                        scan_result = scan_service.scan(currency, regime=regime)
                        inserted = self._defined_risk_harness.record_scan(scan_result, structure_type, scan_time=hour)
                        self._defined_risk_harness.resolve_due(currency, structure_type)

                        should_send, top_entry, reason = alert_rule.should_alert(scan_result)
                        alert_sent = False
                        if should_send:
                            message = format_defined_risk_alert(scan_result, structure_type)
                            alert_sent = self._defined_risk_telegram.send(message)
                            if alert_sent:
                                self.repo.mark_defined_risk_scan_alert_sent(
                                    currency=currency, expiration=top_entry["expiry"],
                                    structure_type=structure_type, scan_time=hour,
                                )

                        scanner_summary[currency][structure_type] = {
                            "inserted": inserted, "alert_sent": alert_sent, "reason": reason,
                        }
                        logger.info(f"  {currency} {structure_type} scan: {scanner_summary[currency][structure_type]}")
                    except Exception as e:
                        logger.warning(f"  {structure_type} scanner failed for {currency}: {e}")
                        scanner_summary[currency][structure_type] = {"error": str(e)}
        except Exception as e:
            logger.warning(f"  Defined-risk scanner block failed: {e}")
        result["defined_risk_scanner"] = scanner_summary

    def _collect_currency(
        self,
        currency: str,
        hour: datetime
    ) -> Dict[str, Any]:
        """
        Collect data for a single currency.

        Args:
            currency: Currency to collect (BTC, ETH)
            hour: Hour bucket

        Returns:
            Collection counts
        """
        trades = 0
        instruments = 0

        # 1. Fetch recent trades
        logger.info(f"  Fetching recent {currency} trades...")
        try:
            trade_result = self._fetch_trades(currency, hour)
            trades = trade_result.get("count", 0)
        except Exception as e:
            logger.error(f"    Error fetching trades: {e}")

        # 2. Fetch book summary and store snapshots
        logger.info(f"  Fetching {currency} book summary (with OI)...")
        book_result = None
        try:
            book_result = self._fetch_book_summary(currency, hour)
            instruments = book_result.get("count", 0)
        except Exception as e:
            logger.error(f"    Error fetching book summary: {e}")

        # 3. Run on-chain analysis and store results
        if book_result and book_result.get("instruments"):
            logger.info(f"  Running on-chain analysis for {currency}...")
            try:
                self._run_onchain_analysis(currency, hour, book_result.get("instruments"))
            except Exception as e:
                logger.error(f"    Error in on-chain analysis: {e}", exc_info=True)

        # 4. Fetch and store DVOL data
        logger.info(f"  Fetching {currency} DVOL data...")
        try:
            self._fetch_dvol(currency)
        except Exception as e:
            logger.error(f"    Error fetching DVOL: {e}")

        # 5. Fetch and store funding rate data
        logger.info(f"  Fetching {currency} funding rate...")
        try:
            self._fetch_funding_rate(currency)
        except Exception as e:
            logger.error(f"    Error fetching funding rate: {e}")

        # 6. Fetch and store latest OHLCV daily candle
        logger.info(f"  Fetching {currency} OHLCV daily candle...")
        try:
            self._fetch_ohlcv(currency)
        except Exception as e:
            logger.error(f"    Error fetching OHLCV: {e}")

        return {
            "trades": trades,
            "instruments": instruments
        }

    def _fetch_trades(
        self,
        currency: str,
        hour: datetime
    ) -> Dict[str, Any]:
        """
        Fetch recent trades for currency.

        Args:
            currency: Currency symbol
            hour: Hour to filter trades

        Returns:
            Trade fetch result
        """
        try:
            # Fetch recent trades using API service
            response = self.api.get_last_trades_by_currency(
                currency=currency,
                kind="option",
                count=1000
            )

            # Extract trades from response
            if isinstance(response, dict):
                trades = response.get("trades", [])
            elif isinstance(response, list):
                trades = response
            else:
                trades = []

            # Filter trades to last hour
            hour_start = int(hour.timestamp() * 1000)
            hour_end = int((hour + timedelta(hours=1)).timestamp() * 1000)

            hour_trades = [
                t for t in trades
                if hour_start <= t.get("timestamp", 0) < hour_end
            ]

            logger.info(f"    Found {len(hour_trades)} trades in hour {hour}")

            # Store trades in database
            stored_count = 0
            for trade in hour_trades:
                try:
                    self._store_trade(trade, currency, hour)
                    stored_count += 1
                except Exception as e:
                    logger.warning(f"    Failed to store trade {trade.get('trade_id')}: {e}")

            return {
                "count": stored_count,
                "total_fetched": len(trades),
                "hour_filtered": len(hour_trades)
            }

        except Exception as e:
            logger.error(f"    Error fetching trades: {e}")
            return {"count": 0, "error": str(e)}

    def _store_trade(
        self,
        trade: Dict[str, Any],
        currency: str,
        hour: datetime
    ) -> None:
        """
        Store a single trade in database.

        Args:
            trade: Trade data from API
            currency: Currency symbol
            hour: Hour bucket
        """
        # Get database connection
        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            # Extract trade data
            trade_id = trade.get("trade_id")
            timestamp = trade.get("timestamp")
            instrument_name = trade.get("instrument_name")
            price = trade.get("price")
            amount = trade.get("amount")
            direction = trade.get("direction")
            iv = trade.get("iv")
            mark_price = trade.get("mark_price")
            index_price = trade.get("index_price")

            # Parse instrument details
            strike = None
            expiration = None
            option_type = None

            if instrument_name and "-" in instrument_name:
                parts = instrument_name.split("-")
                if len(parts) >= 4:  # e.g., ETH-31JAN25-3200-C
                    expiration = parts[1]
                    strike = float(parts[2])
                    option_type = parts[3]

            # Insert into historical_trades
            cursor.execute("""
                INSERT INTO historical_trades (
                    trade_id,
                    trade_seq,
                    trade_timestamp,
                    captured_at,
                    instrument_name,
                    currency,
                    expiration,
                    strike,
                    option_type,
                    price,
                    amount,
                    direction,
                    iv,
                    mark_price,
                    index_price
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (trade_id, trade_timestamp) DO NOTHING
            """, (
                trade_id,
                trade.get("trade_seq"),
                timestamp,
                datetime.now(),
                instrument_name,
                currency,
                expiration,
                strike,
                option_type,
                price,
                amount,
                direction,
                iv,
                mark_price,
                index_price
            ))

            conn.commit()

        except Exception as e:
            conn.rollback()
            raise e

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _fetch_book_summary(
        self,
        currency: str,
        hour: datetime
    ) -> Dict[str, Any]:
        """
        Fetch book summary for currency and store to database.

        Args:
            currency: Currency symbol
            hour: Hour bucket for this snapshot

        Returns:
            Book summary fetch result
        """
        try:
            # Fetch book summary using API service
            response = self.api.get_book_summary(
                currency=currency,
                kind="option"
            )

            # Response is already a list from API service
            instruments = response if isinstance(response, list) else []

            logger.info(f"    Found {len(instruments)} instruments")

            # Store to snapshots table using repository method
            try:
                rows_saved = self.repo.save_snapshot(
                    currency=currency,
                    data=instruments,
                    captured_at=datetime.now()
                )
                logger.info(f"    Stored {rows_saved} snapshots to database")
            except Exception as e:
                logger.error(f"    Failed to save snapshots: {e}")
                rows_saved = 0

            return {
                "count": len(instruments),
                "stored": rows_saved,
                "instruments": instruments
            }

        except Exception as e:
            logger.error(f"    Error fetching book summary: {e}")
            return {"count": 0, "error": str(e)}

    def _run_onchain_analysis(
        self,
        currency: str,
        hour: datetime,
        instruments: List[Dict]
    ) -> None:
        """
        Run on-chain analysis (GEX/DEX, max pain, S/R) and save to database.

        Args:
            currency: Currency symbol (BTC, ETH).
            hour: Hour bucket for this snapshot.
            instruments: List of instrument dicts from book summary.
        """
        try:
            # Create on-chain analyzer
            analyzer = OnChainMetricsCalculator(data=instruments, currency=currency)

            # Parse instruments by expiration
            grouped = analyzer.parse_instruments()

            # bugfix_spec.md Item 7: the spot index is fetched explicitly
            # now -- no heuristic, no volume-race pick across the whole
            # book. Falls back to the nearest-expiry median
            # underlying_price (the smallest-basis proxy available without
            # the index) if the index fetch itself fails -- never silently
            # reverts to the old global highest-volume pick.
            try:
                index_price = self.api.get_index_price(currency=currency)
            except Exception as e:
                index_price = analyzer.nearest_expiry_median_underlying_price()
                logger.error(
                    f"    get_index_price failed for {currency}: {e} -- "
                    f"falling back to nearest-expiry median underlying_price "
                    f"({index_price})"
                )
            analyzer.set_index_price(index_price)
            underlying_price = index_price

            logger.info(f"    Analyzing {len(grouped)} expirations...")

            snapshots_saved = 0

            # Build raw-instrument lookup keyed by name for GEX/DEX greek enrichment.
            # parse_instruments() strips greeks to keep the parsed dicts lean; we
            # re-attach them here from the original book-summary items before feeding
            # GexDexCalculator (which needs delta/gamma at the top level).
            raw_by_name = {inst.get("instrument_name", ""): inst for inst in instruments}

            # Process each expiration
            for expiration, instruments_for_exp in grouped.items():
                try:
                    # Run on-chain analysis for this expiration. T10
                    # (refactor_design_spec.md compat-map row #8):
                    # analyze_expiration() returns the typed
                    # ExpirationAnalysisResult directly now (was a dict).
                    analysis_data = analyzer.analyze_expiration(expiration)
                    if analysis_data is None:
                        logger.warning(
                            f"    analyze_expiration returned None for {expiration} "
                            f"(expiration missing from parsed data) — skipping"
                        )
                        continue

                    # bugfix_spec.md Item 7 anchor table: BS-fallback greeks
                    # and max-pain distance are settlement-space (a
                    # contract's own delta/gamma, and strike-vs-settlement
                    # distance) -- this expiry's own forward, not the index.
                    forward_price = analyzer.forward_price_by_expiration.get(expiration)
                    if forward_price is None:
                        logger.warning(
                            f"    No forward price for {expiration} -- "
                            f"falling back to index price"
                        )
                        forward_price = underlying_price

                    # Enrich with greeks for GEX/DEX (nested → top-level, with BS fallback)
                    gex_instruments = self._enrich_with_greeks(
                        instruments_for_exp, raw_by_name, forward_price
                    )

                    # Run GEX/DEX calculation. GEX/DEX are exposures to a
                    # move in the underlying SPOT -- index, not the forward
                    # (GEX's S² term amplifies any basis error).
                    gex_calc = GexDexCalculator(
                        instruments=gex_instruments,
                        spot_price=underlying_price
                    )
                    # T10 (refactor_design_spec.md compat-map row #9):
                    # save_onchain_snapshot now reads the typed GexDexResult
                    # directly (attribute access) — the .to_dict() shim
                    # is no longer needed at this call site.
                    gex_dex_data = gex_calc.calculate()

                    # Save to database
                    self.repo.save_onchain_snapshot(
                        snapshot_hour=hour,
                        currency=currency,
                        expiration=expiration,
                        analysis_data=analysis_data,
                        gex_dex_data=gex_dex_data,
                        underlying_price=underlying_price,
                        forward_price=forward_price,
                    )

                    # institutional_metrics_spec.md Migration M3 / section 3
                    # (Task C4): full-chain delta-interpolated RR25/BF25,
                    # persisted to volatility_skew_history. gex_instruments
                    # is this expiry's full enriched chain (already holds
                    # mark_iv, delta, bid_price/ask_price) -- zero
                    # additional API cost. Isolated in its own try/except
                    # (own helper) so a failure here never blocks the
                    # GEX/DEX save above, which just succeeded.
                    self._calculate_and_save_skew(
                        currency, hour, expiration, gex_instruments, forward_price,
                    )

                    snapshots_saved += 1

                except (AttributeError, TypeError):
                    # Review fix (task A6, Important #3 -- the A4 lesson
                    # applied): AttributeError/TypeError here means a
                    # producer/consumer shape mismatch (e.g. the wrong
                    # object type reaching save_onchain_snapshot's typed
                    # attribute reads) -- a programming error, not a data
                    # condition. Swallowing it the same way as the broad
                    # except below (which correctly skips one expiration's
                    # genuinely bad data and keeps processing the rest) is
                    # exactly the failure mode that hid the original A4
                    # bug: snapshots_saved silently stays low/zero and the
                    # only trace is an INFO-level "Saved N snapshots" line,
                    # no error-level signal at all. Re-raise instead --
                    # this method's own outer except (below) logs it at
                    # ERROR with a full traceback and re-raises again, and
                    # the daemon's caller (run_hourly_cycle) already logs
                    # that and moves on to the next step, so the process
                    # does not crash -- but the failure is now loud.
                    raise
                except Exception as e:
                    logger.warning(f"    Failed to analyze expiration {expiration}: {e}")
                    continue

            logger.info(f"    Saved {snapshots_saved} on-chain snapshots")

            # Phase 3: resolve previous hour's predictions, then record a new one
            # using the front-month (nearest expiry) snapshot metrics.
            try:
                self._forward_harness.resolve_pending_predictions(currency)
            except Exception as e:
                logger.warning(f"    Forward harness resolve failed for {currency}: {e}")

            if snapshots_saved > 0 and grouped:
                try:
                    front_exp = sorted(grouped.keys())[0]
                    # T10 (refactor_design_spec.md compat-map row #8):
                    # analyze_expiration() returns the typed
                    # ExpirationAnalysisResult now -- attribute access
                    # instead of the legacy dict's .get() chains.
                    front_data = analyzer.analyze_expiration(front_exp)
                    if front_data is None:
                        logger.warning(
                            f"    analyze_expiration returned None for front-month "
                            f"{front_exp} — skipping forward-harness prediction"
                        )
                    else:
                        moneyness = front_data.moneyness
                        max_pain_strike = front_data.max_pain.max_pain_strike
                        # bugfix_spec.md Item 7 anchor table: max-pain
                        # distance is settlement-space -- front_data.
                        # underlying_price is this expiry's own forward
                        # (analyze_expiration already anchors it there),
                        # not the index.
                        front_forward_price = front_data.underlying_price
                        max_pain_dist = (
                            (max_pain_strike - front_forward_price) / front_forward_price * 100
                            if max_pain_strike and front_forward_price else None
                        )
                        metrics = {
                            "itm_call_oi_pct": moneyness.calls.itm_pct,
                            "otm_call_oi_pct": moneyness.calls.otm_pct,
                            "itm_put_oi_pct": moneyness.puts.itm_pct,
                            "otm_put_oi_pct": moneyness.puts.otm_pct,
                            "max_pain_distance_pct": max_pain_dist,
                            # pc_far_otm_ratio: live vol-surface computation deferred;
                            # harness will use None and fall back to the 5 common metrics.
                            "pc_far_otm_ratio": None,
                        }
                        self._forward_harness.record_prediction(
                            currency=currency,
                            snapshot_hour=hour,
                            metrics=metrics,
                            spot_price=underlying_price,
                        )
                except (AttributeError, TypeError):
                    # Same reasoning as the per-expiration loop above: a
                    # shape mismatch here is a programming error, not a
                    # data condition -- re-raise so it is loud (this
                    # method's own outer except logs it at ERROR and
                    # re-raises; the daemon's caller already catches that
                    # and moves on, so the process does not crash).
                    raise
                except Exception as e:
                    logger.warning(f"    Forward harness record failed for {currency}: {e}")

        except Exception as e:
            logger.error(f"    On-chain analysis failed: {e}", exc_info=True)
            raise

    def _calculate_and_save_skew(
        self,
        currency: str,
        hour: datetime,
        expiration: str,
        gex_instruments: List[Dict],
        forward_price: float,
    ) -> None:
        """
        Compute the delta-interpolated RR25/BF25 term-structure row for one
        expiration and persist it to ``volatility_skew_history``
        (institutional_metrics_spec.md Migration M3 / section 3, Task C4).

        Spot anchor is ``forward_price`` (this expiry's own forward), not
        the index -- bugfix_spec.md Item 7 anchor table: 25d strike
        selection and ATM IV are settlement-space, matching the existing
        convention in ``on_chain_analysis_service.py``'s
        ``VolatilitySurfaceCalculator`` call site.

        Isolated in its own try/except: a failure here (bad chain shape,
        DB hiccup) is logged and skipped -- it must never take down the
        GEX/DEX save this method runs immediately after, which already
        succeeded by the time this is called.
        """
        try:
            surface_calc = VolatilitySurfaceCalculator(
                instruments=gex_instruments,
                spot_price=forward_price,
                expiration=expiration,
            )
            skew = surface_calc.calculate_risk_reversal_butterfly()

            now_utc = datetime.now(timezone.utc)
            dte_days = MarketWideCalculator._calculate_days_to_expiry(expiration, now_utc)
            dte_years = (dte_days / 365.0) if dte_days is not None else None

            self.repo.save_volatility_skew(
                snapshot_hour=hour,
                currency=currency,
                expiration=expiration,
                dte_years=dte_years,
                skew=skew,
            )
        except Exception as e:
            logger.warning(
                f"    Failed to compute/save RR25/BF25 skew for {expiration}: {e}"
            )

    def _enrich_with_greeks(
        self,
        instruments: List[Dict],
        raw_by_name: Dict[str, Dict],
        underlying_price: float,
    ) -> List[Dict]:
        """
        Promote greeks from the nested 'greeks' dict in the raw book-summary items
        to the top level of each instrument dict. Also carries bid_price/ask_price
        from the same raw item (needed by VolatilitySurfaceCalculator's RR25/BF25
        "quoted" filter, institutional_metrics_spec.md section 3(b) step 1 --
        parse_instruments() strips these from the top-level parsed dict).

        Falls back to Black-Scholes when the API omits greeks (delta/gamma are 0
        or absent). This is required before feeding GexDexCalculator, which reads
        delta and gamma at the top level.
        """
        bs = BlackScholesCalculator()
        enriched = []

        for inst in instruments:
            raw = raw_by_name.get(inst.get("instrument_name", ""), {})
            nested = raw.get("greeks") or {}
            delta = nested.get("delta") or inst.get("delta")
            gamma = nested.get("gamma") or inst.get("gamma")
            vega = nested.get("vega") or inst.get("vega")
            theta = nested.get("theta") or inst.get("theta")

            # BS fallback when exchange didn't return greeks
            if (not delta or not gamma) and underlying_price > 0:
                mark_iv = inst.get("mark_iv")
                strike = inst.get("strike")
                name = inst.get("instrument_name", "")
                if mark_iv and strike and name:
                    parsed = bs.parse_instrument_name(name)
                    if parsed:
                        # institutional_metrics_spec.md section 4(b), "Known
                        # latent bug to fix in the same change": parsed
                        # expiry_time is naive-UTC (BlackScholesCalculator.
                        # parse_instrument_name always builds it at 08:00
                        # UTC settlement). datetime.now() here was naive
                        # LOCAL time -- on this machine (CET/CEST) that is a
                        # 1-2 hour τ error, material for 0-DTE options,
                        # feeding directly into this BS-fallback greeks path
                        # (and therefore into GexDexCalculator and the new
                        # ExposureProfileCalculator, both of which consume
                        # this enriched instrument list). Fixed to naive-UTC
                        # so both sides of the subtraction agree.
                        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                        tte = bs.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
                        if tte > 0:
                            iv_decimal = float(mark_iv) / 100.0
                            calc = bs.calculate_greeks(
                                spot_price=underlying_price,
                                strike_price=float(strike),
                                time_to_expiry=tte,
                                implied_volatility=iv_decimal,
                                option_type=parsed["option_type"],
                            )
                            delta = delta or calc["delta"]
                            gamma = gamma or calc["gamma"]
                            vega = vega or calc["vega"]
                            theta = theta or calc["theta"]

            # institutional_metrics_spec.md section 3(b) step 1 (Task C4):
            # the RR25/BF25 delta-interpolation's "quoted" filter needs
            # bid_price/ask_price, which parse_instruments() already
            # stripped from `inst`. Pulled from the same raw book-summary
            # item greeks came from -- no extra API call.
            enriched.append({
                **inst,
                "delta": delta or 0,
                "gamma": gamma or 0,
                "vega": vega or 0,
                "theta": theta or 0,
                "bid_price": raw.get("bid_price"),
                "ask_price": raw.get("ask_price"),
            })

        return enriched

    def _fetch_dvol(self, currency: str) -> None:
        """
        Fetch DVOL (Deribit Volatility Index) and save to database.

        Args:
            currency: Currency symbol (BTC, ETH).
        """
        try:
            dvol_result = self.api.get_volatility_index_data(
                currency=currency,
                resolution=3600,  # 1 hour resolution
                start_timestamp=None,
                end_timestamp=None
            )

            if dvol_result and "data" in dvol_result and dvol_result["data"]:
                # Get latest DVOL value
                latest_dvol = dvol_result["data"][-1]
                if len(latest_dvol) >= 5:
                    dvol_timestamp = latest_dvol[0]
                    dvol_value = latest_dvol[4]  # Close price

                    # Save to database
                    self.repo.save_dvol(
                        currency=currency,
                        index_name=f"{currency}DVOL",
                        timestamp=dvol_timestamp,
                        date=datetime.fromtimestamp(dvol_timestamp / 1000),
                        dvol=dvol_value
                    )

                    logger.info(f"    Saved DVOL: {dvol_value:.2f}")
                else:
                    logger.warning(f"    DVOL data incomplete: {latest_dvol}")
            else:
                logger.warning(f"    No DVOL data returned for {currency}")

        except Exception as e:
            logger.error(f"    Failed to fetch/save DVOL: {e}")
            raise

    def _fetch_funding_rate(self, currency: str) -> None:
        """
        Fetch funding rate from perpetual contract and save to database.

        Args:
            currency: Currency symbol (BTC, ETH).
        """
        try:
            instrument_name = f"{currency}-PERPETUAL"
            ticker = self.api.get_ticker(instrument_name)

            if ticker:
                funding_8h = ticker.get("funding_8h")
                ticker_timestamp = ticker.get("timestamp", int(time.time() * 1000))

                if funding_8h is not None:
                    # Save to database (already in decimal form)
                    self.repo.save_funding_rate(
                        currency=currency,
                        instrument_name=instrument_name,
                        timestamp=ticker_timestamp,
                        date=datetime.fromtimestamp(ticker_timestamp / 1000),
                        funding_rate=funding_8h / 100  # Convert from percentage
                    )

                    logger.info(f"    Saved funding rate: {funding_8h:.4f}%")
                else:
                    logger.warning(f"    No funding rate in ticker for {instrument_name}")
            else:
                logger.warning(f"    No ticker data returned for {instrument_name}")

        except Exception as e:
            logger.error(f"    Failed to fetch/save funding rate: {e}")
            raise

    def _fetch_ohlcv(self, currency: str) -> None:
        """
        Fetch and save the last 2 days of daily OHLCV candles.

        Runs on every 30-min cycle. ON CONFLICT DO NOTHING in save_ohlcv
        makes this idempotent — duplicate candles are silently skipped.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH").
        """
        try:
            instrument = f"{currency}-PERPETUAL"
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (2 * 24 * 60 * 60 * 1000)  # 2 days back

            result = self.api.get_tradingview_chart_data(
                instrument_name=instrument,
                resolution="1D",
                start_timestamp=start_ms,
                end_timestamp=now_ms
            )

            if not result or "ticks" not in result:
                logger.warning(f"No OHLCV data returned for {instrument}")
                return

            ticks = result["ticks"]
            opens = result.get("open", [])
            highs = result.get("high", [])
            lows = result.get("low", [])
            closes = result.get("close", [])
            volumes = result.get("volume", [])

            processed = 0
            for i, ts_ms in enumerate(ticks):
                try:
                    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                    self.repo.save_ohlcv(
                        currency=currency,
                        instrument_name=instrument,
                        timestamp=ts_ms,
                        date=dt,
                        open_price=float(opens[i]) if i < len(opens) else 0.0,
                        high=float(highs[i]) if i < len(highs) else 0.0,
                        low=float(lows[i]) if i < len(lows) else 0.0,
                        close=float(closes[i]) if i < len(closes) else 0.0,
                        volume=float(volumes[i]) if i < len(volumes) else 0.0
                    )
                    processed += 1
                except Exception as e:
                    logger.warning(f"Failed to save OHLCV candle for {instrument} at {ts_ms}: {e}")

            logger.info(f"OHLCV: {processed}/{len(ticks)} candles processed for {instrument}")

        except Exception as e:
            logger.error(f"    Failed to fetch/save OHLCV: {e}")
            raise
