"""
Morning note generation service.

Orchestrates the full morning note pipeline:
    OnChainAnalysisService → SynthesisMapper → SynthesisEngine → Executive Summary
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from coding.core.analytics.synthesis import SynthesisEngine, SynthesisMapper
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

logger = logging.getLogger(__name__)


class MorningNoteService:
    """
    Generates an institutional-grade morning note executive summary.

    Runs the full on-chain analysis pipeline and passes structured data
    through the SynthesisMapper to SynthesisEngine.
    """

    def __init__(self, on_chain_service: OnChainAnalysisService):
        """
        Initialize service.

        Args:
            on_chain_service: Fully configured OnChainAnalysisService instance.
        """
        self.on_chain_service = on_chain_service
        self.engine = SynthesisEngine()

    def generate(
        self,
        currency: str = "BTC",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Run on-chain analysis and return the morning note summary string.

        Args:
            currency: Currency symbol (BTC, ETH).
            progress_callback: Optional callback for progress updates.

        Returns:
            Formatted executive summary string.
        """
        logger.info(f"Generating morning note for {currency}...")

        _, analyzer = self.on_chain_service.fetch_and_analyze(
            currency=currency,
            progress_callback=progress_callback,
            return_analyzer=True,
        )

        market, expiries = SynthesisMapper.build_all(analyzer)

        if not expiries:
            logger.warning("No expiry metrics could be built — synthesis may be incomplete")

        note = self.engine.run(market, expiries)
        logger.info("Morning note generation complete")
        return note

    def generate_from_analyzer(self, analyzer) -> str:
        """
        Generate a morning note from an already-populated OnChainAnalyzer.

        Skips the fetch step — use when the caller already holds the analyzer.

        Args:
            analyzer: Populated OnChainAnalyzer instance.

        Returns:
            Formatted executive summary string.
        """
        market, expiries = SynthesisMapper.build_all(analyzer)

        if not expiries:
            logger.warning("No expiry metrics could be built — synthesis may be incomplete")

        note = self.engine.run(market, expiries)
        return note

    def save_report_bundle(
        self,
        currency: str,
        report: str,
        synthesis: str,
    ) -> Path:
        """
        Save the on-chain report and synthesis into a timestamped folder.

        Folder: output/data/onchain_analysis/{currency}/report/{YYYYMMDD_HHMMSS}/
        Files:  report.txt, synthesis.txt

        Args:
            currency: Currency symbol (BTC, ETH).
            report: Full on-chain analysis report text.
            synthesis: Morning note synthesis text.

        Returns:
            Path to the folder where files were saved.
        """
        project_root = Path(__file__).parent.parent.parent.parent
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = project_root / "output" / "data" / "onchain_analysis" / currency / "report" / timestamp
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "report.txt").write_text(report, encoding="utf-8")
        (folder / "synthesis.txt").write_text(synthesis, encoding="utf-8")

        logger.info(f"Report bundle saved to {folder}")
        return folder

    def generate_and_save(
        self,
        currency: str = "BTC",
        output_dir: str = "output/morning_notes",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Generate morning note and save to a timestamped file.

        Args:
            currency: Currency symbol (BTC, ETH).
            output_dir: Directory to save the note (created if missing).
            progress_callback: Optional callback for progress updates.

        Returns:
            Generated morning note string.
        """
        note = self.generate(currency=currency, progress_callback=progress_callback)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_path / f"{currency}_morning_note_{timestamp}.txt"

        filename.write_text(note, encoding="utf-8")
        logger.info(f"Morning note saved to {filename}")

        return note
