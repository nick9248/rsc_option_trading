"""
IBITFetcher — fetches IBIT options P/C ratio from CBOE delayed public data.

BTC-only signal (D10). ETH callers should not use this fetcher.
Fallback: returns None. Callers treat None as neutral (D10 = 0).
"""
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_IBIT.json"
_TIMEOUT_SEC = 10


class IBITFetcher:
    """Fetches IBIT put/call ratio from CBOE public delayed data feed."""

    def _parse_pc_ratio(self, data: dict) -> Optional[float]:
        """Parse CBOE response to extract put_call_ratio field."""
        try:
            return float(data["data"]["put_call_ratio"])
        except (KeyError, TypeError, ValueError):
            return None

    def fetch_pc_ratio(self) -> Optional[float]:
        """
        Fetch current IBIT options put/call ratio.

        Returns None on any failure — treat as neutral (D10 = 0).
        """
        try:
            resp = requests.get(_URL, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning(
                    "IBITFetcher: HTTP %s — falling back to neutral D10",
                    resp.status_code
                )
                return None
            ratio = self._parse_pc_ratio(resp.json())
            if ratio is None:
                logger.warning("IBITFetcher: missing put_call_ratio in response — D10 neutral")
            return ratio
        except Exception as exc:
            logger.warning("IBITFetcher unavailable: %s — D10 neutral", exc)
            return None
