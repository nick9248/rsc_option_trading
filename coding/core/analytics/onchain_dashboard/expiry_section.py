"""
The "Expiries" tab of the on-chain dashboard: one expiry select and one chart choice. Each view is a Plotly figure of one
expiry; the key levels and statistics of the expiry are in the caption above it. Per-strike values come from
``build_levels_table`` (already joined and computed), nothing is calculated here.
"""

from typing import Any, Dict, List, Optional, Tuple

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from coding.core.analytics.levels_table_builder import build_levels_table
from coding.core.analytics.menthorq_replication_calculator import parse_expiry_settlement
from coding.core.analytics.onchain_dashboard.formatting import (
    BLUE, GRAY, GREEN, ORANGE, PURPLE, RED, add_markers, clean, clean_all, count_text, empty_figure, has_values,
    number_text, percent_text, ratio_text, strike_text, text_or_missing, usd_text, utc_text)
from coding.core.analytics.report.figure_style import figure_json
from coding.core.analytics.report.report_page import (
    ReportSection, SwitcherControl, paragraph_fragment, switcher_configuration, switcher_fragment, table_fragment,
    view_key)
from coding.core.analytics.report.section_fragments import NOT_COMPUTED
from coding.core.analytics.results.analysis_result import ExpirationBundle, OnChainAnalysisResult
from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.core.analytics.results.vol_surface_results import MoneynessBucket
from coding.core.analytics.thresholds import OI_CHANGE_SIGNIFICANT_ABS_THRESHOLD, OI_CHANGE_SIGNIFICANT_PCT_THRESHOLD
from coding.core.analytics.results.levels_table_results import LevelsTable, LevelsTableRow

CHARTS: List[Tuple[str, str]] = [
    ("open-interest", "Open interest"), ("volume", "Volume"), ("gex", "Net GEX"), ("dex", "Net DEX"),
    ("vanna-charm", "Vanna / charm"), ("iv", "IV by strike"), ("iv-change", "IV change, 1 day"),
    ("taker-flow", "Taker flow"), ("table", "Strike table")]
SWITCHER_ID = "expiries-main"
MARKER_COLORS = {"spot": GRAY, "max pain": PURPLE, "call resistance": GREEN, "put support": RED, "zero gamma level": BLUE}
CHART_NOTES = {
    "open-interest": "Open interest in coins by strike.",
    "volume": "Traded volume in coins by strike.",
    "gex": ("Net GEX in USD per 1% spot move. Holder side assumes every contract is held long; the assumed dealer view assumes "
            "dealers are long calls and short puts. The inferred view (from taker flow) is shown only when its coverage check passed."),
    "dex": "Net DEX in coins. Holder side is the raw delta of all open contracts; the assumed dealer view is its negative.",
    "vanna-charm": "Holder-side vanna (USD per vol point) and charm (USD per day) by strike.",
    "iv": "Mark IV in percent by strike, calls and puts.",
    "iv-change": "Day-over-day change of mark IV in vol points, averaged over the call and put leg of a strike.",
    "taker-flow": "Net taker flow by strike (buys minus sells, in coins) in the flow window, calls and puts together.",
    "table": ("Every per-strike value of this expiry. Flags: CW call resistance, PS put support, HVL cumulative GEX zero "
              "strike, MP max pain (lower case: inferred view)."),
}


def ordered_bundles(result: OnChainAnalysisResult) -> List[ExpirationBundle]:
    """
    The expiries by settlement date (the stored order is alphabetical by label, which is not chronological). When a label
    does not parse, the stored order is kept.
    """
    stored = list(result.expirations)
    settlement = {bundle.expiration: parse_expiry_settlement(bundle.expiration) for bundle in stored}
    if any(value is None for value in settlement.values()):
        return stored
    return sorted(stored, key=lambda bundle: settlement[bundle.expiration])


