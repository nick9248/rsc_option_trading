"""
Taker buy/sell flow of a perpetual future, from hourly volumes.

A taker is the side of a trade that crossed the spread (an aggressive buyer
or seller). The taker buy/sell ratio is taker buy volume divided by taker
sell volume: above 1 buyers were more aggressive, below 1 sellers were.
A ratio over several hours or days is always total buy volume over total
sell volume (volume-weighted), never an average of hourly ratios.

Missing data is never replaced: a ratio whose inputs are missing is None,
and a day counts only when all 24 of its hours are present.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HOURS_PER_DAY = 24
SUMMARY_DAYS = 7


def taker_buy_sell_ratio(taker_buy_usd: float, taker_sell_usd: float) -> Optional[float]:
    """Taker buy volume over taker sell volume, or None when there was no sell volume."""
    if taker_sell_usd <= 0:
        return None
    return taker_buy_usd / taker_sell_usd


@dataclass(frozen=True)
class TakerFlowHour:
    """Taker volume of one complete UTC hour, in USD."""

    symbol: str
    hour_utc: datetime
    taker_buy_usd: float
    taker_sell_usd: float
    trade_count: Optional[int] = None

    @property
    def ratio(self) -> Optional[float]:
        """Taker buy/sell ratio of this hour, or None without sell volume."""
        return taker_buy_sell_ratio(self.taker_buy_usd, self.taker_sell_usd)


@dataclass(frozen=True)
class TakerFlowDay:
    """Taker volume of one complete UTC day (all 24 hours present), in USD."""

    day: date
    taker_buy_usd: float
    taker_sell_usd: float

    @property
    def ratio(self) -> Optional[float]:
        """Taker buy/sell ratio of the day, or None without sell volume."""
        return taker_buy_sell_ratio(self.taker_buy_usd, self.taker_sell_usd)

    @property
    def net_usd(self) -> float:
        """Taker buy volume minus taker sell volume (positive = net aggressive buying)."""
        return self.taker_buy_usd - self.taker_sell_usd


@dataclass(frozen=True)
class TakerFlowSummary:
    """
    The numbers of the weekly brief for one symbol. A field is None when
    its inputs are not all present; ``not_computed`` says why.
    """

    symbol: str
    latest_hour_utc: Optional[datetime]
    last_24_hours_ratio: Optional[float]
    last_complete_day: Optional[TakerFlowDay]
    seven_day_ratio: Optional[float]
    seven_day_days_used: int
    highest_day: Optional[TakerFlowDay]
    lowest_day: Optional[TakerFlowDay]
    not_computed: List[str]


def start_of_utc_hour(instant: datetime) -> datetime:
    """``instant`` rounded down to the full UTC hour (must be timezone-aware)."""
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware (UTC)")
    return instant.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def complete_days(hours: Iterable[TakerFlowHour]) -> List[TakerFlowDay]:
    """
    Group hours into UTC days and keep the days that have all 24 distinct
    hours, oldest first. A day with a missing hour is left out, not
    estimated.
    """
    by_day: Dict[date, Dict[datetime, TakerFlowHour]] = {}
    for hour in hours:
        hour_start = start_of_utc_hour(hour.hour_utc)
        by_day.setdefault(hour_start.date(), {})[hour_start] = hour
    days = []
    for day in sorted(by_day):
        day_hours = by_day[day]
        if len(day_hours) != HOURS_PER_DAY:
            continue
        days.append(TakerFlowDay(
            day=day,
            taker_buy_usd=sum(hour.taker_buy_usd for hour in day_hours.values()),
            taker_sell_usd=sum(hour.taker_sell_usd for hour in day_hours.values()),
        ))
    return days


def missing_hours(present: Iterable[datetime], first_hour: datetime, last_hour: datetime) -> List[datetime]:
    """
    The UTC hours from ``first_hour`` to ``last_hour`` (both inclusive,
    full hours) that are not in ``present``, oldest first.
    """
    have = {start_of_utc_hour(hour) for hour in present}
    missing = []
    hour = start_of_utc_hour(first_hour)
    last = start_of_utc_hour(last_hour)
    while hour <= last:
        if hour not in have:
            missing.append(hour)
        hour += timedelta(hours=1)
    return missing


def rolling_ratio(hours: Iterable[TakerFlowHour], window_hours: int = HOURS_PER_DAY) -> List[Tuple[datetime, float]]:
    """
    The volume-weighted taker buy/sell ratio over the ``window_hours`` hours
    ending with each hour (the point's time is the START of that last hour), oldest first. A window with a missing hour, or without
    sell volume, gives no point (the line has a gap there, never an estimate).
    """
    by_hour = {start_of_utc_hour(hour.hour_utc): hour for hour in hours}
    points = []
    for end in sorted(by_hour):
        window = [by_hour.get(end - timedelta(hours=offset)) for offset in range(window_hours)]
        if any(hour is None for hour in window):
            continue
        ratio = taker_buy_sell_ratio(sum(hour.taker_buy_usd for hour in window if hour is not None),
                                     sum(hour.taker_sell_usd for hour in window if hour is not None))
        if ratio is not None:
            points.append((end, ratio))
    return points


def summarise(symbol: str, hours: Sequence[TakerFlowHour]) -> TakerFlowSummary:
    """
    The brief numbers from stored hours (any order, one symbol):
    the last 24 hours up to the newest stored hour (needs all 24), the last
    complete UTC day, the volume-weighted ratio of the complete days among
    the 7 days up to that day, and the highest and lowest of those days.
    """
    reasons: List[str] = []
    if not hours:
        return TakerFlowSummary(symbol, None, None, None, None, 0, None, None,
                                [f"{symbol}: no hourly data stored"])
    by_hour = {start_of_utc_hour(hour.hour_utc): hour for hour in hours}
    latest = max(by_hour)

    window = [by_hour.get(latest - timedelta(hours=offset)) for offset in range(HOURS_PER_DAY)]
    last_24_hours_ratio = None
    if all(hour is not None for hour in window):
        last_24_hours_ratio = taker_buy_sell_ratio(
            sum(hour.taker_buy_usd for hour in window if hour is not None),
            sum(hour.taker_sell_usd for hour in window if hour is not None),
        )
    else:
        reasons.append(f"{symbol}: last 24 hours ratio not computed, "
                       f"{sum(hour is None for hour in window)} of 24 hours missing")

    days = complete_days(by_hour.values())
    if not days:
        reasons.append(f"{symbol}: no complete UTC day stored")
        return TakerFlowSummary(symbol, latest, last_24_hours_ratio, None, None, 0, None, None, reasons)

    last_day = days[-1]
    first_wanted = last_day.day - timedelta(days=SUMMARY_DAYS - 1)
    week = [day for day in days if day.day >= first_wanted]
    if len(week) < SUMMARY_DAYS:
        reasons.append(f"{symbol}: only {len(week)} of {SUMMARY_DAYS} days are complete")
    rated = [day for day in week if day.ratio is not None]
    return TakerFlowSummary(
        symbol=symbol,
        latest_hour_utc=latest,
        last_24_hours_ratio=last_24_hours_ratio,
        last_complete_day=last_day,
        seven_day_ratio=taker_buy_sell_ratio(sum(day.taker_buy_usd for day in week),
                                             sum(day.taker_sell_usd for day in week)),
        seven_day_days_used=len(week),
        highest_day=max(rated, key=lambda day: day.ratio or 0.0) if rated else None,
        lowest_day=min(rated, key=lambda day: day.ratio if day.ratio is not None else float("inf")) if rated else None,
        not_computed=reasons,
    )
