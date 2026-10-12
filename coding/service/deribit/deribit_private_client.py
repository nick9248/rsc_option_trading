"""
DeribitPrivateClient: READ-ONLY access to our own Deribit account.

It can only call ``private/get_*`` endpoints (account summary, positions, trades,
order history, settlements). Any other method name is refused before a request is
made, so this class can never place, edit or cancel an order. The API key itself
should also be created with read-only scopes on Deribit.

Credentials come from the environment (.env): ``client_id`` and ``deribit_key``
(the client secret). When either is missing the client reports
``is_configured == False`` and callers skip the account steps.
"""
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parents[3] / ".env")

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.deribit.com/api/v2"
_TIMEOUT_SEC = 20
_TOKEN_SAFETY_MARGIN_SEC = 60
_PAGE_SIZE = 1000
_READ_ONLY_PREFIX = "private/get_"
_LAST_PAGE_TOKEN = "none"  # Deribit's continuation value on the final page


class DeribitPrivateClient:
    """Authenticated, read-only Deribit client (see module docstring)."""

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """
        Args:
            client_id: Deribit API client id. Defaults to env ``client_id``.
            client_secret: Deribit API client secret. Defaults to env ``deribit_key``.
        """
        self._client_id = client_id or os.getenv("client_id")
        self._client_secret = client_secret or os.getenv("deribit_key")
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    @property
    def is_configured(self) -> bool:
        """True when both credentials are available."""
        return bool(self._client_id and self._client_secret)

    # ── transport ──────────────────────────────────────────────────────────

    def _authenticate(self) -> None:
        response = requests.get(
            f"{_BASE_URL}/public/auth",
            params={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=_TIMEOUT_SEC,
        )
        response.raise_for_status()
        body = response.json()
        if "result" not in body:
            raise RuntimeError(f"Deribit authentication failed: {body.get('error')}")
        result = body["result"]
        self._access_token = result["access_token"]
        self._token_expires_at = time.monotonic() + result["expires_in"] - _TOKEN_SAFETY_MARGIN_SEC

    def _call(self, method: str, params: Dict[str, Any]) -> Any:
        """Call one read-only private method and return its ``result``."""
        if not method.startswith(_READ_ONLY_PREFIX):
            raise ValueError(f"Only read-only '{_READ_ONLY_PREFIX}*' methods are allowed, got {method!r}")
        if not self.is_configured:
            raise RuntimeError("Deribit credentials (client_id, deribit_key) are not configured")
        if self._access_token is None or time.monotonic() >= self._token_expires_at:
            self._authenticate()

        response = requests.get(
            f"{_BASE_URL}/{method}",
            params=params,
            headers={"Authorization": f"Bearer {self._access_token}"},
            timeout=_TIMEOUT_SEC,
        )
        body = response.json()
        if "result" not in body:
            raise RuntimeError(f"Deribit {method} failed: {body.get('error')}")
        return body["result"]

    # ── reads ──────────────────────────────────────────────────────────────

    def get_account_summary(self, currency: str) -> Dict[str, Any]:
        """Equity, balance, margin, session P&L and portfolio Greeks for one currency."""
        return self._call("private/get_account_summary", {"currency": currency, "extended": "true"})

    def get_positions(self, currency: str) -> List[Dict[str, Any]]:
        """Currently open futures and option positions in one currency."""
        return self._call("private/get_positions", {"currency": currency})

    def get_user_trades(self, currency: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        """
        Our own fills between two timestamps (epoch ms), oldest first, all pages.

        Uses ``historical=true`` so records older than 24 hours are included.
        Pages are advanced by timestamp; a trade seen twice on a page boundary
        is returned once (keyed by ``trade_id``).
        """
        trades: Dict[str, Dict[str, Any]] = {}
        cursor_ms = start_ms
        while True:
            result = self._call("private/get_user_trades_by_currency_and_time", {
                "currency": currency,
                "start_timestamp": cursor_ms,
                "end_timestamp": end_ms,
                "count": _PAGE_SIZE,
                "sorting": "asc",
                "historical": "true",
            })
            page = result["trades"]
            new_trades = [trade for trade in page if trade["trade_id"] not in trades]
            for trade in new_trades:
                trades[trade["trade_id"]] = trade
            if not result.get("has_more") or not new_trades:
                break
            cursor_ms = max(trade["timestamp"] for trade in page)
        return list(trades.values())

    def get_orders(self, currency: str) -> List[Dict[str, Any]]:
        """
        Our order history, all pages. ``historical=true`` is how Deribit serves old
        orders; it cannot be combined with ``include_unfilled``, so cancelled orders
        that never filled at all may be missing.
        """
        orders: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = self._call("private/get_order_history_by_currency", {
                "currency": currency,
                "count": _PAGE_SIZE,
                "offset": offset,
                "historical": "true",
            })
            orders.extend(page)
            if len(page) < _PAGE_SIZE:
                return orders
            offset += _PAGE_SIZE

    def get_settlements(self, currency: str) -> List[Dict[str, Any]]:
        """Settlement, delivery and bankruptcy events that affected the account, all pages."""
        settlements: List[Dict[str, Any]] = []
        continuation: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"currency": currency, "count": _PAGE_SIZE}
            if continuation:
                params["continuation"] = continuation
            result = self._call("private/get_settlement_history_by_currency", params)
            settlements.extend(result["settlements"])
            continuation = result.get("continuation")
            if not continuation or continuation == _LAST_PAGE_TOKEN or not result["settlements"]:
                return settlements