def _strikes_and_series(table: LevelsTable, name: str) -> Tuple[List[float], List[Optional[float]]]:
    """Strikes and the values of one levels-table column (None where the strike has none)."""
    return [row.strike for row in table.rows], clean_all(getattr(row, name) for row in table.rows)


def _sign_colors(values: List[Optional[float]]) -> List[str]:
    """Green for positive, red for negative or missing."""
    return [GREEN if value is not None and value >= 0 else RED for value in values]


def _bar_layout(figure: go.Figure, y_title: str) -> None:
    figure.update_layout(barmode="group", height=460, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Strike",
                         yaxis_title=y_title, legend=dict(orientation="h", y=1.1))


def _spot_marker(spot: float) -> Dict[str, Optional[float]]:
    """The spot (index) price marker; the underlying of an expiry is its future, which sits elsewhere."""
    return {"spot": clean(spot)}


def _open_interest_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    rows = bundle.analysis.strike_rows
    strikes = [row.strike for row in rows]
    figure = go.Figure([go.Bar(x=strikes, y=clean_all(row.call_oi for row in rows), name="Call OI", marker_color=GREEN),
                        go.Bar(x=strikes, y=clean_all(row.put_oi for row in rows), name="Put OI", marker_color=RED)])
    add_markers(figure, {**_spot_marker(spot), "max pain": clean(bundle.analysis.max_pain.max_pain_strike)}, MARKER_COLORS)
    _bar_layout(figure, "Open interest (coins)")
    return figure


def _volume_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    rows = bundle.analysis.strike_rows
    strikes = [row.strike for row in rows]
    figure = go.Figure([go.Bar(x=strikes, y=clean_all(row.call_volume for row in rows), name="Call volume", marker_color=GREEN),
                        go.Bar(x=strikes, y=clean_all(row.put_volume for row in rows), name="Put volume", marker_color=RED)])
    add_markers(figure, _spot_marker(spot), MARKER_COLORS)
    _bar_layout(figure, "Volume (coins)")
    return figure


def _gex_markers(bundle: ExpirationBundle, spot: float) -> Dict[str, Optional[float]]:
    markers = _spot_marker(spot)
    if bundle.gex_dex is not None:
        levels = bundle.gex_dex.key_levels
        markers["call resistance"] = clean(levels.call_resistance.strike) if levels.call_resistance is not None else None
        markers["put support"] = clean(levels.put_support.strike) if levels.put_support is not None else None
        markers["zero gamma level"] = clean(levels.zero_gamma_level)
    return markers


def _gex_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    if not table.gex_dex_available:
        return empty_figure("Net GEX not computed: no Greeks result for this expiry.")
    strikes, holder = _strikes_and_series(table, "net_gex_holder")
    _, assumed = _strikes_and_series(table, "net_gex_assumed")
    figure = go.Figure([go.Bar(x=strikes, y=holder, name="Holder side", marker_color=BLUE),
                        go.Bar(x=strikes, y=assumed, name="Assumed dealer", marker_color=ORANGE)])
    if table.inferred_available:
        _, inferred = _strikes_and_series(table, "net_gex_inferred")
        figure.add_trace(go.Bar(x=strikes, y=inferred, name="Inferred from taker flow", marker_color=PURPLE))
    add_markers(figure, _gex_markers(bundle, spot), MARKER_COLORS)
    _bar_layout(figure, "Net GEX (USD per 1% move)")
    return figure


def _dex_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    if not table.gex_dex_available or bundle.gex_dex is None:
        return empty_figure("Net DEX not computed: no Greeks result for this expiry.")
    strikes, holder = _strikes_and_series(table, "net_dex")
    dealer_by_strike = {row.strike: clean(row.dealer_delta_exposure) for row in bundle.gex_dex.strike_rows}
    figure = go.Figure([go.Bar(x=strikes, y=holder, name="Holder side", marker_color=BLUE),
                        go.Bar(x=strikes, y=[dealer_by_strike.get(strike) for strike in strikes], name="Assumed dealer",
                               marker_color=ORANGE)])
    add_markers(figure, _spot_marker(spot), MARKER_COLORS)
    _bar_layout(figure, "Net DEX (coins)")
    return figure


