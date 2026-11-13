"""
Flow-based dealer levels of one expiry next to the open-interest based levels.

Usage (from repo root; sync the local database first for current trades and chain: scripts/sync_from_vps.py):
    .venv\\Scripts\\python -m scripts.dealer_flow_levels --currency BTC --expiry 27NOV26

Reads the local database and writes a PNG into outputs/dealer_flow_levels/ (git-ignored). Reads only; places no orders.
"""

import argparse
import logging
from pathlib import Path
from typing import Optional, Sequence

from coding.core.analytics.option_flow.dealer_levels_figure import build_dealer_levels_figure
from coding.core.logging.logging_setup import init_logging
from coding.service.option_flow.dealer_flow_levels_service import DealerFlowLevelsService

logger = logging.getLogger(__name__)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "outputs" / "dealer_flow_levels"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Compute both views of one expiry, log the numbers and write the chart."""
    parser = argparse.ArgumentParser(description="Flow-based dealer levels of one expiry")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--expiry", required=True, help="Deribit label, e.g. 27NOV26")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    init_logging(task_name="dealer_flow_levels", log_to_file=False)

    levels = DealerFlowLevelsService().levels(arguments.currency, arguments.expiry)
    logger.info("%s %s: index %.0f, chain %s, flow since %s (%d trades)", levels.currency, levels.expiry, levels.index_price,
                levels.chain_captured_at.isoformat(), levels.flow_start.isoformat(), levels.trades_used)
    logger.info("flow-based: call wall %s, put wall %s, gamma flip %s, total GEX %.2fM", levels.flow_call_wall,
                levels.flow_put_wall, levels.flow_gamma_flip, levels.total_flow_gex / 1e6)
    logger.info("OI-based:   call wall %s, put wall %s, gamma flip %s, total GEX %s", levels.oi_call_wall, levels.oi_put_wall,
                levels.oi_gamma_flip, "not computed" if levels.total_oi_gex is None else f"{levels.total_oi_gex / 1e6:.2f}M")
    logger.info("plausibility check %s (%s): %d of %d options exceed their open interest, trade coverage %.1f%%",
                "PASSED" if levels.gate_passed else "NOT PASSED", levels.gate_reason, levels.violations, levels.options_checked,
                levels.coverage * 100)
    arguments.out.mkdir(parents=True, exist_ok=True)
    path = arguments.out / f"{levels.currency}_dealer_flow_levels_{levels.expiry}.png"
    figure = build_dealer_levels_figure(levels)
    figure.write_image(path, width=1500, height=figure.layout.height)
    logger.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
