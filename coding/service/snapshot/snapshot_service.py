"""
Snapshot service.

Handles fetching and filtering option chain snapshots.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class SnapshotService:
    """
    Service for fetching and filtering option chain snapshots.

    Handles book summary fetching, expiration filtering,
    volume filtering, optional Greek fetching, and CSV export.
    """

    def __init__(self, api_service: Optional[DeribitApiService] = None):
        """
        Initialize service with API service.

        Args:
            api_service: Deribit API service instance. Not required for the
                CSV export methods (save_snapshot_to_csv, transform_to_modified_format),
                which operate purely on already-fetched data.
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

        # Extract the true current underlying price from the FULL dataset before
        # any expiry filtering. Far-expiry instruments trade infrequently and Deribit
        # caches their underlying_price per-instrument, so those values go stale.
        # The full dataset always contains near-expiry high-volume instruments whose
        # cached price is current — we use the highest-volume instrument as the source.
        true_underlying_price = self._extract_underlying_price(all_data)
        if true_underlying_price:
            progress(f"Underlying price (from highest-volume instrument): ${true_underlying_price:,.2f}")
        else:
            logger.warning("Could not determine underlying price from book summary")

        # Fetch the INDEX price — this, not underlying_price (the per-expiry
        # future), is the correct basis for converting BTC/ETH premiums to USD.
        # See DeribitApiService.get_option_chain_snapshot docstring for the
        # full index-vs-future rule. Failure here must not break the snapshot;
        # transform_to_modified_format falls back to underlying_price if
        # index_price is absent.
        index_price = None
        try:
            index_price = self.api.get_index_price(currency=currency)
            progress(f"Index price: ${index_price:,.2f}")
        except Exception as e:
            logger.warning(f"Could not fetch index price for {currency}: {e}")

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

        # Inject the true underlying price into every filtered instrument.
        # This replaces each instrument's own stale cached value.
        if true_underlying_price:
            for item in filtered_data:
                item["underlying_price"] = true_underlying_price

        # Inject the index price into every filtered instrument. Consumers
        # (transform_to_modified_format) use this for USD conversion instead
        # of underlying_price.
        if index_price is not None:
            for item in filtered_data:
                item["index_price"] = index_price

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

    def _extract_underlying_price(self, all_data: List[Dict]) -> float:
        """
        Extract the current underlying price from the full book summary dataset.

        Uses the highest-volume instrument since it is the most recently traded
        and therefore has the most up-to-date cached underlying_price from Deribit.

        Args:
            all_data: All instruments from book summary (all expirations).

        Returns:
            Current underlying price, or 0.0 if not determinable.
        """
        active = [
            item for item in all_data
            if (item.get("volume") or 0) > 0 and item.get("underlying_price")
        ]

        if active:
            return max(active, key=lambda x: x.get("volume", 0)).get("underlying_price", 0.0)

        # Fallback: use any instrument with a price
        for item in all_data:
            if item.get("underlying_price"):
                return float(item["underlying_price"])

        return 0.0

    def save_snapshot_to_csv(
        self,
        data: List[Dict],
        currency: str,
        modified_format: bool = False
    ) -> Path:
        """
        Save snapshot data to a CSV file under output/data/snapshots.

        Args:
            data: Snapshot instrument dictionaries (raw or already filtered).
            currency: Currency symbol (BTC, ETH) - used in the output filename.
            modified_format: If True, transform to the ordered/USD-priced format
                via transform_to_modified_format before saving; otherwise save raw.

        Returns:
            Path to the created CSV file.
        """
        from coding.core.api.response_parser import ResponseParser

        parser = ResponseParser()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if modified_format:
            output_data = self.transform_to_modified_format(data)
            filename = f"snapshot_{currency.lower()}_{timestamp}_modified"
        else:
            output_data = data
            filename = f"snapshot_{currency.lower()}_{timestamp}_raw"

        path = parser.to_csv(output_data, filename, "snapshots")
        logger.info(f"Saved snapshot CSV to {path}")
        return path

    def transform_to_modified_format(self, data: List[Dict]) -> List[Dict]:
        """
        Transform raw snapshot data to modified format with ordered columns and USD prices.

        USD conversion uses the INDEX price (item["index_price"]), NOT
        underlying_price (the per-expiry future) — this is the same basis
        Deribit's own website uses to display premium USD values. See
        DeribitApiService.get_option_chain_snapshot docstring for the full
        index-vs-future rule this codebase must never regress on.

        If an item has no index_price (e.g. legacy raw data fetched before
        this fix, or an index_price fetch failure upstream), this falls back
        to underlying_price and logs a warning so the caller can see the
        USD figures are on the wrong basis rather than silently producing
        the same wrong numbers with no trace.

        Args:
            data: Raw snapshot data. Expected to carry "index_price" per item
                (injected by get_filtered_instruments); "underlying_price" is
                still reported as-is (it is correct for strike-space math,
                just not for USD conversion).

        Returns:
            Transformed data with ordered columns and calculated USD prices.
        """
        modified_data = []

        for item in data:
            underlying_price = item.get("underlying_price") or 0
            index_price = item.get("index_price")

            if index_price:
                usd_basis = index_price
            else:
                usd_basis = underlying_price
                logger.warning(
                    f"{item.get('instrument_name', '<unknown>')}: no index_price available, "
                    f"falling back to underlying_price (future) for USD conversion — "
                    f"this basis is wrong by the futures basis, see get_option_chain_snapshot docstring"
                )

            # Calculate USD prices
            bid_price = item.get("bid_price")
            mark_price = item.get("mark_price")
            mid_price = item.get("mid_price")
            ask_price = item.get("ask_price")

            bid_price_usd = (bid_price * usd_basis) if bid_price and usd_basis else None
            mark_price_usd = (mark_price * usd_basis) if mark_price and usd_basis else None
            mid_price_usd = (mid_price * usd_basis) if mid_price and usd_basis else None
            ask_price_usd = (ask_price * usd_basis) if ask_price and usd_basis else None

            # Convert timestamp to human readable
            creation_timestamp = item.get("creation_timestamp")
            if creation_timestamp:
                try:
                    timestamp_readable = datetime.fromtimestamp(creation_timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError, OSError):
                    timestamp_readable = str(creation_timestamp)
            else:
                timestamp_readable = None

            # Build ordered row
            row = {
                "instrument_name": item.get("instrument_name"),
                "bid_price": bid_price,
                "bid_price_usd": round(bid_price_usd, 4) if bid_price_usd else None,
                "mark_price": mark_price,
                "mark_price_usd": round(mark_price_usd, 4) if mark_price_usd else None,
                "mid_price": mid_price,
                "mid_price_usd": round(mid_price_usd, 4) if mid_price_usd else None,
                "ask_price": ask_price,
                "ask_price_usd": round(ask_price_usd, 4) if ask_price_usd else None,
                "open_interest": item.get("open_interest"),
                "underlying_price": underlying_price,
                "volume": item.get("volume"),
                "volume_usd": item.get("volume_usd"),
                "delta": item.get("delta"),
                "gamma": item.get("gamma"),
                "vega": item.get("vega"),
                "theta": item.get("theta"),
                "rho": item.get("rho"),
                "timestamp": timestamp_readable,
            }

            modified_data.append(row)

        return modified_data