def _vanna_charm_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    if not table.exposure_available:
        return empty_figure("Vanna and charm not computed: the exposure profile of this expiry is missing.")
    strikes, vanna = _strikes_and_series(table, "vex")
    _, charm = _strikes_and_series(table, "cex")
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                           subplot_titles=("Vanna exposure (USD per vol point)", "Charm exposure (USD per day)"))
    figure.add_trace(go.Bar(x=strikes, y=vanna, name="Vanna", marker_color=BLUE), row=1, col=1)
    figure.add_trace(go.Bar(x=strikes, y=charm, name="Charm", marker_color=ORANGE), row=2, col=1)
    figure.update_layout(height=560, margin=dict(l=70, r=20, t=50, b=50), showlegend=False)
    figure.update_xaxes(title_text="Strike", row=2, col=1)
    return figure


def _iv_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    if bundle.vol_surface is None:
        return empty_figure("IV by strike not computed: no volatility surface for this expiry.")
    merged = bundle.vol_surface.merged_iv_by_strike()
    strikes = list(merged)
    call_iv = clean_all(merged[strike]["call_iv"] for strike in strikes)
    put_iv = clean_all(merged[strike]["put_iv"] for strike in strikes)
    if not has_values(call_iv, put_iv):
        return empty_figure("IV by strike not computed: no mark IV for this expiry.")
    figure = go.Figure([go.Scatter(x=strikes, y=call_iv, mode="lines+markers", name="Call IV", line=dict(color=GREEN)),
                        go.Scatter(x=strikes, y=put_iv, mode="lines+markers", name="Put IV", line=dict(color=RED))])
    add_markers(figure, _spot_marker(spot), MARKER_COLORS)
    figure.update_layout(height=460, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Strike", yaxis_title="Mark IV (%)",
                         legend=dict(orientation="h", y=1.1))
    return figure


def _iv_change_reason(bundle: ExpirationBundle) -> str:
    comparison = bundle.fixed_strike_vol
    if comparison is None:
        return "no fixed-strike vol comparison for this expiry"
    if comparison.stale_prior:
        return f"the prior snapshot is not from the expected day ({comparison.expected_prior_date.isoformat()})"
    return "no strike matched between the two snapshots"


def _iv_change_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    strikes, changes = _strikes_and_series(table, "delta_1d_iv")
    if not table.delta_1d_iv_available or not has_values(changes):
        return empty_figure(f"IV change not computed: {_iv_change_reason(bundle)}.")
    figure = go.Figure([go.Bar(x=strikes, y=changes, name="IV change", marker_color=_sign_colors(changes))])
    add_markers(figure, _spot_marker(spot), MARKER_COLORS)
    _bar_layout(figure, "IV change (vol points)")
    figure.update_layout(showlegend=False)
    return figure


def _taker_flow_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    if not table.net_taker_flow_available:
        reason = "no flow result" if bundle.flow is None else f"only {bundle.flow.trade_count} trades in the window, too few"
        return empty_figure(f"Taker flow not computed: {reason}.")
    strikes, flow = _strikes_and_series(table, "net_taker_flow")
    figure = go.Figure([go.Bar(x=strikes, y=flow, name="Net taker flow", marker_color=_sign_colors(flow))])
    add_markers(figure, _spot_marker(spot), MARKER_COLORS)
    _bar_layout(figure, "Net taker flow (coins)")
    figure.update_layout(showlegend=False)
    return figure


def _flags_text(row: LevelsTableRow) -> str:
    flags = [text for text, active in (("CW", row.is_call_wall_assumed), ("PS", row.is_put_support_assumed),
                                       ("HVL", row.is_hvl_assumed), ("MP", row.is_max_pain),
                                       ("cw", row.is_call_wall_inferred), ("ps", row.is_put_support_inferred),
                                       ("hvl", row.is_hvl_inferred)) if active]
    return " ".join(flags)


