"""
Market regime detection service.

Orchestrates data fetching, indicator calculation, and regime detection.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from coding.core.analytics.market_regime_detector import MarketRegimeDetector
from coding.core.analytics.technical_indicator_calculator import TechnicalIndicatorCalculator
from coding.core.api.external_apis import ExternalMetricsFetcher
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class RegimeDetectionService:
    """
    Service for detecting market regime using multiple data sources.

    Coordinates:
    1. Historical OHLCV data fetching
    2. Technical indicator calculation
    3. On-chain metrics extraction
    4. External sentiment metrics
    5. Regime detection algorithm
    6. Database persistence
    """

    def __init__(
        self,
        api_service,
        repository: DatabaseRepository
    ):
        """
        Initialize regime detection service.

        Args:
            api_service: Deribit API service instance.
            repository: Database repository instance.
        """
        self.api_service = api_service
        self.repository = repository
        self.indicator_calculator = TechnicalIndicatorCalculator()
        self.regime_detector = MarketRegimeDetector()
        self.external_fetcher = ExternalMetricsFetcher()

        logger.info("RegimeDetectionService initialized")

    def detect_regime(self, currency: str) -> Dict:
        """
        Detect current market regime for a currency.

        Args:
            currency: Currency symbol (BTC, ETH).

        Returns:
            Dictionary with regime analysis and all component data.
        """
        start_time = time.time()
        logger.info(f"Starting regime detection for {currency}")

        try:
            # Step 1: Fetch historical OHLCV data
            logger.info("Fetching historical OHLCV data")
            instrument_name = f"{currency}-PERPETUAL"
            ohlcv_result = self.api_service.get_tradingview_chart_data(
                instrument_name=instrument_name,
                resolution="1D",
                start_timestamp=None,  # Will fetch 200 days by default
                end_timestamp=None
            )

            if not ohlcv_result or "ticks" not in ohlcv_result:
                logger.error("Failed to fetch OHLCV data")
                return {"error": "Failed to fetch OHLCV data"}

            # Transform columnar format to row format
            # API returns: {ticks: [...], open: [...], high: [...], low: [...], close: [...], volume: [...]}
            # Need: [[timestamp, open, high, low, close, volume], ...]
            timestamps = ohlcv_result["ticks"]
            opens = ohlcv_result.get("open", [])
            highs = ohlcv_result.get("high", [])
            lows = ohlcv_result.get("low", [])
            closes = ohlcv_result.get("close", [])
            volumes = ohlcv_result.get("volume", [])

            ohlcv_data = []
            for i in range(len(timestamps)):
                ohlcv_data.append([
                    timestamps[i],
                    opens[i] if i < len(opens) else 0,
                    highs[i] if i < len(highs) else 0,
                    lows[i] if i < len(lows) else 0,
                    closes[i] if i < len(closes) else 0,
                    volumes[i] if i < len(volumes) else 0,
                ])

            logger.info(f"Fetched {len(ohlcv_data)} OHLCV data points")

            # Step 2: Calculate technical indicators
            logger.info("Calculating technical indicators")
            indicators_df = self.indicator_calculator.calculate_all_indicators(ohlcv_data)

            if indicators_df.empty:
                logger.error("Failed to calculate technical indicators")
                return {"error": "Failed to calculate indicators"}

            latest_indicators = self.indicator_calculator.get_latest_indicators(indicators_df)

            # Compute velocity indicators (rate-of-change signals)
            velocity_indicators = self.indicator_calculator.get_velocity_indicators(
                indicators_df, lookback=5
            )
            logger.info(
                f"Velocity: EMA={velocity_indicators.get('ema_50_velocity', 'N/A')}, "
                f"RSI={velocity_indicators.get('rsi_velocity', 'N/A')}, "
                f"Hist={velocity_indicators.get('macd_histogram_velocity', 'N/A')}"
            )

            current_price = latest_indicators.get("close")

            if not current_price:
                logger.error("No current price in indicators")
                return {"error": "No current price available"}

            logger.info(f"Current price: ${current_price:,.2f}")

            # Save technical indicators to database
            try:
                indicator_date = datetime.fromtimestamp(timestamps[-1] / 1000)
                self.repository.save_technical_indicators(
                    currency=currency,
                    date=indicator_date,
                    indicators=latest_indicators
                )
            except Exception as e:
                logger.warning(f"Failed to save technical indicators: {e}")

            # Step 3: Get on-chain metrics
            logger.info("Fetching on-chain metrics")
            onchain_metrics = self._get_onchain_metrics(currency, ohlcv_data=ohlcv_data)

            # Step 4: Get external sentiment metrics
            logger.info("Fetching external sentiment metrics")
            external_metrics = self.external_fetcher.fetch_all_metrics()

            # Save external metrics to database
            try:
                # Extract values from nested structure
                fear_greed_data = external_metrics.get("fear_greed") or {}
                fear_greed_value = fear_greed_data.get("value") if isinstance(fear_greed_data, dict) else None
                fear_greed_classification = fear_greed_data.get("classification") if isinstance(fear_greed_data, dict) else None

                self.repository.save_external_metrics(
                    date=datetime.now(),
                    fear_greed_value=fear_greed_value,
                    fear_greed_classification=fear_greed_classification,
                    btc_dominance=external_metrics.get("btc_dominance"),
                    eth_dominance=external_metrics.get("eth_dominance")
                )
            except Exception as e:
                logger.warning(f"Failed to save external metrics: {e}")

            # Step 5: Detect regime
            logger.info("Running regime detection algorithm")
            regime_result = self.regime_detector.detect_regime(
                technical_indicators=latest_indicators,
                onchain_metrics=onchain_metrics,
                external_metrics=external_metrics,
                current_price=current_price,
                velocity_indicators=velocity_indicators,
                currency=currency,
            )

            # Step 6: Build comprehensive result
            result = {
                "currency": currency,
                "detected_at": datetime.now(),
                "current_price": current_price,
                "regime": regime_result["regime"],
                "confidence": regime_result["confidence"],
                "composite_score": regime_result["composite_score"],
                "component_scores": {
                    "trend": regime_result["trend_score"],
                    "volatility": regime_result["volatility_score"],
                    "momentum": regime_result["momentum_score"],
                    "onchain": regime_result["onchain_score"],
                    "sentiment": regime_result["sentiment_score"],
                },
                "technical_indicators": latest_indicators,
                "onchain_metrics": onchain_metrics,
                "external_metrics": external_metrics,
                "reasoning": regime_result["reasoning"],
                "detection_time_seconds": time.time() - start_time,
            }

            # Step 7: Save to database (if repository method exists)
            try:
                self._save_regime_detection(result)
            except Exception as e:
                logger.warning(f"Failed to save regime detection to database: {e}")

            # Safe formatting for logging (defensive check)
            confidence_str = f"{result['confidence']:.1f}" if result.get('confidence') is not None else "N/A"
            time_str = f"{result['detection_time_seconds']:.2f}" if result.get('detection_time_seconds') is not None else "N/A"
            logger.info(
                f"Regime detection complete: {result['regime']} "
                f"(confidence={confidence_str}%, time={time_str}s)"
            )

            return result

        except Exception as e:
            logger.error(f"Regime detection failed: {e}", exc_info=True)
            return {"error": str(e)}

    def _get_onchain_metrics(self, currency: str, ohlcv_data: list = None) -> Dict:
        """
        Get on-chain metrics for regime detection.

        Args:
            currency: Currency symbol.

        Returns:
            Dictionary with funding rate, put/call ratio, etc.
        """
        metrics = {}

        try:
            # Get funding rate from perpetual contract
            instrument_name = f"{currency}-PERPETUAL"
            ticker = self.api_service.get_ticker(instrument_name)

            if ticker:
                # Funding rate (8h)
                funding_8h = ticker.get("funding_8h")
                if funding_8h is not None:
                    metrics["funding_rate"] = funding_8h  # Raw % — detector thresholds match this unit
                    logger.info(f"Funding rate: {funding_8h:.4f}%")

                    # Save funding rate to database
                    try:
                        ticker_timestamp = ticker.get("timestamp", int(time.time() * 1000))
                        self.repository.save_funding_rate(
                            currency=currency,
                            instrument_name=instrument_name,
                            timestamp=ticker_timestamp,
                            date=datetime.fromtimestamp(ticker_timestamp / 1000),
                            funding_rate=funding_8h / 100
                        )
                    except Exception as save_error:
                        logger.warning(f"Failed to save funding rate: {save_error}")

                # Current funding rate
                current_funding = ticker.get("current_funding")
                if current_funding is not None:
                    metrics["current_funding"] = current_funding

                # Interest rate
                interest_rate = ticker.get("interest_rate")
                if interest_rate is not None:
                    metrics["interest_rate"] = interest_rate

                # Index price (underlying)
                index_price = ticker.get("index_price")
                if index_price is not None:
                    metrics["index_price"] = index_price

            # Get DVOL if available
            try:
                dvol_result = self.api_service.get_volatility_index_data(
                    currency=currency,
                    resolution=3600,  # 1 hour
                    start_timestamp=None,
                    end_timestamp=None
                )
                if dvol_result and "data" in dvol_result and dvol_result["data"]:
                    # Get latest DVOL value (last close)
                    latest_dvol = dvol_result["data"][-1]
                    if len(latest_dvol) >= 5:
                        dvol_timestamp = latest_dvol[0]  # Timestamp
                        dvol_value = latest_dvol[4]  # Close price
                        metrics["dvol"] = dvol_value
                        logger.info(f"DVOL: {dvol_value:.2f}")

                        # --- DVOL Percentile (30-day rolling hourly) ---
                        try:
                            all_dvol_closes = [row[4] for row in dvol_result["data"] if len(row) >= 5]
                            if all_dvol_closes and dvol_value is not None:
                                below_count = sum(1 for v in all_dvol_closes if v < dvol_value)
                                metrics["dvol_percentile"] = (below_count / len(all_dvol_closes)) * 100
                                logger.info(f"DVOL percentile: {metrics['dvol_percentile']:.1f}%")

                            # --- DVOL Term Structure Ratio (current / 30d avg) ---
                            if all_dvol_closes and dvol_value is not None and len(all_dvol_closes) > 1:
                                dvol_avg = sum(all_dvol_closes) / len(all_dvol_closes)
                                if dvol_avg > 0:
                                    metrics["dvol_term_structure_ratio"] = dvol_value / dvol_avg
                                    logger.info(f"DVOL term structure ratio: {metrics['dvol_term_structure_ratio']:.3f}")
                        except Exception as e:
                            logger.warning(f"Failed to compute DVOL metrics: {e}")

                        # --- VRP Signal ---
                        try:
                            if dvol_value is not None and ohlcv_data:
                                from coding.core.analytics.vrp_calculator import VRPCalculator
                                vrp_calc = VRPCalculator(currency=currency, lookback_days=30)
                                price_history = [
                                    {"timestamp": row[0] / 1000, "close": row[4]}
                                    for row in ohlcv_data if len(row) >= 5
                                ]
                                rv_30d = vrp_calc.calculate_realized_volatility(price_history, window_days=30)
                                if rv_30d > 0:
                                    vrp_result = vrp_calc.calculate_vrp(
                                        implied_vol=dvol_value / 100,  # DVOL is in %, VRPCalculator expects decimal
                                        realized_vol=rv_30d,
                                    )
                                    metrics["vrp_percentage"] = vrp_result.get("vrp_percentage", 0.0)
                                    logger.info(f"VRP: {metrics['vrp_percentage']:.1f}%")
                        except Exception as e:
                            logger.warning(f"Failed to compute VRP: {e}")

                        # Save DVOL to database
                        try:
                            self.repository.save_dvol(
                                currency=currency,
                                index_name=f"{currency}DVOL",
                                timestamp=dvol_timestamp,
                                date=datetime.fromtimestamp(dvol_timestamp / 1000),
                                dvol=dvol_value
                            )
                        except Exception as save_error:
                            logger.warning(f"Failed to save DVOL: {save_error}")
            except Exception as e:
                logger.warning(f"Failed to fetch DVOL: {e}")

            # Get put/call ratio from current book summary
            try:
                book_summary = self.api_service.get_book_summary(
                    currency=currency,
                    kind="option"
                )

                if book_summary:
                    # --- Wings Skew (OTM put IV minus OTM call IV, nearest expiry DTE 7-45) ---
                    try:
                        from datetime import timezone

                        def parse_deribit_expiry(instrument_name: str):
                            """Extract expiry datetime from instrument name like BTC-28MAR25-80000-C."""
                            parts = instrument_name.split("-")
                            if len(parts) < 2:
                                return None
                            exp_str = parts[1]  # e.g. "28MAR25"
                            try:
                                return datetime.strptime(exp_str, "%d%b%y").replace(tzinfo=timezone.utc)
                            except ValueError:
                                return None

                        now_utc = datetime.now(timezone.utc)
                        spot = None
                        for item in book_summary:
                            if item.get("underlying_price"):
                                spot = item["underlying_price"]
                                break

                        if spot and spot > 0:
                            expiry_dte: dict = {}
                            for item in book_summary:
                                name = item.get("instrument_name", "")
                                exp_dt = parse_deribit_expiry(name)
                                if exp_dt:
                                    dte = (exp_dt - now_utc).days
                                    exp_key = name.split("-")[1]
                                    if exp_key not in expiry_dte:
                                        expiry_dte[exp_key] = dte

                            valid_expiries = {k: v for k, v in expiry_dte.items() if 7 <= v <= 45}

                            if valid_expiries:
                                nearest_expiry = min(valid_expiries, key=lambda k: valid_expiries[k])

                                otm_put_ivs = []
                                otm_call_ivs = []
                                lower_put = spot * 0.90
                                upper_put = spot * 0.97
                                lower_call = spot * 1.03
                                upper_call = spot * 1.10

                                for item in book_summary:
                                    name = item.get("instrument_name", "")
                                    parts = name.split("-")
                                    if len(parts) < 4:
                                        continue
                                    if parts[1] != nearest_expiry:
                                        continue
                                    mark_iv = item.get("mark_iv")
                                    if not mark_iv or mark_iv <= 0:
                                        continue
                                    try:
                                        strike = float(parts[2])
                                    except ValueError:
                                        continue
                                    opt_type = parts[3]

                                    if opt_type == "P" and lower_put <= strike <= upper_put:
                                        otm_put_ivs.append(mark_iv)
                                    elif opt_type == "C" and lower_call <= strike <= upper_call:
                                        otm_call_ivs.append(mark_iv)

                                if otm_put_ivs and otm_call_ivs:
                                    avg_put_iv = sum(otm_put_ivs) / len(otm_put_ivs)
                                    avg_call_iv = sum(otm_call_ivs) / len(otm_call_ivs)
                                    wings_skew = avg_put_iv - avg_call_iv
                                    metrics["wings_skew"] = wings_skew
                                    logger.info(
                                        f"Wings skew: {wings_skew:.2f}pp "
                                        f"(puts: {avg_put_iv:.1f}%, calls: {avg_call_iv:.1f}%)"
                                    )
                    except Exception as e:
                        logger.warning(f"Failed to compute wings skew: {e}")

                    # --- OI Direction (compare total OI to previous detection >= 4h ago) ---
                    try:
                        total_oi_current = sum(
                            item.get("open_interest", 0) for item in book_summary
                            if item.get("open_interest") is not None
                        )
                        metrics["total_oi"] = total_oi_current

                        cutoff = datetime.now() - timedelta(hours=4)
                        try:
                            history = self.repository.get_regime_detections(
                                currency=currency,
                                start_time=datetime.now() - timedelta(days=7),
                                end_time=cutoff,
                            )
                            if history:
                                prev = history[-1]
                                prev_data = prev.get("regime_data", {}) if isinstance(prev, dict) else {}
                                prev_oi = prev_data.get("onchain_metrics", {}).get("total_oi")
                                prev_price = prev_data.get("current_price")
                                current_price_for_oi = metrics.get("index_price")

                                if prev_oi and prev_oi > 0 and prev_price and current_price_for_oi:
                                    oi_change_pct = (total_oi_current - prev_oi) / prev_oi * 100
                                    price_up = current_price_for_oi > prev_price
                                    oi_rising = oi_change_pct > 2
                                    oi_falling = oi_change_pct < -2

                                    if price_up and oi_rising:
                                        metrics["oi_direction"] = 20
                                    elif price_up and oi_falling:
                                        metrics["oi_direction"] = 10
                                    elif not price_up and oi_rising:
                                        metrics["oi_direction"] = -20
                                    elif not price_up and oi_falling:
                                        metrics["oi_direction"] = -10
                                    else:
                                        metrics["oi_direction"] = 0

                                    logger.info(
                                        f"OI direction: {metrics['oi_direction']} "
                                        f"(OI change: {oi_change_pct:.1f}%, price_up={price_up})"
                                    )
                        except Exception as db_error:
                            logger.warning(f"Could not fetch previous OI from DB: {db_error}")
                            metrics["oi_direction"] = 0
                    except Exception as e:
                        logger.warning(f"Failed to compute OI direction: {e}")

                    from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer

                    analyzer = OnChainAnalyzer(book_summary, currency)
                    analyzer.parse_instruments()

                    # Get all active strikes (combine all expirations for overall market sentiment)
                    all_strikes = {}
                    for exp_data in analyzer.parsed_data.values():
                        for instrument in exp_data:
                            strike = instrument.get("strike")
                            option_type = instrument.get("option_type")
                            oi = instrument.get("open_interest", 0)

                            if strike and option_type and oi > 0:
                                if strike not in all_strikes:
                                    all_strikes[strike] = {"call_oi": 0, "put_oi": 0}

                                if option_type == "C":
                                    all_strikes[strike]["call_oi"] += oi
                                elif option_type == "P":
                                    all_strikes[strike]["put_oi"] += oi

                    # Calculate total P/C ratio
                    total_call_oi = sum(data["call_oi"] for data in all_strikes.values())
                    total_put_oi = sum(data["put_oi"] for data in all_strikes.values())

                    if total_call_oi > 0:
                        put_call_ratio = total_put_oi / total_call_oi
                        metrics["put_call_ratio"] = put_call_ratio
                        logger.info(f"Put/Call Ratio: {put_call_ratio:.2f}")

            except Exception as e:
                logger.warning(f"Failed to calculate Put/Call ratio: {e}")

        except Exception as e:
            logger.error(f"Failed to fetch on-chain metrics: {e}", exc_info=True)

        return metrics

    def _save_regime_detection(self, result: Dict) -> None:
        """
        Save regime detection result to database.

        Args:
            result: Regime detection result dictionary.
        """
        try:
            row_id = self.repository.save_regime_detection(
                currency=result.get("currency"),
                detected_at=result.get("detected_at"),
                regime_data=result
            )
            logger.info(f"Saved regime detection to database (ID: {row_id})")
        except Exception as e:
            logger.error(f"Failed to save regime detection: {e}", exc_info=True)

    def get_regime_history(
        self,
        currency: str,
        days: int = 30
    ) -> list:
        """
        Get historical regime detections.

        Args:
            currency: Currency symbol.
            days: Number of days of history to retrieve.

        Returns:
            List of regime detection results.
        """
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)

            results = self.repository.get_regime_detections(
                currency=currency,
                start_time=start_time,
                end_time=end_time
            )

            logger.info(f"Fetched {len(results)} regime detections for {currency}")
            return results

        except Exception as e:
            logger.error(f"Failed to fetch regime history: {e}", exc_info=True)
            return []
