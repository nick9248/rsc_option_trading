"""
Text formatting for the DELTA-ADJUSTED FLOW report section
(institutional_metrics_spec.md section 6 / task C7).

Renders the signed directional (HIRO) flow and premium (Decision D8:
signed delta is the PRIMARY metric) side by side with the unsigned gross
|delta| hedging-impact magnitude -- both are always shown together, never
just one, per the brief's scope item 3.

Review fix (Minor #3): uses the ASCII label "Delta" rather than the U+0394
"Δ" character in rendered output -- this machine's default console codec
is cp1252, which raises ``UnicodeEncodeError`` on that character. The
report is always written to files with explicit ``encoding="utf-8"``
(``on_chain_analysis_service.py``'s report-save path), so the Unicode
character itself was never unsafe for the FILE -- but any caller that
prints this string straight to a cp1252 console (a very real path: ad hoc
debugging, some log handlers) would crash. ASCII-safe wins with no loss of
information.
"""

from datetime import datetime
from typing import Optional, Sequence

from coding.core.analytics.reporting.flow_formatter import format_flow_strike_tables
from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.core.analytics.results.flow_results import FlowResult

_SEPARATOR = "-" * 80
_ALL_KEY = "ALL"


def _format_lookback_label(lookback_hours: float) -> str:
    if lookback_hours == int(lookback_hours):
        return f"{lookback_hours:.0f}h"
    return f"{lookback_hours:.1f}h"


def _format_row(label: str, bucket: FlowBucket) -> str:
    return (
        f"{label:<16}{bucket.hiro_usd:>+20,.0f}{bucket.premium_usd:>+16,.0f}"
        f"{bucket.gross_delta_usd:>+24,.0f}"
    )


def _find_total(buckets: Sequence[FlowBucket]) -> Optional[FlowBucket]:
    return next((b for b in buckets if b.expiration == _ALL_KEY), None)


def format_delta_adjusted_flow_section(
    bucket: Optional[FlowBucket],
    flow_result: Optional[FlowResult],
) -> str:
    """
    Render the per-expiry DELTA-ADJUSTED FLOW section
    (institutional_metrics_spec.md section 6(c) / section 9(b) per-expiry
    order item 5, Task D2 independent-review fix, Important #1).

    Spec 6(c): "Report (replaces the contract-count 'net flow' headline;
    keep the per-strike table)" -- this is a MERGE instruction, not a
    choice between the old buy/sell-flow-analyzer section and the new
    HIRO/premium/gross series. This function implements the merge: this
    expiration's own signed HIRO/premium/gross line (from ``bucket``, the
    per-expiry entry in ``OnChainAnalysisResult.delta_flow_buckets``)
    REPLACES the old "EXPIRATION-LEVEL FLOW" bias/trend headline
    (``format_flow_section``'s contract-count claim), while the existing
    TOP 5 STRIKES BY BUYING/SELLING PRESSURE tables (from ``flow_result``,
    the buy/sell-flow analyzer -- ``flow_formatter.format_flow_strike_
    tables``) are KEPT, unchanged.

    ``bucket`` and ``flow_result`` come from two independent data sources
    (the persisted ``flow_delta_hourly`` table vs. the buy/sell-flow
    analyzer's own ad hoc trade fetch) with independent availability --
    each is rendered whenever it is present, regardless of the other's
    state. Returns "" only when BOTH are None (matches the codebase's
    "no data -> no section" convention).

    Args:
        bucket: This expiration's own FlowBucket (``expiration`` field
            matching, not ``"ALL"``), or None when ``delta_flow_buckets``
            has no persisted row for this expiration in the window.
        flow_result: This expiration's buy/sell-flow result, or None.

    Returns:
        Formatted multi-line string, or "" when both inputs are None.
    """
    if bucket is None and flow_result is None:
        return ""

    lines = ["DELTA-ADJUSTED FLOW", _SEPARATOR]

    if bucket is not None:
        lines.append(
            f"{'':<16}{'Directional (HIRO)':>20}{'Premium':>16}{'Gross |Delta| notional':>24}"
        )
        lines.append(_format_row("This expiry", bucket))
        skip_rate = bucket.skip_rate
        if skip_rate is None:
            lines.append(f"Trades: {bucket.trade_count} (no trade activity in window)")
        else:
            lines.append(
                f"Trades: {bucket.trade_count:,}  (skipped {bucket.skipped_count}, "
                f"{skip_rate * 100:.2f}%)"
            )
    else:
        lines.append("HIRO/premium/gross: no data for this expiry")
    lines.append("")

    if flow_result is not None:
        if not flow_result.sufficient_data:
            lines.append(
                f"  ** INSUFFICIENT FLOW DATA ** - {flow_result.trade_count} trade(s) in "
                "window. Per-strike flow tables suppressed."
            )
            lines.append("")
        else:
            lines.append(format_flow_strike_tables(flow_result))

    return "\n".join(lines)


