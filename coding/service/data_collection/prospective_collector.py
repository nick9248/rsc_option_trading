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

from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.config import SUPPORTED_CURRENCIES
from coding.core.database.repository import DatabaseRepository
from coding.service.data_collection.hourly_aggregation_service import HourlyAggregationService
from coding.service.deribit.deribit_api_service import DeribitApiService

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

        return result

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
            analyzer = OnChainAnalyzer(data=instruments, currency=currency)

            # Parse instruments by expiration
            grouped = analyzer.parse_instruments()

            # Get underlying price (from analyzer's extraction)
            underlying_price = analyzer._extract_underlying_price(instruments)

            logger.info(f"    Analyzing {len(grouped)} expirations...")

            snapshots_saved = 0

            # Process each expiration
            for expiration, instruments_for_exp in grouped.items():
                try:
                    # Run on-chain analysis for this expiration
                    analysis_data = analyzer.analyze_expiration(expiration)

                    # Run GEX/DEX calculation
                    gex_calc = GexDexCalculator(
                        instruments=instruments_for_exp,
                        spot_price=underlying_price
                    )
                    gex_dex_data = gex_calc.calculate()

                    # Save to database
                    self.repo.save_onchain_snapshot(
                        snapshot_hour=hour,
                        currency=currency,
                        expiration=expiration,
                        analysis_data=analysis_data,
                        gex_dex_data=gex_dex_data,
                        underlying_price=underlying_price
                    )

                    snapshots_saved += 1

                except Exception as e:
                    logger.warning(f"    Failed to analyze expiration {expiration}: {e}")
                    continue

            logger.info(f"    Saved {snapshots_saved} on-chain snapshots")

        except Exception as e:
            logger.error(f"    On-chain analysis failed: {e}", exc_info=True)
            raise

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
