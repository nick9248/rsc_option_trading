"""
Shared settlement-price lookup for scanner forward-test harnesses.
Extracted 2026-07-20 from StraddleForwardTestHarness so the defined-risk
harness doesn't duplicate it. Settlement price source: ohlcv_history.close
on the expiry's calendar date (08:00 UTC = Deribit's daily settlement instant).
"""
from datetime import datetime, timezone
from typing import Optional


def parse_expiry_settlement(expiry: str) -> Optional[datetime]:
    """Deribit expiration string ('25SEP26') -> settlement datetime (08:00 UTC)."""
    try:
        expiry_date = datetime.strptime(expiry, "%d%b%y")
        return expiry_date.replace(hour=8, tzinfo=timezone.utc)
    except ValueError:
        return None


def lookup_settlement_price(repo, currency: str, settle_dt: datetime) -> Optional[float]:
    """
    ohlcv_history.close on the expiry's calendar date. Returns None (never
    raises) if that row hasn't been collected yet.
    """
    day_start = settle_dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    rows = repo.get_ohlcv_by_date_range(currency, day_start, day_end)
    if not rows:
        return None
    return rows[-1]["close"]