def format_delta_flow_section(
    buckets: Sequence[FlowBucket],
    lookback_hours: float,
    hours_present: int = 0,
    stale_since: Optional[datetime] = None,
) -> str:
    """
    Render the DELTA-ADJUSTED FLOW section.

    Args:
        buckets: FlowBucket rows for the window. Must include exactly one
            with ``expiration == "ALL"`` (the currency-level total); every
            other entry is rendered as a per-expiration sub-row, sorted by
            expiration string.
        lookback_hours: Window length in hours (the report caller's SUM
            window over ``flow_delta_hourly``, typically 24).
        hours_present: Review fix (Important #4) -- how many hourly "ALL"
            rows actually exist in the window (``DatabaseRepository.
            get_delta_flow_coverage``). Always rendered as a "Coverage:"
            line, distinct from the staleness note below -- a currency
            whose feature just shipped and has only accumulated a few
            hours of history is informative to disclose even when the
            daemon is NOT currently lagging.
        stale_since: Review fix (Important #4) -- the most recently
            persisted hour, set ONLY when it is more than
            ``OnChainAnalysisService._DELTA_FLOW_STALENESS_THRESHOLD_HOURS``
            behind "now" (mirrors ``format_historical_context_section``'s
            "STALE: history ends {ts}" convention/placement). ``None``
            means fresh -- no staleness note is printed.

    Returns "" when ``buckets`` is empty, or when it has no ``"ALL"`` entry
    (a per-expiration-only shape the report cannot trust to have a total) --
    matches the codebase's existing "no data -> no section" convention
    (e.g. ``format_historical_context_section``). This happens when
    ``flow_delta_hourly`` has no rows yet for this currency/window (feature
    just shipped, or the daemon hasn't run) -- NEVER when there was genuine
    zero trading activity in the window: that case still has a real,
    persisted "ALL" row with ``trade_count == 0``, which IS rendered (see
    the zero-trades vs. all-skipped distinction below).
    """
    total = _find_total(buckets)
    if total is None:
        return ""

    per_expiration = sorted(
        (b for b in buckets if b.expiration != _ALL_KEY),
        key=lambda b: b.expiration,
    )

    lines = [
        f"DELTA-ADJUSTED FLOW ({_format_lookback_label(lookback_hours)}, taker-signed, USD notional)",
        _SEPARATOR,
    ]

    if stale_since is not None:
        lines.append(f"STALE: most recent persisted hour {stale_since.strftime('%Y-%m-%d %H:%M')}")

    lookback_label = _format_lookback_label(lookback_hours)
    lines.append(f"Coverage: {hours_present}/{lookback_label} hourly rows persisted")

    lines.append(f"{'':<16}{'Directional (HIRO)':>20}{'Premium':>16}{'Gross |Delta| notional':>24}")
    lines.append(_format_row("Total", total))
    for bucket in per_expiration:
        lines.append(_format_row(f"  {bucket.expiration}", bucket))

    skip_rate = total.skip_rate
    if skip_rate is None:
        # trade_count == skipped_count == 0 -- genuinely zero trading
        # activity in the window. A '0.00%' skip rate here would read as
        # "checked everything, found nothing wrong" when the truth is
        # "there was nothing to check" -- print an explicit statement
        # instead of a fabricated percentage.
        lines.append(f"Trades: {total.trade_count} (no trade activity in window)")
    else:
        lines.append(
            f"Trades: {total.trade_count:,}  (skipped {total.skipped_count}, {skip_rate * 100:.2f}%)"
        )
    lines.append("")

    return "\n".join(lines)
