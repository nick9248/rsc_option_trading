"""
Research backtest: does the estimated liquidation heatmap agree with REAL liquidations?

Usage (from repo root):
    .venv\\Scripts\\python -m scripts.backtest_liquidation_heatmap

By default it uses every day CryptoQuant serves; the Basic plan gives only the last 30 days, which is a small
sample (see the research notes).

For each UTC day the heatmap state known at the start of the day (volume-based model, positions created before
it) is swept by the day's real low and high, and the liquidation volume the levels give is compared with
CryptoQuant's real Binance BTC/USDT daily liquidations (rank correlation), next to a naive predictor (size of
the move times volume) and with the heatmap's contribution beyond that naive predictor (partial rank
correlation). Configurations are fixed in CONFIGURATIONS before the run and are not tuned on this data.
Reads Binance public data and CryptoQuant (key from .env, never printed). Writes a JSON and a CSV into
outputs/liquidation_heatmap/ (git-ignored). Places no orders. Method and caveats:
documentation/liquidation_heatmap_research.md.
"""

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from coding.core.analytics.liquidation_heatmap.liquidation_backtest import (
    DayRow,
    daily_price_stats,
    partial_spearman,
    spearman,
    swept_levels_usd,
)
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, HeatmapSettings, simulate_heatmap
from coding.core.logging.logging_setup import init_logging
from coding.service.liquidation_heatmap.binance_history_loader import BinanceHistoryLoader
from coding.service.liquidation_heatmap.cryptoquant_liquidation_client import load_daily_liquidations, read_token

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "liquidation_heatmap"
WARMUP_DAYS = 30


def _tiers(low: int, high: int, step: int):
    tiers = tuple(range(low, high + 1, step))
    return tiers, tuple(1 / len(tiers) for _ in tiers)


def _volume_settings(low: int, high: int, step: int, half_life: Optional[float]) -> HeatmapSettings:
    tiers, weights = _tiers(low, high, step)
    return HeatmapSettings(bin_size_usd=100.0, leverage_tiers=tiers, leverage_weights=weights,
                           position_half_life_hours=half_life, creation_source="volume")


# Fixed before the run: the two best volume configurations of the screenshot comparison, and the first one
# without turnover. Not tuned on the liquidation data.
CONFIGURATIONS = {
    "volume 25-100x/5, half-life 48h": _volume_settings(25, 100, 5, 48.0),
    "volume 10-100x/10, half-life 48h": _volume_settings(10, 100, 10, 48.0),
    "volume 25-100x/5, no turnover": _volume_settings(25, 100, 5, None),
}


def report_line(text: str) -> None:
    """One line of the result table (logged, so it goes to the configured log handlers)."""
    logger.info("%s", text)


def parse_day(text: str) -> date:
    """``2023-01-01`` as a date."""
    return date.fromisoformat(text)


def build_day_rows(settings: HeatmapSettings, candles, prices, real: Dict[date, tuple],
                   state_lag_days: int = 0) -> List[DayRow]:
    """
    The day rows of one configuration (days with complete candles and real liquidations only).

    ``state_lag_days`` is for the placebo check: the heatmap state is taken that many days EARLIER than the
    start of the day (0 = the state at the start of the day, the real test), so a stale state shows whether
    the current structure of the levels matters.
    """
    days = sorted(day for day in prices if day in real and day - timedelta(days=1) in prices)
    starts = {day: datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - STEP - timedelta(days=state_lag_days)
              for day in days}
    result = simulate_heatmap(candles, {}, settings, record_from=candles[0].open_time, record_at=list(starts.values()))
    row_of = {moment: index for index, moment in enumerate(result.times)}
    rows = []
    for day in days:
        index = row_of.get(starts[day])
        if index is None or not np.isfinite(result.long_btc[index]).any():
            continue
        today, yesterday = prices[day], prices[day - timedelta(days=1)]
        long_usd, short_usd = swept_levels_usd(result.long_btc[index], result.short_btc[index], result.bin_centers,
                                               today.open_price, today.low, today.high)
        rows.append(DayRow(
            day=day, predicted_long_usd=long_usd, predicted_short_usd=short_usd,
            drop_percent=max(0.0, (today.open_price - today.low) / today.open_price * 100),
            rise_percent=max(0.0, (today.high - today.open_price) / today.open_price * 100),
            volume_usd=today.volume_usd, previous_volume_usd=yesterday.volume_usd,
            real_long_usd=real[day][0], real_short_usd=real[day][1]))
    return rows


