"""
Evidence report: the evidence of every source, the price map, reference lines and caveats for one currency. (Until 2026-11-21 it also
scored scenarios and labelled confidence; that was removed because the scores were never calibrated.)

Usage (from repo root; sync the local database first for current data: scripts/sync_from_vps.py):
    .venv\\Scripts\\python -m scripts.decision_maker --currency BTC
    .venv\\Scripts\\python -m scripts.decision_maker --currency ETH --save --json output/decision_maker/eth.json

Reads the local database and logs a readable summary. ``--save`` stores the report in ``decision_snapshots`` (migration
036; never rewritten). ``--json`` writes the full report. Places no orders; the output is reference material, not a forecast.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional, Sequence

from coding.core.logging.logging_setup import init_logging
from coding.service.decision.decision_maker_service import DecisionMakerService, summary_lines

logger = logging.getLogger(__name__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Build the report, log its summary and optionally store it or write it as JSON."""
    parser = argparse.ArgumentParser(description="Evidence report: levels, evidence and caveats (no scoring)")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--save", action="store_true", help="store the report in decision_snapshots")
    parser.add_argument("--json", type=Path, default=None, help="write the full report to this JSON file")
    arguments = parser.parse_args(argv)
    init_logging(task_name="decision_maker", log_to_file=False)

    service = DecisionMakerService()
    report = service.build_report(arguments.currency)
    for line in summary_lines(report):
        logger.info(line)
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(report.to_dict(), indent=1, allow_nan=False), encoding="utf-8")
        logger.info("Wrote %s", arguments.json)
    if arguments.save:
        stored = service.save_snapshot(report)
        logger.info("Snapshot %s (%s, %s)", "stored" if stored else "already stored, kept unchanged", report.currency,
                    report.as_of.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
