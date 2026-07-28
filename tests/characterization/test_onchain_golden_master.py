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

import dataclasses
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


def _build_structured_snapshot(result) -> Dict[str, Any]:
    """
    Assemble a JSON-serializable dict of every numeric field the pipeline
    computed, independent of how the text report happens to format/round
    them. Catches value drift that text formatting rounds away — see
    refactor_design_spec.md section 7.4, test_structured_data_snapshot.

    T10 (refactor_design_spec.md): rebuilt from the typed
    ``OnChainAnalysisResult`` -- the analyzer's own gex_dex_structured/
    buy_sell_flow_structured/volatility_surface_structured/
    market_wide_structured/trend_data dict bookkeeping this used to read
    directly is deleted. Each per-expiration field is reconstructed via
    the exact same ``.to_dict()`` shim the legacy dict consumers
    (``repository.save_onchain_snapshot`` etc.) already rely on, so the
    shape is unchanged for every field except ``market_wide_structured``
    (see the reviewed golden delta noted below).
    """
    per_expiration: Dict[str, Any] = {}
    for expiration in result.expiration_names():
        bundle = result.bundle(expiration)
        analysis_dict = bundle.analysis.to_dict()

        flow_dict = None
        if bundle.flow is not None:
            flow_dict = bundle.flow.to_dict()
            # F6.3.4 (carried from A4 review): to_dict()'s legacy shim
            # doesn't carry the data-sufficiency bookkeeping keys the
            # legacy flow_result_dict always added explicitly.
            flow_dict["sufficient_data"] = bundle.flow.sufficient_data
            flow_dict["low_confidence"] = bundle.flow.low_confidence
            flow_dict["lookback_hours"] = bundle.flow.lookback_hours

        trend_dict = None
        if bundle.trend is not None:
            t = bundle.trend
            trend_dict = {}
            if t.max_pain_strike is not None:
                trend_dict["max_pain_strike"] = t.max_pain_strike
            if t.call_oi is not None:
                trend_dict["call_oi"] = t.call_oi
            if t.put_oi is not None:
                trend_dict["put_oi"] = t.put_oi
            # pc_ratio/volume_ratio were always set by the legacy
            # _fetch_trend_data (even to None), unlike the other four
            # fields -- mirrored here for exact byte parity.
            trend_dict["pc_ratio"] = t.pc_ratio
            if t.total_volume is not None:
                trend_dict["total_volume"] = t.total_volume
            trend_dict["volume_ratio"] = t.volume_ratio

        per_expiration[expiration] = {
            "underlying_price": analysis_dict["underlying_price"],
            "total_instruments": analysis_dict["total_instruments"],
            "call_count": analysis_dict["call_count"],
            "put_count": analysis_dict["put_count"],
            "max_pain": analysis_dict["max_pain"],
            "put_call_ratio": analysis_dict["put_call_ratio"],
            "volume_stats": analysis_dict["volume_stats"],
            "moneyness": analysis_dict["moneyness"],
            "support_resistance": analysis_dict["support_resistance"],
            "gex_dex": bundle.gex_dex.to_dict() if bundle.gex_dex is not None else None,
            "buy_sell_flow": flow_dict,
            "volatility_surface": (
                bundle.vol_surface.to_dict() if bundle.vol_surface is not None else None
            ),
            "trend_data": trend_dict,
        }

    mm = result.market_metrics
    market_metrics_dict = {
        "dvol": mm.dvol, "iv_percentile": mm.iv_percentile, "iv_rank": mm.iv_rank,
        "current_funding": mm.current_funding, "funding_8h": mm.funding_8h,
    }

    return {
        "currency": result.currency,
        "underlying_price": result.underlying_price,
        "market_metrics": market_metrics_dict,
        # T10 golden delta (reviewed, additive-only in practice against
        # this fixture -- confirmed via a direct dict-diff before
        # re-recording): analyzer.market_wide_structured (the legacy flat
        # accumulator dict) is deleted along with the rest of the report-
        # text bookkeeping. MarketWideResult.to_flat_dict() is the typed
        # model's own flattened-dict reproduction (already used by
        # SynthesisMapper.build_market_wide) and the closest available
        # equivalent -- it omits a small number of keys the legacy dict
        # carried that nothing downstream of the typed pipeline reads
        # (e.g. funding_annualized_pct), by the same design note already
        # on to_flat_dict() itself.
        "market_wide_structured": result.market_wide.to_flat_dict(),
        "gex_dex_aggregate": (
            result.market_wide.aggregate_gex_dex.to_dict()
            if result.market_wide.aggregate_gex_dex is not None else None
        ),
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
    snapshot_text = _dump_json(_build_structured_snapshot(pipeline_result["result"]))

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

    T8 (refactor_design_spec.md): the legacy text-splitter this test used
    to pin scanned "EXPIRATION:" to "EXPIRATION:", so the LAST expiration's
    slice ran to the end of the full report string and picked up the
    trailing MARKET-WIDE METRICS block — never the method's own documented
    intent ("each expiration folder gets only its section"). The T8
    rewrite renders straight from the typed result and does not reproduce
    that leak, so this test's own slice is bounded at the MARKET-WIDE
    METRICS marker too (reviewed, narrow deviation from "unmodified" — see
    task-A5-report.md: VolatilityConeResult's current fields can't
    reconstruct the legacy cone table well enough to make byte-identical
    reproduction of the leak worth the model change it would require).
    """
    report = pipeline_result["report"]
    currency = pipeline_result["currency"]
    output_root = pipeline_result["output_root"]

    lines = report.split("\n")
    first_exp_idx = next(i for i, line in enumerate(lines) if line.startswith("EXPIRATION:"))
    header = "\n".join(lines[:first_exp_idx])

    market_wide_title_idx = next(
        (i for i, line in enumerate(lines) if line.strip() == "MARKET-WIDE METRICS"), None
    )
    # The market-wide block's own opening separator sits one line above its
    # title — exclude it too, it belongs to that block, not the expiration.
    expirations_end_idx = market_wide_title_idx - 1 if market_wide_title_idx is not None else len(lines)

    exp_sections: Dict[str, str] = {}
    current_exp = None
    current_lines = []
    for line in lines[first_exp_idx:expirations_end_idx]:
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
    refactor_design_spec.md section T6/T10 proof: run the pipeline against
    the fixture (return_result=True dual-write) and assert the builder's
    typed result agrees with independently-recomputed values from
    OnChainMetricsCalculator's own kept methods/attributes.

    T10 note: before this task, the analyzer additionally kept a parallel
    dict-shaped bookkeeping of every phase's output (gex_dex_structured,
    buy_sell_flow_structured, volatility_surface_structured,
    market_wide_structured, trend_data) that this test used to compare the
    builder's result against, field-by-field. T10 deletes all of that
    bookkeeping by design — ``OnChainAnalysisResult`` is now the SOLE
    aggregate the rest of the pipeline (report rendering, synthesis,
    persistence) reads, not a second representation kept in sync with a
    first, so there is no longer an independent "legacy dict" to compare
    against for those fields. What remains directly comparable is
    ``analyze_expiration()``'s determinism (recomputing it here must
    reproduce exactly what the builder captured at the top of
    ``fetch_and_analyze``) and the state ``OnChainMetricsCalculator`` still
    keeps as plain attributes (``parsed_data``, ``market_metrics``,
    ``_recent_trades``) — both checked below. Whole-report correctness
    (including gex_dex/flow/vol_surface/market_wide content) is covered by
    ``test_full_report_matches_golden`` and
    ``test_structured_data_snapshot``, which build their comparison
    directly from this same ``result`` (see ``_build_structured_snapshot``).
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

        # ExpirationAnalysisResult must match analyze_expiration()'s own
        # (typed, T10) return value — analyze_expiration recomputes
        # deterministically from parsed_data, so calling it again here
        # reproduces the exact values the builder captured at the top of
        # fetch_and_analyze.
        #
        # bugfix_spec.md Item 10 (task C1): put_call_ratio.bias/
        # percentile_90d/history_n_90d are the one deliberate exception.
        # OnChainAnalysisService._apply_pcr_percentile_classification
        # overwrites them AFTER the builder step (analyze_expiration is
        # core -- no DB access -- so calling it fresh here always
        # reproduces the OLD hard-coded-threshold bias, never the
        # percentile-based one). The ratio/OI values it was computed from
        # must still match exactly.
        fresh_analysis = analyzer.analyze_expiration(expiration)
        assert bundle.analysis.put_call_ratio.ratio == fresh_analysis.put_call_ratio.ratio
        assert (
            bundle.analysis.put_call_ratio.total_call_oi
            == fresh_analysis.put_call_ratio.total_call_oi
        )
        assert (
            bundle.analysis.put_call_ratio.total_put_oi
            == fresh_analysis.put_call_ratio.total_put_oi
        )
        reconciled = dataclasses.replace(
            bundle.analysis, put_call_ratio=fresh_analysis.put_call_ratio,
        )
        assert reconciled == fresh_analysis

    assert checked_expirations > 0, "No expirations found — fixture may be empty"
    assert result.expiration_names() == tuple(sorted(analyzer.get_expirations())), (
        "Builder must only include expirations analyze_expiration() actually produced a result for"
    )

    # Market-wide: dvol is the one field still directly comparable against
    # a plain analyzer attribute (market_metrics, kept as real cross-phase
    # data — see the on_chain_analyzer.py module docstring). Everything
    # else on MarketWideResult is checked by test_structured_data_snapshot
    # against the golden JSON instead.
    assert result.market_wide.dvol == analyzer.market_metrics.get("dvol")
