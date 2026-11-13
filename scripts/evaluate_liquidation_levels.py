"""
Forward test of the estimated liquidation levels against REAL liquidations recorded on the VPS.

Usage (from repo root, after scripts/sync_from_vps.py has copied the recorder tables):
    .venv\\Scripts\\python -m scripts.evaluate_liquidation_levels --days 7

Reads ``liquidation_events`` (OKX BTC-USDT-SWAP) and ``liquidation_heatmap_snapshots`` from the local database. Each
real liquidation is scored against the latest level snapshot computed BEFORE it (no hindsight): the percentile of the
snapshot's intensity at the event's price among the prices near the reference price (0.5 = no better than a random
price, 1.0 = at the strongest level), plus the share of events in the top 10 % of intensity (random: 10 %). The same
events are also scored against a snapshot that was already 24 hours old: if the current snapshot is not clearly
better, the levels tell nothing. Reads only; places no orders. Method: documentation/liquidation_heatmap_research.md.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from coding.core.analytics.liquidation_heatmap.level_evaluation import score_events, summarise_scores
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

logger = logging.getLogger(__name__)

VENUE, INSTRUMENT, SYMBOL = "okx", "BTC-USDT-SWAP", "BTCUSDT"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "liquidation_heatmap"


def format_number(value: Optional[float], digits: int = 2) -> str:
    """A number with fixed decimals, or n/a."""
    return "n/a" if value is None else f"{value:.{digits}f}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the evaluation; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Score real liquidations against the stored level snapshots.")
    parser.add_argument("--days", type=int, default=7, help="how many days back from now (default 7)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    arguments = parser.parse_args(argv)
    init_logging(task_name="evaluate_liquidation_levels", log_to_file=False)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=arguments.days)
    repository = DatabaseRepository()
    events = repository.get_liquidation_events_between(VENUE, INSTRUMENT, start, end)
    snapshots = repository.get_liquidation_heatmap_snapshots_between(SYMBOL, start - timedelta(days=2), end)
    logger.info("Events: %d, snapshots: %d (%s .. %s UTC)", len(events), len(snapshots), start, end)
    if not events or not snapshots:
        logger.error("Nothing to evaluate: sync the VPS tables first and check that the recorder runs")
        return 1

    scored = score_events(events, snapshots)
    summary = summarise_scores(scored)
    print(f"\nReal liquidations {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC: {len(events)} events, "
          f"{len(scored)} scored ({len(events) - len(scored)} without a recent snapshot or outside +-5 % of price)")
    print("Chance level: mean percentile 0.50, top-decile share 0.10.\n")
    print(f"{'side':>6} {'events':>7} | {'mean pct':>8} {'USD-wtd':>8} {'top 10%':>8} | "
          f"{'control events':>14} {'ctrl mean':>9} {'ctrl top 10%':>12}")
    for side, values in summary.items():
        print(f"{side:>6} {values['events']:>7} | {format_number(values['mean_percentile']):>8} "
              f"{format_number(values['usd_weighted_mean']):>8} {format_number(values['top_decile_share']):>8} | "
              f"{values['control_events']:>14} {format_number(values['control_mean_percentile']):>9} "
              f"{format_number(values['control_top_decile_share']):>12}")
    arguments.out.mkdir(parents=True, exist_ok=True)
    (arguments.out / "level_evaluation_summary.json").write_text(
        json.dumps({"start": start.isoformat(), "end": end.isoformat(), "events": len(events), "scored": len(scored),
                    "summary": summary}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
