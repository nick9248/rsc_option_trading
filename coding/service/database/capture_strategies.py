"""
Capture strategy classes for different data types.

Each strategy handles capture logic for a specific data type,
following the strategy pattern for modularity.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics import chart_generator
from coding.core.database import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


class CaptureStrategy(ABC):
    """
    Abstract base class for capture strategies.

    Each strategy handles a specific type of data capture
    and chart generation.
    """

    def __init__(
        self,
        repository: DatabaseRepository,
        service: DeribitApiService,
        currency: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize capture strategy.

        Args:
            repository: Database repository for saving data.
            service: Deribit API service for fetching data.
            currency: Currency symbol (ETH, BTC).
            progress_callback: Optional callback for progress updates.
        """
        self.repository = repository
        self.service = service
        self.currency = currency
        self.progress_callback = progress_callback

    def emit_progress(self, message: str) -> None:
        """Emit progress update if callback is set."""
        if self.progress_callback:
            self.progress_callback(message)
        logger.info(message)

    @abstractmethod
    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """
        Capture data to database.

        Args:
            analyzer: OnChainAnalyzer with parsed data.
            raw_data: Raw book summary data.
            captured_at: Timestamp for capture.

        Returns:
            Number of records captured.
        """
        pass

    @abstractmethod
    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """
        Generate charts for captured data.

        Args:
            analyzer: OnChainAnalyzer with parsed data.

        Returns:
            List of generated chart paths.
        """
        pass


