"""
Daily historical backfill and backtest of the MenthorQ-style levels
(BTC, Deribit) from the local database. Read-only; renders no charts.

Usage (from repo root):
    .venv\\Scripts\\python -m scripts.backtest_menthorq_levels
    .venv\\Scripts\\python -m scripts.backtest_menthorq_levels --first-date 2026-08-26

For every date: levels at 23:59 UTC with open interest from 00:00 UTC of the
same date (the convention fitted on three MenthorQ charts), for the
"monthly" (primary) and "all" expiry universes; day-over-day attribution of
level changes to expiry settlements; next 1d / 3d realized volatility and
absolute return from hourly index samples; a fixed battery of predictive
tests; candidate alert thresholds with historical counts.

Writes to --output-directory (default outputs/menthorq_replicate/backtest,
gitignored): {currency}_daily_levels.csv / .json and
{currency}_backtest_analysis.json.
"""

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from coding.core.logging.logging_setup import init_logging
from coding.service.on_chain.menthorq_levels_backtest_service import MenthorqLevelsBacktestService

logger = logging.getLogger(__name__)

FIRST_SNAPSHOT_DATE = date(2026, 3, 15)


def build_parser() -> argparse.ArgumentParser:
    """Command-line arguments of the backtest."""
    parser = argparse.ArgumentParser(description="Daily backtest of the MenthorQ-style levels.")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--first-date", type=date.fromisoformat, default=FIRST_SNAPSHOT_DATE,
                        help="First UTC date (default: the first snapshot date, 2026-03-15)")
    parser.add_argument("--last-date", type=date.fromisoformat, default=None,
                        help="Last UTC date (default: yesterday, UTC)")
    parser.add_argument("--output-directory", type=Path,
                        default=Path("outputs") / "menthorq_replicate" / "backtest")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse arguments and run the backtest."""
    arguments = build_parser().parse_args(argv)
    last_date = arguments.last_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    init_logging(level="INFO", task_name="backtest_menthorq_levels")
    MenthorqLevelsBacktestService().run(
        currency=arguments.currency,
        first_date=arguments.first_date,
        last_date=last_date,
        output_directory=arguments.output_directory,
    )


if __name__ == "__main__":
    main()