def _table_figure(bundle: ExpirationBundle, table: LevelsTable, spot: float) -> go.Figure:
    if not table.rows:
        return empty_figure("Strike table empty: this expiry has no strikes.")
    headers = ["Strike", "Call OI", "Put OI", "GEX holder", "GEX assumed", "GEX inferred", "DEX holder", "VEX holder",
               "CEX holder", "Taker flow", "IV chg 1d", "Flags"]
    columns: List[List[str]] = [[] for _ in headers]
    for row in table.rows:
        cells = [strike_text(row.strike), count_text(row.call_oi), count_text(row.put_oi),
                 number_text(row.net_gex_holder, 0, signed=True), number_text(row.net_gex_assumed, 0, signed=True),
                 number_text(row.net_gex_inferred, 0, signed=True) if table.inferred_available else "not available",
                 number_text(row.net_dex, 2, signed=True), number_text(row.vex, 0, signed=True),
                 number_text(row.cex, 0, signed=True), number_text(row.net_taker_flow, 1, signed=True),
                 number_text(row.delta_1d_iv, 2, signed=True), _flags_text(row)]
        for column, cell in zip(columns, cells):
            column.append(cell)
    figure = go.Figure(go.Table(header=dict(values=headers, align="right", fill_color="#F2F3F5"),
                                cells=dict(values=columns, align="right", height=24)))
    figure.update_layout(height=70 + 25 * len(table.rows), margin=dict(l=10, r=10, t=10, b=10))
    return figure


FIGURE_BUILDERS = {"open-interest": _open_interest_figure, "volume": _volume_figure, "gex": _gex_figure,
                   "dex": _dex_figure, "vanna-charm": _vanna_charm_figure, "iv": _iv_figure,
                   "iv-change": _iv_change_figure, "taker-flow": _taker_flow_figure, "table": _table_figure}


# ------------------------------------------------------------------ caption (key levels and statistics)

def _level_row(name: str, strike: Optional[float], detail: str = "") -> List[str]:
    return [name, strike_text(strike), detail]


def key_level_rows(bundle: ExpirationBundle, spot: float) -> List[List[str]]:
    """Rows [level, strike, detail] of the key levels of one expiry (``spot`` is the index price); missing levels read "not computed"."""
    analysis = bundle.analysis
    rows = [["Spot (index)", number_text(spot, 2), ""],
            ["Underlying of this expiry (its future)", number_text(analysis.underlying_price, 2), ""], _level_row("Max pain", analysis.max_pain.max_pain_strike)]
    if bundle.gex_dex is None:
        rows.append(["GEX levels", NOT_COMPUTED, "no Greeks result for this expiry"])
    else:
        levels = bundle.gex_dex.key_levels
        call_wall, put_support = levels.call_resistance, levels.put_support
        rows += [
            _level_row("Call resistance (assumed dealer)", call_wall.strike if call_wall else None,
                       f"net GEX {usd_text(call_wall.net_gex)}" if call_wall else ""),
            _level_row("Put support (assumed dealer)", put_support.strike if put_support else None,
                       f"net GEX {usd_text(put_support.net_gex)}" if put_support else ""),
            _level_row("Cumulative GEX zero strike", levels.cumulative_gex_zero_strike,
                       "strike-axis sign change, not a re-priced gamma flip"),
            _level_row("Zero gamma level (re-priced)", levels.zero_gamma_level,
                       f"regime {text_or_missing(levels.gamma_regime)}, net GEX at spot {usd_text(levels.net_gex_at_spot)}"),
        ]
    inferred = bundle.dealer_inventory
    if inferred is not None and inferred.render_inferred:
        levels = inferred.key_levels
        rows += [
            _level_row("Call resistance (inferred)", levels.call_resistance.strike if levels.call_resistance else None),
            _level_row("Put support (inferred)", levels.put_support.strike if levels.put_support else None),
            _level_row("HVL (inferred)", levels.hvl)]
    support = analysis.support_resistance
    resistance, floor = support.short_term_resistance, support.short_term_support
    rows += [
        _level_row("Short-term resistance (call OI)", resistance.strike if resistance else None,
                   f"{count_text(resistance.open_interest)} coins" if resistance else ""),
        _level_row("Short-term support (put OI)", floor.strike if floor else None,
                   f"{count_text(floor.open_interest)} coins" if floor else "")]
    return rows