class SnapshotCaptureStrategy(CaptureStrategy):
    """Strategy for capturing raw snapshot data with distribution charts."""

    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture raw snapshot data."""
        self.emit_progress("Saving snapshot to database...")
        return self.repository.save_snapshot(self.currency, raw_data, captured_at)

    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """Generate OI and Volume distribution charts per expiration."""
        chart_paths = []
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.emit_progress(f"Generating snapshot charts for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if not analysis:
                continue

            strike_data = analysis.get("strike_data", {})
            max_pain_strike = analysis["max_pain"]["max_pain_strike"] or 0

            # OI distribution chart
            path = chart_generator.generate_snapshot_oi_distribution(
                strike_data,
                self.currency,
                exp,
                analyzer.underlying_price,
                max_pain_strike
            )
            if path:
                chart_paths.append(path)

            # Volume distribution chart
            path = chart_generator.generate_snapshot_volume_distribution(
                strike_data,
                self.currency,
                exp,
                analyzer.underlying_price
            )
            if path:
                chart_paths.append(path)

        return chart_paths


class MaxPainCaptureStrategy(CaptureStrategy):
    """Strategy for capturing max pain data."""

    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture max pain for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.emit_progress(f"Calculating max pain for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if analysis and analysis["max_pain"]["max_pain_strike"]:
                self.repository.save_max_pain(
                    currency=self.currency,
                    expiration=exp,
                    max_pain_strike=analysis["max_pain"]["max_pain_strike"],
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += 1

        return count

    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """Generate max pain trend charts."""
        chart_paths = []
        expirations = self.repository.get_available_expirations(self.currency, "max_pain")

        for exp in expirations:
            self.emit_progress(f"Generating max pain chart for {exp}...")
            data = self.repository.get_max_pain_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_max_pain_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths


class OpenInterestCaptureStrategy(CaptureStrategy):
    """Strategy for capturing open interest data."""

    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture open interest for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.emit_progress(f"Calculating OI for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if analysis:
                pcr = analysis["put_call_ratio"]
                self.repository.save_open_interest(
                    currency=self.currency,
                    expiration=exp,
                    total_call_oi=pcr["total_call_oi"],
                    total_put_oi=pcr["total_put_oi"],
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += 1

        return count

    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """Generate OI and P/C ratio trend charts."""
        chart_paths = []
        expirations = self.repository.get_available_expirations(self.currency, "open_interest")

        for exp in expirations:
            self.emit_progress(f"Generating OI charts for {exp}...")
            data = self.repository.get_open_interest_history(self.currency, exp)

            if len(data) >= 2:
                # OI trend
                path = chart_generator.generate_open_interest_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

                # P/C ratio trend
                path = chart_generator.generate_pc_ratio_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths


class VolumeCaptureStrategy(CaptureStrategy):
    """Strategy for capturing volume data."""

    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture volume for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.emit_progress(f"Calculating volume for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if analysis:
                vol = analysis["volume_stats"]
                self.repository.save_volume(
                    currency=self.currency,
                    expiration=exp,
                    total_call_volume=vol["total_call_volume"],
                    total_put_volume=vol["total_put_volume"],
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += 1

        return count

    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """Generate volume trend charts."""
        chart_paths = []
        expirations = self.repository.get_available_expirations(self.currency, "volume")

        for exp in expirations:
            self.emit_progress(f"Generating volume chart for {exp}...")
            data = self.repository.get_volume_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_volume_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths


class LevelsCaptureStrategy(CaptureStrategy):
    """Strategy for capturing support/resistance levels."""

    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture levels for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.emit_progress(f"Calculating levels for {exp}...")
            analysis = analyzer.analyze_expiration(exp)

            if not analysis:
                continue

            sr = analysis["support_resistance"]
            levels = []

            # Resistance levels
            for i, level in enumerate(sr.get("resistance_levels", []), 1):
                levels.append({
                    "level_type": f"resistance_{i}",
                    "strike": level["strike"],
                    "value": level["call_oi"]
                })

            # Support levels
            for i, level in enumerate(sr.get("support_levels", []), 1):
                levels.append({
                    "level_type": f"support_{i}",
                    "strike": level["strike"],
                    "value": level["put_oi"]
                })

            # Short-term levels
            if sr.get("short_term_resistance"):
                levels.append({
                    "level_type": "short_term_resistance",
                    "strike": sr["short_term_resistance"]["strike"],
                    "value": sr["short_term_resistance"]["call_oi"]
                })

            if sr.get("short_term_support"):
                levels.append({
                    "level_type": "short_term_support",
                    "strike": sr["short_term_support"]["strike"],
                    "value": sr["short_term_support"]["put_oi"]
                })

            if levels:
                self.repository.save_levels(
                    currency=self.currency,
                    expiration=exp,
                    levels=levels,
                    underlying_price=analyzer.underlying_price,
                    captured_at=captured_at
                )
                count += len(levels)

        return count

    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """Generate levels trend charts."""
        chart_paths = []
        expirations = self.repository.get_available_expirations(self.currency, "levels")

        for exp in expirations:
            self.emit_progress(f"Generating levels chart for {exp}...")
            data = self.repository.get_levels_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_levels_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths


class GexDexCaptureStrategy(CaptureStrategy):
    """Strategy for capturing GEX/DEX data."""

    def capture(
        self,
        analyzer: OnChainAnalyzer,
        raw_data: List[Dict],
        captured_at: datetime
    ) -> int:
        """Capture GEX/DEX for all expirations."""
        count = 0
        expirations = analyzer.get_expirations()

        for exp in expirations:
            self.emit_progress(f"Calculating GEX/DEX for {exp}...")

            instruments = analyzer.parsed_data.get(exp, [])
            if not instruments:
                continue

            # Fetch Greeks for each instrument
            instruments_with_greeks = self._fetch_greeks(instruments)

            if not instruments_with_greeks:
                continue

            # Calculate GEX/DEX
            calculator = GexDexCalculator(instruments_with_greeks, analyzer.underlying_price)
            result = calculator.calculate()

            key_levels = result.get("key_levels", {})
            call_res = key_levels.get("call_resistance")
            put_sup = key_levels.get("put_support")

            self.repository.save_gex_dex(
                currency=self.currency,
                expiration=exp,
                total_net_gex=result.get("total_net_gex", 0),
                total_net_dex=result.get("total_net_dex", 0),
                call_resistance_strike=call_res["strike"] if call_res else None,
                call_resistance_gex=call_res["net_gex"] if call_res else None,
                put_support_strike=put_sup["strike"] if put_sup else None,
                put_support_gex=put_sup["net_gex"] if put_sup else None,
                hvl_strike=key_levels.get("hvl"),
                underlying_price=analyzer.underlying_price,
                captured_at=captured_at
            )
            count += 1

        return count

    def _fetch_greeks(self, instruments: List[Dict]) -> List[Dict]:
        """Fetch Greeks for instruments via ticker API."""
        instruments_with_greeks = []

        for inst in instruments:
            instrument_name = inst.get("instrument_name")
            if not instrument_name:
                continue

            try:
                ticker = self.service.get_ticker(instrument_name)
                greeks = ticker.get("greeks", {})

                instruments_with_greeks.append({
                    "instrument_name": instrument_name,
                    "strike": inst["strike"],
                    "option_type": inst["option_type"],
                    "open_interest": inst["open_interest"],
                    "gamma": greeks.get("gamma"),
                    "delta": greeks.get("delta"),
                })
            except Exception as e:
                logger.debug(f"Failed to get Greeks for {instrument_name}: {e}")
                continue

        return instruments_with_greeks

    def generate_charts(self, analyzer: OnChainAnalyzer) -> List[str]:
        """Generate GEX/DEX trend charts."""
        chart_paths = []
        expirations = self.repository.get_available_expirations(self.currency, "gex_dex")

        for exp in expirations:
            self.emit_progress(f"Generating GEX/DEX chart for {exp}...")
            data = self.repository.get_gex_dex_history(self.currency, exp)

            if len(data) >= 2:
                path = chart_generator.generate_gex_dex_trend(data, self.currency, exp)
                if path:
                    chart_paths.append(path)

        return chart_paths


# Strategy factory for creating strategies by type
CAPTURE_STRATEGIES = {
    "snapshot": SnapshotCaptureStrategy,
    "max_pain": MaxPainCaptureStrategy,
    "open_interest": OpenInterestCaptureStrategy,
    "volume": VolumeCaptureStrategy,
    "levels": LevelsCaptureStrategy,
    "gex_dex": GexDexCaptureStrategy,
}


def get_capture_strategy(
    capture_type: str,
    repository: DatabaseRepository,
    service: DeribitApiService,
    currency: str,
    progress_callback: Optional[Callable[[str], None]] = None
) -> CaptureStrategy:
    """
    Factory function to get capture strategy by type.

    Args:
        capture_type: Type of capture (snapshot, max_pain, etc.).
        repository: Database repository.
        service: Deribit API service.
        currency: Currency symbol.
        progress_callback: Optional progress callback.

    Returns:
        CaptureStrategy instance.

    Raises:
        ValueError: If capture_type is unknown.
    """
    strategy_class = CAPTURE_STRATEGIES.get(capture_type)
    if not strategy_class:
        raise ValueError(f"Unknown capture type: {capture_type}")

    return strategy_class(repository, service, currency, progress_callback)
