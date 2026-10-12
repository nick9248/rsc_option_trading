"""
AccountCollector: copies our own Deribit trading activity into the database.

Read-only (see DeribitPrivateClient). Every hourly cycle, per currency it stores:
the account summary, the open positions, new fills, the order history and the
settlement events. Each step has its own try/except, so one failing endpoint never
blocks the others or the rest of the data-collection cycle.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_private_client import DeribitPrivateClient

logger = logging.getLogger(__name__)


class AccountCollector:
    """Stores account state and trading history for one currency per call."""

    def __init__(
        self,
        client: Optional[DeribitPrivateClient] = None,
        repository: Optional[DatabaseRepository] = None,
    ):
        """
        Args:
            client: Read-only Deribit client (created from the environment if None).
            repository: Database repository (created if None).
        """
        self.client = client or DeribitPrivateClient()
        self.repo = repository or DatabaseRepository()

    def collect(self, currency: str, snapshot_hour: datetime, now_ms: int) -> Dict[str, int]:
        """
        Collect and store everything for one currency.

        Args:
            currency: BTC or ETH.
            snapshot_hour: Hour bucket of this cycle, UTC-aware.
            now_ms: Current time in epoch milliseconds (end of the fills window).

        Returns:
            Rows written per kind: account, positions, trades, orders, settlements.
            A step that failed is None; a step that was skipped is absent. Nothing is
            collected when no credentials are configured.
        """
        written: Dict[str, Optional[int]] = {}
        if not self.client.is_configured:
            logger.info("    Deribit account credentials not configured; skipping account data")
            return written

        steps = {
            "trades": lambda: self._collect_trades(currency, now_ms),
            "orders": lambda: self.repo.save_orders(currency, self.client.get_orders(currency)),
            "settlements": lambda: self.repo.save_settlements(currency, self.client.get_settlements(currency)),
        }
        # The account state is "now". Stamping it on an earlier hour (a backfill or a re-run
        # of a past hour) would put today's equity and positions into that hour's history.
        current_hour = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).replace(
            minute=0, second=0, microsecond=0)
        if snapshot_hour == current_hour:
            steps["account"] = lambda: self.repo.save_account_snapshot(
                currency, snapshot_hour, self.client.get_account_summary(currency))
            steps["positions"] = lambda: self.repo.save_position_snapshots(
                currency, snapshot_hour, self.client.get_positions(currency))
        else:
            logger.warning(f"    {snapshot_hour} is not the current hour: account snapshot and positions skipped")

        for name, step in steps.items():
            try:
                written[name] = step()
            except Exception as error:
                written[name] = None
                logger.error(f"    Account step '{name}' failed for {currency}: {error}")
        logger.info(f"    Account data stored for {currency}: {written}")
        return written

    def _collect_trades(self, currency: str, now_ms: int) -> int:
        """Fetch fills from our newest stored one (all history on the first run)."""
        start_ms = self.repo.get_latest_user_trade_ms(currency) or 0
        return self.repo.save_user_trades(currency, self.client.get_user_trades(currency, start_ms, now_ms))