def _moneyness_bucket_text(bucket: MoneynessBucket) -> str:
    """Put/call open interest ratio of one moneyness bucket; an empty bucket is said to be empty, never 0."""
    if bucket.ratio is None:
        return "no instruments in this range"
    return f"{ratio_text(bucket.ratio)} (calls {count_text(bucket.call_oi)}, puts {count_text(bucket.put_oi)})"


def statistic_rows(bundle: ExpirationBundle, delta_flow_bucket: Optional[FlowBucket] = None, delta_flow_window: str = "") -> List[List[str]]:
    """
    Rows [metric, value] with the totals and ratios of one expiry.

    Args:
        bundle: The expiry.
        delta_flow_bucket: The delta-adjusted flow of this expiry in the report window (None when there is none).
        delta_flow_window: Plain text on the window behind it (hours, rows present, stale since); see ``delta_flow_window_text``.
    """
    analysis = bundle.analysis
    ratio, volume = analysis.put_call_ratio, analysis.volume_stats
    percentile = (f", {ratio.percentile_90d:.0f}th percentile of {ratio.history_n_90d} observations in 90 days"
                  if ratio.percentile_90d is not None else ", percentile not computed")
    rows = [
        ["Instruments", f"{analysis.total_instruments} ({analysis.call_count} calls, {analysis.put_count} puts)"],
        ["Put/call ratio (open interest)", f"{ratio_text(ratio.ratio)} (calls {count_text(ratio.total_call_oi)}, puts "
                                           f"{count_text(ratio.total_put_oi)}{percentile})"],
        ["Volume", f"{number_text(volume.total_volume, 1)} coins, put/call {ratio_text(volume.volume_ratio)}"],
    ]
    surface = bundle.vol_surface
    if surface is None:
        rows.append(["Volatility surface", NOT_COMPUTED])
    else:
        skew = surface.skew_25d
        rows += [["ATM IV", percent_text(surface.atm_iv)],
                 ["25-delta risk reversal (call minus put)", (NOT_COMPUTED if skew.risk_reversal_25d is None else f"{number_text(skew.risk_reversal_25d, 1, ' vol pts', True)}; "
                  f"put IV {percent_text(skew.put_25d_iv)}, call IV {percent_text(skew.call_25d_iv)}")],
                 ["VWAP IV", f"{percent_text(surface.vwap_iv)} vs matched mark {percent_text(surface.mark_iv_average)} "
                             f"({surface.traded_instrument_count} instruments)"]]
        for label, bucket in (("at the money", surface.pc_by_moneyness.atm), ("near out of the money", surface.pc_by_moneyness.near_otm),
                              ("far out of the money", surface.pc_by_moneyness.far_otm)):
            rows.append([f"Put/call by moneyness, {label} ({bucket.range_label})", _moneyness_bucket_text(bucket)])
    gex = bundle.gex_dex
    if gex is None:
        rows.append(["GEX / DEX totals", NOT_COMPUTED])
    else:
        rows += [["Gamma exposure, holder side", usd_text(gex.gamma_exposure_holder_total)],
                 ["Gamma exposure, assumed dealer", usd_text(gex.dealer_gamma_exposure_total)],
                 ["Delta exposure, holder side", number_text(gex.delta_exposure_holder_total, 2, " coins", True)],
                 ["Delta exposure, assumed dealer", number_text(gex.dealer_delta_exposure_total, 2, " coins", True)]]
    money = bundle.analysis.moneyness
    rows.append(["In the money, share of open interest value (notional)",
                 f"calls {percent_text(money.calls.itm_pct)}, puts {percent_text(money.puts.itm_pct)}"])
    if delta_flow_bucket is None:
        rows.append(["Delta-adjusted flow, report window", f"{NOT_COMPUTED}: no hourly flow rows for this expiry"])
    else:
        rows += [["Delta-adjusted flow, signed (HIRO)", usd_text(delta_flow_bucket.hiro_usd)],
                 ["Delta-adjusted flow, signed premium", usd_text(delta_flow_bucket.premium_usd)],
                 ["Delta-adjusted flow, gross |delta| notional", usd_text(delta_flow_bucket.gross_delta_usd, False)],
                 ["Trades behind the delta-adjusted flow", f"{delta_flow_bucket.trade_count:,} (skipped {delta_flow_bucket.skipped_count})"]]
        if delta_flow_window:
            rows.append(["Window of the delta-adjusted flow", delta_flow_window])
    exposure = bundle.exposure_profile
    rows.append(["Vanna / charm, holder side", NOT_COMPUTED if exposure is None else
                 f"vanna {number_text(exposure.total_vex_holder, 0, ' USD per vol point', True)}, charm "
                 f"{number_text(exposure.total_cex_holder, 0, ' USD per day', True)}"])
    return rows


