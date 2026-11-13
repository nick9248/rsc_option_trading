"""
Research script: estimate the Binance BTCUSDT liquidation heatmap for a window and draw it, to
compare with the Coinglass chart of the same window.

Usage (from repo root):
    .venv\\Scripts\\python -m scripts.research_liquidation_heatmap --end 2026-11-13T14:20Z --hours 24
    .venv\\Scripts\\python -m scripts.research_liquidation_heatmap --hours 12 --tiers 10,25,50,100 --weights 0.1,0.3,0.3,0.3

The window is ``[end - hours, end]`` in UTC (default end: the latest complete 5-minute step).
The model state is warmed up over ``--warmup-days`` before the window. Reads Binance public data
(from the PC, never the VPS), writes a PNG and a JSON (settings, data notes, the strongest levels
at the end of the window) into ``outputs/liquidation_heatmap/`` (git-ignored). Places no orders.
Method and caveats: documentation/liquidation_heatmap_research.md.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import (
    STEP,
    HeatmapResult,
    HeatmapSettings,
    simulate_heatmap,
)
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_renderer import render_heatmap
from coding.core.logging.logging_setup import init_logging
from coding.service.liquidation_heatmap.binance_history_loader import BinanceHistoryLoader

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "liquidation_heatmap"
TOP_LEVELS = 12


def parse_utc(text: str) -> datetime:
    """``2026-11-13T14:20Z`` or ``2026-11-13T14:20:00+00:00`` as a UTC-aware datetime."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} has no time zone (use ...Z or +00:00)")
    return parsed.astimezone(timezone.utc)


def floor_to_step(instant: datetime) -> datetime:
    """``instant`` rounded down to the 5-minute grid."""
    return instant.replace(minute=instant.minute - instant.minute % 5, second=0, microsecond=0)


def parse_numbers(text: str, kind) -> list:
    """``25,50,100`` as a list of ``kind`` values."""
    return [kind(part) for part in text.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    """Command-line arguments."""
    parser = argparse.ArgumentParser(description="Estimate and draw the BTCUSDT liquidation heatmap (research).")
    parser.add_argument("--end", type=parse_utc, default=None, help="window end, UTC (default: now)")
    parser.add_argument("--hours", type=int, default=24, help="window length in hours (default 24)")
    parser.add_argument("--warmup-days", type=int, default=14, help="days used to build the state before the window")
    parser.add_argument("--bin-size", type=float, default=HeatmapSettings.bin_size_usd, help="price bin in USD")
    parser.add_argument("--tiers", default=",".join(str(tier) for tier in HeatmapSettings.leverage_tiers),
                        help="leverage tiers, comma separated")
    parser.add_argument("--weights", default=None, help="tier weights summing to 1 (default: equal)")
    parser.add_argument("--maintenance-margin", type=float, default=HeatmapSettings.maintenance_margin_rate)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIRECTORY, help="output folder")
    return parser


def strongest_levels(result: HeatmapResult, count: int = TOP_LEVELS) -> List[dict]:
    """The ``count`` strongest price bins at the last recorded step that has data."""
    rows = np.where(np.isfinite(result.long_btc).any(axis=1))[0]
    if len(rows) == 0:
        return []
    row = rows[-1]
    long_btc, short_btc = result.long_btc[row], result.short_btc[row]
    usd = (long_btc + short_btc) * result.bin_centers
    levels = []
    for column in np.argsort(usd)[::-1][:count]:
        if usd[column] <= 0:
            break
        levels.append({"price": float(result.bin_centers[column]), "usd": float(usd[column]),
                       "side": "long" if long_btc[column] >= short_btc[column] else "short"})
    return levels


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the research estimate; returns the process exit code."""
    arguments = build_parser().parse_args(argv)
    init_logging(task_name="research_liquidation_heatmap", log_to_file=False)
    tiers = parse_numbers(arguments.tiers, int)
    weights = parse_numbers(arguments.weights, float) if arguments.weights else [1 / len(tiers)] * len(tiers)
    settings = HeatmapSettings(bin_size_usd=arguments.bin_size, leverage_tiers=tuple(tiers),
                               leverage_weights=tuple(weights), maintenance_margin_rate=arguments.maintenance_margin)

    end = floor_to_step(arguments.end or datetime.now(timezone.utc))
    window_start = end - timedelta(hours=arguments.hours)
    load_start = window_start - timedelta(days=arguments.warmup_days)
    loader = BinanceHistoryLoader(arguments.out / "cache")

    logger.info("Loading %s candles and open interest %s .. %s (UTC)", SYMBOL, load_start, end)
    candles = loader.load_candles(SYMBOL, load_start, end)
    open_interest, notes = loader.load_open_interest(SYMBOL, load_start, end + STEP)
    for note in notes:
        logger.warning(note)
    logger.info("Loaded %d candles and %d open-interest samples", len(candles), len(open_interest))

    result = simulate_heatmap(candles, open_interest, settings, record_from=window_start)
    for note in result.notes:
        logger.warning(note)
    window_candles = [candle for candle in candles if candle.open_time >= window_start]

    arguments.out.mkdir(parents=True, exist_ok=True)
    stem = f"heatmap_{end.strftime('%Y%m%dT%H%MZ')}_{arguments.hours}h"
    footer = (f"Model v0 (estimate, not Coinglass): tiers {list(settings.leverage_tiers)} weights "
              f"{[round(weight, 3) for weight in settings.leverage_weights]}, bin {settings.bin_size_usd:g} USD, "
              f"warm-up {arguments.warmup_days} d. Window {window_start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC.")
    png = render_heatmap(result, window_candles, arguments.out / f"{stem}.png",
                         f"Estimated Binance {SYMBOL} liquidation heatmap ({arguments.hours} h, UTC)", footer)
    summary = {
        "symbol": SYMBOL, "window_start_utc": window_start.isoformat(), "window_end_utc": end.isoformat(),
        "warmup_days": arguments.warmup_days,
        "settings": {"bin_size_usd": settings.bin_size_usd, "leverage_tiers": list(settings.leverage_tiers),
                     "leverage_weights": list(settings.leverage_weights),
                     "maintenance_margin_rate": settings.maintenance_margin_rate},
        "data_notes": notes + result.notes,
        "strongest_levels_at_window_end": strongest_levels(result),
    }
    (arguments.out / f"{stem}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Wrote %s", png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
