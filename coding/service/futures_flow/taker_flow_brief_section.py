"""
The "Futures flow (not options)" section of the MenthorQ monitor's Telegram
brief: the taker buy/sell ratio of the Binance BTC and ETH perpetuals.

This is the futures market, shown separately from the option levels. The
ratio is taker buy volume over taker sell volume (above 1 = buyers were the
more aggressive side). Numbers only, no signal or advice.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence

from coding.core.analytics.futures_flow.taker_flow import (
    SUMMARY_DAYS,
    TakerFlowSummary,
    start_of_utc_hour,
    summarise,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.taker_flow_settings import SYMBOLS, TakerFlowSettings
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    NOT_AVAILABLE,
    bullet_lines,
    code_block,
    escape,
    format_duration,
    format_utc,
)

logger = logging.getLogger(__name__)

SECTION_TITLE = "<b>Futures flow (not options)</b>"
EXPLANATION = ("<i>Binance perpetuals, taker buy/sell ratio: above 1 = buyers were the more "
               "aggressive side, below 1 = sellers.</i>")
STALE_AFTER = timedelta(hours=3)
LOAD_DAYS = SUMMARY_DAYS + 2          # enough hours for the 7 complete days plus the partial ones
MAXIMUM_REASONS = 3


def _ratio_text(ratio) -> str:
    return NOT_AVAILABLE if ratio is None else f"{ratio:.2f}"


def symbol_label(symbol: str) -> str:
    """``BTCUSDT`` -> ``BTC``."""
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def format_taker_flow_section(summaries: Sequence[TakerFlowSummary], now: datetime) -> str:
    """
    The section as Telegram HTML from the per-symbol summaries.

    Args:
        summaries: ``summarise`` results, one per symbol.
        now: Wall clock (UTC-aware), to flag stale data.
    """
    header = f"{'':<5}{'24h':>7}{'Day':>7}{'7d':>7}{'Low':>7}{'High':>7}"
    rows = [header]
    for summary in summaries:
        day = summary.last_complete_day
        rows.append(
            f"{symbol_label(summary.symbol):<5}{_ratio_text(summary.last_24_hours_ratio):>7}"
            f"{_ratio_text(day.ratio if day else None):>7}{_ratio_text(summary.seven_day_ratio):>7}"
            f"{_ratio_text(summary.lowest_day.ratio if summary.lowest_day else None):>7}"
            f"{_ratio_text(summary.highest_day.ratio if summary.highest_day else None):>7}"
        )
    lines = [SECTION_TITLE, EXPLANATION, code_block("\n".join(rows))]

    notes: List[str] = []
    days = [summary.last_complete_day.day for summary in summaries if summary.last_complete_day]
    if days:
        notes.append(f"Day = {max(days).isoformat()} (UTC). 7d, Low and High cover the last "
                     f"{SUMMARY_DAYS} complete days.")
    latest_hours = [summary.latest_hour_utc for summary in summaries if summary.latest_hour_utc]
    if latest_hours:
        newest = min(latest_hours) + timedelta(hours=1)        # the end of the newest complete hour
        age = now - newest
        notes.append(f"Data up to {format_utc(newest)}.")
        if age > STALE_AFTER:
            notes.append(f"DATA STALE: the newest hour ended {format_duration(age)} ago.")
    for summary in summaries:
        notes.extend(summary.not_computed)
    lines.append(bullet_lines(notes, MAXIMUM_REASONS + 2))
    return "\n".join(lines)


class TakerFlowBriefSectionBuilder:
    """Builds the section from the stored hours."""

    def __init__(self, repository: DatabaseRepository, symbols: Sequence[str] = SYMBOLS):
        """
        Args:
            repository: Where ``taker_flow_hourly`` is read.
            symbols: Binance symbols shown, in order.
        """
        self.repository = repository
        self.symbols = tuple(symbols)

    def build(self, now: datetime) -> str:
        """
        The section for ``now``. A read failure is logged and shown as
        "not computed" in the section (the rest of the brief is unaffected).
        """
        try:
            last_hour = start_of_utc_hour(now)
            first_hour = last_hour - timedelta(days=LOAD_DAYS)
            summaries = [summarise(symbol, self.repository.get_taker_flow_hours(symbol, first_hour, last_hour))
                         for symbol in self.symbols]
        except Exception as error:
            logger.exception("Taker flow brief section: the stored hours could not be read")
            return (f"{SECTION_TITLE}\nnot computed: stored taker flow could not be read "
                    f"({escape(type(error).__name__)}).")
        return format_taker_flow_section(summaries, now)


def brief_section_provider(repository: DatabaseRepository) -> Optional[Callable[[datetime], str]]:
    """
    The section builder for the monitor's brief, or None unless
    TAKER_FLOW_ENABLED=1 (the same switch as the collection, so a brief never
    shows a section nothing fills). A bad setting is logged and also gives None:
    it must not stop the MenthorQ monitor.
    """
    try:
        if not TakerFlowSettings.from_environment().enabled:
            return None
    except Exception:
        logger.exception("Taker flow settings invalid; the brief has no futures flow section")
        return None
    return TakerFlowBriefSectionBuilder(repository).build
