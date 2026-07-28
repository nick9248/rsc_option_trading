"""
Unit tests for coding.core.analytics.reporting.historical_context_formatter
(institutional_metrics_spec.md section 1(c) report format).
"""

from coding.core.analytics.historical_normalizer import NormalizedMetric
from coding.core.analytics.reporting.historical_context_formatter import (
    format_historical_context_section,
)


def _metric(name, value, unit, percentile_30d=61.0, z_30d=0.35, percentile_90d=44.0,
            z_90d=-0.12, regime_30d="ELEVATED", n_30d=720, n_90d=2160, sufficient=True):
    return NormalizedMetric(
        name=name, value=value,
        percentile_30d=percentile_30d, z_30d=z_30d,
        percentile_90d=percentile_90d, z_90d=z_90d,
        regime_30d=regime_30d, n_30d=n_30d, n_90d=n_90d,
        sufficient=sufficient, unit=unit,
    )


def test_empty_dict_returns_empty_string():
    assert format_historical_context_section({}) == ""


def test_net_gex_line_format():
    metrics = {"net_gex": _metric("net_gex", 22_700_000.0, "USD")}
    text = format_historical_context_section(metrics)
    assert "Net GEX" in text
    assert "22.70M USD" in text
    assert "30d: p61" in text
    assert "z+0.35" in text
    assert "ELEVATED" in text
    assert "90d: p44" in text
    assert "z-0.12" in text
    assert "NORMAL" in text  # regime_90d recomputed from percentile_90d=44 -> NORMAL


def test_pcr_oi_ratio_formatting():
    metrics = {"pcr_oi": _metric("pcr_oi", 0.135, "ratio", percentile_30d=18.0, regime_30d="LOW")}
    text = format_historical_context_section(metrics)
    assert "PCR (OI)" in text
    assert "0.135" in text
    assert "p18" in text
    assert "LOW" in text


def test_dvol_and_funding_formatting():
    metrics = {
        "dvol": _metric("dvol", 37.69, "vol pts"),
        "funding": _metric("funding", 0.0007, "%"),
    }
    text = format_historical_context_section(metrics)
    assert "DVOL" in text
    assert "37.69" in text
    assert "Funding (8h)" in text
    assert "0.0700%" in text  # 0.0007 as a fraction -> 0.0700% displayed


def test_total_oi_formatting():
    metrics = {"total_oi": _metric("total_oi", 92345.0, "coins")}
    text = format_historical_context_section(metrics)
    assert "Total OI" in text
    assert "92,345" in text


def test_insufficient_history_renders_n_a():
    metrics = {
        "net_gex": _metric(
            "net_gex", 100.0, "USD",
            percentile_30d=None, z_30d=None, percentile_90d=None, z_90d=None,
            regime_30d=None, n_30d=18, n_90d=18, sufficient=False,
        )
    }
    text = format_historical_context_section(metrics)
    assert "30d: n/a (18 obs)" in text
    assert "90d: n/a (18 obs)" in text


def test_zero_variance_z_is_n_a():
    metrics = {
        "dvol": _metric(
            "dvol", 40.0, "vol pts",
            z_30d=None, z_90d=None, percentile_30d=50.0, percentile_90d=50.0,
            regime_30d="NORMAL",
        )
    }
    text = format_historical_context_section(metrics)
    assert "z n/a" in text


def test_negative_net_gex_shows_sign():
    metrics = {"net_gex": _metric("net_gex", -8_431_002.0, "USD")}
    text = format_historical_context_section(metrics)
    assert "-8.43M USD" in text


def test_fixed_metric_order_regardless_of_dict_insertion_order():
    metrics = {
        "funding": _metric("funding", 0.0007, "%"),
        "net_gex": _metric("net_gex", 22_700_000.0, "USD"),
        "dvol": _metric("dvol", 37.69, "vol pts"),
    }
    text = format_historical_context_section(metrics)
    net_gex_pos = text.index("Net GEX")
    dvol_pos = text.index("DVOL")
    funding_pos = text.index("Funding")
    assert net_gex_pos < dvol_pos < funding_pos


def test_header_title_present():
    metrics = {"dvol": _metric("dvol", 37.69, "vol pts")}
    text = format_historical_context_section(metrics)
    assert "HISTORICAL CONTEXT" in text
