"""
Reads real liquidation orders of an OKX perpetual from the public endpoint (no key, read-only):
``/api/v5/public/liquidation-orders``. Used by the liquidation recorder because Binance's own feed is not
reachable from the USA VPS; liquidations happen at the same prices on every venue.

A liquidated long is a forced SELL, a liquidated short a forced BUY, so the liquidated side is taken from the order's
``side`` (this also holds in net position mode, where ``posSide`` is "net"). The price is the bankruptcy price OKX
reports (``bkPx``); the size is in contracts and converted to BTC with the contract value from the instrument list.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from coding.core.analytics.liquidation_heatmap.level_snapshot import RecordedLiquidation

logger = logging.getLogger(__name__)

BASE_URL = "https://www.okx.com"
VENUE = "okx"
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 20


class OkxDataError(RuntimeError):
    """OKX answered, but not with usable data."""


class OkxLiquidationClient:
    """Fetches pages of filled liquidation orders of one underlying, newest first."""

    def __init__(self, underlying: str = "BTC-USDT", session: Optional[requests.Session] = None,
                 base_url: str = BASE_URL):
        """
        Args:
            underlying: OKX underlying of the swap, e.g. ``BTC-USDT`` (instrument ``BTC-USDT-SWAP``).
            session: HTTP session (a new one by default; tests pass a fake).
            base_url: OKX host.
        """
        self.underlying = underlying
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self._contract_values: Dict[str, float] = {}

    def _get(self, path: str, params: dict) -> list:
        response = self.session.get(self.base_url + path, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise OkxDataError(f"{path}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise OkxDataError(f"{path}: the answer is not JSON") from None
        if not isinstance(body, dict) or body.get("code") != "0" or not isinstance(body.get("data"), list):
            raise OkxDataError(f"{path}: unexpected answer (code {body.get('code') if isinstance(body, dict) else '?'})")
        return body["data"]

    def contract_value(self, instrument: str) -> float:
        """Contract value in BTC of a swap (cached); raises when OKX does not list it or it is not BTC-based."""
        if instrument not in self._contract_values:
            for item in self._get("/api/v5/public/instruments", {"instType": "SWAP", "uly": self.underlying}):
                if item.get("instId") == instrument:
                    if item.get("ctValCcy") != "BTC":
                        raise OkxDataError(f"{instrument}: contract value is not in BTC ({item.get('ctValCcy')})")
                    self._contract_values[instrument] = float(item["ctVal"])
                    break
            else:
                raise OkxDataError(f"{instrument}: not in OKX's instrument list")
        return self._contract_values[instrument]

    def fetch_page(self, earlier_than_ms: Optional[int] = None) -> List[RecordedLiquidation]:
        """
        One page (at most 100) of filled liquidation orders, newest first. With ``earlier_than_ms`` only
        orders older than that timestamp. Rows that are incomplete or implausible are skipped and logged.

        Raises:
            OkxDataError: on an HTTP error or an unexpected body.
        """
        params = {"instType": "SWAP", "uly": self.underlying, "state": "filled", "limit": PAGE_SIZE}
        if earlier_than_ms is not None:
            params["after"] = str(earlier_than_ms)
        events: List[RecordedLiquidation] = []
        for group in self._get("/api/v5/public/liquidation-orders", params):
            instrument = group.get("instId", "")
            for detail in group.get("details", []):
                try:
                    size = float(detail["sz"])
                    price = float(detail["bkPx"])
                    side = detail["side"]
                    if size <= 0 or price <= 0 or side not in ("buy", "sell"):
                        raise ValueError("implausible values")
                    events.append(RecordedLiquidation(
                        venue=VENUE, instrument=instrument,
                        event_time=datetime.fromtimestamp(int(detail["ts"]) / 1000, tz=timezone.utc),
                        liquidated_side="long" if side == "sell" else "short",
                        price=price, size_btc=size * self.contract_value(instrument), raw_size=size))
                except (KeyError, TypeError, ValueError) as error:
                    logger.warning("OKX liquidation: skipped a row (%s)", error)
        events.sort(key=lambda event: event.event_time, reverse=True)
        return events
