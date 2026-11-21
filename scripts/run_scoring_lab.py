"""
The scoring lab: which deterministic rule picks the better candidate structure? Research only: places no orders, calls no model, costs nothing.

Usage (from repo root; the local database should be synced first):
    .venv\\Scripts\\python -m scripts.run_scoring_lab --currency BTC --from 2026-03-20 --to 2026-10-31 --capital 200

For every date one expiry (nearest 7 days), every defined-risk candidate (long options, spreads, iron condors), its metrics at entry and its
realised result at expiry in units of its maximum loss (R). Each rule's pick is compared with the average candidate of the same date.
Read documentation/scoring_lab.md before trusting a number.
"""

import argparse
import logging
from datetime import date
from typing import Optional, Sequence

from coding.core.analytics.risk.budget import RiskBudget
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging
from coding.service.candidates.scoring_lab_service import LabResult, ScoringLabService

logger = logging.getLogger(__name__)


def _number(value: Optional[float], digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def log_result(result: LabResult, budget: RiskBudget) -> None:
    """Log the tables of one run."""
    logger.info("%d dates, %d candidates, skipped %s", result.dates, result.candidates, result.skipped or "nothing")
    logger.info("Budget %.0f USD, drawdown limit %.0f%%, risk per trade %.2f USD: %.0f%% of the candidates fit at the exchange's minimum "
                "order size", budget.capital_usd, budget.max_drawdown_fraction * 100, budget.risk_per_trade_usd, result.feasible_share * 100)
    if result.capital_needed_usd is not None:
        logger.info("The cheapest candidate of the run needs an account of about %s USD at the minimum order size", f"{result.capital_needed_usd:,.0f}")
    logger.info("%-40s %5s %9s %9s %9s %7s %9s %9s", "rule", "dates", "pick R", "all R", "excess R", "t", "beat avg", "rank corr")
    for report in result.rule_reports:
        logger.info("%-40s %5d %9s %9s %9s %7s %9s %9s", report.rule, report.dates, _number(report.mean_pick_r), _number(report.mean_all_r),
                    _number(report.mean_excess_r), _number(report.t_statistic, 2),
                    "n/a" if report.positive_dates is None else f"{report.positive_dates:.0%}", _number(report.mean_rank_correlation))
    logger.info("%-22s %10s %9s %9s", "family (all candidates)", "candidates", "mean R", "win rate")
    for stat in result.family_stats:
        logger.info("%-22s %10d %9.3f %8.0f%%", stat.family, stat.candidates, stat.mean_r, stat.win_rate * 100)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the lab and log the tables."""
    parser = argparse.ArgumentParser(description="Scoring lab for candidate option structures (research only)")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--from", dest="first_day", type=date.fromisoformat, default=date(2026, 3, 20))
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, default=date(2026, 10, 31))
    parser.add_argument("--capital", type=float, default=200.0, help="account size in USD")
    parser.add_argument("--max-drawdown", type=float, default=0.20, help="fraction of capital")
    parser.add_argument("--target-dte", type=float, default=7.0)
    parser.add_argument("--entry-hour", type=int, default=9)
    arguments = parser.parse_args(argv)
    init_logging(task_name="run_scoring_lab", log_to_file=False)
    budget = RiskBudget(arguments.capital, arguments.max_drawdown)
    result = ScoringLabService(DatabaseRepository()).run(arguments.currency, arguments.first_day, arguments.last_day, budget,
                                                         arguments.entry_hour, arguments.target_dte)
    log_result(result, budget)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
