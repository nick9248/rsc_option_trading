"""
Backtest of simple option structures on the stored chain snapshots (point in time).

For every entry day: the newest stored snapshot at or before the entry time (it must be fresh), the index price of that instant, the
expiry nearest the target days to expiry, the legs chosen by rule from that snapshot only, filled at its bid and ask. The trade is
settled on an estimate of the delivery price and held to expiry. Nothing after the entry is looked at when the trade is chosen.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.backtest.engine import Summary, Trade, simulate_trade, summarize
from coding.core.analytics.backtest.structures import Quote, StructureParameters, select_legs
from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.menthorq_replication_calculator import fill_missing_implied_volatility, parse_expiry_settlement
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)

MAX_SNAPSHOT_AGE_MINUTES = 45.0
"""A snapshot older than this at the entry time is not used (the collector captures every 30 minutes)."""
MAX_INDEX_PRICE_GAP_MINUTES = 15.0
"""The index price must come from a trade at most this long before the snapshot."""
MIN_SETTLEMENT_TRADES = 5
"""Trades needed in the last 30 minutes before the settlement for the delivery price estimate."""


@dataclass(frozen=True)
class BacktestResult:
    """The trades of one structure and their statistics."""

    structure: str
    trades: Tuple[Trade, ...]
    summary: Summary


class BacktestService:
    """Runs a structure over a range of entry days."""

    def __init__(self, repository: DatabaseRepository):
        """
        Args:
            repository: Local database (read only).
        """
        self.repository = repository
        self._calculator = BlackScholesCalculator()

    def run(self, currency: str, structure: str, first_day: date, last_day: date, entry_hour_utc: int = 9,
            target_days_to_expiry: float = 7.0, minimum_days_to_expiry: float = 2.0,
            parameters: StructureParameters = StructureParameters(), now_utc: Optional[datetime] = None) -> BacktestResult:
        """
        Enter the structure once a day and hold it to expiry.

        Args:
            currency: "BTC" or "ETH".
            structure: One of ``structures.STRUCTURES``.
            first_day: First entry day (UTC).
            last_day: Last entry day (UTC).
            entry_hour_utc: Entry time of every day.
            target_days_to_expiry: The expiry nearest this many days away is traded.
            minimum_days_to_expiry: Expiries closer than this are not traded.
            parameters: The strike rule.
            now_utc: The current instant; an expiry that has not settled by then is not traded.

        Returns:
            The trades and their statistics; days without a trade are counted by reason in ``summary.skipped``.
        """
        now = now_utc or datetime.now(timezone.utc)
        trades: List[Trade] = []
        skipped: Counter = Counter()
        day = first_day
        while day <= last_day:
            entry_time = datetime.combine(day, time(entry_hour_utc), tzinfo=timezone.utc)
            day += timedelta(days=1)
            if entry_time > now:
                break
            trade, reason = self._trade_for(currency, structure, entry_time, target_days_to_expiry, minimum_days_to_expiry,
                                            parameters, now)
            if trade is None:
                skipped[reason] += 1
            else:
                trades.append(trade)
        logger.info("%s: %d trades, skipped %s", structure, len(trades), dict(skipped))
        return BacktestResult(structure, tuple(trades), summarize(trades, skipped))

    def _trade_for(self, currency: str, structure: str, entry_time: datetime, target_days: float, minimum_days: float,
                   parameters: StructureParameters, now: datetime) -> Tuple[Optional[Trade], str]:
        snapshot = self.repository.get_option_chain_snapshot_at_or_before(currency, entry_time)
        if snapshot is None:
            return None, "no snapshot"
        captured_at, rows = snapshot
        if (entry_time - captured_at).total_seconds() / 60.0 > MAX_SNAPSHOT_AGE_MINUTES:
            return None, "no fresh snapshot"
        index = self.repository.get_index_price_at_or_before(currency, captured_at)
        if index is None or (captured_at - index[1]).total_seconds() / 60.0 > MAX_INDEX_PRICE_GAP_MINUTES:
            return None, "no index price at the snapshot"
        spot = index[0]

        expiration, settlement_at = self._choose_expiry(rows, captured_at, target_days, minimum_days)
        if expiration is None or settlement_at is None:
            return None, "no expiry in range"
        if settlement_at > now:
            return None, "expiry not settled yet"
        settlement = self.repository.get_settlement_index_price(currency, settlement_at)
        if settlement is None or settlement[1] < MIN_SETTLEMENT_TRADES:
            return None, "no settlement price"

        # Snapshots before about August carry no mark IV: it is rebuilt from the mark price and the forward (Black-76), as in the
        # MenthorQ replay; a row that still has none gets no delta and is not used.
        rows, _ = fill_missing_implied_volatility([row for row in rows if row["expiration"] == expiration], captured_at)
        quotes = self._quotes(rows, spot, captured_at)
        legs, reason = select_legs(structure, quotes, spot, parameters)
        if legs is None:
            return None, reason
        return simulate_trade(structure, expiration, entry_time, spot, legs, settlement[0]), ""

    @staticmethod
    def _choose_expiry(rows: Sequence[Dict], captured_at: datetime, target_days: float, minimum_days: float
                       ) -> Tuple[Optional[str], Optional[datetime]]:
        best: Optional[Tuple[float, str, datetime]] = None
        for expiration in {row["expiration"] for row in rows}:
            settlement_at = parse_expiry_settlement(expiration)
            if settlement_at is None:
                continue
            days = (settlement_at - captured_at).total_seconds() / 86400.0
            if days < minimum_days:
                continue
            distance = abs(days - target_days)
            if best is None or distance < best[0]:
                best = (distance, expiration, settlement_at)
        return (best[1], best[2]) if best else (None, None)

    def _quotes(self, rows: Sequence[Dict], spot: float, captured_at: datetime) -> List[Quote]:
        """USD quotes of the expiry's options with the Black-Scholes delta of the snapshot's own mark IV and forward."""
        captured_naive = captured_at.astimezone(timezone.utc).replace(tzinfo=None)
        quotes: List[Quote] = []
        for row in rows:
            kind = {"C": "call", "P": "put"}.get(row["option_type"])
            if kind is None or row["strike"] is None:
                continue
            greeks = self._calculator.calculate_greeks_safe(
                {"instrument_name": row["instrument_name"], "strike": row["strike"], "option_type": row["option_type"]},
                row["mark_iv"], row["underlying_price"], captured_naive)
            if greeks is None:
                continue
            bid, ask = row.get("bid_price"), row.get("ask_price")
            quotes.append(Quote(row["instrument_name"], float(row["strike"]), kind, bid * spot if bid else None,
                                ask * spot if ask else None, greeks["delta"]))
        return quotes
