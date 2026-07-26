"""
Golden-master characterization test for the on-chain analysis pipeline
(Task A2 / refactor_design_spec.md section T1 / 7.4).

Runs the exact production ``OnChainAnalysisService.fetch_and_analyze`` pipeline
offline against a recorded live fixture (via ``FakeDeribitApiService`` /
``FakeDatabaseRepository``), and asserts the report text, synthesis text,
per-expiration saved files, and a structured numeric snapshot are all
byte-identical to the stored golden files.

This is the safety net for the whole on-chain-overhaul refactor: every
subsequent task (T2-T12) proves behavior preservation by running
``pytest tests/characterization -q`` before and after its change and
expecting it to stay green (or, for an intentional, reviewed output change,
by re-running with ``--update-golden`` and describing the diff in the commit
message).

The golden files here capture POST-A1 (commit 72d3ce4) behavior — the GEX
doubling bug and the VolSurface zero-spot KeyError are already fixed, so this
is the correct baseline, not the pre-fix one.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from coding.service.morning_note.morning_note_service import MorningNoteService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from tests.fakes.fake_deribit_api_service import FakeDeribitApiService
from tests.fakes.fake_repository import FakeDatabaseRepository
from tests.fakes.fixture_io import load_json_gz

CHAR_DIR = Path(__file__).parent
FIXTURES_ROOT = CHAR_DIR.parent / "fixtures" / "onchain"
GOLDEN_DIR = CHAR_DIR.parent / "golden"

GOLDEN_REPORT = GOLDEN_DIR / "onchain_report_BTC.txt"
GOLDEN_SYNTHESIS = GOLDEN_DIR / "onchain_synthesis_BTC.txt"
GOLDEN_RESULT = GOLDEN_DIR / "onchain_result_BTC.json"

CURRENCY = "BTC"


def _find_fixture_dir(currency: str) -> Path:
    candidates = sorted(FIXTURES_ROOT.glob(f"{currency}_*"))
    if not candidates:
        raise RuntimeError(
            f"No recorded fixture found under {FIXTURES_ROOT} for {currency}. "
            "Run: python -m scripts.record_onchain_fixture --currency "
            f"{currency}"
        )
    return candidates[-1]


def _build_structured_snapshot(analyzer) -> Dict[str, Any]:
    """
    Assemble a JSON-serializable dict of every numeric field the pipeline
    computed, independent of how the text report happens to format/round
    them. Catches value drift that text formatting rounds away — see
    refactor_design_spec.md section 7.4, test_structured_data_snapshot.
    """
    per_expiration: Dict[str, Any] = {}
    for expiration in analyzer.get_expirations():
        analysis = analyzer.analyze_expiration(expiration)
        per_expiration[expiration] = {
            "underlying_price": analysis.get("underlying_price"),
            "total_instruments": analysis.get("total_instruments"),
            "call_count": analysis.get("call_count"),
            "put_count": analysis.get("put_count"),
            "max_pain": analysis.get("max_pain"),
            "put_call_ratio": analysis.get("put_call_ratio"),
            "volume_stats": analysis.get("volume_stats"),
            "moneyness": analysis.get("moneyness"),
            "support_resistance": analysis.get("support_resistance"),
            "gex_dex": analyzer.gex_dex_structured.get(expiration),
            "buy_sell_flow": analyzer.buy_sell_flow_structured.get(expiration),
            "volatility_surface": analyzer.volatility_surface_structured.get(expiration),
            "trend_data": analyzer.trend_data.get(expiration),
        }

    return {
        "currency": analyzer.currency,
        "underlying_price": analyzer.underlying_price,
        "market_metrics": analyzer.market_metrics,
        "market_wide_structured": analyzer.market_wide_structured,
        "gex_dex_aggregate": analyzer.gex_dex_structured.get("AGGREGATE"),
        "per_expiration": per_expiration,
    }


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, allow_nan=True)


@pytest.fixture(scope="module")
def fixture_dir() -> Path:
    return _find_fixture_dir(CURRENCY)


@pytest.fixture(scope="module")
def meta(fixture_dir: Path) -> Dict[str, Any]:
    return load_json_gz(fixture_dir / "meta.json.gz")


@pytest.fixture(scope="module")
def pipeline_result(fixture_dir, meta, module_monkeypatch, module_tmp_path):
    """
    Run the exact fetch_and_analyze pipeline offline against the fixture.

    Isolation measures beyond the spec's 6 listed determinism sources
    (documented in the A2 report):
      - Chart generation (Plotly HTML/PNG writes under output/charts/, plus
        the repository.get_hourly_flow_volumes call inside
        generate_flow_trend_chart) is monkeypatched to no-ops. Charts are not
        part of the asserted golden output, and running the real chart
        pipeline would (a) write real files into the project's output/
        directory as an uncontrolled side effect and (b) require a DB method
        this fixture does not record.
      - _save_reports_per_expiration hardcodes
        ``project_root = Path(__file__).parent.parent.parent.parent`` — to
        avoid writing into the real project's output/ directory, the
        service module's ``__file__`` global is monkeypatched to a path
        under a pytest tmp dir with the same 4-level nesting, so the
        production code's own Path arithmetic resolves under tmp instead.
        Production behavior/logic is unchanged; only where __file__ points.
    """
    import coding.service.on_chain.on_chain_analysis_service as oc_service_module

    module_monkeypatch.setattr(oc_service_module, "generate_flow_distribution_chart", lambda **kw: None)
    module_monkeypatch.setattr(oc_service_module, "generate_net_flow_chart", lambda **kw: None)
    module_monkeypatch.setattr(oc_service_module, "generate_flow_trend_chart", lambda **kw: None)
    module_monkeypatch.setattr(oc_service_module, "save_chart", lambda *a, **kw: "")
    module_monkeypatch.setattr(oc_service_module, "inject_hover_js", lambda *a, **kw: None)

    fake_module_file = module_tmp_path / "coding" / "service" / "on_chain" / "on_chain_analysis_service.py"
    module_monkeypatch.setattr(oc_service_module, "__file__", str(fake_module_file))

    # frozen_clock (tests/conftest.py) is function-scoped (uses the built-in
    # `monkeypatch` fixture); this fixture is module-scoped so the whole
    # pipeline runs once for all four tests. Apply the exact same freezing
    # logic via the shared helper instead of duplicating it.
    from tests.conftest import apply_frozen_clock

    apply_frozen_clock(module_monkeypatch, meta["recorded_at_epoch"])

    api = FakeDeribitApiService(fixture_dir)
    repo = FakeDatabaseRepository(fixture_dir)
    service = OnChainAnalysisService(api_service=api, repository=repo)

    report, analyzer, result = service.fetch_and_analyze(
        meta["currency"], return_analyzer=True, return_result=True
    )
    synthesis = MorningNoteService(service).generate(result)

    return {
        "report": report,
        "analyzer": analyzer,
        "result": result,
        "synthesis": synthesis,
        "output_root": module_tmp_path,
        "currency": meta["currency"],
    }


# ── module-scoped monkeypatch / tmp_path (pytest's built-ins are function-scoped) ──


@pytest.fixture(scope="module")
def module_monkeypatch():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def module_tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("onchain_golden_master")


# ── Tests ────────────────────────────────────────────────────────────────────


def test_full_report_matches_golden(pipeline_result, update_golden):
    report = pipeline_result["report"]
    if update_golden:
        GOLDEN_REPORT.write_text(report, encoding="utf-8")
        pytest.skip("--update-golden: rewrote tests/golden/onchain_report_BTC.txt")

    assert GOLDEN_REPORT.exists(), (
        f"{GOLDEN_REPORT} does not exist — run pytest with --update-golden once to record it."
    )
    expected = GOLDEN_REPORT.read_text(encoding="utf-8")
    assert report == expected


def test_synthesis_matches_golden(pipeline_result, update_golden):
    synthesis = pipeline_result["synthesis"]
    if update_golden:
        GOLDEN_SYNTHESIS.write_text(synthesis, encoding="utf-8")
        pytest.skip("--update-golden: rewrote tests/golden/onchain_synthesis_BTC.txt")

    assert GOLDEN_SYNTHESIS.exists(), (
        f"{GOLDEN_SYNTHESIS} does not exist — run pytest with --update-golden once to record it."
    )
    expected = GOLDEN_SYNTHESIS.read_text(encoding="utf-8")
    assert synthesis == expected


def test_structured_data_snapshot(pipeline_result, update_golden):
    """
    A JSON dump of the result's numeric fields, independent of text
    formatting — catches value drift that rounding/formatting in the text
    report would round away.
    """
    snapshot_text = _dump_json(_build_structured_snapshot(pipeline_result["analyzer"]))

    if update_golden:
        GOLDEN_RESULT.write_text(snapshot_text, encoding="utf-8")
        pytest.skip("--update-golden: rewrote tests/golden/onchain_result_BTC.json")

    assert GOLDEN_RESULT.exists(), (
        f"{GOLDEN_RESULT} does not exist — run pytest with --update-golden once to record it."
    )
    expected = GOLDEN_RESULT.read_text(encoding="utf-8")
    assert snapshot_text == expected


def test_per_expiration_files_match_golden(pipeline_result):
    """
    Each file _save_reports_per_expiration wrote under
    output/data/onchain_analysis/{currency}/{expiration}/report_*.txt must
    equal the full report's header plus that expiration's own section —
    i.e. the on-disk split is a lossless slice of the in-memory report,
    never a separate rendering.
    """
    report = pipeline_result["report"]
    currency = pipeline_result["currency"]
    output_root = pipeline_result["output_root"]

    lines = report.split("\n")
    first_exp_idx = next(i for i, line in enumerate(lines) if line.startswith("EXPIRATION:"))
    header = "\n".join(lines[:first_exp_idx])

    exp_sections: Dict[str, str] = {}
    current_exp = None
    current_lines = []
    for line in lines[first_exp_idx:]:
        if line.startswith("EXPIRATION:"):
            if current_exp and current_lines:
                exp_sections[current_exp] = "\n".join(current_lines)
            current_exp = line.split(":", 1)[1].strip()
            current_lines = [line]
        elif current_exp:
            current_lines.append(line)
    if current_exp and current_lines:
        exp_sections[current_exp] = "\n".join(current_lines)

    onchain_dir = output_root / "output" / "data" / "onchain_analysis" / currency
    assert onchain_dir.exists(), f"Expected per-expiration output dir {onchain_dir} to exist"

    checked = 0
    for expiration, section_content in exp_sections.items():
        exp_dir = onchain_dir / expiration
        report_files = list(exp_dir.glob("report_*.txt"))
        assert len(report_files) == 1, (
            f"Expected exactly one report file for {expiration} under {exp_dir}, found {report_files}"
        )
        saved_content = report_files[0].read_text(encoding="utf-8")

        expected_content = header
        if header and not header.endswith("\n"):
            expected_content += "\n"
        expected_content += "\n" + section_content

        assert saved_content == expected_content, f"Per-expiration file mismatch for {expiration}"
        checked += 1

    assert checked == len(exp_sections), "Not every expiration section was verified"
    assert checked > 0, "No expiration sections found — fixture may be empty"


def test_builder_result_matches_analyzer_dicts(pipeline_result):
    """
    refactor_design_spec.md section T6 proof: run the pipeline against the
    fixture (return_result=True dual-write) and assert every builder field
    equals its analyzer-dict counterpart. This is the T6 safety net — the
    builder must describe the SAME run the analyzer/report/golden-master
    already describe, not a parallel computation that happens to agree on
    a synthetic test case.
    """
    analyzer = pipeline_result["analyzer"]
    result = pipeline_result["result"]

    assert result.currency == analyzer.currency
    assert result.underlying_price == analyzer.underlying_price
    assert result.parsed_instruments == {
        exp: tuple(instr) for exp, instr in analyzer.parsed_data.items()
    }
    assert result.recent_trades == tuple(analyzer._recent_trades)

    # Market metrics
    mm = result.market_metrics
    assert mm.dvol == analyzer.market_metrics.get("dvol")
    assert mm.iv_percentile == analyzer.market_metrics.get("iv_percentile")
    assert mm.iv_rank == analyzer.market_metrics.get("iv_rank")
    assert mm.current_funding == analyzer.market_metrics.get("current_funding")
    assert mm.funding_8h == analyzer.market_metrics.get("funding_8h")

    checked_expirations = 0
    for expiration in result.expiration_names():
        bundle = result.bundle(expiration)
        assert bundle is not None
        checked_expirations += 1

        # ExpirationAnalysisResult must match analyze_expiration()'s own dict
        # (analyze_expiration recomputes deterministically from parsed_data,
        # so calling it again here reproduces the exact values the builder
        # captured at the top of fetch_and_analyze).
        assert bundle.analysis.to_dict() == analyzer.analyze_expiration(expiration)

        expected_gex_dex = analyzer.gex_dex_structured.get(expiration)
        if expected_gex_dex is not None:
            assert bundle.gex_dex is not None
            assert bundle.gex_dex.to_dict() == expected_gex_dex
        else:
            assert bundle.gex_dex is None

        expected_flow = analyzer.buy_sell_flow_structured.get(expiration)
        if expected_flow is not None:
            assert bundle.flow is not None
            # F6.3.4 (carried from A4 review): the service extends
            # to_dict()'s legacy shim with sufficient_data/low_confidence/
            # lookback_hours bookkeeping keys the typed FlowResult itself
            # never included in to_dict() — compare those separately.
            extra_keys = {"sufficient_data", "low_confidence", "lookback_hours"}
            assert bundle.flow.to_dict() == {
                k: v for k, v in expected_flow.items() if k not in extra_keys
            }
            assert bundle.flow.sufficient_data == expected_flow.get("sufficient_data")
            assert bundle.flow.low_confidence == expected_flow.get("low_confidence")
            assert bundle.flow.lookback_hours == expected_flow.get("lookback_hours")
        else:
            assert bundle.flow is None

        expected_vol_surface = analyzer.volatility_surface_structured.get(expiration)
        if expected_vol_surface is not None:
            assert bundle.vol_surface is not None
            assert bundle.vol_surface.to_dict() == expected_vol_surface
            if expected_vol_surface["atm_iv"] is not None:
                assert result.atm_iv_by_expiration[expiration] == expected_vol_surface["atm_iv"]
        else:
            assert bundle.vol_surface is None

        expected_trend = analyzer.trend_data.get(expiration)
        if expected_trend:
            assert bundle.trend is not None
            assert bundle.trend.max_pain_strike == expected_trend.get("max_pain_strike")
            assert bundle.trend.call_oi == expected_trend.get("call_oi")
            assert bundle.trend.put_oi == expected_trend.get("put_oi")
            assert bundle.trend.pc_ratio == expected_trend.get("pc_ratio")
            assert bundle.trend.total_volume == expected_trend.get("total_volume")
            assert bundle.trend.volume_ratio == expected_trend.get("volume_ratio")
        else:
            assert bundle.trend is None

    assert checked_expirations > 0, "No expirations found — fixture may be empty"
    assert result.expiration_names() == tuple(sorted(analyzer.get_expirations())), (
        "Builder must only include expirations analyze_expiration() actually produced a result for"
    )

    # Market-wide: check the fields directly analogous to the legacy dict
    # (market_wide_structured carries extra/renamed keys not on
    # MarketWideResult.to_flat_dict() by design — e.g. funding_annualized_pct
    # is new in the typed model's structured dict but not part of the
    # to_flat_dict() shape synthesis reads today).
    mw = result.market_wide
    legacy_mw = analyzer.market_wide_structured
    assert mw.spot_price == legacy_mw.get("spot_price")
    assert mw.dvol == analyzer.market_metrics.get("dvol")
    if mw.term_structure is not None:
        assert mw.term_structure.shape == legacy_mw.get("shape")
        assert mw.term_structure.spread == legacy_mw.get("spread")
        assert mw.term_structure.iv_by_dte == legacy_mw.get("iv_by_dte")
    if mw.futures_basis is not None:
        assert mw.futures_basis.futures_basis == legacy_mw.get("futures_basis")
    if mw.realized_volatility is not None:
        assert mw.realized_volatility.rv_10d == legacy_mw.get("rv_10d")
        assert mw.realized_volatility.rv_20d == legacy_mw.get("rv_20d")
        assert mw.realized_volatility.rv_30d == legacy_mw.get("rv_30d")
    if mw.perpetual_funding is not None:
        assert mw.perpetual_funding.perp_open_interest == legacy_mw.get("perp_oi")
        assert mw.perpetual_funding.funding_trend == legacy_mw.get("perp_funding_trend")
        assert mw.perpetual_funding.funding_8h == legacy_mw.get("funding_8h")
