"""
AccountCollector: copies our own Deribit trading activity into the database.

Read-only (see DeribitPrivateClient). Every hourly cycle, per currency it stores:
the account summary, the open positions, new fills, the order history and the
settlement events. Each step has its own try/except, so one failing endpoint never
blocks the others or the rest of the data-collection cycle.
"""
import logging
from datetime import datetime
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
            All zero when no credentials are configured.
        """
        written = {"account": 0, "positions": 0, "trades": 0, "orders": 0, "settlements": 0}
        if not self.client.is_configured:
            logger.info("    Deribit account credentials not configured; skipping account data")
            return written

        steps = {
            "account": lambda: self.repo.save_account_snapshot(
                currency, snapshot_hour, self.client.get_account_summary(currency)),
            "positions": lambda: self.repo.save_position_snapshots(
                currency, snapshot_hour, self.client.get_positions(currency)),
            "trades": lambda: self._collect_trades(currency, now_ms),
            "orders": lambda: self.repo.save_orders(currency, self.client.get_orders(currency)),
            "settlements": lambda: self.repo.save_settlements(currency, self.client.get_settlements(currency)),
        }
        for name, step in steps.items():
            try:
                written[name] = step()
            except Exception as error:
                logger.error(f"    Account step '{name}' failed for {currency}: {error}", exc_info=True)
        logger.info(f"    Account data stored for {currency}: {written}")
        return written

    def _collect_trades(self, currency: str, now_ms: int) -> int:
        """Fetch fills from our newest stored one (all history on the first run)."""
        start_ms = self.repo.get_latest_user_trade_ms(currency) or 0
        return self.repo.save_user_trades(currency, self.client.get_user_trades(currency, start_ms, now_ms))
