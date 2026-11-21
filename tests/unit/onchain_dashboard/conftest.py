"""
Fixtures of the on-chain dashboard tests: a REAL typed ``OnChainAnalysisResult`` from the production pipeline run offline
against the recorded fixture (same isolation as tests/characterization/test_onchain_golden_master.py: nothing is written
into output/ and no database is touched), and a hand-built result with every optional part missing.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from coding.core.analytics.results.analysis_result import MarketMetricsResult, OnChainAnalysisResult
from coding.core.analytics.results.market_wide_results import MarketWideResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from tests.fakes.fake_deribit_api_service import FakeDeribitApiService
from tests.fakes.fake_repository import FakeDatabaseRepository
from tests.fakes.fixture_io import load_json_gz

FIXTURES_ROOT = Path(__file__).parents[2] / "fixtures" / "onchain"
FAKE_PLOTLY = "/* plotly.js stand-in */"


@pytest.fixture(scope="module")
def real_result(tmp_path_factory):
    """The typed result of the production pipeline against the recorded BTC fixture (offline, nothing written to output/)."""
    from _pytest.monkeypatch import MonkeyPatch
    from tests.conftest import apply_frozen_clock
    import coding.service.on_chain.on_chain_analysis_service as service_module

    fixture_dir = sorted(FIXTURES_ROOT.glob("BTC_*"))[-1]
    meta = load_json_gz(fixture_dir / "meta.json.gz")
    scratch = tmp_path_factory.mktemp("onchain_dashboard_pipeline")
    patch = MonkeyPatch()
    try:
        patch.setattr(service_module, "__file__", str(scratch / "coding" / "service" / "on_chain" / "on_chain_analysis_service.py"))
        apply_frozen_clock(patch, meta["recorded_at_epoch"])
        service = OnChainAnalysisService(api_service=FakeDeribitApiService(fixture_dir), repository=FakeDatabaseRepository(fixture_dir))
        _, result = service.fetch_and_analyze(meta["currency"], return_result=True)
    finally:
        patch.undo()
    return result


@pytest.fixture
def empty_result():
    """A result with no expiry and every optional market-wide part missing."""
    return OnChainAnalysisResult(
        currency="BTC", underlying_price=90000.0, generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        market_metrics=MarketMetricsResult(dvol=None, iv_percentile=None, iv_rank=None, current_funding=None, funding_8h=None),
        expirations=(),
        market_wide=MarketWideResult(
            spot_price=90000.0, currency="BTC", dvol=None, iv_percentile_365d=None, aggregate_gex_dex=None,
            term_structure=None, futures_basis=None, realized_volatility=None, variance_risk_premium=None,
            volatility_cone=None, perpetual_funding=None, block_trades=None, cross_asset_correlation=None, failed_sections=()),
        parsed_instruments={}, atm_iv_by_expiration={}, recent_trades=())
