"""
On-chain analysis service.

Orchestrates fetching and analyzing on-chain option data.
"""

import logging
import time
from typing import Callable, Dict, Optional

from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class OnChainAnalysisService:
    """
    Service for fetching and analyzing on-chain option data.

    Handles data fetching, Greek fetching, GEX/DEX calculation,
    and report generation.
    """

    def __init__(
        self,
        api_service: DeribitApiService,
        repository: Optional[DatabaseRepository] = None
    ):
        """
        Initialize service with API service and optional database repository.

        Args:
            api_service: Deribit API service instance.
            repository: Database repository for querying trade data (optional).
        """
        self.api = api_service
        self.repository = repository

    def fetch_and_analyze(
        self,
        currency: str,
        fetch_gex_dex: bool = False,
        fetch_buy_sell_flow: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """
        Fetch and analyze on-chain data for a currency.

        Args:
            currency: Currency symbol (BTC, ETH).
            fetch_gex_dex: Whether to fetch Greeks and calculate GEX/DEX.
            fetch_buy_sell_flow: Whether to fetch and analyze buy/sell flow.
            progress_callback: Optional callback for progress updates.

        Returns:
            Analysis report as formatted string.
        """
        def progress(message: str):
            """Send progress update if callback provided."""
            if progress_callback:
                progress_callback(message)
            logger.info(message)

        progress(f"Fetching book summary for {currency} options...")

        all_data = self.api.get_book_summary(
            currency=currency,
            kind="option"
        )

        progress(f"Received {len(all_data)} instruments")

        # Create analyzer and parse data
        progress("Parsing instruments and grouping by expiration...")
        analyzer = OnChainAnalyzer(all_data, currency)
        analyzer.parse_instruments()

        expirations = analyzer.get_expirations()
        progress(f"Found {len(expirations)} expirations")

        # Fetch market metrics (DVOL, funding rate)
        self._fetch_market_metrics(analyzer, progress)

        # Optionally fetch Greeks for GEX/DEX
        if fetch_gex_dex:
            self._fetch_greeks_and_store_gex_dex(analyzer, progress)

        # Optionally fetch buy/sell flow
        if fetch_buy_sell_flow:
            self._calculate_buy_sell_flow(analyzer, progress)

        # Generate report (includes GEX/DEX and flow if data was fetched)
        progress("Generating analysis report...")
        report = analyzer.generate_report()

        progress("Analysis complete")
        return report

    def _fetch_greeks_and_store_gex_dex(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Fetch Greeks for all instruments and store GEX/DEX data in analyzer.

        Args:
            analyzer: OnChainAnalyzer with parsed data.
            progress_callback: Callback for progress updates.
        """
        for expiration in analyzer.get_expirations():
            instruments = analyzer.parsed_data.get(expiration, [])
            if not instruments:
                continue

            progress_callback(f"Fetching Greeks for {expiration} ({len(instruments)} instruments)...")

            # Fetch Greeks for each instrument
            instruments_with_greeks = []
            for i, item in enumerate(instruments):
                try:
                    ticker = self.api.get_ticker(item["instrument_name"])
                    greeks = ticker.get("greeks", {})

                    item_with_greeks = item.copy()
                    item_with_greeks["delta"] = greeks.get("delta")
                    item_with_greeks["gamma"] = greeks.get("gamma")
                    instruments_with_greeks.append(item_with_greeks)

                    if (i + 1) % 20 == 0:
                        progress_callback(
                            f"  Fetched {i + 1}/{len(instruments)} for {expiration}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to fetch Greeks for {item['instrument_name']}: {e}")

            # Calculate GEX/DEX and store in analyzer
            if instruments_with_greeks:
                progress_callback(f"Calculating GEX/DEX for {expiration}...")
                calculator = GexDexCalculator(
                    instruments_with_greeks,
                    analyzer.underlying_price
                )
                gex_dex_report = calculator.generate_report_section()
                analyzer.set_gex_dex_data(expiration, gex_dex_report)

    def _calculate_buy_sell_flow(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Calculate buy/sell flow for all expirations and store in analyzer.

        Args:
            analyzer: OnChainAnalyzer with parsed data.
            progress_callback: Callback for progress updates.
        """
        if self.repository is None:
            logger.warning("Repository not available - skipping buy/sell flow analysis")
            progress_callback("Warning: Repository not available for buy/sell flow")
            return

        for expiration in analyzer.get_expirations():
            progress_callback(f"Calculating buy/sell flow for {expiration}...")

            try:
                flow_analyzer = BuySellFlowAnalyzer(
                    repository=self.repository,
                    currency=analyzer.currency,
                    expiration=expiration,
                    spot_price=analyzer.underlying_price,
                    lookback_hours=24
                )

                flow_report = flow_analyzer.generate_report_section()
                analyzer.set_buy_sell_flow_data(expiration, flow_report)

            except Exception as e:
                logger.warning(f"Failed to calculate buy/sell flow for {expiration}: {e}")
                progress_callback(f"Warning: Failed to calculate flow for {expiration}")

    def _fetch_market_metrics(
        self,
        analyzer: OnChainAnalyzer,
        progress_callback: Callable[[str], None]
    ) -> None:
        """
        Fetch market-wide metrics (DVOL, funding rate) and store in analyzer.

        Args:
            analyzer: OnChainAnalyzer to store metrics in.
            progress_callback: Callback for progress updates.
        """
        dvol = None
        iv_percentile = None
        current_funding = None
        funding_8h = None

        # Fetch DVOL data for past 365 days
        try:
            progress_callback("Fetching DVOL data for IV percentile calculation...")

            end_timestamp = int(time.time() * 1000)
            start_timestamp = end_timestamp - (365 * 24 * 60 * 60 * 1000)  # 365 days ago

            dvol_data = self.api.get_volatility_index_data(
                currency=analyzer.currency,
                resolution=86400,  # Daily resolution for 365 days
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp
            )

            if dvol_data and "data" in dvol_data and dvol_data["data"]:
                # Data format: [timestamp, open, high, low, close]
                close_values = [point[4] for point in dvol_data["data"] if len(point) > 4]

                if close_values:
                    dvol = close_values[-1]  # Current DVOL (most recent close)

                    # Calculate IV percentile
                    values_below = sum(1 for v in close_values if v < dvol)
                    iv_percentile = (values_below / len(close_values)) * 100

                    progress_callback(
                        f"DVOL: {dvol:.2f}, IV Percentile: {iv_percentile:.1f}% "
                        f"(based on {len(close_values)} days)"
                    )

        except Exception as e:
            logger.warning(f"Failed to fetch DVOL data: {e}")

        # Fetch funding rate from perpetual ticker
        try:
            progress_callback("Fetching funding rate...")

            perpetual_ticker = self.api.get_ticker(f"{analyzer.currency}-PERPETUAL")
            current_funding = perpetual_ticker.get("current_funding")
            funding_8h = perpetual_ticker.get("funding_8h")

            if current_funding is not None:
                progress_callback(
                    f"Current Funding: {current_funding * 100:.4f}%, "
                    f"8h Funding: {funding_8h * 100:.4f}%"
                )

        except Exception as e:
            logger.warning(f"Failed to fetch funding rate: {e}")

        # Store in analyzer
        analyzer.set_market_metrics(
            dvol=dvol,
            iv_percentile=iv_percentile,
            current_funding=current_funding,
            funding_8h=funding_8h
        )
