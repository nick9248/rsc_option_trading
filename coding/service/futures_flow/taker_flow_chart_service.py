"""
Generates the taker flow charts (BTC and ETH perpetuals) from the stored hourly
volumes, into a dated folder, with the numbers beside them as JSON.

Reads ``taker_flow_hourly`` (on the PC: the local mirror that
scripts/sync_from_vps.py fills). Nothing is fetched or invented: a day with a
missing hour is not drawn and is reported as a warning.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from coding.core.analytics.futures_flow.taker_flow import (
    TakerFlowDay,
    complete_days,
    rolling_ratio,
    start_of_utc_hour,
)
from coding.core.analytics.futures_flow.taker_flow_chart_renderer import render_taker_flow_chart
from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.taker_flow_brief_section import STALE_AFTER, symbol_label
from coding.service.futures_flow.taker_flow_settings import BACKFILL_DAYS, SYMBOLS

logger = logging.getLogger(__name__)

DATA_FILE_NAME = "taker_flow_data.json"


@dataclass
class TakerFlowChartResult:
    """What one generation produced."""

    folder: Path
    files: List[Path] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    newest_hour_utc: Optional[datetime] = None


class TakerFlowChartService:
    """Writes one chart per symbol plus the data file."""

    def __init__(
        self,
        repository: Optional[DatabaseRepository] = None,
        symbols: Sequence[str] = SYMBOLS,
        days: int = BACKFILL_DAYS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            repository: Where the hours are read (default: the configured database).
            symbols: Binance symbols to chart.
            days: How many days back to chart.
            clock: Wall clock (UTC-aware).
        """
        self.repository = repository or DatabaseRepository()
        self.symbols = tuple(symbols)
        self.days = days
        self.clock = clock

    @staticmethod
    def _days_with_missing_hours(first_hour: datetime, now: datetime, days: Sequence[TakerFlowDay]) -> List[str]:
        """
        UTC dates that should be complete (a full day between the oldest stored
        hour, at most ``days`` back, and yesterday) but are not. The partial first
        day and today are expected to be incomplete and are not reported; days
        before the oldest stored hour are simply not stored yet.
        """
        first_full_day = first_hour.date() if first_hour.hour == 0 else first_hour.date() + timedelta(days=1)
        last_full_day = now.date() - timedelta(days=1)
        complete = {day.day for day in days}
        expected = [first_full_day + timedelta(days=offset)
                    for offset in range((last_full_day - first_full_day).days + 1)]
        return [day.isoformat() for day in expected if day not in complete]

    def generate(self, output_directory: Path) -> TakerFlowChartResult:
        """
        Write ``<output_directory>/<YYYY-MM-DD>/<label>_taker_flow.png`` for each
        symbol with data, and ``taker_flow_data.json``.

        Returns:
            The folder, the written files, warnings (missing data, stale data) and the
            newest stored hour of any symbol.
        """
        now = self.clock()
        folder = output_directory / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        result = TakerFlowChartResult(folder=folder)
        first_hour = start_of_utc_hour(now) - timedelta(days=self.days)
        data = {"generated_at_utc": now.isoformat(),
                "source": "taker_flow_hourly (Binance USD-M perpetual klines)", "symbols": {}}

        for symbol in self.symbols:
            label = symbol_label(symbol)
            hours = self.repository.get_taker_flow_hours(symbol, first_hour, start_of_utc_hour(now))
            if not hours:
                result.warnings.append(f"{label}: no hourly data stored in the last {self.days} days "
                                       "(run scripts/sync_from_vps.py after the collector has run)")
                continue
            newest = max(hour.hour_utc for hour in hours)
            result.newest_hour_utc = max(newest, result.newest_hour_utc) if result.newest_hour_utc else newest
            days = complete_days(hours)
            gap_days = self._days_with_missing_hours(max(first_hour, min(hour.hour_utc for hour in hours)), now, days)
            if gap_days:
                result.warnings.append(f"{label}: missing hours on {', '.join(gap_days)}; "
                                       "those days are not drawn")
            if now - (newest + timedelta(hours=1)) > STALE_AFTER:
                result.warnings.append(f"{label}: data ends at {(newest + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')} UTC "
                                       "(more than 3 hours old; sync the data)")
            if not days:
                result.warnings.append(f"{label}: no complete day, chart not drawn")
                continue
            note = (f"Source: Binance USD-M {symbol} perpetual hourly klines (taker volume in USD). "
                    f"Data up to {(newest + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')} UTC.")
            path = render_taker_flow_chart(label, days, rolling_ratio(hours), folder / f"{label}_taker_flow.png", note)
            result.files.append(path)
            data["symbols"][symbol] = {
                "newest_hour_utc": newest.isoformat(),
                "days": [{"day": day.day.isoformat(), "taker_buy_usd": day.taker_buy_usd,
                          "taker_sell_usd": day.taker_sell_usd, "ratio": day.ratio} for day in days],
            }

        data_path = folder / DATA_FILE_NAME
        data_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        result.files.append(data_path)
        return result
