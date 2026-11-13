"""
Draw the buy/sell volume per strike of the Deribit option trades (the Laevitas-style chart).

Usage (from repo root; sync the local database first for the latest trades: scripts/sync_from_vps.py):
    .venv\\Scripts\\python -m scripts.option_flow_by_strike --hours 24
    .venv\\Scripts\\python -m scripts.option_flow_by_strike --hours 24 --expiry 27NOV26 --currency BTC

Reads the stored trades (historical_trades) and writes a PNG and a JSON into outputs/option_flow/ (git-ignored). Reads
only; places no orders.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from coding.core.analytics.option_flow.strike_flow import SERIES
from coding.core.logging.logging_setup import init_logging
from coding.service.option_flow.option_flow_service import OptionFlowService

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "option_flow"


def parse_utc(text: str) -> datetime:
    """``2026-11-12T14:19Z`` or ``...+00:00`` as a UTC-aware datetime."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} has no time zone (use ...Z or +00:00)")
    return parsed.astimezone(timezone.utc)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Generate the chart; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Buy/sell volume per strike of Deribit option trades.")
    parser.add_argument("--currency", default="BTC", choices=("BTC", "ETH"))
    parser.add_argument("--hours", type=int, default=24, help="window length in hours (default 24)")
    parser.add_argument("--expiry", default=None, help="one expiration, e.g. 27NOV26 (default: all)")
    parser.add_argument("--end", type=parse_utc, default=None, help="end of the window, UTC, e.g. 2026-11-12T14:19Z (default: now)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    arguments = parser.parse_args(argv)
    init_logging(task_name="option_flow_by_strike", log_to_file=False)

    report = OptionFlowService().generate_chart(arguments.out, arguments.currency, arguments.hours, arguments.expiry,
                                                arguments.end)
    flow = report.flow
    if not flow.strikes:
        logger.error("No trades in the window (%s .. %s); sync the database first", report.window_start, report.window_end)
        return 1
    logger.info("%d trades used, %d skipped, %d strikes; newest stored trade %s UTC", flow.trades_used,
                flow.trades_skipped, len(flow.strikes), report.newest_trade)
    for series in SERIES:
        total = sum(flow.cell(strike, series.key).notional_usd for strike in flow.strikes)
        contracts = sum(flow.cell(strike, series.key).contracts for strike in flow.strikes)
        logger.info("  %-18s %10.1f BTC  $%8.1fM", series.label, contracts, total / 1e6)
    for path in report.files:
        logger.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
