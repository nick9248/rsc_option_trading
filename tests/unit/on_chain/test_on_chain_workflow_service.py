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
    save_report,
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
    Patch the collaborators OnChainWorkflowService composes, so ``run()`` can be exercised without a live API, DB
    or file write.
    """
    fake_result = _make_result()

    with patch(
        "coding.service.on_chain.on_chain_workflow_service.OnChainAnalysisService"
    ) as mock_service_cls, patch(
        "coding.service.on_chain.on_chain_workflow_service.save_report", return_value=Path("/tmp/bundle")
    ) as mock_save, patch(
        "coding.service.on_chain.on_chain_workflow_service.DeribitApiService"
    ) as mock_api_cls, patch(
        "coding.service.on_chain.on_chain_workflow_service.DatabaseRepository"
    ) as mock_repo_cls:
        mock_service = mock_service_cls.return_value
        mock_service.fetch_and_analyze.return_value = ("REPORT TEXT", fake_result)

        # DeribitApiService used as a context manager in run()
        mock_api_instance = MagicMock()
        mock_api_cls.return_value.__enter__.return_value = mock_api_instance
        mock_api_cls.return_value.__exit__.return_value = False

        yield {
            "service_cls": mock_service_cls,
            "service": mock_service,
            "save": mock_save,
            "api_cls": mock_api_cls,
            "repo_cls": mock_repo_cls,
            "result": fake_result,
        }


class TestSingleCallContract:
    def test_run_returns_workflow_output_with_all_three_fields(self, patched_collaborators):
        workflow = OnChainWorkflowService("BTC")
        output = workflow.run()

        assert isinstance(output, OnChainWorkflowOutput)
        assert output.result is patched_collaborators["result"]
        assert output.report_text == "REPORT TEXT"
        assert not hasattr(output, "synthesis_text")
        assert output.bundle_path == Path("/tmp/bundle")

    def test_run_calls_fetch_and_analyze_exactly_once_with_return_result(self, patched_collaborators):
        workflow = OnChainWorkflowService("ETH")
        workflow.run()

        mock_service = patched_collaborators["service"]
        assert mock_service.fetch_and_analyze.call_count == 1
        _, kwargs = mock_service.fetch_and_analyze.call_args
        assert kwargs["currency"] == "ETH"
        assert kwargs["return_result"] is True

    def test_run_saves_the_report_text_once(self, patched_collaborators):
        workflow = OnChainWorkflowService("BTC")
        workflow.run()

        patched_collaborators["save"].assert_called_once_with("BTC", "REPORT TEXT")

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

    def test_save_report_error_propagates(self, patched_collaborators):
        patched_collaborators["save"].side_effect = OSError("disk full")
        workflow = OnChainWorkflowService("BTC")

        with pytest.raises(OSError, match="disk full"):
            workflow.run()


class TestSaveReport:
    def test_writes_only_report_txt_in_a_timestamped_folder(self, tmp_path):
        folder = save_report("BTC", "REPORT TEXT", output_directory=tmp_path)

        assert folder.parent == tmp_path / "BTC" / "report"
        assert [path.name for path in folder.iterdir()] == ["report.txt"]
        assert (folder / "report.txt").read_text(encoding="utf-8") == "REPORT TEXT"
