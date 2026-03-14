"""
StablecoinFetcher — fetches stablecoin exchange inflow from CryptoQuant free API.

Fallback: returns None. Callers must treat None as neutral (D8 score = 0).
"""
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

_URL = "https://api.cryptoquant.com/v1/stablecoins/exchange-inflow"
_TIMEOUT_SEC = 10


class StablecoinFetcher:
    """Fetches stablecoin exchange inflow percentage of total supply."""

    def _parse_inflow_pct(self, data: dict) -> Optional[float]:
        """
        Parse CryptoQuant response.
        Returns inflow as percentage of total stablecoin supply.
        Returns None if data is missing or malformed.
        """
        rows = data.get("data", [])
        if not rows:
            return None
        row = rows[-1]  # most recent
        inflow_usd = row.get("inflow_usd")
        total_supply = row.get("total_supply")
        if inflow_usd is None or total_supply is None or total_supply == 0:
            return None
        return (float(inflow_usd) / float(total_supply)) * 100.0

    def fetch_inflow_pct(self) -> Optional[float]:
        """
        Fetch 3-day stablecoin exchange inflow as % of total supply.

        Returns None on any failure — treat as neutral signal (D8 = 0).
        """
        try:
            resp = requests.get(_URL, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning(
                    "StablecoinFetcher: HTTP %s — falling back to neutral D8",
                    resp.status_code
                )
                return None
            return self._parse_inflow_pct(resp.json())
        except Exception as exc:
            logger.warning("StablecoinFetcher unavailable: %s — D8 neutral", exc)
            return None