def summarise(rows: Sequence[DayRow]) -> Dict[str, dict]:
    """Rank correlations of the heatmap and of the naive predictors with the real liquidations, per side."""
    summary = {}
    for side in ("long", "short"):
        used = [row for row in rows if (row.real_long_usd if side == "long" else row.real_short_usd) > 0]
        real = [row.real_long_usd if side == "long" else row.real_short_usd for row in used]
        predicted = [row.predicted_long_usd if side == "long" else row.predicted_short_usd for row in used]
        move = [row.drop_percent if side == "long" else row.rise_percent for row in used]
        naive_today = [m * row.volume_usd for m, row in zip(move, used)]
        naive_before = [m * row.previous_volume_usd for m, row in zip(move, used)]
        rho, p_value = spearman(predicted, real)
        partial, partial_p = partial_spearman(predicted, real, naive_today)
        summary[side] = {
            "days": len(used),
            "heatmap_rho": rho, "heatmap_p": p_value,
            "naive_same_day_rho": spearman(naive_today, real)[0],
            "naive_previous_day_rho": spearman(naive_before, real)[0],
            "heatmap_partial_rho_given_naive_same_day": partial, "partial_p": partial_p,
        }
    return summary


def format_value(value: Optional[float]) -> str:
    """A correlation with sign, or n/a."""
    return "n/a" if value is None else f"{value:+.2f}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the backtest; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Backtest the liquidation heatmap against real liquidations.")
    parser.add_argument("--start", type=parse_day, default=None,
                        help="first day (UTC); default: the first day CryptoQuant serves (the plan allows 30 days)")
    parser.add_argument("--end", type=parse_day, default=None, help="last day (UTC); default: the last day served")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    arguments = parser.parse_args(argv)
    init_logging(task_name="backtest_liquidation_heatmap", log_to_file=False)

    real = load_daily_liquidations(read_token(), arguments.start and arguments.start - timedelta(days=1), arguments.end)
    if not real:
        raise SystemExit("CryptoQuant returned no days")
    logger.info("Real liquidations: %d days (%s .. %s)", len(real), min(real), max(real))
    arguments.start = arguments.start or min(real)
    arguments.end = arguments.end or max(real)
    load_start = datetime(arguments.start.year, arguments.start.month, arguments.start.day,
                          tzinfo=timezone.utc) - timedelta(days=WARMUP_DAYS)
    load_end = datetime(arguments.end.year, arguments.end.month, arguments.end.day, tzinfo=timezone.utc) + timedelta(days=1)
    candles = BinanceHistoryLoader(arguments.out / "cache").load_candles_long(SYMBOL, load_start, load_end)
    logger.info("Candles: %d (%s .. %s)", len(candles), candles[0].open_time, candles[-1].open_time)
    prices = daily_price_stats(candles)

    arguments.out.mkdir(parents=True, exist_ok=True)
    report = {"start": arguments.start.isoformat(), "end": arguments.end.isoformat(), "configurations": {}}
    for name, settings in CONFIGURATIONS.items():
        logger.info("Simulating: %s", name)
        rows = build_day_rows(settings, candles, prices, real)
        by_period = {"all": rows}
        for year in sorted({row.day.year for row in rows}):
            by_period[str(year)] = [row for row in rows if row.day.year == year]
        report["configurations"][name] = {period: summarise(period_rows) for period, period_rows in by_period.items()}
        report_line(f"\n== {name}: {len(rows)} days")
        report_line(f"{'period':>7} {'side':>5} {'days':>5} | {'heatmap':>8} {'naive day':>10} {'naive prev':>11} | "
              f"{'heatmap | naive':>16} {'p':>7}")
        for period, summary in report["configurations"][name].items():
            for side, values in summary.items():
                report_line(f"{period:>7} {side:>5} {values['days']:>5} | {format_value(values['heatmap_rho']):>8} "
                      f"{format_value(values['naive_same_day_rho']):>10} {format_value(values['naive_previous_day_rho']):>11} | "
                      f"{format_value(values['heatmap_partial_rho_given_naive_same_day']):>16} "
                      f"{'n/a' if values['partial_p'] is None else format(values['partial_p'], '.3f'):>7}")
        if name == next(iter(CONFIGURATIONS)):
            with open(arguments.out / "backtest_days.csv", "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["day", "predicted_long_usd", "predicted_short_usd", "drop_percent", "rise_percent",
                                 "volume_usd", "real_long_usd", "real_short_usd"])
                for row in rows:
                    writer.writerow([row.day, round(row.predicted_long_usd), round(row.predicted_short_usd),
                                     round(row.drop_percent, 3), round(row.rise_percent, 3), round(row.volume_usd),
                                     round(row.real_long_usd), round(row.real_short_usd)])
    (arguments.out / "backtest_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
