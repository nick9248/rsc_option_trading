"""
Loads the stored taker flow (BTC and ETH perpetuals) for the report's interactive
figure: the hourly volumes, the complete days and the warnings.

Reads ``taker_flow_hourly`` (on the PC: the local mirror that
scripts/sync_from_vps.py fills). Nothing is fetched or invented: a day with a
missing hour is not drawn and is reported as a warning.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence, Tuple

from coding.core.analytics.futures_flow.taker_flow import (
    TakerFlowDay,
    TakerFlowHour,
    complete_days,
    rolling_ratio,
    start_of_utc_hour,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.taker_flow_settings import STALE_AFTER, symbol_label

logger = logging.getLogger(__name__)

CHART_DAYS = 30      # days drawn (the collector backfills 30, but the two are set separately)


@dataclass
class TakerFlowFigureData:
    """The stored taker flow of one symbol for the interactive figure, with its warnings."""

    symbol: str
    label: str
    hours: List[TakerFlowHour] = field(default_factory=list)
    rolling_points: List[Tuple[datetime, float]] = field(default_factory=list)
    days: List[TakerFlowDay] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    newest_hour_utc: Optional[datetime] = None


class TakerFlowChartService:
    """Loads the stored taker flow of each symbol for the report figure."""

    def __init__(
        self,
        repository: Optional[DatabaseRepository] = None,
        days: int = CHART_DAYS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            repository: Where the hours are read (default: the configured database).
            days: How many days back to chart.
            clock: Wall clock (UTC-aware).
        """
        self.repository = repository or DatabaseRepository()
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
        last_closed_hour = start_of_utc_hour(now) - timedelta(hours=1)
        # A day is expected complete once its hour 23 has closed.
        last_full_day = last_closed_hour.date() if last_closed_hour.hour == 23 else last_closed_hour.date() - timedelta(days=1)
        complete = {day.day for day in days}
        expected = [first_full_day + timedelta(days=offset)
                    for offset in range((last_full_day - first_full_day).days + 1)]
        return [day.isoformat() for day in expected if day not in complete]

    def _load(self, symbol: str, now: datetime) -> TakerFlowFigureData:
        """
        The stored hours of ``symbol`` in the window, its complete days and rolling ratio, and the warnings (no data, missing
        hours, stale data).
        """
        label = symbol_label(symbol)
        first_hour = start_of_utc_hour(now) - timedelta(days=self.days)
        hours = self.repository.get_taker_flow_hours(symbol, first_hour, start_of_utc_hour(now))
        data = TakerFlowFigureData(symbol=symbol, label=label, hours=list(hours))
        if not hours:
            data.warnings.append(f"{label}: no hourly data stored in the last {self.days} days "
                                 "(run scripts/sync_from_vps.py after the collector has run)")
            return data
        newest = max(hour.hour_utc for hour in hours)
        data.newest_hour_utc = newest
        data.days = complete_days(hours)
        data.rolling_points = rolling_ratio(hours)
        gap_days = self._days_with_missing_hours(max(first_hour, min(hour.hour_utc for hour in hours)), now, data.days)
        if gap_days:
            data.warnings.append(f"{label}: missing hours on {', '.join(gap_days)}; "
                                 "those days are not drawn")
        if now - (newest + timedelta(hours=1)) > STALE_AFTER:
            data.warnings.append(f"{label}: data ends at {(newest + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')} UTC "
                                 "(more than 3 hours old; sync the data)")
        if not data.days:
            data.warnings.append(f"{label}: no complete day, chart not drawn")
        return data

    def figure_data(self, symbol: str) -> TakerFlowFigureData:
        """
        Everything the interactive figure needs for one symbol (nothing is written): the hours, rolling ratio, complete
        days, the warnings (no data, missing hours, stale data), and the newest stored hour (None without data).
        """
        return self._load(symbol, self.clock())
