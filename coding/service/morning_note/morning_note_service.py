"""
Morning note generation service.

Orchestrates the full morning note pipeline:
    OnChainAnalysisService → SynthesisMapper → SynthesisEngine → Executive Summary
"""

import logging
from datetime import datetime
from pathlib import Path

from coding.core.analytics.results.analysis_result import OnChainAnalysisResult
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

    def generate(self, result: OnChainAnalysisResult) -> str:
        """
        Generate a morning note from a typed OnChainAnalysisResult.

        refactor_design_spec.md section T7: replaces
        ``generate_from_analyzer(analyzer)`` — ``SynthesisMapper`` now reads
        the pipeline's frozen result aggregate directly instead of an
        analyzer's dict-shaped attributes.

        Args:
            result: The typed aggregate from
                ``OnChainAnalysisService.fetch_and_analyze(..., return_result=True)``.

        Returns:
            Formatted executive summary string.
        """
        market, expiries = SynthesisMapper.build_all(result)

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

