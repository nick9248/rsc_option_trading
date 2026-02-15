"""
On-chain analysis service.

Orchestrates fetching and analyzing on-chain option data.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
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
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """
        Fetch and analyze on-chain data for a currency.

        Always includes GEX/DEX and buy/sell flow analysis.

        Args:
            currency: Currency symbol (BTC, ETH).
            progress_callback: Optional callback for progress updates.

        Returns:
            Analysis report text.
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

        # Always fetch Greeks for GEX/DEX
        self._fetch_greeks_and_store_gex_dex(analyzer, progress)

        # Always fetch buy/sell flow
        self._calculate_buy_sell_flow(analyzer, progress)

        # Generate report (includes GEX/DEX and flow)
        progress("Generating analysis report...")
        report = analyzer.generate_report()

        # Save reports per expiration
        self._save_reports_per_expiration(report, currency, analyzer)

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

        Also saves flow metrics to database for chart generation.

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

                # Calculate flow data
                flow_result = flow_analyzer.calculate()

                # Save to database for chart queries
                try:
                    self.repository.save_flow_metrics(
                        currency=analyzer.currency,
                        expiration=expiration,
                        flow_data=flow_result["flow_data"],
                        underlying_price=analyzer.underlying_price,
                        window_hours=24
                    )
                    logger.info(f"Saved flow metrics to database for {expiration}")
                except Exception as save_error:
                    logger.warning(f"Failed to save flow metrics for {expiration}: {save_error}")

                # Generate report section and store in analyzer
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

    def _save_reports_per_expiration(
        self,
        full_report: str,
        currency: str,
        analyzer: OnChainAnalyzer
    ) -> None:
        """
        Parse full report and save per-expiration sections.

        Each expiration folder gets only its section (header + expiration data).
        Full report remains in GUI only.

        Directory: output/data/onchain_analysis/{currency}/{expiration}/
        Filename: report_{timestamp}.txt

        Args:
            full_report: Full analysis report text.
            currency: Currency symbol (BTC, ETH).
            analyzer: OnChainAnalyzer instance with parsed data.
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Get project root (3 levels up from this file)
            project_root = Path(__file__).parent.parent.parent.parent

            # Split report into lines for parsing
            lines = full_report.split('\n')

            # Find header (everything before first "EXPIRATION:")
            header_lines = []
            first_exp_idx = None
            for i, line in enumerate(lines):
                if line.startswith("EXPIRATION:"):
                    first_exp_idx = i
                    break
                header_lines.append(line)

            if first_exp_idx is None:
                logger.warning("No EXPIRATION sections found in report")
                return

            header = '\n'.join(header_lines)

            # Find all expiration sections
            exp_sections = {}
            current_exp = None
            current_lines = []

            for i in range(first_exp_idx, len(lines)):
                line = lines[i]

                if line.startswith("EXPIRATION:"):
                    # Save previous section if exists
                    if current_exp and current_lines:
                        exp_sections[current_exp] = '\n'.join(current_lines)

                    # Start new section
                    current_exp = line.split(":", 1)[1].strip()
                    current_lines = [line]
                elif current_exp:
                    current_lines.append(line)

            # Save last section
            if current_exp and current_lines:
                exp_sections[current_exp] = '\n'.join(current_lines)

            logger.info(f"Parsed {len(exp_sections)} expiration sections")

            # Save each section to its folder
            for expiration, section_content in exp_sections.items():
                # Create directory structure
                output_dir = project_root / "output" / "data" / "onchain_analysis" / currency / expiration
                output_dir.mkdir(parents=True, exist_ok=True)

                # Save header + section (not full report)
                report_path = output_dir / f"report_{timestamp}.txt"
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(header)
                    if header and not header.endswith('\n'):
                        f.write('\n')
                    f.write('\n')
                    f.write(section_content)

                logger.info(f"Saved report for {expiration} to {report_path}")

        except Exception as e:
            logger.error(f"Failed to save per-expiration reports: {e}", exc_info=True)
