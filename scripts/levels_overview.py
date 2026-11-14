"""
All price levels of the different sources on one chart (MenthorQ replica, dealer GEX open-interest and flow-based, option flow,
liquidation estimate), with bands where at least three sources agree.

Usage (from repo root; sync the local database first: scripts/sync_from_vps.py):
    .venv\\Scripts\\python -m scripts.levels_overview --currency BTC
    .venv\\Scripts\\python -m scripts.levels_overview --currency BTC --expiry 27NOV26 --band-percent 1.0

Writes a PNG and an HTML page into outputs/levels_overview/ (git-ignored). Reads only; places no orders.
"""

import argparse
import logging
from pathlib import Path
from typing import Optional, Sequence

from coding.core.analytics.option_flow.level_overview import build_level_overview_figure, find_confluences
from coding.core.logging.logging_setup import init_logging
from coding.service.option_flow.level_overview_service import LevelOverviewService

logger = logging.getLogger(__name__)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "outputs" / "levels_overview"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Collect the levels, log them with the confluences and write the chart."""
    parser = argparse.ArgumentParser(description="All levels of all sources on one chart")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--expiry", default=None, help="expiry for the dealer levels, default: the nearest monthly")
    parser.add_argument("--flow-hours", type=int, default=24)
    parser.add_argument("--band-percent", type=float, default=1.0, help="width of a confluence band in percent of the spot")
    parser.add_argument("--minimum-sources", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    init_logging(task_name="levels_overview", log_to_file=False)

    overview = LevelOverviewService().overview(arguments.currency, arguments.expiry, arguments.flow_hours)
    for mark in sorted(overview.marks, key=lambda item: -item.price):
        logger.info("%10s  %-46s %s", f"{mark.price:,.0f}", mark.source, mark.name)
    confluences = find_confluences(overview.marks, arguments.band_percent / 100 * overview.spot, arguments.minimum_sources)
    for band in confluences:
        logger.info("CONFLUENCE %s-%s (%d sources): %s", f"{band.low:,.0f}", f"{band.high:,.0f}", len(band.sources),
                    "; ".join(f"{mark.source.split(' (')[0]}: {mark.name}" for mark in band.marks))
    if not confluences:
        logger.info("No band of %.1f%% holds levels of %d different sources", arguments.band_percent, arguments.minimum_sources)
    for note in overview.notes:
        logger.info("note: %s", note)

    footer = (f"Spot {overview.spot:,.0f}. Shaded band = levels of at least {arguments.minimum_sources} different sources within "
              f"{arguments.band_percent:.1f}% of the spot. " + " ".join(f"{note}." for note in overview.notes) +
              " A coincidence of methods is not a proof: several sources use the same open interest.")
    figure = build_level_overview_figure(overview.marks, overview.spot, confluences,
                                         f"{overview.currency} levels of all sources (dealer expiry {overview.expiry})", footer)
    arguments.out.mkdir(parents=True, exist_ok=True)
    stem = arguments.out / f"{overview.currency}_levels_overview"
    figure.write_html(stem.with_suffix(".html"), include_plotlyjs=True)
    figure.write_image(stem.with_suffix(".png"), width=1500, height=figure.layout.height)
    logger.info("Wrote %s and .html", stem.with_suffix(".png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
