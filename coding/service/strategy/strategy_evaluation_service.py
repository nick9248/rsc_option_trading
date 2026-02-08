"""
Strategy evaluation service for scoring and ranking option strategies.

This service orchestrates the complete evaluation pipeline:
1. Fetch market context (on-chain data)
2. Fetch ticker data (greeks)
3. Build and score strategies
4. Filter and rank results
5. Save to database
"""

import logging
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.database.repository import DatabaseRepository
from coding.core.strategy.definitions import create_strategy, is_spread_strategy
from coding.core.strategy.models import (
    EvaluationResult,
    StrategyConfig,
    StrategySignal,
)
from coding.core.strategy.scoring import CompositeScorer, IntrinsicScorer, OnChainScorer
from coding.core.strategy.report_generator import StrategyReportGenerator
from coding.core.strategy.chart_generators import get_chart_generator

logger = logging.getLogger(__name__)


class StrategyEvaluationService:
    """
    Service for evaluating and scoring option strategies.

    Coordinates API calls, on-chain analysis, strategy construction,
    and scoring to produce ranked strategy signals.
    """

    def __init__(
        self,
        api_service,
        repository: DatabaseRepository,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ):
        """
        Initialize evaluation service.

        Args:
            api_service: Deribit API service instance
            repository: Database repository instance
            progress_callback: Optional callback for progress updates (message, current, total)
        """
        self.api_service = api_service
        self.repository = repository
        self.progress_callback = progress_callback

        # Initialize scorers
        self.intrinsic_scorer = IntrinsicScorer()
        self.on_chain_scorer = OnChainScorer(repository=repository)

        # Initialize report generator
        self.report_generator = StrategyReportGenerator()

        logger.info("StrategyEvaluationService initialized")

    @staticmethod
    def normalize_market_regime(regime: Optional[str]) -> Optional[str]:
        """
        Normalize market regime to match database constraint.

        Database allows: 'bullish', 'bearish', 'neutral', NULL
        Regime detection returns: 'Strong Bullish', 'Weak Bearish', 'Sideways', etc.

        Args:
            regime: Raw regime string from detection

        Returns:
            Normalized regime for database ('bullish', 'bearish', 'neutral', or None)
        """
        if regime is None:
            return None

        regime_lower = regime.lower()

        if "bullish" in regime_lower:
            return "bullish"
        elif "bearish" in regime_lower:
            return "bearish"
        elif "sideways" in regime_lower or "neutral" in regime_lower:
            return "neutral"
        else:
            # Default to neutral for unknown regimes
            logger.warning(f"Unknown regime '{regime}', defaulting to 'neutral'")
            return "neutral"

    def evaluate_strategies(
        self,
        currency: str,
        expiration: str,
        config: StrategyConfig
    ) -> EvaluationResult:
        """
        Evaluate strategies for a specific currency and expiration.

        Args:
            currency: Currency symbol (BTC, ETH)
            expiration: Expiration date string
            config: Strategy configuration

        Returns:
            EvaluationResult with signals and errors
        """
        start_time = time.time()
        result = EvaluationResult(success=False)

        try:
            logger.info(
                f"Starting strategy evaluation: {currency}-{expiration}, "
                f"strategies={config.strategy_names}"
            )

            # Step 1: Fetch ticker data first (needed for both GEX/DEX and strategy building)
            self._report_progress("Fetching ticker data", 0, 4)
            ticker_data = self._fetch_ticker_data(currency, expiration)

            if not ticker_data:
                result.add_error("ALL", "Failed to fetch ticker data")
                return result

            # Step 2: Fetch market context (uses ticker_data for GEX/DEX)
            self._report_progress("Fetching market context", 1, 4)
            market_context = self._fetch_market_context(currency, expiration, ticker_data)

            if not market_context:
                result.add_error("ALL", "Failed to fetch market context")
                return result

            # Step 3: Evaluate each strategy
            self._report_progress("Evaluating strategies", 2, 4)

            for i, strategy_name in enumerate(config.strategy_names):
                try:
                    signals = self._evaluate_single_strategy(
                        strategy_name=strategy_name,
                        currency=currency,
                        expiration=expiration,
                        ticker_data=ticker_data,
                        market_context=market_context,
                        config=config
                    )

                    # Apply filters to each signal (may be multiple for spreads)
                    for signal in signals:
                        if self._passes_filters(signal, config):
                            result.signals.append(signal)
                            logger.info(f"{strategy_name}: PASSED filters (composite={signal.composite_score:.2f})")
                        else:
                            logger.warning(
                                f"{strategy_name}: FILTERED OUT - "
                                f"composite={signal.composite_score:.2f} (min={config.min_composite_score}), "
                                f"intrinsic={signal.intrinsic_score:.2f} (min={config.min_intrinsic_score}), "
                                f"on_chain={signal.on_chain_score:.2f} (min={config.min_on_chain_score}), "
                                f"max_loss={signal.max_loss_percentage:.2f}% (max={config.max_loss_filter})"
                            )

                except Exception as e:
                    logger.error(
                        f"Strategy evaluation failed ({strategy_name}): {e}",
                        exc_info=True
                    )
                    result.add_error(strategy_name, str(e))
                    # Continue with next strategy (graceful degradation)

            # Step 4: Rank and save
            self._report_progress("Ranking and saving signals", 3, 4)

            if result.signals:
                # Sort by composite score
                result.signals.sort(key=lambda s: s.composite_score, reverse=True)

                # Assign ranks
                for rank, signal in enumerate(result.signals[:config.top_n], start=1):
                    signal.rank = rank

                # Save to database and generate reports
                for signal in result.signals[:config.top_n]:
                    try:
                        self.repository.save_strategy_signal(signal.to_dict())
                    except Exception as e:
                        logger.error(f"Failed to save signal: {e}", exc_info=True)

                    # Generate detailed report file
                    try:
                        report_path = self.report_generator.generate_report(
                            signal=signal,
                            market_context=market_context,
                            currency=currency,
                            expiration=expiration
                        )
                        logger.debug(f"Generated report: {report_path}")
                    except Exception as e:
                        logger.error(f"Failed to generate report: {e}", exc_info=True)

                    # Generate interactive chart (using strategy-specific generator)
                    try:
                        chart_generator = get_chart_generator(
                            strategy_name=signal.strategy_name,
                            repository=self.repository
                        )
                        chart_path = chart_generator.generate_strategy_chart(
                            signal=signal,
                            market_context=market_context,
                            currency=currency,
                            expiration=expiration
                        )
                        signal.chart_path = chart_path
                        logger.debug(f"Generated chart: {chart_path}")
                    except Exception as e:
                        logger.error(f"Failed to generate chart: {e}", exc_info=True)

                # Trim to top_n
                result.signals = result.signals[:config.top_n]

            result.success = True
            result.evaluation_time_seconds = time.time() - start_time

            logger.info(
                f"Evaluation complete: {len(result.signals)} signals generated, "
                f"{len(result.errors)} errors, time={result.evaluation_time_seconds:.2f}s"
            )

            self._report_progress("Complete", 4, 4)

            return result

        except Exception as e:
            logger.error(f"Evaluation failed: {e}", exc_info=True)
            result.add_error("SYSTEM", str(e))
            result.evaluation_time_seconds = time.time() - start_time
            return result

    def _evaluate_single_strategy(
        self,
        strategy_name: str,
        currency: str,
        expiration: str,
        ticker_data: Dict,
        market_context: Dict,
        config: StrategyConfig
    ) -> List[StrategySignal]:
        """
        Evaluate a single strategy.

        For spread strategies with skew_aware method, this may return multiple signals
        (top N spread variations). For other strategies, returns a single-element list.

        Args:
            strategy_name: Strategy name
            currency: Currency symbol
            expiration: Expiration date
            ticker_data: Ticker data for all strikes
            market_context: On-chain market data
            config: Strategy configuration

        Returns:
            List of StrategySignal instances (usually 1, but may be N for spreads)

        Raises:
            Exception: If evaluation fails
        """
        logger.debug(f"Evaluating strategy: {strategy_name}")

        # Get underlying price from market context
        underlying_price = market_context.get("underlying_price", 0)

        if underlying_price == 0:
            raise ValueError("Underlying price not available in market context")

        # Create strategy instance
        strategy = create_strategy(
            name=strategy_name,
            currency=currency,
            expiration=expiration,
            underlying_price=underlying_price,
            take_profit_percentage=config.take_profit_percentage
        )

        # Build strategy legs
        strike_config = config.get_strike_config(strategy_name)

        # Check if this is a spread strategy (multi-leg) using factory helper
        is_spread = is_spread_strategy(strategy_name)
        if is_spread:
            # Spread strategies use SpreadStrikeConfig
            from coding.core.strategy.models.spread_config import SpreadStrikeConfig

            # Check if strike_config is a dict (optimal mode from GUI)
            if isinstance(strike_config, dict):
                mode = strike_config.get("mode", "optimal")

                if mode == "optimal":
                    # Use skew-aware optimization
                    # Get return_top_n from GUI config (default to 5 if not specified)
                    return_top_n = strike_config.get("return_top_n", 5)

                    # Determine optimization mode based on budget constraint
                    if config.max_budget is not None:
                        # User specified budget - optimize for max width within budget
                        spread_config = SpreadStrikeConfig(
                            method="skew_aware",
                            optimize_for="max_width_for_budget",
                            max_budget=config.max_budget,
                            min_profit_debit_ratio=0.3,
                            quantity=1,
                            return_top_n=return_top_n
                        )
                        logger.info(
                            f"Using budget constraint: ${config.max_budget:.2f} "
                            f"(optimize for max width within budget), return_top_n={return_top_n}"
                        )
                    else:
                        # No budget constraint - optimize for profit/debit ratio
                        spread_config = SpreadStrikeConfig(
                            method="skew_aware",
                            optimize_for="profit_debit_ratio",
                            min_profit_debit_ratio=0.3,
                            quantity=1,
                            return_top_n=return_top_n
                        )
                        logger.info(f"Using optimal (skew-aware) strike selection, return_top_n={return_top_n}")
                else:
                    # Manual mode - should have been converted to SpreadStrikeConfig by GUI
                    logger.error(f"Manual mode dict not converted to SpreadStrikeConfig: {strike_config}")
                    raise ValueError("Manual spread config should be SpreadStrikeConfig object")
            elif isinstance(strike_config, SpreadStrikeConfig):
                # Already a SpreadStrikeConfig (manual mode from GUI)
                spread_config = strike_config
                logger.info(f"Using manual spread config: {spread_config.method}")
            else:
                # Fallback to optimal mode
                logger.warning(f"Unknown strike config type for spread: {type(strike_config)}, using optimal")
                spread_config = SpreadStrikeConfig(
                    method="skew_aware",
                    optimize_for="profit_debit_ratio",
                    min_profit_debit_ratio=0.3,
                    quantity=1
                )

            strategy.build_legs(
                ticker_data=ticker_data,
                spread_config=spread_config
            )
        else:
            # Single-leg strategies use Pydantic config objects
            if strike_config:
                # Create appropriate Pydantic config based on strategy type
                if "Call" in strategy_name:
                    from coding.core.strategy.models.long_call_config import LongCallConfig

                    # Map method and create config
                    if strike_config.method == "by_delta":
                        single_config = LongCallConfig(
                            method="by_delta",
                            target_delta=strike_config.target_delta,
                            quantity=strike_config.quantity
                        )
                    elif strike_config.method == "by_moneyness":
                        single_config = LongCallConfig(
                            method="by_moneyness",
                            moneyness_pct=strike_config.moneyness_pct,
                            quantity=strike_config.quantity
                        )
                    elif strike_config.method == "by_strike":
                        single_config = LongCallConfig(
                            method="by_strike",
                            specific_strike=strike_config.specific_strike,
                            quantity=strike_config.quantity
                        )
                    else:
                        raise ValueError(f"Unsupported method for Long Call: {strike_config.method}")

                elif "Put" in strategy_name:
                    from coding.core.strategy.models.long_put_config import LongPutConfig

                    # Map method and create config
                    if strike_config.method == "by_delta":
                        single_config = LongPutConfig(
                            method="by_delta",
                            target_delta=abs(strike_config.target_delta) if strike_config.target_delta else 0.30,
                            quantity=strike_config.quantity
                        )
                    elif strike_config.method == "by_moneyness":
                        single_config = LongPutConfig(
                            method="by_moneyness",
                            moneyness_pct=strike_config.moneyness_pct,
                            quantity=strike_config.quantity
                        )
                    elif strike_config.method == "by_strike":
                        single_config = LongPutConfig(
                            method="by_strike",
                            specific_strike=strike_config.specific_strike,
                            quantity=strike_config.quantity
                        )
                    else:
                        raise ValueError(f"Unsupported method for Long Put: {strike_config.method}")
                else:
                    raise ValueError(f"Unknown single-leg strategy: {strategy_name}")

                strategy.build_legs(
                    ticker_data=ticker_data,
                    config=single_config
                )
            else:
                # Use defaults (by_delta with 0.30 delta for directional strategies)
                if "Call" in strategy_name:
                    from coding.core.strategy.models.long_call_config import LongCallConfig
                    single_config = LongCallConfig(
                        method="by_delta",
                        target_delta=0.30,
                        quantity=1
                    )
                elif "Put" in strategy_name:
                    from coding.core.strategy.models.long_put_config import LongPutConfig
                    single_config = LongPutConfig(
                        method="by_delta",
                        target_delta=0.30,  # Absolute value
                        quantity=1
                    )
                else:
                    raise ValueError(f"Unknown strategy: {strategy_name}")

                strategy.build_legs(
                    ticker_data=ticker_data,
                    config=single_config
                )

        # Validate strategy
        if not strategy.validate_legs():
            raise ValueError(f"Strategy {strategy_name} has invalid legs")

        # Score strategy
        composite_scorer = CompositeScorer(
            intrinsic_scorer=self.intrinsic_scorer,
            on_chain_scorer=self.on_chain_scorer,
            intrinsic_weight=config.intrinsic_weight,
            on_chain_weight=config.on_chain_weight
        )

        scores = composite_scorer.evaluate_strategy(
            strategy=strategy,
            market_context=market_context,
            market_regime=config.market_regime
        )

        # Build signal
        signal = StrategySignal(
            strategy_name=strategy_name,
            currency=currency,
            expiration=expiration,
            generated_at=datetime.now(),
            legs=[leg.__dict__ for leg in strategy.legs],
            intrinsic_score=scores["intrinsic_score"],
            on_chain_score=scores["on_chain_score"],
            composite_score=scores["composite_score"],
            intrinsic_breakdown=scores["intrinsic_breakdown"],
            on_chain_breakdown=scores["on_chain_breakdown"],
            underlying_price=underlying_price,
            implied_volatility=market_context.get("implied_volatility"),
            max_pain_strike=market_context.get("max_pain_strike"),
            max_risk=strategy.get_max_risk(),
            max_profit=strategy.get_max_profit(),
            total_cost=strategy.get_total_cost(),
            breakeven_points=strategy.get_breakeven_points(),
            max_loss_percentage=strategy.get_max_loss_percentage(),
            take_profit_percentage=strategy.take_profit_percentage,
            market_regime=self.normalize_market_regime(market_context.get("market_regime")),
            net_delta=strategy.get_net_greeks()["delta"],
            net_gamma=strategy.get_net_greeks()["gamma"],
            net_theta=strategy.get_net_greeks()["theta"],
            net_vega=strategy.get_net_greeks()["vega"]
        )

        logger.info(
            f"{strategy_name} scored: composite={signal.composite_score:.2f}, "
            f"intrinsic={signal.intrinsic_score:.2f}, "
            f"on_chain={signal.on_chain_score:.2f}"
        )

        signals = [signal]

        # Check if this is a spread strategy with multiple variations
        # (only for skew_aware optimization with return_top_n > 1)
        if is_spread and isinstance(spread_config, SpreadStrikeConfig):
            if spread_config.method == "skew_aware" and spread_config.return_top_n > 1:
                try:
                    # Get all spread variations from the strategy
                    variations = strategy.get_all_spread_variations()

                    # We already have signal for variation #1, so start from #2
                    if len(variations) > 1:
                        logger.info(
                            f"Generating {len(variations) - 1} additional signals "
                            f"for spread variations #2-#{len(variations)}"
                        )

                        for i, (long_name, short_name) in enumerate(variations[1:], start=2):
                            # Create a fresh strategy instance for this variation
                            variation_strategy = create_strategy(
                                name=strategy_name,
                                currency=currency,
                                expiration=expiration,
                                underlying_price=underlying_price,
                                take_profit_percentage=config.take_profit_percentage
                            )

                            # Build legs with the specific strikes
                            from coding.core.strategy.models.spread_config import SpreadStrikeConfig
                            long_strike = variation_strategy.extract_strike_from_name(long_name)
                            short_strike = variation_strategy.extract_strike_from_name(short_name)

                            # Determine spread type based on strategy type
                            spread_type = "put" if "Put" in strategy_name else "call"

                            variation_config = SpreadStrikeConfig(
                                method="by_strike",
                                long_specific_strike=long_strike,
                                short_specific_strike=short_strike,
                                quantity=spread_config.quantity,
                                return_top_n=1,
                                spread_type=spread_type
                            )

                            variation_strategy.build_legs(
                                ticker_data=ticker_data,
                                spread_config=variation_config
                            )

                            # Validate
                            if not variation_strategy.validate_legs():
                                logger.warning(f"Variation #{i} has invalid legs, skipping")
                                continue

                            # Score the variation
                            variation_scores = composite_scorer.evaluate_strategy(
                                strategy=variation_strategy,
                                market_context=market_context,
                                market_regime=config.market_regime
                            )

                            # Create signal
                            variation_signal = StrategySignal(
                                strategy_name=strategy_name,
                                currency=currency,
                                expiration=expiration,
                                generated_at=datetime.now(),
                                legs=[leg.__dict__ for leg in variation_strategy.legs],
                                intrinsic_score=variation_scores["intrinsic_score"],
                                on_chain_score=variation_scores["on_chain_score"],
                                composite_score=variation_scores["composite_score"],
                                intrinsic_breakdown=variation_scores["intrinsic_breakdown"],
                                on_chain_breakdown=variation_scores["on_chain_breakdown"],
                                underlying_price=underlying_price,
                                implied_volatility=market_context.get("implied_volatility"),
                                max_pain_strike=market_context.get("max_pain_strike"),
                                max_risk=variation_strategy.get_max_risk(),
                                max_profit=variation_strategy.get_max_profit(),
                                total_cost=variation_strategy.get_total_cost(),
                                breakeven_points=variation_strategy.get_breakeven_points(),
                                max_loss_percentage=variation_strategy.get_max_loss_percentage(),
                                take_profit_percentage=variation_strategy.take_profit_percentage,
                                market_regime=self.normalize_market_regime(market_context.get("market_regime")),
                                net_delta=variation_strategy.get_net_greeks()["delta"],
                                net_gamma=variation_strategy.get_net_greeks()["gamma"],
                                net_theta=variation_strategy.get_net_greeks()["theta"],
                                net_vega=variation_strategy.get_net_greeks()["vega"]
                            )

                            logger.info(
                                f"{strategy_name} variation #{i} scored: "
                                f"composite={variation_signal.composite_score:.2f}"
                            )

                            signals.append(variation_signal)

                except Exception as e:
                    logger.warning(
                        f"Failed to generate additional spread variations: {e}. "
                        f"Returning primary signal only."
                    )

        return signals

    def _fetch_market_context(self, currency: str, expiration: str, ticker_data: Dict[str, Dict] = None) -> Dict:
        """
        Fetch market context including on-chain metrics.

        Args:
            currency: Currency symbol
            expiration: Expiration date
            ticker_data: Optional pre-fetched ticker data for GEX/DEX calculation

        Returns:
            Dictionary with market data
        """
        try:
            # Get book summary data
            book_summary_data = self.api_service.get_book_summary(currency=currency, kind="option")

            if not book_summary_data:
                logger.error("Failed to fetch book summary data")
                return {}

            # Filter for this expiration
            expiration_data = [
                item for item in book_summary_data
                if expiration in item.get("instrument_name", "")
            ]

            if not expiration_data:
                logger.error(f"No data found for expiration {expiration}")
                return {}

            # Run on-chain analysis
            analyzer = OnChainAnalyzer(book_summary_data, currency)
            analyzer.parse_instruments()
            on_chain_metrics = analyzer.analyze_expiration(expiration)

            # Get underlying_price from FRESH ticker_data (not cached book_summary)
            # ticker_data is fetched via get_ticker() which gives current prices
            if ticker_data:
                # All tickers have same underlying_price, get from first available
                first_ticker = next(iter(ticker_data.values()))
                underlying_price = first_ticker.get("underlying_price", 0)
                if underlying_price == 0:
                    logger.warning("No underlying_price in ticker_data, falling back to book_summary")
                    underlying_price = analyzer.underlying_price
                else:
                    logger.debug(f"Using underlying_price from fresh ticker_data: {underlying_price:.2f}")
            else:
                # Fallback to book_summary if no ticker_data provided
                underlying_price = analyzer.underlying_price
                logger.warning("Using underlying_price from book_summary (may be cached)")

            # If ticker_data provided, use it for GEX/DEX (more reliable greeks)
            if ticker_data:
                # Filter ticker data for this expiration and extract greeks
                instruments_with_greeks = []
                for inst_name, ticker in ticker_data.items():
                    if expiration in inst_name and ticker.get("greeks", {}).get("gamma") is not None:
                        # Parse instrument name: "BTC-31JAN25-100000-C" -> strike=100000, option_type=C
                        parts = inst_name.split("-")
                        if len(parts) == 4:
                            strike = float(parts[2])
                            option_type = parts[3]  # "C" or "P"
                        else:
                            continue

                        # Convert ticker format to format expected by GexDexCalculator
                        # GexDexCalculator expects greeks at top level, not nested
                        greeks = ticker.get("greeks", {})
                        inst_data = {
                            "instrument_name": inst_name,
                            "strike": strike,
                            "option_type": option_type,
                            "gamma": greeks.get("gamma", 0),
                            "delta": greeks.get("delta", 0),
                            "open_interest": ticker.get("open_interest", 0),
                            "underlying_price": ticker.get("underlying_price", underlying_price)
                        }
                        instruments_with_greeks.append(inst_data)

                logger.info(f"Using {len(instruments_with_greeks)} instruments with greeks for GEX/DEX calculation")
            else:
                # Fallback to book_summary data (may have incomplete greeks)
                instruments_for_gex = analyzer.parsed_data.get(expiration, [])
                instruments_with_greeks = [
                    inst for inst in instruments_for_gex
                    if inst.get("greeks") and "gamma" in inst.get("greeks", {})
                ]
                logger.warning(
                    f"Using book_summary data for GEX/DEX ({len(instruments_with_greeks)} instruments), "
                    f"may have incomplete greeks"
                )

            gex_calculator = GexDexCalculator(instruments_with_greeks, underlying_price)
            gex_dex_metrics = gex_calculator.calculate()

            # Detect market regime once for all strategies (prevents circular dependencies)
            market_regime, regime_composite_score = self._detect_market_regime(currency)

            # Combine metrics
            market_context = {
                "underlying_price": underlying_price,
                "max_pain_strike": on_chain_metrics["max_pain"]["max_pain_strike"],
                "total_oi": on_chain_metrics["put_call_ratio"]["total_call_oi"] + on_chain_metrics["put_call_ratio"]["total_put_oi"],
                "call_oi": on_chain_metrics["put_call_ratio"]["total_call_oi"],
                "put_oi": on_chain_metrics["put_call_ratio"]["total_put_oi"],
                "put_call_ratio": on_chain_metrics["put_call_ratio"]["ratio"],
                "total_volume": on_chain_metrics["volume_stats"]["total_volume"],
                "gex_total": gex_dex_metrics["total_net_gex"],
                "dex_total": gex_dex_metrics["total_net_dex"],
                "gex_dex_data": gex_dex_metrics,  # Full GEX/DEX data for report
                "support_resistance": on_chain_metrics["support_resistance"],  # Top OI-based levels for chart
                "implied_volatility": None,  # Could add IV calculation later
                "market_regime": market_regime,  # Detected regime for on-chain scoring
                "regime_composite_score": regime_composite_score  # Regime strength score
            }

            logger.info(
                f"Market context fetched: underlying={market_context['underlying_price']:.2f}, "
                f"max_pain={market_context['max_pain_strike']:.2f}, "
                f"GEX={market_context['gex_total']:.2f}, DEX={market_context['dex_total']:.2f}"
            )

            return market_context

        except Exception as e:
            logger.error(f"Failed to fetch market context: {e}", exc_info=True)
            return {}

    def _detect_market_regime(self, currency: str) -> tuple:
        """
        Detect current market regime using ML prediction (preferred) with heuristic fallback.

        Tries ML-based prediction first. If ML model is not available or prediction fails,
        falls back to the existing RegimeDetectionService heuristic.

        Args:
            currency: Currency symbol (e.g., "BTC", "ETH")

        Returns:
            Tuple of (regime_name, composite_score) or (None, None) on error
        """
        # Try ML prediction first
        try:
            from coding.service.ml.ml_training_service import MLTrainingService

            logger.debug(f"Attempting ML-based regime prediction for {currency}...")

            ml_service = MLTrainingService(repository=self.repository)
            prediction = ml_service.predict(currency)

            # Check if prediction succeeded
            regime_pred = prediction.get("regime", {})

            if not regime_pred.get("error") and regime_pred.get("confidence", 0) > 0.5:
                ml_regime = regime_pred.get("regime")
                confidence = regime_pred.get("confidence", 0.5)

                # Map ML regime to strategy regime names
                # ML outputs: bullish, bearish, sideways, high_vol, low_vol
                # Strategy expects: Strong Bullish, Weak Bullish, Sideways, Weak Bearish, Strong Bearish
                regime_map = {
                    "bullish": "Weak Bullish",
                    "bearish": "Weak Bearish",
                    "sideways": "Sideways",
                    "high_vol": "Sideways",
                    "low_vol": "Sideways"
                }

                # High confidence → Strong, Low confidence → Weak
                if confidence > 0.75:
                    if ml_regime == "bullish":
                        mapped_regime = "Strong Bullish"
                    elif ml_regime == "bearish":
                        mapped_regime = "Strong Bearish"
                    else:
                        mapped_regime = regime_map.get(ml_regime, "Sideways")
                else:
                    mapped_regime = regime_map.get(ml_regime, "Sideways")

                # Convert confidence (0-1) to composite score (-100 to +100)
                # Bullish: positive score, Bearish: negative score
                if "Bullish" in mapped_regime:
                    composite_score = confidence * 100
                elif "Bearish" in mapped_regime:
                    composite_score = -confidence * 100
                else:
                    composite_score = 0.0

                logger.info(
                    f"ML regime prediction for {currency}: "
                    f"{mapped_regime} (confidence={confidence:.2f}, score={composite_score:.1f})"
                )

                return mapped_regime, composite_score

            else:
                error_msg = regime_pred.get("error", "Low confidence")
                logger.debug(f"ML prediction not usable: {error_msg}, falling back to heuristic")

        except Exception as e:
            logger.debug(f"ML prediction unavailable, falling back to heuristic: {e}")

        # Fallback to existing RegimeDetectionService
        try:
            from coding.service.regime.regime_detection_service import RegimeDetectionService

            logger.debug(f"Using heuristic regime detection for {currency}...")

            regime_service = RegimeDetectionService(
                api_service=self.api_service,
                repository=self.repository
            )

            result = regime_service.detect_regime(currency)

            if "error" in result:
                logger.error(f"Regime detection error: {result['error']}")
                return None, None

            regime = result.get("regime")
            composite_score = result.get("composite_score")

            # Handle None composite_score (can happen if regime detection partially fails)
            if composite_score is not None:
                logger.info(
                    f"Heuristic regime detection for {currency}: "
                    f"{regime} (score={composite_score:.1f})"
                )
            else:
                logger.warning(
                    f"Heuristic regime detection for {currency}: "
                    f"{regime} (score=None, using default)"
                )

            return regime, composite_score

        except Exception as e:
            logger.error(f"Failed to detect market regime (both ML and heuristic): {e}", exc_info=True)
            return None, None

    def _fetch_ticker_data(self, currency: str, expiration: str) -> Dict[str, Dict]:
        """
        Fetch ticker data for all strikes in this expiration.

        Args:
            currency: Currency symbol
            expiration: Expiration date

        Returns:
            Dictionary mapping instrument names to ticker data
        """
        try:
            # Get all instruments for this currency
            instruments = self.api_service.get_instruments(currency, kind="option")

            if not instruments:
                logger.error("Failed to fetch instruments")
                return {}

            # Filter for this expiration
            expiration_instruments = [
                inst for inst in instruments
                if expiration in inst.get("instrument_name", "")
            ]

            if not expiration_instruments:
                logger.error(f"No instruments found for expiration {expiration}")
                return {}

            logger.info(f"Fetching ticker data for {len(expiration_instruments)} instruments")

            # Fetch ticker data for each instrument
            # Note: This could be slow if there are many strikes
            ticker_data = {}

            for inst in expiration_instruments:
                inst_name = inst["instrument_name"]

                try:
                    ticker = self.api_service.get_ticker(inst_name)

                    if ticker:
                        ticker_data[inst_name] = ticker

                except Exception as e:
                    logger.warning(f"Failed to fetch ticker for {inst_name}: {e}")
                    continue

            logger.info(f"Fetched ticker data for {len(ticker_data)} instruments")

            return ticker_data

        except Exception as e:
            logger.error(f"Failed to fetch ticker data: {e}", exc_info=True)
            return {}

    def _passes_filters(self, signal: StrategySignal, config: StrategyConfig) -> bool:
        """
        Check if signal passes configured filters.

        Args:
            signal: Strategy signal to check
            config: Strategy configuration with filters

        Returns:
            True if signal passes all filters
        """
        # Intrinsic score filter
        if signal.intrinsic_score < config.min_intrinsic_score:
            return False

        # On-chain score filter
        if signal.on_chain_score < config.min_on_chain_score:
            return False

        # Composite score filter
        if signal.composite_score < config.min_composite_score:
            return False

        # Max loss filter
        if config.max_loss_filter is not None:
            if signal.max_loss_percentage > config.max_loss_filter:
                logger.debug(
                    f"{signal.strategy_name}: Filtered by max_loss "
                    f"({signal.max_loss_percentage:.2f}% > {config.max_loss_filter}%)"
                )
                return False

        return True

    def _report_progress(self, message: str, current: int, total: int) -> None:
        """
        Report progress to callback if configured.

        Args:
            message: Progress message
            current: Current step
            total: Total steps
        """
        if self.progress_callback:
            try:
                self.progress_callback(message, current, total)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    @staticmethod
    def convert_widget_config_to_strike_config(
        strategy_name: str,
        widget_config: Dict
    ):
        """
        Convert widget configuration dict to StrikeConfig or SpreadStrikeConfig.

        Args:
            strategy_name: Name of the strategy
            widget_config: Config dict from widget.get_config()

        Returns:
            StrikeConfig for single-leg strategies
            SpreadStrikeConfig for spread strategies (or widget_config dict for service to handle)
        """
        from coding.core.strategy.models.spread_config import SpreadStrikeConfig

        # Check if this is a spread strategy
        is_spread = "Spread" in strategy_name

        if is_spread:
            # Spread configuration
            mode = widget_config.get("mode", "optimal")

            if mode == "optimal":
                # Optimal (skew-aware) mode - return dict for service to handle
                # Service will use skew-aware with budget constraint if specified
                return widget_config
            else:
                # Manual mode - create SpreadStrikeConfig from widget config
                method = widget_config.get("method")

                if method == "by_delta":
                    return SpreadStrikeConfig(
                        method="by_delta",
                        long_target_delta=widget_config.get("long_target_delta", 0.45),
                        short_target_delta=widget_config.get("short_target_delta", 0.25),
                        quantity=1
                    )
                elif method == "by_moneyness":
                    return SpreadStrikeConfig(
                        method="by_moneyness",
                        long_moneyness_pct=widget_config.get("long_moneyness_pct", 10.0),
                        short_moneyness_pct=widget_config.get("short_moneyness_pct", 20.0),
                        quantity=1
                    )
                elif method == "by_strike":
                    return SpreadStrikeConfig(
                        method="by_strike",
                        long_specific_strike=widget_config.get("long_specific_strike", 50000.0),
                        short_specific_strike=widget_config.get("short_specific_strike", 55000.0),
                        quantity=1
                    )
                else:
                    logger.warning(f"Unknown spread method: {method}, defaulting to skew-aware")
                    return widget_config
        else:
            # Single-leg configuration - convert to StrikeConfig
            method = widget_config.get("method")

            # Adjust delta sign for puts
            if method == "by_delta":
                target_delta = widget_config.get("target_delta", 0.30)
                if "Put" in strategy_name:
                    target_delta = -abs(target_delta)
                else:
                    target_delta = abs(target_delta)

                return StrikeConfig(
                    method="by_delta",
                    target_delta=target_delta,
                    quantity=1
                )
            elif method == "by_moneyness":
                return StrikeConfig(
                    method="by_moneyness",
                    moneyness_pct=widget_config.get("moneyness_pct", 5.0),
                    quantity=1
                )
            elif method == "by_strike":
                return StrikeConfig(
                    method="by_strike",
                    specific_strike=widget_config.get("specific_strike", 100000.0),
                    quantity=1
                )
            else:
                logger.warning(f"Unknown method: {method}, defaulting to by_delta")
                return StrikeConfig(method="by_delta", target_delta=0.30, quantity=1)
