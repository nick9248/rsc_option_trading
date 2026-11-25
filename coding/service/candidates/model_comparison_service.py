"""
Collects, for past entries, what each price model said about every eligible candidate and what happened (read only, point in time).
"""

import logging
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from coding.core.analytics.backtest.engine import simulate_trade
from coding.core.analytics.candidates.distribution import LognormalDistribution, StudentTDistribution
from coding.core.analytics.candidates.generator import generate_candidates
from coding.core.analytics.candidates.metrics import compute_metrics
from coding.core.analytics.candidates.smile_distribution import fit_market_smile
from coding.core.analytics.har_volatility import har_annual_volatility
from coding.core.analytics.realised_volatility import last_completed_candle_start, realised_volatility
from coding.core.analytics.strategy_pipeline.shortlist import EXCLUDED_FAMILIES, MAX_COST_FRACTION_OF_RISK
from coding.core.analytics.validation.model_comparison import ALL_MODELS, crps, probability_integral_transform
from coding.core.database.repository import DatabaseRepository
from coding.service.backtest.backtest_service import BacktestService
from coding.service.strategy_pipeline.facts_service import FactsAsOfService

logger = logging.getLogger(__name__)

TARGET_DAYS = (3, 7, 14, 30)
"""The expiries the Analysis tab asks about."""
ENTRY_HOUR_UTC = 9
OPTION_CHECK_LOG_RANGE = 0.4
"""C1 compares option values with mids for strikes within this log-distance of the forward."""


class ModelComparisonService:
    """Builds the entries of the price-model comparison from the local database."""

    def __init__(self, repository: DatabaseRepository):
        """
        Args:
            repository: Local database (read only).
        """
        self.repository = repository
        self.backtest = BacktestService(repository)
        self.facts_service = FactsAsOfService(repository)

    def collect(self, currency: str, first_day: date, last_day: date, now_utc: Optional[datetime] = None,
                progress: Callable[[str], None] = logger.info) -> Tuple[List[Dict[str, Any]], Counter]:
        """
        One entry per day and settled expiry nearest each of ``TARGET_DAYS``.

        Returns:
            (entries, skipped): each entry holds the forecast scores of every model and, per eligible candidate, the expected result under every
            model and the realised result; ``skipped`` counts the looks that gave no entry, by reason.
        """
        now = now_utc or datetime.now(timezone.utc)
        entries: List[Dict[str, Any]] = []
        skipped: Counter = Counter()
        day = first_day
        while day <= last_day:
            instant = datetime.combine(day, time(ENTRY_HOUR_UTC), tzinfo=timezone.utc)
            seen = set()
            for target in TARGET_DAYS:
                data, reason = self.backtest.load_entry(currency, instant, float(target), 1.0, now, settled=True)
                if data is None:
                    skipped[reason] += 1
                    continue
                if data.expiration in seen:
                    continue
                seen.add(data.expiration)
                entry, reason = self._entry(currency, day, instant, target, data)
                if entry is None:
                    skipped[reason] += 1
                    continue
                entries.append(entry)
            if day.day == 1:
                progress(f"{currency} {day}: {len(entries)} entries so far")
            day += timedelta(days=1)
        return entries, skipped

    def _entry(self, currency: str, day: date, instant: datetime, target: int, data) -> Tuple[Optional[Dict[str, Any]], str]:
        years = (data.settlement_at - data.captured_at).total_seconds() / 86400.0 / 365.0
        days = years * 365.0
        end = last_completed_candle_start(instant)
        recent = realised_volatility(self.repository.get_ohlcv_by_date_range(currency, end - timedelta(days=45), end), 30)
        forecast = har_annual_volatility(self.facts_service.daily_log_returns(currency, instant), max(1, round(days)))
        implied = data.atm_implied_volatility
        if not recent or not forecast or not implied:
            return None, "no volatility"
        smile = fit_market_smile(data.quotes, data.forward, years)
        if smile is None:
            return None, "no smile"
        distributions = {"realised": LognormalDistribution(data.forward, recent, days),
                         "implied_t": StudentTDistribution(data.forward, implied, days),
                         "har_t": StudentTDistribution(data.forward, forecast, days),
                         "smile": smile.distribution(), "smile_rv": smile.distribution(recent), "smile_har": smile.distribution(forecast)}
        candidates = []
        for candidate in generate_candidates(data.quotes, data.spot):
            if candidate.family in EXCLUDED_FAMILIES:
                continue
            costs = compute_metrics(candidate, data.spot, {})
            if costs.max_loss_usd <= 0 or costs.spread_cost_usd + costs.trading_fees_usd > MAX_COST_FRACTION_OF_RISK * costs.max_loss_usd:
                continue
            metrics = compute_metrics(candidate, data.spot, distributions)
            trade = simulate_trade(candidate.family, data.expiration, instant, data.settlement_at, data.spot, candidate.legs, data.settlement_price)
            results = metrics.by_distribution
            candidates.append({"family": candidate.family, "r": trade.pnl_usd / metrics.max_loss_usd, "won": trade.pnl_usd > 0,
                               "costs_usd": metrics.spread_cost_usd + metrics.trading_fees_usd,
                               "expected_usd": {model: results[model].expected_pnl_usd for model in ALL_MODELS},
                               "expected_per_risk": {model: results[model].expected_pnl_usd / metrics.max_loss_usd for model in ALL_MODELS},
                               "probability": {model: results[model].probability_of_profit for model in ALL_MODELS}})
        if not candidates:
            return None, "no eligible candidate"
        outcome = float(np.log(data.settlement_price / data.forward))
        scores, transforms = {}, {}
        for model in ALL_MODELS:
            prices, weights = distributions[model].grid()
            returns = np.log(prices / data.forward)
            scores[model] = crps(returns, weights, outcome)
            transforms[model] = probability_integral_transform(returns, weights, outcome)
        errors, halves = self._option_errors(data, distributions["smile"])
        return {"currency": currency, "day": day, "horizon": target, "expiration": data.expiration, "cluster": f"{currency} {data.expiration}",
                "candidates": candidates, "crps": scores, "pit": transforms, "option_errors": errors, "half_spreads": halves,
                "smile_error": smile.median_error}, ""

    @staticmethod
    def _option_errors(data, market) -> Tuple[Sequence[float], Sequence[float]]:
        """|value under the market smile - mid| and the half-spread of every two-sided option near the forward."""
        prices, weights = market.grid()
        errors, halves = [], []
        for quote in data.quotes:
            if not quote.bid_usd or not quote.ask_usd or abs(np.log(quote.strike / data.forward)) > OPTION_CHECK_LOG_RANGE:
                continue
            pay = np.maximum(prices - quote.strike, 0.0) if quote.option_type == "call" else np.maximum(quote.strike - prices, 0.0)
            errors.append(abs(float((pay * weights).sum()) - 0.5 * (quote.bid_usd + quote.ask_usd)))
            halves.append(0.5 * (quote.ask_usd - quote.bid_usd))
        return errors, halves
