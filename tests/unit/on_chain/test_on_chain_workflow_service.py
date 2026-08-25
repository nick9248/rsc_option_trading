"""
Unit tests for OnChainWorkflowService (refactor_design_spec.md section T9).

Proof required by section 6 of the spec: "single-call contract; error
propagation". Everything here is exercised with fakes/mocks -- no live API
or DB calls.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coding.core.analytics.results.analysis_result import (
    MarketMetricsResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.market_wide_results import MarketWideResult
from coding.service.on_chain.on_chain_workflow_service import (
    OnChainWorkflowOutput,
    OnChainWorkflowService,
)


def _make_result(currency: str = "BTC") -> OnChainAnalysisResult:
    return OnChainAnalysisResult(
        currency=currency,
        underlying_price=90000.0,
        generated_at=__import__("datetime").datetime(2026, 1, 1),
        market_metrics=MarketMetricsResult(
            dvol=None, iv_percentile=None, iv_rank=None,
            current_funding=None, funding_8h=None,
        ),
        expirations=(),
        market_wide=MarketWideResult(
            spot_price=90000.0, currency=currency, dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
            failed_sections=(),
        ),
        parsed_instruments={},
        atm_iv_by_expiration={},
        recent_trades=(),
    )


@pytest.fixture
def patched_collaborators():
    """
    Patch the three collaborators OnChainWorkflowService composes, so
    ``run()`` can be exercised without a live API, DB, or synthesis engine.
    """
    fake_result = _make_result()

    with patch(
        "coding.service.on_chain.on_chain_workflow_service.OnChainAnalysisService"
    ) as mock_service_cls, patch(
        "coding.service.on_chain.on_chain_workflow_service.MorningNoteService"
    ) as mock_morning_cls, patch(
        "coding.service.on_chain.on_chain_workflow_service.DeribitApiService"
    ) as mock_api_cls, patch(
        "coding.service.on_chain.on_chain_workflow_service.DatabaseRepository"
    ) as mock_repo_cls:
        mock_service = mock_service_cls.return_value
        mock_service.fetch_and_analyze.return_value = ("REPORT TEXT", fake_result)

        mock_morning = mock_morning_cls.return_value
        mock_morning.generate.return_value = "SYNTHESIS TEXT"
        mock_morning.save_report_bundle.return_value = Path("/tmp/bundle")

        # DeribitApiService used as a context manager in run()
        mock_api_instance = MagicMock()
        mock_api_cls.return_value.__enter__.return_value = mock_api_instance
        mock_api_cls.return_value.__exit__.return_value = False

        yield {
            "service_cls": mock_service_cls,
            "service": mock_service,
            "morning_cls": mock_morning_cls,
            "morning": mock_morning,
            "api_cls": mock_api_cls,
            "repo_cls": mock_repo_cls,
            "result": fake_result,
        }


class TestSingleCallContract:
    def test_run_returns_workflow_output_with_all_four_fields(self, patched_collaborators):
        workflow = OnChainWorkflowService("BTC")
        output = workflow.run()

        assert isinstance(output, OnChainWorkflowOutput)
        assert output.result is patched_collaborators["result"]
        assert output.report_text == "REPORT TEXT"
        assert output.synthesis_text == "SYNTHESIS TEXT"
        assert output.bundle_path == Path("/tmp/bundle")

    def test_run_calls_fetch_and_analyze_exactly_once_with_return_result(self, patched_collaborators):
        workflow = OnChainWorkflowService("ETH")
        workflow.run()

        mock_service = patched_collaborators["service"]
        assert mock_service.fetch_and_analyze.call_count == 1
        _, kwargs = mock_service.fetch_and_analyze.call_args
        assert kwargs["currency"] == "ETH"
        assert kwargs["return_result"] is True

    def test_run_calls_generate_then_save_report_bundle_once_each(self, patched_collaborators):
        workflow = OnChainWorkflowService("BTC")
        workflow.run()

        mock_morning = patched_collaborators["morning"]
        assert mock_morning.generate.call_count == 1
        assert mock_morning.save_report_bundle.call_count == 1

    def test_save_bundle_false_skips_save_report_bundle(self, patched_collaborators):
        """
        Carried finding #3 (A6 review): morning_note_checker.py's health
        check runs the full workflow on every invocation, including
        save_report_bundle -- an unflagged side effect (timestamped folders
        under output/data/onchain_analysis/) and failure surface for what
        should be a read-only synthesis check. save_bundle=False must skip
        it entirely while still generating the synthesis.
        """
        workflow = OnChainWorkflowService("BTC")
        output = workflow.run(save_bundle=False)

        mock_morning = patched_collaborators["morning"]
        assert mock_morning.generate.call_count == 1
        assert mock_morning.save_report_bundle.call_count == 0
        assert output.bundle_path is None

    def test_save_bundle_default_true_preserves_existing_behavior(self, patched_collaborators):
        """save_bundle defaults to True -- existing callers (GUI worker) are
        unaffected by this parameter's addition."""
        workflow = OnChainWorkflowService("BTC")
        output = workflow.run()

        mock_morning = patched_collaborators["morning"]
        assert mock_morning.save_report_bundle.call_count == 1
        assert output.bundle_path == Path("/tmp/bundle")

    def test_progress_callback_forwarded_to_fetch_and_analyze(self, patched_collaborators):
        workflow = OnChainWorkflowService("BTC")
        callback = MagicMock()
        workflow.run(progress_callback=callback)

        mock_service = patched_collaborators["service"]
        _, kwargs = mock_service.fetch_and_analyze.call_args
        assert kwargs["progress_callback"] is callback

    def test_no_repository_injected_constructs_a_fresh_one(self, patched_collaborators):
        """GUI usage: OnChainWorkflowService(currency).run() must not require
        the caller to construct a DatabaseRepository/DeribitApiService."""
        workflow = OnChainWorkflowService("BTC")
        workflow.run()

        patched_collaborators["repo_cls"].assert_called_once_with()
        patched_collaborators["api_cls"].assert_called_once_with(timeout=90)

    def test_injected_repository_is_used_instead_of_a_fresh_one(self, patched_collaborators):
        """Health-checker usage: an injected repository (environment-scoped)
        must be the exact instance passed to OnChainAnalysisService, and no
        fresh DatabaseRepository() must be constructed."""
        injected_repo = MagicMock(name="injected_repo")
        workflow = OnChainWorkflowService("BTC", repository=injected_repo)
        workflow.run()

        patched_collaborators["repo_cls"].assert_not_called()
        mock_service_cls = patched_collaborators["service_cls"]
        _, kwargs = mock_service_cls.call_args
        assert kwargs["repository"] is injected_repo

    def test_injected_api_service_is_used_instead_of_a_fresh_one(self, patched_collaborators):
        injected_api = MagicMock(name="injected_api")
        workflow = OnChainWorkflowService("BTC", api_service=injected_api)
        workflow.run()

        patched_collaborators["api_cls"].assert_not_called()
        mock_service_cls = patched_collaborators["service_cls"]
        args, _ = mock_service_cls.call_args
        assert args[0] is injected_api


class TestErrorPropagation:
    def test_fetch_and_analyze_error_propagates(self, patched_collaborators):
        patched_collaborators["service"].fetch_and_analyze.side_effect = RuntimeError("api down")
        workflow = OnChainWorkflowService("BTC")

        with pytest.raises(RuntimeError, match="api down"):
            workflow.run()

    def test_morning_note_generate_error_propagates(self, patched_collaborators):
        patched_collaborators["morning"].generate.side_effect = ValueError("bad synthesis")
        workflow = OnChainWorkflowService("BTC")

        with pytest.raises(ValueError, match="bad synthesis"):
            workflow.run()

    def test_save_report_bundle_error_propagates(self, patched_collaborators):
        patched_collaborators["morning"].save_report_bundle.side_effect = OSError("disk full")
        workflow = OnChainWorkflowService("BTC")

        with pytest.raises(OSError, match="disk full"):
            workflow.run()
