"""Tests for save_onchain_snapshot parameter mapping.

Regression: GexDexCalculator._detect_key_levels() returns call_resistance and
put_support as GexDexLevel instances ({"strike": ..., "net_gex": ...}).
save_onchain_snapshot must extract the strike scalar — passing the object
raw makes psycopg2 fail with "can't adapt type" (observed on the VPS on
2026-07-13, saving 0 rows, originally against the pre-T4 dict shape).

refactor_design_spec.md section T10 (compatibility-map row #9):
analysis_data/gex_dex_data are the typed ExpirationAnalysisResult/
GexDexResult now (attribute access), not legacy dicts (.get()) --
ProspectiveCollector (the only production caller) constructs and passes
these same typed objects as of this same task.
"""
from unittest.mock import MagicMock, patch

from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
)
from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _leg():
    return MoneynessLeg(
        itm_oi=0.0, otm_oi=0.0, total_oi=0.0, itm_notional=0.0,
        otm_notional=0.0, total_notional=0.0, itm_pct=0.0, otm_pct=0.0,
    )


def _make_analysis_data() -> ExpirationAnalysisResult:
    """Minimal typed ExpirationAnalysisResult -- the values here are not
    what these tests assert on; only the GEX/DEX key-level flattening is."""
    return ExpirationAnalysisResult(
        expiration="25SEP26",
        underlying_price=62000.0,
        total_instruments=0,
        call_count=0,
        put_count=0,
        strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=None, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(total_call_oi=0.0, total_put_oi=0.0, ratio=0.0, bias="Neutral"),
        volume_stats=VolumeStatsResult(total_call_volume=0.0, total_put_volume=0.0, total_volume=0.0, volume_ratio=0.0),
        moneyness=MoneynessResult(calls=_leg(), puts=_leg(), totals=_leg(), oi_skew="Balanced"),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(), short_term_resistance=None, short_term_support=None,
        ),
    )


def _make_gex_dex_data(call_resistance, put_support, hvl, total_net_gex=1_234_567.89, total_net_dex=-42.5) -> GexDexResult:
    return GexDexResult(
        strike_rows=(),
        cumulative_gex={},
        cumulative_dex={},
        key_levels=GexDexKeyLevels(
            call_resistance=call_resistance, put_support=put_support, hvl=hvl, gamma_flip=None,
        ),
        spot_price=62000.0,
        total_net_gex=total_net_gex,
        total_net_dex=total_net_dex,
        currency="BTC",
    )


def _capture_params(repo, analysis_data, gex_dex_data):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_onchain_snapshot(
            snapshot_hour="2026-07-13 17:00:00",
            currency="BTC",
            expiration="25SEP26",
            analysis_data=analysis_data,
            gex_dex_data=gex_dex_data,
            underlying_price=62000.0,
        )

    return mock_cursor.execute.call_args[0][1]


def test_key_level_dicts_are_flattened_to_strike_scalars():
    """call_resistance/put_support GexDexLevel instances must not reach SQL."""
    repo = _make_repo()

    # Shape exactly as GexDexCalculator.calculate() produces with real greeks
    gex_dex_data = _make_gex_dex_data(
        call_resistance=GexDexLevel(strike=70000.0, net_gex=900_000.0),
        put_support=GexDexLevel(strike=55000.0, net_gex=-600_000.0),
        hvl=62000.0,
    )

    params = _capture_params(repo, analysis_data=_make_analysis_data(), gex_dex_data=gex_dex_data)

    for value in params:
        assert not isinstance(value, dict), f"dict leaked into SQL params: {value}"
        assert not hasattr(value, "__dataclass_fields__"), (
            f"dataclass instance leaked into SQL params: {value!r}"
        )

    # call_resistance_strike and put_support_strike are params 12 and 13 (0-indexed 11, 12)
    assert params[11] == 70000.0
    assert params[12] == 55000.0
    assert params[13] == 62000.0  # hvl is already a scalar


def test_none_key_levels_stay_none():
    """Zero-greek data (all levels None) must keep saving NULLs as before."""
    repo = _make_repo()

    gex_dex_data = _make_gex_dex_data(
        call_resistance=None, put_support=None, hvl=None,
        total_net_gex=0.0, total_net_dex=0.0,
    )

    params = _capture_params(repo, analysis_data=_make_analysis_data(), gex_dex_data=gex_dex_data)

    assert params[11] is None
    assert params[12] is None
    assert params[13] is None
