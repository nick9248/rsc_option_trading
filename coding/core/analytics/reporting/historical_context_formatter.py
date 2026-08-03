"""
Report rendering for HistoricalNormalizer output
(institutional_metrics_spec.md section 1(c)).

One line per metric: value, then 30d and 90d percentile/z/regime, so a
bare number is never printed without its own-history context. Regime for
the 90d window is recomputed from ``percentile_90d`` via
``HistoricalNormalizer.regime_label`` at render time rather than stored on
``NormalizedMetric`` -- the dataclass (institutional_metrics_spec.md
section 1(c)) only carries ``regime_30d``, so this keeps that class exactly
as specified while still surfacing a 90d regime label in the report.

C1 review Important #1/#4: the block header names the front-month
expiration these per-expiry metrics (net GEX, PCR-OI, total OI) describe,
and a "STALE: history ends {ts}" line is prepended when
``OnChainAnalysisService._compute_historical_context_staleness`` finds the
most-stale queried table's data older than the spec's 3h threshold.
"""

from datetime import datetime
from typing import Dict, Optional

from coding.core.analytics.historical_normalizer import HistoricalNormalizer, NormalizedMetric

_SUB_SEPARATOR = "-" * 80

# Fixed render order and display label per metric key -- matches
# institutional_metrics_spec.md section 1(c)'s example block ordering.
_METRIC_ORDER = ("net_gex", "pcr_oi", "total_oi", "dvol", "funding")
_METRIC_LABELS = {
    "net_gex": "Net GEX",
    "pcr_oi": "PCR (OI)",
    "total_oi": "Total OI",
    "dvol": "DVOL",
    "funding": "Funding (8h)",
}


def _format_value(value: float, unit: str) -> str:
    """Format ``value`` for display according to its unit."""
    if unit == "USD":
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:+.2f}M USD"
        return f"{value:+,.2f} USD"
    if unit == "ratio":
        return f"{value:.3f}"
    if unit == "vol pts":
        return f"{value:.2f}"
    if unit == "%":
        # value is a raw decimal fraction (e.g. 0.0007 == 0.07%).
        return f"{value * 100:+.4f}%"
    if unit == "coins":
        return f"{value:,.0f} coins"
    return f"{value}"


def _format_window(percentile, z, regime, n: int, sufficient: bool) -> str:
    """
    Format one window's ("30d" or "90d") percentile/z/regime, or the
    insufficient-history fallback.
    """
    if not sufficient or percentile is None:
        return f"n/a ({n} obs)"
    z_str = f"z{z:+.2f}" if z is not None else "z n/a"
    regime_str = regime if regime is not None else "n/a"
    return f"p{percentile:.0f}  {z_str}  {regime_str}"


def format_metric_value(value: float, unit: str) -> str:
    """
    Public wrapper of ``_format_value`` (institutional_metrics_spec.md
    section 9(c), Task D3): the GUI's normalized-metric strip needs the
    exact same value-by-unit formatting the text report already uses, so
    it reuses this function rather than duplicating the unit-formatting
    rules in ``on_chain_analysis_tab.py`` (CLAUDE.md Code Quality Checklist
    section 3 -- no business logic in the GUI layer).
    """
    return _format_value(value, unit)


def format_window_summary(
    percentile: Optional[float],
    z: Optional[float],
    regime: Optional[str],
    n: int,
    sufficient: bool,
) -> str:
    """
    Public wrapper of ``_format_window`` (institutional_metrics_spec.md
    section 9(c), Task D3) -- see ``format_metric_value``'s docstring for
    why the GUI reuses this instead of re-deriving it.
    """
    return _format_window(percentile, z, regime, n, sufficient)


def _format_metric_line(metric: NormalizedMetric) -> str:
    label = _METRIC_LABELS.get(metric.name, metric.name)
    value_str = _format_value(metric.value, metric.unit)

    window_30d = _format_window(
        metric.percentile_30d, metric.z_30d, metric.regime_30d,
        metric.n_30d, metric.sufficient,
    )

    # regime_90d is not stored on NormalizedMetric (section 1(c)'s dataclass
    # only has regime_30d) -- recompute it from percentile_90d here.
    sufficient_90d = metric.n_90d >= HistoricalNormalizer.MIN_OBS
    regime_90d = HistoricalNormalizer.regime_label(metric.percentile_90d)
    window_90d = _format_window(
        metric.percentile_90d, metric.z_90d, regime_90d,
        metric.n_90d, sufficient_90d,
    )

    return f"{label:<16} {value_str:<14} 30d: {window_30d}  |  90d: {window_90d}"


def format_historical_context_section(
    metrics: Dict[str, NormalizedMetric],
    front_month_expiration: Optional[str] = None,
    stale_since: Optional[datetime] = None,
) -> str:
    """
    Render the HISTORICAL CONTEXT section: one line per metric present in
    ``metrics``, in the fixed order net GEX / PCR (OI) / Total OI / DVOL /
    Funding (8h) -- metrics absent from the dict (e.g. VRP, deliberately
    never wired -- see OnChainAnalysisService._build_normalized_metrics)
    are simply not printed.

    Args:
        metrics: Per-metric NormalizedMetric, keyed by name.
        front_month_expiration: Which expiration the per-expiry metrics
            (net GEX, PCR-OI, total OI) describe (C1 review Important #1)
            -- printed in the header when given; omitted when None (e.g.
            no expiration parsed as a valid date).
        stale_since: When not None, prefixes the block with "STALE:
            history ends {ts}" (C1 review Important #4 / institutional_
            metrics_spec.md section 1(c)) -- the caller
            (OnChainAnalysisService._compute_historical_context_staleness)
            already applied the 3h threshold, so any non-None value here
            renders the prefix unconditionally.

    Returns "" when ``metrics`` is empty (matches the codebase's existing
    "no data -> no section" convention, e.g. render_market_wide_from_result).
    """
    if not metrics:
        return ""

    header = "HISTORICAL CONTEXT"
    if front_month_expiration is not None:
        header += f" (front-month: {front_month_expiration})"

    lines = [header, _SUB_SEPARATOR]
    if stale_since is not None:
        lines.append(f"STALE: history ends {stale_since.strftime('%Y-%m-%d %H:%M')}")

    for key in _METRIC_ORDER:
        metric = metrics.get(key)
        if metric is not None:
            lines.append(_format_metric_line(metric))

    # Any metric not in the known fixed order (forward-compatible with a
    # future AVAILABLE metric) still gets printed, appended after the
    # fixed-order ones rather than silently dropped.
    for key, metric in metrics.items():
        if key not in _METRIC_ORDER:
            lines.append(_format_metric_line(metric))

    return "\n".join(lines)
