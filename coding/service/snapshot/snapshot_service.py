"""
Snapshot service.

Handles fetching and filtering option chain snapshots.
"""

import logging
from typing import Callable, Dict, List, Optional

from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class SnapshotService:
    """
    Service for fetching and filtering option chain snapshots.

    Handles book summary fetching, expiration filtering,
    volume filtering, and optional Greek fetching.
    """

    def __init__(self, api_service: DeribitApiService):
        """
        Initialize service with API service.

        Args:
            api_service: Deribit API service instance.
        """
        self.api = api_service

    def get_filtered_instruments(
        self,
        currency: str,
        expirations: List[str],
        min_volume: float = 0.0,
        fetch_greeks: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[Dict]:
        """
        Get filtered option instruments with optional Greeks.

        Args:
            currency: Currency symbol (BTC, ETH).
            expirations: List of expiration dates to include.
            min_volume: Minimum volume filter (0 = no filter).
            fetch_greeks: Whether to fetch Greeks from ticker endpoint.
            progress_callback: Optional callback for progress updates.

        Returns:
            List of filtered instrument dictionaries.
        """
        def progress(message: str):
            """Send progress update if callback provided."""
            if progress_callback:
                progress_callback(message)
            logger.info(message)

        progress(f"Fetching book summary for {currency} options...")

        # Get all options book summary
        all_data = self.api.get_book_summary(
            currency=currency,
            kind="option"
        )

        progress(f"Received {len(all_data)} instruments")

        # Filter by expiration dates
        filtered_data = []
        for item in all_data:
            instrument_name = item.get("instrument_name", "")
            # Extract expiration from instrument name (e.g., ETH-10JAN25-3400-C)
            parts = instrument_name.split("-")
            if len(parts) >= 2:
                expiry = parts[1]
                if expiry in expirations:
                    filtered_data.append(item)

        progress(f"Filtered to {len(filtered_data)} instruments for selected expirations")

        # Filter by volume
        if min_volume > 0:
            filtered_data = [
                item for item in filtered_data
                if item.get("volume", 0) >= min_volume
            ]
            progress(f"After volume filter: {len(filtered_data)} instruments")

        # Optionally fetch Greeks from ticker
        if fetch_greeks and filtered_data:
            progress("Fetching Greeks from ticker...")
            for i, item in enumerate(filtered_data):
                try:
                    ticker = self.api.get_ticker(item["instrument_name"])
                    greeks = ticker.get("greeks", {})
                    item["delta"] = greeks.get("delta")
                    item["gamma"] = greeks.get("gamma")
                    item["vega"] = greeks.get("vega")
                    item["theta"] = greeks.get("theta")
                    item["rho"] = greeks.get("rho")

                    if (i + 1) % 10 == 0:
                        progress(f"Fetched Greeks for {i + 1}/{len(filtered_data)} instruments")
                except Exception as e:
                    logger.warning(f"Failed to fetch Greeks for {item['instrument_name']}: {e}")

        # Sort by instrument name
        filtered_data.sort(key=lambda x: x.get("instrument_name", ""))

        return filtered_data
