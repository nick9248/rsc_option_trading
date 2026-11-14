"""
Collects the price levels of every source for one currency, so they can be compared on one chart:

1. MenthorQ replica: call resistance, put support and HVL of all monthly expiries (open-interest based, ``replicate_menthorq``).
2. Dealer gamma levels of one expiry, open-interest based and flow-based (``DealerFlowLevelsService``).
3. Option flow: the strikes with the most taker buying and selling volume of calls and puts in the last hours
   (``OptionFlowService``; blocked and screen volume together, notional = contracts x strike).
4. Liquidation estimate: the strongest estimated long and short liquidation levels of the newest stored snapshot.

Every source that cannot be computed is skipped with a note (never drawn as a made-up level). Reads only.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence

from coding.core.analytics.menthorq_replication_calculator import replicate_menthorq, select_drawn_levels
from coding.core.analytics.option_flow.level_overview import LevelMark, LevelOverview
from coding.core.analytics.option_flow.strike_flow import StrikeFlow, series_key
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader
from coding.service.option_flow.dealer_flow_levels_service import DealerFlowLevelsService
from coding.service.option_flow.option_flow_service import OptionFlowService

logger = logging.getLogger(__name__)

MENTHORQ_SOURCE = "MenthorQ replica (monthly expiries)"
LIQUIDATION_SNAPSHOT_MAX_AGE_HOURS = 24
LIQUIDATION_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def top_strikes(flow: StrikeFlow, option_type: str, direction: str, count: int) -> List[tuple]:
    """The ``count`` strikes with the largest notional (screen + blocked) of one type and side, as (strike, notional) pairs."""
    totals = []
    for strike in flow.strikes:
        notional = (flow.cell(strike, series_key(option_type, direction, False)).notional_usd
                    + flow.cell(strike, series_key(option_type, direction, True)).notional_usd)
        if notional > 0:
            totals.append((strike, notional))
    return sorted(totals, key=lambda item: -item[1])[:count]


def strongest_separated(levels: Sequence[Sequence[float]], column: int, minimum_gap: float, count: int) -> List[tuple]:
    """
    The ``count`` strongest ``[price, long_usd, short_usd]`` rows by ``column`` (1 = long, 2 = short), skipping a row that is
    closer than ``minimum_gap`` to a stronger one already taken. Returns (price, usd) pairs.
    """
    chosen: List[tuple] = []
    for row in sorted(levels, key=lambda item: -item[column]):
        if row[column] <= 0:
            break
        if all(abs(row[0] - price) >= minimum_gap for price, _ in chosen):
            chosen.append((row[0], row[column]))
        if len(chosen) == count:
            break
    return chosen


class LevelOverviewService:
    """Builds the list of level marks of every source."""

    def __init__(self, repository: Optional[DatabaseRepository] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """
        Args:
            repository: Database access (the configured database by default); only read methods are used.
            clock: Wall clock (UTC-aware).
        """
        self.repository = repository or DatabaseRepository()
        self.clock = clock

    def expirations(self, currency: str = "BTC") -> List[str]:
        """The not-yet-expired expirations of the newest stored chain snapshot, soonest first (for the dealer levels)."""
        return DealerFlowLevelsService(self.repository, self.clock).expirations(currency)

    def overview(self, currency: str = "BTC", expiry: Optional[str] = None, flow_hours: int = 24,
                 flow_levels_per_side: int = 2) -> LevelOverview:
        """
        Collect the levels of all sources.

        Args:
            currency: "BTC" or "ETH".
            expiry: Expiry for the dealer levels; the nearest monthly expiry when omitted.
            flow_hours: Window of the option flow.
            flow_levels_per_side: How many strikes per type and side the flow source contributes.

        Raises:
            LookupError: when no chain snapshot or index price is stored (nothing can be drawn without a spot).
        """
        now = self.clock()
        inputs = MenthorqInputLoader(self.repository).load_inputs(currency, now)
        spot, captured_at = inputs["index_price"], inputs["snapshot_captured_at_utc"]
        marks: List[LevelMark] = []
        notes: List[str] = []

        menthorq = replicate_menthorq(inputs["chain_rows"], spot, captured_at, currency=currency)
        monthly = menthorq["settings"]["expirations"]
        dealer_expiry = (expiry or (monthly[0] if monthly else "")).upper()
        drawn = select_drawn_levels(menthorq["iv_times_open_interest"]["levels"])
        for key, name, kind in (("call_resistance", "call resistance", "resistance"), ("put_support", "put support", "support"),
                                ("hvl", "HVL", "flip")):
            if drawn.get(key) is not None:
                marks.append(LevelMark(MENTHORQ_SOURCE, name, drawn[key], kind))
            else:
                notes.append(f"MenthorQ replica: no {name}")

        self._add_dealer_levels(currency, dealer_expiry, marks, notes)
        self._add_flow_levels(currency, flow_hours, flow_levels_per_side, marks, notes)
        self._add_liquidation_levels(currency, now, marks, notes)
        notes.append(f"chain snapshot {captured_at:%Y-%m-%d %H:%M} UTC, index {spot:,.0f}")
        return LevelOverview(currency, spot, dealer_expiry, marks, notes)

    def _add_dealer_levels(self, currency: str, expiry: str, marks: List[LevelMark], notes: List[str]) -> None:
        """Open-interest based and flow-based dealer levels of ``expiry``."""
        try:
            levels = DealerFlowLevelsService(self.repository, self.clock).levels(currency, expiry)
        except LookupError as error:
            notes.append(f"dealer levels {expiry}: {error}")
            return
        checked = "" if levels.gate_passed else ", check failed"
        for source, call_wall, put_wall, flip in (
                (f"Dealer GEX {expiry}, open interest", levels.open_interest_call_wall, levels.open_interest_put_wall,
                 levels.open_interest_gamma_flip),
                (f"Dealer GEX {expiry}, flow-based{checked}", levels.flow_call_wall, levels.flow_put_wall, levels.flow_gamma_flip)):
            for name, kind, price in (("call wall", "resistance", call_wall), ("put wall", "support", put_wall),
                                      ("gamma flip", "flip", flip)):
                if price is not None:
                    marks.append(LevelMark(source, name, price, kind))
        if not levels.gate_passed:
            notes.append(f"flow-based dealer levels failed their plausibility check: {levels.gate_reason}")

    def _add_flow_levels(self, currency: str, hours: int, count: int, marks: List[LevelMark], notes: List[str]) -> None:
        """The strikes with the most taker buying/selling of calls and puts."""
        report = OptionFlowService(self.repository, self.clock).strike_flow(currency, hours)
        source = f"Option flow {hours}h (top {count} strikes)"
        if report.flow.trades_used == 0:
            notes.append(f"option flow: no trades in the last {hours} h (sync the database)")
            return
        for option_type, direction, label in (("C", "buy", "calls bought"), ("C", "sell", "calls sold"),
                                              ("P", "buy", "puts bought"), ("P", "sell", "puts sold")):
            for strike, notional in top_strikes(report.flow, option_type, direction, count):
                marks.append(LevelMark(source, f"{label} {notional / 1e6:,.0f}M USD", strike, "buy" if direction == "buy" else "sell"))
        if report.newest_trade is not None and self.clock() - report.newest_trade > timedelta(hours=3):
            notes.append(f"option flow: newest stored trade is from {report.newest_trade:%Y-%m-%d %H:%M} UTC (sync the database)")

    def _add_liquidation_levels(self, currency: str, now: datetime, marks: List[LevelMark], notes: List[str]) -> None:
        """The strongest long (below spot) and short (above spot) liquidation levels of the newest stored snapshot."""
        symbol = LIQUIDATION_SYMBOLS.get(currency)
        snapshots = self.repository.get_liquidation_heatmap_snapshots_between(
            symbol, now - timedelta(hours=LIQUIDATION_SNAPSHOT_MAX_AGE_HOURS), now + timedelta(minutes=1)) if symbol else []
        if not snapshots:
            notes.append(f"liquidation estimate: no snapshot of the last {LIQUIDATION_SNAPSHOT_MAX_AGE_HOURS} h (sync the database)")
            return
        snapshot = snapshots[-1]
        source = "Liquidation estimate (Binance)"
        gap = 0.01 * snapshot.reference_price
        below = [row for row in snapshot.levels if row[0] < snapshot.reference_price]
        above = [row for row in snapshot.levels if row[0] > snapshot.reference_price]
        for price, usd in strongest_separated(below, 1, gap, 2):
            marks.append(LevelMark(source, f"longs {usd / 1e6:,.0f}M USD", price, "long_liquidation"))
        for price, usd in strongest_separated(above, 2, gap, 2):
            marks.append(LevelMark(source, f"shorts {usd / 1e6:,.0f}M USD", price, "short_liquidation"))
        notes.append(f"liquidation estimate computed {snapshot.computed_at:%Y-%m-%d %H:%M} UTC; research model, under forward test")
