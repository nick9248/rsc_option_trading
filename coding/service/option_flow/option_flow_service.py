"""
Buy/sell volume per strike for a window and, optionally, one expiration: reads the stored Deribit trades
(``historical_trades``, which the collector fills), adds them up (``aggregate_by_strike``) and writes the Laevitas-style
chart and the numbers behind it. On the PC the table is the local mirror, so sync first
(``scripts/sync_from_vps.py``) for the latest 24 hours.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional

from coding.core.analytics.option_flow.strike_flow import SERIES, StrikeFlow, aggregate_by_strike
from coding.core.analytics.option_flow.strike_flow_chart_renderer import render_strike_flow_chart
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


@dataclass
class StrikeFlowReport:
    """The flow of a window with the files written for it."""

    currency: str
    window_start: datetime
    window_end: datetime
    expiry: Optional[str]
    flow: StrikeFlow
    newest_trade: Optional[datetime]
    files: List[Path] = field(default_factory=list)


class OptionFlowService:
    """Computes and draws the buy/sell volume per strike."""

    def __init__(self, repository: Optional[DatabaseRepository] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """
        Args:
            repository: Where the trades are read (the configured database by default).
            clock: Wall clock (UTC-aware); the window ends at its reading.
        """
        self.repository = repository or DatabaseRepository()
        self.clock = clock

    def strike_flow(self, currency: str = "BTC", hours: int = 24, expiry: Optional[str] = None,
                    end: Optional[datetime] = None) -> StrikeFlowReport:
        """
        The buy/sell volume per strike of the ``hours`` hours before ``end`` (now by default), for all expirations or
        ``expiry`` only.

        Raises:
            ValueError: when ``end`` is not timezone-aware.
        """
        if end is not None and end.tzinfo is None:
            raise ValueError("end must be timezone-aware (UTC)")
        end = end or self.clock()
        start = end - timedelta(hours=hours)
        start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        trades = self.repository.get_option_trades_between(currency, start_ms, end_ms, expiry)
        newest = self.repository.get_latest_trade_time(currency)
        return StrikeFlowReport(currency, start, end, expiry, aggregate_by_strike(trades, expiry), newest)

    def generate_chart(self, output_directory: Path, currency: str = "BTC", hours: int = 24,
                       expiry: Optional[str] = None, end: Optional[datetime] = None) -> StrikeFlowReport:
        """
        Write ``<output_directory>/<currency>_strike_flow_<hours>h_<expiry or all>.png`` and the matching ``.json`` with
        the numbers (contracts, notional and trades per strike and series).
        """
        report = self.strike_flow(currency, hours, expiry, end)
        scope = expiry or "all"
        title = f"{currency} DERIBIT Buy/Sell Volume Last {hours}H {'All Expirations' if expiry is None else expiry}"
        footer = (f"Window {report.window_start:%Y-%m-%d %H:%M} to {report.window_end:%Y-%m-%d %H:%M} UTC. "
                  f"{report.flow.trades_used} trades ({report.flow.trades_skipped} skipped: no usable index price or side). "
                  "Buy/sell = the taker's side (aggressor); notional = contracts x index price; blocked = block trades. "
                  f"Newest stored trade: {report.newest_trade:%Y-%m-%d %H:%M} UTC." if report.newest_trade else
                  f"Window {report.window_start:%Y-%m-%d %H:%M} to {report.window_end:%Y-%m-%d %H:%M} UTC.")
        output_directory.mkdir(parents=True, exist_ok=True)
        stem = f"{currency}_strike_flow_{hours}h_{scope}" + (f"_to_{end:%Y%m%dT%H%M}" if end is not None else "")
        png = render_strike_flow_chart(report.flow, title, footer, output_directory / f"{stem}.png")
        data_path = output_directory / f"{stem}.json"
        data_path.write_text(json.dumps({
            "currency": currency, "expiry": expiry, "window_start_utc": report.window_start.isoformat(),
            "window_end_utc": report.window_end.isoformat(), "trades_used": report.flow.trades_used,
            "trades_skipped": report.flow.trades_skipped,
            "strikes": {f"{strike:g}": {
                series.key: {"contracts": round(report.flow.cell(strike, series.key).contracts, 4),
                             "notional_usd": round(report.flow.cell(strike, series.key).notional_usd, 2),
                             "trades": report.flow.cell(strike, series.key).trades}
                for series in SERIES if report.flow.cell(strike, series.key).trades} for strike in report.flow.strikes},
        }, indent=1), encoding="utf-8")
        report.files = [png, data_path]
        return report