def evidence_text(bundle: ExpirationBundle) -> str:
    """One line on how much evidence backs the expiry (flow sample, inferred view)."""
    flow, inferred = bundle.flow, bundle.dealer_inventory
    if flow is None:
        flow_text = "flow not computed"
    elif not flow.sufficient_data:
        flow_text = f"flow insufficient ({flow.trade_count} trades)"
    else:
        flow_text = f"flow {flow.trade_count} trades" + (", low confidence" if flow.low_confidence else "")
    inferred_text = ("inferred dealer view not computed" if inferred is None else
                     "inferred dealer view shown" if inferred.render_inferred else
                     f"inferred dealer view unavailable ({text_or_missing(inferred.unavailable_reason)})")
    return f"{flow_text}; {inferred_text}"


def strike_pressure_tables(bundle: ExpirationBundle) -> str:
    """
    The five strikes with the most buying and the most selling pressure of the last 24 hours (taker side). Notional is
    contracts times the index price at the trade. Missing or too thin flow is said so, never shown as empty tables.
    """
    flow = bundle.flow
    if flow is None:
        return paragraph_fragment(f"Top strikes by flow: {NOT_COMPUTED} (no flow result).", "note")
    if not flow.sufficient_data:
        return paragraph_fragment(f"Top strikes by flow: not enough trades ({flow.trade_count}) in the window.", "note")
    html = ""
    for title, side, entries in (("Top 5 strikes by buying pressure, 24 h", "Buy", flow.top_buy_strikes),
                                 ("Top 5 strikes by selling pressure, 24 h", "Sell", flow.top_sell_strikes)):
        if not entries:
            html += paragraph_fragment(f"{title}: none (no net {'buying' if side == 'Buy' else 'selling'}).", "note")
            continue
        rows = [[strike_text(entry.strike), "Call" if entry.option_type == "C" else "Put", number_text(entry.net_flow, 1, signed=True),
                 number_text(entry.volume, 1), number_text(entry.notional, 0)] for entry in entries]
        html += table_fragment(["Strike", "Type", "Net flow (coins)", f"{side} volume (coins)", f"{side} notional (USD, contracts x index)"],
                               rows, caption=title, numeric_columns=(2, 3, 4))
    return html


