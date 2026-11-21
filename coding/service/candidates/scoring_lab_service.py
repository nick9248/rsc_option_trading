"""
The scoring lab: for many past dates, every candidate structure of an expiry, its metrics at entry and what it really earned at expiry.

Free to run (no model, no network): it answers which deterministic scoring rule, if any, picks better than the average candidate. Point in
time: the chain, the spot and the volatilities are of the entry instant (realised volatility from daily closes that had finished before the
entry day); only the settlement price comes from the future, and only to settle.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

from coding.core.analytics.backtest.engine import simulate_trade
from coding.core.analytics.candidates.distribution import LognormalDistribution
from coding.core.analytics.candidates.generator import FAMILIES, CandidateLimits, generate_candidates
from coding.core.analytics.candidates.metrics import compute_metrics
from coding.core.analytics.candidates.scoring import IMPLIED, REALISED, RULES, CandidateOutcome, RuleReport, evaluate_rule
from coding.core.analytics.realised_volatility import MINIMUM_RETURNS, last_completed_candle_start, realised_volatility
from coding.core.analytics.risk.budget import RiskBudget, size_position
from coding.core.database.repository import DatabaseRepository
from coding.service.backtest.backtest_service import BacktestService

logger = logging.getLogger(__name__)

REALISED_VOL_DAYS = 30
MINIMUM_QUANTITY = {"BTC": 0.1, "ETH": 1.0}
"""Deribit's minimum order size of an option, in coins (checked against get_instruments on 2026-11-21)."""


@dataclass(frozen=True)
class FamilyStat:
    """What all candidates of one family earned, in units of their maximum loss."""

    family: str
    candidates: int
    mean_r: float
    win_rate: float


@dataclass(frozen=True)
class LabResult:
    """The outcome of a lab run."""

    dates: int
    candidates: int
    skipped: Dict[str, int]
    rule_reports: List[RuleReport]
    family_stats: List[FamilyStat]
    feasible_share: float
    """Share of candidates that fit the budget at the exchange's minimum order size."""
    capital_needed_usd: Optional[float]
    """The account the cheapest candidate of the whole run would need at the minimum order size under the same drawdown rules."""


class ScoringLabService:
    """Builds candidates for past dates, scores them and compares the rules with what happened."""

    def __init__(self, repository: DatabaseRepository):
        """
        Args:
            repository: Local database (read only).
        """
        self.repository = repository
        self.backtest = BacktestService(repository)

    def realised_volatility(self, currency: str, entry_day: date, entry_hour_utc: int = 9) -> Optional[float]:
        """Annualised volatility of the last 30 daily log returns of the perpetual from the candles complete at the entry time (None if too few)."""
        end = last_completed_candle_start(datetime.combine(entry_day, time(entry_hour_utc), tzinfo=timezone.utc))      # naive UTC for the repository
        start = end - timedelta(days=REALISED_VOL_DAYS + 15)
        return realised_volatility(self.repository.get_ohlcv_by_date_range(currency, start, end), REALISED_VOL_DAYS, MINIMUM_RETURNS)

    def run(self, currency: str, first_day: date, last_day: date, budget: RiskBudget, entry_hour_utc: int = 9,
            target_days_to_expiry: float = 7.0, minimum_days_to_expiry: float = 2.0, limits: CandidateLimits = CandidateLimits(),
            now_utc: Optional[datetime] = None) -> LabResult:
        """
        One expiry per day (the nearest the target), every candidate, every rule.

        Args:
            currency: "BTC" or "ETH".
            first_day: First entry day (UTC).
            last_day: Last entry day (UTC).
            budget: The account; only used to tell how many candidates would fit at the exchange's minimum order size.
            entry_hour_utc: Entry time of every day.
            target_days_to_expiry: The expiry nearest this many days away is used.
            minimum_days_to_expiry: Expiries closer than this are not used.
            limits: Which strikes and widths are tried.
            now_utc: The current instant; an expiry that has not settled by then is not used.
        """
        now = now_utc or datetime.now(timezone.utc)
        dates: List[List[CandidateOutcome]] = []
        skipped: Counter = Counter()
        feasible = total = 0
        cheapest_loss: Optional[float] = None
        day = first_day
        while day <= last_day:
            entry_time = datetime.combine(day, time(entry_hour_utc), tzinfo=timezone.utc)
            entry_day, day = day, day + timedelta(days=1)
            if entry_time > now:
                break
            entry, reason = self.backtest.load_entry(currency, entry_time, target_days_to_expiry, minimum_days_to_expiry, now)
            if entry is None:
                skipped[reason] += 1
                continue
            realised_vol = self.realised_volatility(currency, entry_day, entry_hour_utc)
            if realised_vol is None or entry.atm_implied_volatility is None:
                skipped["no volatility"] += 1
                continue
            days_to_expiry = (entry.settlement_at - entry.captured_at).total_seconds() / 86400.0
            distributions = {REALISED: LognormalDistribution(entry.forward, realised_vol, days_to_expiry),
                             IMPLIED: LognormalDistribution(entry.forward, entry.atm_implied_volatility, days_to_expiry)}
            outcomes: List[CandidateOutcome] = []
            fitting = 0
            day_cheapest: Optional[float] = None
            for candidate in generate_candidates(entry.quotes, entry.spot, limits):
                metrics = compute_metrics(candidate, entry.spot, distributions)
                if metrics.max_loss_usd <= 0:
                    continue                                                   # a free lunch in the quotes: stale prices, not a trade
                trade = simulate_trade(candidate.family, entry.expiration, entry_time, entry.settlement_at, entry.spot, candidate.legs,
                                       entry.settlement_price)
                outcomes.append(CandidateOutcome(candidate.family, metrics, trade.pnl_usd))
                fitting += size_position(metrics.max_loss_usd, budget, MINIMUM_QUANTITY[currency]).feasible
                day_cheapest = metrics.max_loss_usd if day_cheapest is None else min(day_cheapest, metrics.max_loss_usd)
            if len(outcomes) < 3:
                skipped["too few candidates"] += 1
                continue
            dates.append(outcomes)                                              # only an accepted date counts in the totals
            total += len(outcomes)
            feasible += fitting
            cheapest_loss = day_cheapest if cheapest_loss is None else min(cheapest_loss, day_cheapest)
        reports = [evaluate_rule(rule, dates) for rule in RULES]
        needed = (None if cheapest_loss is None else cheapest_loss * MINIMUM_QUANTITY[currency] * budget.losses_in_a_row
                  / budget.max_drawdown_fraction)
        return LabResult(len(dates), total, dict(skipped), reports, self._family_stats(dates), feasible / total if total else 0.0, needed)

    @staticmethod
    def _family_stats(dates: Sequence[Sequence[CandidateOutcome]]) -> List[FamilyStat]:
        by_family: Dict[str, List[float]] = {family: [] for family in FAMILIES}
        for outcomes in dates:
            for outcome in outcomes:
                by_family[outcome.family].append(outcome.realised_r)
        return [FamilyStat(family, len(values), float(np.mean(values)), float(np.mean([value > 0 for value in values])))
                for family, values in by_family.items() if values]
