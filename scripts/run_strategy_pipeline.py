"""
The strategy pipeline: facts and candidates by code, the model chooses among them, the position is sized from a loss budget, and the choice is
settled and compared with the baselines. Research only: places no orders. Every call is paid (about 0.08 USD).

Usage (from repo root; the local database should be synced first; the key is claude_api_key in .env):
    .venv\\Scripts\\python -m scripts.run_strategy_pipeline replay --from 2026-09-01 --to 2026-10-15 --every-days 5 --yes
    .venv\\Scripts\\python -m scripts.run_strategy_pipeline live --expiry 27NOV26
    .venv\\Scripts\\python -m scripts.run_strategy_pipeline evaluate
    .venv\\Scripts\\python -m scripts.run_strategy_pipeline report

replay: the same pipeline at past instants (09:00 UTC of the chosen days; the chain, facts and volatilities are those of that instant; only the
delivery price comes from the future, to evaluate). An instant already stored is not paid for again. A stop at the cost budget (15 USD of all
stored model calls) is built in. evaluate settles every stored pick whose expiry has passed. report compares the model with the baselines.
"""

import argparse
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import anthropic
from dotenv import load_dotenv

from coding.core.analytics.risk.budget import RiskBudget
from coding.core.analytics.strategy_pipeline.pick_report import summarize_picks
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging
from coding.service.strategy.strategy_suggestion_service import BudgetExceeded, DEFAULT_MODEL
from coding.service.strategy_pipeline.strategy_pick_service import (
    DEFAULT_RESEARCH_CAPITAL_USD, MODE_LIVE, MODE_REPLAY, StrategyPickService)

logger = logging.getLogger(__name__)
ENTRY_HOUR_UTC = 9
APPROXIMATE_COST_PER_CALL_USD = 0.08


def _number(value: Optional[float], digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def log_report(currency: str, repository: DatabaseRepository, mode: str) -> None:
    """Log the comparison of the model with the baselines over the evaluated picks."""
    report = summarize_picks(repository.get_strategy_picks(currency, mode=mode, evaluated=True))
    logger.info("%d evaluated %s picks, no trade in %s", report.picks, mode,
                "n/a" if report.no_trade_share is None else f"{report.no_trade_share:.0%}")
    logger.info("mean R (result in units of the maximum loss): model %s | baseline top-1 %s | shortlist average %s | all candidates average %s",
                _number(report.mean_model_r), _number(report.mean_baseline_r), _number(report.mean_shortlist_r), _number(report.mean_all_r))
    logger.info("model minus baseline %s (t %s), minus shortlist average %s (t %s), minus all candidates %s (t %s); beat the baseline on %s",
                _number(report.model_minus_baseline), _number(report.model_minus_baseline_t, 2), _number(report.model_minus_shortlist),
                _number(report.model_minus_shortlist_t, 2), _number(report.model_minus_all), _number(report.model_minus_all_t, 2),
                "n/a" if report.model_beat_baseline_share is None else f"{report.model_beat_baseline_share:.0%}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one of the four commands."""
    parser = argparse.ArgumentParser(description="Strategy pipeline: pick, replay, evaluate, report (research only)")
    parser.add_argument("command", choices=["live", "replay", "evaluate", "report"])
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--expiry", default=None, help="live: the expiry to pick for (default: the one nearest 7 days)")
    parser.add_argument("--from", dest="first_day", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, default=None)
    parser.add_argument("--every-days", type=int, default=5)
    parser.add_argument("--max-calls", type=int, default=30)
    parser.add_argument("--capital", type=float, default=DEFAULT_RESEARCH_CAPITAL_USD, help="notional account in USD (research results are in R)")
    parser.add_argument("--max-drawdown", type=float, default=0.20)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--yes", action="store_true", help="needed for a replay (many paid calls)")
    arguments = parser.parse_args(argv)
    init_logging(task_name="run_strategy_pipeline", log_to_file=False)
    repository = DatabaseRepository()
    if arguments.command == "report":
        log_report(arguments.currency, repository, MODE_REPLAY)
        log_report(arguments.currency, repository, MODE_LIVE)
        return 0

    load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env")
    api_key = os.environ.get("claude_api_key")
    if arguments.command != "evaluate" and not api_key:
        logger.error("claude_api_key is not set in .env")
        return 1
    service = StrategyPickService(repository, anthropic.Anthropic(api_key=api_key) if api_key else None,
                                  RiskBudget(arguments.capital, arguments.max_drawdown), model=arguments.model)

    if arguments.command == "evaluate":
        evaluated = sum(service.evaluate(row) for row in repository.get_strategy_picks(arguments.currency, evaluated=False))
        logger.info("%d picks evaluated", evaluated)
        return 0

    if arguments.command == "live":
        result = service.pick(arguments.currency, datetime.now(timezone.utc), MODE_LIVE, expiration=arguments.expiry)
        logger.info("%s", "not usable now (see the log above)" if result is None else f"{result.status}, choice {result.choice}, quantity {result.quantity}")
        return 0 if result is not None else 1

    if arguments.first_day is None or arguments.last_day is None:
        parser.error("replay needs --from and --to")
    days = []
    day = arguments.first_day
    while day <= arguments.last_day:
        days.append(day)
        day += timedelta(days=arguments.every_days)
    days = days[:arguments.max_calls]
    logger.info("Replay of up to %d instants, about %.2f USD each: at most %.2f USD", len(days), APPROXIMATE_COST_PER_CALL_USD,
                len(days) * APPROXIMATE_COST_PER_CALL_USD)
    if not arguments.yes:
        logger.error("add --yes to run the paid calls")
        return 1
    for day in days:
        try:
            result = service.pick(arguments.currency, datetime.combine(day, time(ENTRY_HOUR_UTC), tzinfo=timezone.utc), MODE_REPLAY)
        except BudgetExceeded as error:
            logger.error("Budget reached: %s", error)
            break
        if result is not None:
            logger.info("%s: %s, choice %s, cost %.4f USD%s", day, result.status, result.choice, result.cost_usd,
                        " (already stored)" if result.stored_before else "")
    evaluated = sum(service.evaluate(row) for row in repository.get_strategy_picks(arguments.currency, mode=MODE_REPLAY, evaluated=False))
    logger.info("%d picks evaluated", evaluated)
    log_report(arguments.currency, repository, MODE_REPLAY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
