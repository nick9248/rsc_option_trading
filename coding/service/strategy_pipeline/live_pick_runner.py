"""
The live picks of one Analysis run: for each of a few expiries (about 3, 7, 14 and 30 days away) one pick of the model, stored; the Strategy
tab of the unified dashboard shows them. Used only when the user ticks "Ask Claude for strategies": it costs about 0.05 USD per expiry.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Set

from coding.service.strategy_pipeline.strategy_pick_service import MODE_LIVE, StrategyPickService

logger = logging.getLogger(__name__)

LIVE_TARGET_DAYS = (3.0, 7.0, 14.0, 30.0)
"""The expiry nearest each of these many days away is picked (the same expiry for two targets is picked once)."""
MINIMUM_DAYS_TO_EXPIRY = 1.0
UNIT_QUANTITY = 1.0
"""Live picks are scale-free: stored per 1 coin per leg, ranked without a capital or a venue minimum; the reader takes his own fraction (the tab shows what Derive's minimum order needs)."""


class LivePickRunner:
    """Asks the model about a few expiries of one currency, at the newest stored chain."""

    def __init__(self, service: StrategyPickService):
        """
        Args:
            service: The pick service (its budget guard stops the run when the model budget is used).
        """
        self.service = service

    def run(self, currency: str, progress: Callable[[str], None], *, should_stop: Callable[[], bool] = lambda: False,
            now_utc: Optional[datetime] = None) -> str:
        """
        Pick the expiries. ``should_stop`` is asked before every paid call; when it says stop, the picks made so far are kept and the summary says so.

        Returns:
            One line for the run summary.

        Raises:
            BudgetExceeded: when the model budget is used (the picks made so far are stored).
            Whatever the model API raises: nothing is billed for a failed call.
        """
        now = now_utc or datetime.now(timezone.utc)
        seen: Set[str] = set()
        paid = 0
        cost = 0.0
        stopped = False
        without_pick = []
        for target in LIVE_TARGET_DAYS:
            if should_stop():
                stopped = True
                break
            try:
                result = self.service.pick(currency, now, MODE_LIVE, target_days_to_expiry=target, minimum_days_to_expiry=MINIMUM_DAYS_TO_EXPIRY,
                                           now_utc=now)
            except Exception:
                progress(f"{currency}: interrupted; at least {paid} picks stored and paid ({cost:.3f} USD) before the error")        # the money already spent is not lost from view
                raise
            if result is None:
                without_pick.append(f"{target:g}")
                progress(f"{currency}: no pick for the expiry about {target:g} days away (no fresh stored chain, or no usable volatility)")
                continue
            if result.expiration in seen:
                continue
            seen.add(result.expiration)
            paid += 0 if result.stored_before or result.status == "no_candidates" else 1                   # only a pick that asked the model
            cost += result.cost_usd
            progress(f"{currency} {result.expiration}: {result.status}" + (f", {result.choice}" if result.choice else "")
                     + (" (already picked within the hour)" if result.stored_before else f", cost {result.cost_usd:.3f} USD"))
        summary = f"{len(seen)} expiries, {paid} new picks, {cost:.3f} USD"
        if without_pick:
            summary += f"; no pick for the expiry about {', '.join(without_pick)} days away (no fresh stored chain, or no usable volatility)"
        return summary + (" (stopped by the user)" if stopped else "")


def build_live_pick_runner() -> LivePickRunner:
    """
    The real runner (imported lazily: it needs the model client and the database).

    Raises:
        RuntimeError: when the key is not set (claude_api_key in .env).
    """
    import anthropic
    from dotenv import load_dotenv
    from coding.core.database.repository import DatabaseRepository
    load_dotenv(dotenv_path=Path(__file__).parents[3] / ".env")
    api_key = os.environ.get("claude_api_key")
    if not api_key:
        raise RuntimeError("claude_api_key is not set in .env")
    return LivePickRunner(StrategyPickService(DatabaseRepository(), anthropic.Anthropic(api_key=api_key), unit_quantity=UNIT_QUANTITY))
