r"""
Compare the price models of the strategy ranking on past data (criteria: documentation/research/skew_model_test_2026-11-25.md). Read only, free.

Usage (from repo root; sync the local database first):
    .venv\Scripts\python -m scripts.compare_price_models collect --currency BTC --from 2026-03-15 --to 2026-11-24
    .venv\Scripts\python -m scripts.compare_price_models collect --currency ETH --from 2026-03-15 --to 2026-11-24
    .venv\Scripts\python -m scripts.compare_price_models report

collect writes output/research/model_comparison/<CURRENCY>.pkl (a local file made and read only by this script); report reads the files there.
"""

import argparse
import logging
import pickle
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from coding.core.analytics.validation.model_comparison import report_lines
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging
from coding.service.candidates.model_comparison_service import ModelComparisonService

logger = logging.getLogger(__name__)

OUTPUT_FOLDER = Path(__file__).parents[1] / "output" / "research" / "model_comparison"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run collect or report."""
    parser = argparse.ArgumentParser(description="Compare price models of the strategy ranking on past data")
    parser.add_argument("command", choices=["collect", "report"])
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--from", dest="first_day", type=date.fromisoformat, default=date(2026, 3, 15))
    parser.add_argument("--to", dest="last_day", type=date.fromisoformat, default=None)
    arguments = parser.parse_args(argv)
    init_logging(task_name="compare_price_models", log_to_file=False)
    if arguments.command == "collect":
        if arguments.last_day is None:
            parser.error("collect needs --to")
        entries, skipped = ModelComparisonService(DatabaseRepository()).collect(arguments.currency, arguments.first_day, arguments.last_day)
        OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FOLDER / f"{arguments.currency}.pkl", "wb") as handle:
            pickle.dump({"entries": entries, "skipped": dict(skipped), "range": (arguments.first_day, arguments.last_day)}, handle)
        logger.info("%s: %d entries; looks without an entry: %s", arguments.currency, len(entries), dict(skipped))
        return 0
    entries = []
    ranges = {}
    for path in sorted(OUTPUT_FOLDER.glob("*.pkl")):
        with open(path, "rb") as handle:
            stored = pickle.load(handle)
        ranges[path.stem] = stored.get("range")
        entries += stored["entries"]
        logger.info("%s: %d entries; looks without an entry: %s", path.stem, len(stored["entries"]), stored["skipped"])
    if set(ranges) != {"BTC", "ETH"} or len(set(ranges.values())) != 1 or None in ranges.values():
        logger.error("collect BTC and ETH over the same range first (found: %s)", ranges)
        return 1
    for line in report_lines(entries):
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