def oi_change_table(bundle: ExpirationBundle) -> str:
    """The open interest changes of more than 20% since the previous snapshot; says why when there is nothing to show."""
    changes = bundle.oi_changes
    if changes is None:
        return paragraph_fragment(f"Large open interest changes: {NOT_COMPUTED}.", "note")
    if not changes.has_previous_snapshot:
        return paragraph_fragment("Large open interest changes: no previous snapshot to compare with.", "note")
    if not changes.rows:
        return paragraph_fragment(f"Large open interest changes: none of at least {OI_CHANGE_SIGNIFICANT_PCT_THRESHOLD:g}% and "
                                  f"{OI_CHANGE_SIGNIFICANT_ABS_THRESHOLD:g} coins since the previous snapshot.", "note")
    rows = [[strike_text(row.strike), "Call" if row.option_type == "C" else "Put", count_text(row.previous_oi), count_text(row.current_oi),
             number_text(row.change, 0, signed=True), number_text(row.change_pct, 1, "%", signed=True)] for row in changes.rows]
    return table_fragment(["Strike", "Type", "Previous OI", "Current OI", "Change", "Change %"], rows,
                          caption=f"Large open interest changes since the previous snapshot ({changes.total_significant} of at least "
                                  f"{OI_CHANGE_SIGNIFICANT_PCT_THRESHOLD:g}% and {OI_CHANGE_SIGNIFICANT_ABS_THRESHOLD:g} coins)",
                          numeric_columns=(2, 3, 4, 5))


def delta_flow_window_text(result: OnChainAnalysisResult) -> str:
    """The window behind the delta-adjusted flow: its length, how many hourly rows exist, and since when it is stale."""
    text = f"{result.delta_flow_lookback_hours:g} h, {result.delta_flow_hours_present} hourly rows persisted"
    if result.delta_flow_stale_since is not None:
        text += f"; STALE: the newest row is from {utc_text(result.delta_flow_stale_since)}"
    return text


def view_caption(bundle: ExpirationBundle, chart_key: str, spot: float, delta_flow_bucket: Optional[FlowBucket] = None,
                 delta_flow_window: str = "") -> str:
    """The HTML above a chart: evidence line, note on the chart, key levels, statistics, top strikes and OI changes. All text is escaped."""
    return (paragraph_fragment(f"{bundle.expiration}: {evidence_text(bundle)}.", "note")
            + paragraph_fragment(CHART_NOTES[chart_key], "note")
            + table_fragment(["Level", "Strike", "Detail"], key_level_rows(bundle, spot))
            + table_fragment(["Metric", "Value"], statistic_rows(bundle, delta_flow_bucket, delta_flow_window))
            + strike_pressure_tables(bundle)
            + oi_change_table(bundle))


def build_expiries_section(result: OnChainAnalysisResult) -> ReportSection:
    """The "Expiries" tab: expiry select + chart choice, one precomputed figure per combination."""
    section = ReportSection(key="expiries", title="Expiries",
                            description="One expiry at a time: pick the expiry and the chart. Key levels and statistics of the expiry are above the chart.")
    bundles = ordered_bundles(result)
    if not bundles:
        section.body_html = paragraph_fragment("No expiry was analyzed: not computed.", "note")
        return section
    controls = [SwitcherControl("expiry", "Expiry", [(bundle.expiration, bundle.expiration) for bundle in bundles],
                                bundles[0].expiration),
                SwitcherControl("chart", "Chart", list(CHARTS), CHARTS[0][0])]
    views: Dict[str, Dict[str, Any]] = {}
    delta_flow_by_expiry = {bucket.expiration: bucket for bucket in result.delta_flow_buckets if bucket.expiration != "ALL"}
    delta_window = delta_flow_window_text(result)
    for bundle in bundles:
        table = build_levels_table(bundle)
        for chart_key, _ in CHARTS:
            figure_id = f"expiry-{bundle.expiration}-{chart_key}"
            section.figures[figure_id] = figure_json(FIGURE_BUILDERS[chart_key](bundle, table, result.underlying_price))
            views[view_key([bundle.expiration, chart_key])] = {"figure": figure_id, "caption": view_caption(
                bundle, chart_key, result.underlying_price, delta_flow_by_expiry.get(bundle.expiration), delta_window)}
    section.body_html = switcher_fragment(SWITCHER_ID, controls)
    section.switchers[SWITCHER_ID] = switcher_configuration(controls, views)
    return section
