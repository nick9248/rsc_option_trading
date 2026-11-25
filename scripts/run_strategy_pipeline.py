"""
The strategy pipeline: facts and candidates by code, the model chooses among them, and the choice is settled and compared with the baselines
(a replay pick is sized from a loss budget, a live pick is stored per 1 coin per leg). Research only: places no orders. Every call is paid (about 0.08 USD).

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
from coding.service.strategy_pipeline.live_pick_runner import UNIT_QUANTITY
from coding.service.strategy_pipeline.strategy_pick_service import (
    DEFAULT_RESEARCH_CAPITAL_USD, METHOD_VERSION, MODE_LIVE, MODE_REPLAY, StrategyPickService)

logger = logging.getLogger(__name__)
ENTRY_HOUR_UTC = 9
APPROXIMATE_COST_PER_CALL_USD = 0.08


def _number(value: Optional[float], digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def log_report(currency: str, repository: DatabaseRepository, mode: str, method_version: str) -> None:
    """Log the comparison of the model with the baselines over the picks of one method version."""
    report = summarize_picks(repository.get_strategy_picks(currency, mode=mode, method_version=method_version))
    logger.info("%s: %d evaluated %s picks (no trade in %s); %d refused or unusable answers, %d picks waiting for their expiry and %d expiry-days with nothing to offer are not in the comparison",
                method_version, report.picks, mode, "n/a" if report.no_trade_share is None else f"{report.no_trade_share:.0%}", report.unusable_answers,
                report.not_yet_evaluated, report.nothing_offered)
    logger.info("mean R (result in units of the maximum loss): model %s | baseline top-1 %s | shortlist average %s | all candidates average %s",
                _number(report.mean_model_r), _number(report.mean_baseline_r), _number(report.mean_shortlist_r), _number(report.mean_all_r))
    logger.info("model minus baseline %s (t %s), minus shortlist average %s (t %s), minus all candidates %s (t %s); beat the baseline on %s",
                _number(report.model_minus_baseline), _number(report.model_minus_baseline_t, 2), _number(report.model_minus_shortlist),
                _number(report.model_minus_shortlist_t, 2), _number(report.model_minus_all), _number(report.model_minus_all_t, 2),
                "n/a" if report.model_beat_baseline_share is None else f"{report.model_beat_baseline_share:.0%}")


def evaluate_all(service: StrategyPickService, currency: str, mode: Optional[str] = None) -> int:
    """Evaluate every stored pick that can be; one failing row is logged and does not stop the others. Returns how many were stored."""
    stored = 0
    for row in service.repository.get_strategy_picks(currency, mode=mode, evaluated=False):                    # every method version: no model call
        try:
            stored += bool(service.evaluate(row))
        except Exception:
            logger.exception("Evaluation of pick %s failed", row["id"])
    return stored


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
    parser.add_argument("--capital", type=float, default=DEFAULT_RESEARCH_CAPITAL_USD, help="replay: notional account in USD (research results are in R; a live pick is per coin)")
    parser.add_argument("--max-drawdown", type=float, default=0.20)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--method-version", action="append", help="report: the method version(s) to show (default: every version that has stored picks)")
    parser.add_argument("--yes", action="store_true", help="needed for a replay (many paid calls)")
    arguments = parser.parse_args(argv)
    init_logging(task_name="run_strategy_pipeline", log_to_file=False)
    repository = DatabaseRepository()
    if arguments.command == "report":
        stored_versions = sorted({row["method_version"] for row in repository.get_strategy_picks(arguments.currency)})
        for version in arguments.method_version or stored_versions:
            log_report(arguments.currency, repository, MODE_REPLAY, version)
            log_report(arguments.currency, repository, MODE_LIVE, version)
        return 0

    load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env")
    api_key = os.environ.get("claude_api_key")
    if arguments.command != "evaluate" and not api_key:
        logger.error("claude_api_key is not set in .env")
        return 1
    service = StrategyPickService(repository, anthropic.Anthropic(api_key=api_key) if api_key else None,
                                  RiskBudget(arguments.capital, arguments.max_drawdown), model=arguments.model,
                                  unit_quantity=UNIT_QUANTITY if arguments.command == "live" else None)       # a live pick is per coin, as in the Analysis tab

    if arguments.command == "evaluate":
        logger.info("%d picks evaluated", evaluate_all(service, arguments.currency))
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
    logger.info("Replay of %d instants, at most %d paid calls (stored instants are free), about %.2f USD each: at most %.2f USD", len(days),
                arguments.max_calls, APPROXIMATE_COST_PER_CALL_USD, arguments.max_calls * APPROXIMATE_COST_PER_CALL_USD)
    if not arguments.yes:
        logger.error("add --yes to run the paid calls")
        return 1
    paid_calls = 0
    for day in days:
        if paid_calls >= arguments.max_calls:
            logger.info("Stopped at the cap of %d paid calls", arguments.max_calls)
            break
        try:
            result = service.pick(arguments.currency, datetime.combine(day, time(ENTRY_HOUR_UTC), tzinfo=timezone.utc), MODE_REPLAY)
        except BudgetExceeded as error:
            logger.error("Budget reached: %s", error)
            break
        except anthropic.APIError as error:
            logger.error("%s: the call failed (not billed), continuing: %s", day, error)
            continue
        if result is not None:
            paid_calls += 0 if result.stored_before else 1
            logger.info("%s: %s, choice %s, cost %.4f USD%s", day, result.status, result.choice, result.cost_usd,
                        " (already stored)" if result.stored_before else "")
    logger.info("%d picks evaluated", evaluate_all(service, arguments.currency, MODE_REPLAY))
    log_report(arguments.currency, repository, MODE_REPLAY, METHOD_VERSION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
