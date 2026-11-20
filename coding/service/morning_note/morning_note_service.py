"""
Morning note generation service.

Orchestrates the full morning note pipeline:
    OnChainAnalysisService → SynthesisMapper → SynthesisEngine → Executive Summary
"""

import logging

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
