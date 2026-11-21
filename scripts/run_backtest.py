"""
Backtest simple option structures on the stored chain snapshots (research only: places no orders).

Usage (from repo root; the local database should be synced first: scripts/sync_from_vps.py):
    .venv\\Scripts\\python -m scripts.run_backtest --currency BTC --structure all --from 2026-03-20 --to 2026-10-31
    .venv\\Scripts\\python -m scripts.run_backtest --structure short_strangle --short-delta 0.10 --target-dte 14 --csv output/backtests/strangle.csv

One trade per entry day, held to expiry; buys at the ask, sells at the bid, Deribit fees, delivery price estimated from the index prices
of the last 30 minutes before settlement. PnL in USD for one coin per leg, trades independent (they overlap, no margin modelled).
Read coding/core/analytics/backtest/engine.py before trusting a number.
"""

import argparse
import csv
import logging
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from coding.core.analytics.backtest.structures import STRUCTURES, StructureParameters
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging
from coding.service.backtest.backtest_service import BacktestResult, BacktestService

logger = logging.getLogger(__name__)


def _money(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def log_result(result: BacktestResult) -> None:
    """Log the statistics of one structure."""
    summary = result.summary
    win_rate = "n/a" if summary.win_rate is None else f"{summary.win_rate:.0%}"
    logger.info("%-18s trades %3d | win rate %4s | total %9s | mean %7s | median %7s | worst %8s | best %8s | max drawdown %8s | "
                "mean premium %7s | mean fees %5s", result.structure, summary.trades, win_rate, _money(summary.total_pnl_usd), _money(summary.mean_pnl_usd),
                _money(summary.median_pnl_usd), _money(summary.worst_pnl_usd), _money(summary.best_pnl_usd), _money(summary.max_drawdown_usd),
                _money(summary.mean_premium_usd), _money(summary.mean_fees_usd))
    logger.info("%-18s skipped %s", "", summary.skipped or "nothing")
    logger.info("%-18s by month %s", "", {month: round(value) for month, value in sorted(summary.pnl_by_month.items())})


def write_csv(path: Path, result: BacktestResult) -> None:
    """One row per trade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["structure", "entry_time_utc", "expiration", "spot_at_entry", "settlement_price", "legs", "premium_usd",
                         "payoff_usd", "trading_fees_usd", "delivery_fees_usd", "pnl_usd"])
        for trade in result.trades:
            legs = "; ".join(f"{leg.action} {leg.instrument_name} @ {leg.entry_price_usd:.0f}" for leg in trade.legs)
            writer.writerow([trade.structure, trade.entry_time.isoformat(), trade.expiration, f"{trade.spot_at_entry:.2f}",
                             f"{trade.settlement_price:.2f}", legs, f"{trade.premium_usd:.2f}", f"{trade.payoff_usd:.2f}",
                             f"{trade.trading_fees_usd:.2f}", f"{trade.delivery_fees_usd:.2f}", f"{trade.pnl_usd:.2f}"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the chosen structure (or all) and log the statistics."""
    parser = argparse.ArgumentParser(description="Backtest of simple option structures (research only)")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--structure", default="all", choices=["all", *STRUCTURES])
    parser.add_argument("--from", dest="first_day", type=date.fromisoformat, default=date(2026, 3, 20))
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, default=date(2026, 10, 31))
    parser.add_argument("--entry-hour", type=int, default=9, help="entry time, hour of the day in UTC")
    parser.add_argument("--target-dte", type=float, default=7.0, help="days to expiry to aim for")
    parser.add_argument("--min-dte", type=float, default=2.0)
    parser.add_argument("--short-delta", type=float, default=0.16)
    parser.add_argument("--wing", type=float, default=0.05, help="wing distance as a fraction of spot")
    parser.add_argument("--long-delta", type=float, default=0.30)
    parser.add_argument("--csv", type=Path, default=None, help="write the trades (one structure only) to this file")
    arguments = parser.parse_args(argv)
    init_logging(task_name="run_backtest", log_to_file=False)
    if arguments.csv is not None and arguments.structure == "all":
        parser.error("--csv needs one --structure")

    service = BacktestService(DatabaseRepository())
    parameters = StructureParameters(arguments.short_delta, arguments.wing, arguments.long_delta)
    for structure in (STRUCTURES if arguments.structure == "all" else (arguments.structure,)):
        result = service.run(arguments.currency, structure, arguments.first_day, arguments.last_day, arguments.entry_hour,
                             arguments.target_dte, arguments.min_dte, parameters)
        log_result(result)
        if arguments.csv is not None:
            write_csv(arguments.csv, result)
            logger.info("Wrote %s", arguments.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
