"""
The "Market-wide" tab of the on-chain dashboard: charts of the cross-expiry metrics (one chart choice) and tables of the
volatility, funding, correlation, block-trade and delta-flow numbers. Missing parts read "not computed".
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import plotly.graph_objects as go

from coding.core.analytics.onchain_dashboard.formatting import (
    BLUE, GRAY, GREEN, ORANGE, RED, add_markers, clean, clean_all, empty_figure, fraction_percent_text, number_text,
    percent_text, strike_text, text_or_missing, usd_text)
from coding.core.analytics.report.figure_style import figure_json
from coding.core.analytics.report.report_page import (
    ReportSection, SwitcherControl, paragraph_fragment, switcher_configuration, switcher_fragment, table_fragment,
    view_key)
from coding.core.analytics.report.section_fragments import NOT_COMPUTED
from coding.core.analytics.results.analysis_result import OnChainAnalysisResult
from coding.core.analytics.results.market_wide_results import MarketWideResult

SWITCHER_ID = "market-main"
CHARTS = [("aggregate-gex", "Aggregate GEX"), ("aggregate-dex", "Aggregate DEX"), ("gamma-rolloff", "Gamma roll-off"),
          ("term-structure", "IV term structure"), ("skew-term", "Skew term structure"), ("forward-vol", "Forward vol"),
          ("vol-cone", "Vol cone"), ("futures-basis", "Futures basis"), ("delta-flow", "Delta flow")]
CHART_NOTES = {
    "aggregate-gex": "GEX by strike, summed over all expiries (USD per 1% spot move). Holder side vs assumed dealer view.",
    "aggregate-dex": "DEX by strike, summed over all expiries (coins). Holder side vs assumed dealer view.",
    "gamma-rolloff": "Net GEX that expires with each expiry (USD, signed), oldest expiry first.",
    "term-structure": "ATM IV (percent) by days to expiry.",
    "skew-term": "25-delta risk reversal (call IV minus put IV) and butterfly by expiry, in vol points.",
    "forward-vol": "Forward vol between adjacent expiries (percent). A gap means the forward variance was negative (stale IV on one leg).",
    "vol-cone": "Realized volatility window by window: current value against its 25th, median and 75th percentile (percent).",
    "futures-basis": "Annualized futures premium over spot (percent). A gap means the tenor is too short to annualize.",
    "delta-flow": "Signed delta-weighted taker flow (HIRO analog, USD) by expiry over the lookback window, positive is net bullish.",
}


def _percent_values(values) -> List[Optional[float]]:
    """Decimal fractions as percent for a figure (0.585 -> 58.5); missing stays a gap."""
    return [None if clean(value) is None else value * 100 for value in values]


def _aggregate_figure(market: MarketWideResult, holder_name: str, dealer_name: str, y_title: str) -> go.Figure:
    aggregate = market.aggregate_gex_dex
    if aggregate is None:
        return empty_figure("Aggregate exposure not computed.")
    rows = aggregate.strike_rows
    strikes = [row.strike for row in rows]
    figure = go.Figure([
        go.Bar(x=strikes, y=clean_all(getattr(row, holder_name) for row in rows), name="Holder side", marker_color=BLUE),
        go.Bar(x=strikes, y=clean_all(getattr(row, dealer_name) for row in rows), name="Assumed dealer", marker_color=ORANGE)])
    add_markers(figure, {"spot": clean(aggregate.spot_price)}, {"spot": GRAY})
    figure.update_layout(barmode="group", height=460, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Strike",
                         yaxis_title=y_title, legend=dict(orientation="h", y=1.1))
    return figure


def _rolloff_figure(market: MarketWideResult) -> go.Figure:
    rolloff = market.gamma_rolloff
    if rolloff is None or not rolloff.rows:
        return empty_figure("Gamma roll-off not computed.")
    values = clean_all(row.net_gex for row in rolloff.rows)
    figure = go.Figure(go.Bar(x=[row.expiration for row in rolloff.rows], y=values, name="Net GEX",
                              marker_color=[GREEN if value is not None and value >= 0 else RED for value in values]))
    figure.update_layout(height=420, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Expiry",
                         yaxis_title="Net GEX (USD, signed)", showlegend=False)
    return figure


def _term_structure_figure(market: MarketWideResult) -> go.Figure:
    term = market.term_structure
    if term is None or not term.entries:
        return empty_figure("IV term structure not computed.")
    figure = go.Figure(go.Scatter(x=[entry.dte for entry in term.entries], y=clean_all(entry.atm_iv for entry in term.entries),
                                  mode="lines+markers", text=[entry.expiration for entry in term.entries], name="ATM IV",
                                  line=dict(color=BLUE)))
    figure.update_layout(height=420, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Days to expiry",
                         yaxis_title="ATM IV (%)", showlegend=False)
    return figure


def _skew_figure(market: MarketWideResult) -> go.Figure:
    skew = market.skew_term_structure
    if skew is None or not skew.entries:
        return empty_figure("Skew term structure not computed.")
    names = [entry.expiration for entry in skew.entries]
    figure = go.Figure([go.Bar(x=names, y=clean_all(entry.rr_25d for entry in skew.entries), name="25d risk reversal", marker_color=BLUE),
                        go.Bar(x=names, y=clean_all(entry.bf_25d for entry in skew.entries), name="25d butterfly", marker_color=ORANGE)])
    figure.update_layout(barmode="group", height=420, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Expiry",
                         yaxis_title="Vol points", legend=dict(orientation="h", y=1.1))
    return figure


def _forward_vol_figure(market: MarketWideResult) -> go.Figure:
    forward = market.forward_vol
    if forward is None or not forward.buckets:
        return empty_figure("Forward vol not computed.")
    values = clean_all(bucket.fwd_vol_pct for bucket in forward.buckets)
    figure = go.Figure(go.Bar(x=[f"{bucket.from_expiry} to {bucket.to_expiry}" for bucket in forward.buckets], y=values,
                              name="Forward vol", marker_color=BLUE))
    figure.update_layout(height=420, margin=dict(l=70, r=20, t=40, b=90), xaxis_title="Expiry pair",
                         yaxis_title="Forward vol (%)", showlegend=False)
    return figure


def _cone_figure(market: MarketWideResult) -> go.Figure:
    cone = market.volatility_cone
    if cone is None or not cone.stats_by_window:
        return empty_figure("Volatility cone not computed.")
    windows = sorted(cone.stats_by_window)
    stats = [cone.stats_by_window[window] for window in windows]
    labels = [f"{window}d" for window in windows]
    figure = go.Figure([
        go.Scatter(x=labels, y=_percent_values(item.p25 for item in stats), mode="lines+markers", name="25th percentile", line=dict(color=GRAY, dash="dot")),
        go.Scatter(x=labels, y=_percent_values(item.p50 for item in stats), mode="lines+markers", name="Median", line=dict(color=GRAY)),
        go.Scatter(x=labels, y=_percent_values(item.p75 for item in stats), mode="lines+markers", name="75th percentile", line=dict(color=GRAY, dash="dot")),
        go.Scatter(x=labels, y=_percent_values(item.current_rv for item in stats), mode="markers", name="Current",
                   marker=dict(color=ORANGE, size=12))])
    figure.update_layout(height=420, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Window",
                         yaxis_title="Realized vol (%)", legend=dict(orientation="h", y=1.1))
    return figure


def _basis_figure(market: MarketWideResult) -> go.Figure:
    basis = market.futures_basis
    if basis is None or not basis.entries:
        return empty_figure("Futures basis not computed.")
    figure = go.Figure(go.Bar(x=[entry.instrument_name for entry in basis.entries],
                              y=clean_all(entry.annualized_premium_pct for entry in basis.entries), marker_color=BLUE))
    figure.update_layout(height=420, margin=dict(l=70, r=20, t=40, b=110), xaxis_title="Future",
                         yaxis_title="Annualized premium (%)", showlegend=False)
    return figure


def _delta_flow_figure(result: OnChainAnalysisResult) -> go.Figure:
    buckets = [bucket for bucket in result.delta_flow_buckets if bucket.expiration != "ALL"]
    if not buckets:
        return empty_figure("Delta flow not computed: no hourly flow rows in the window.")
    values = clean_all(bucket.hiro_usd for bucket in buckets)
    figure = go.Figure(go.Bar(x=[bucket.expiration for bucket in buckets], y=values,
                              marker_color=[GREEN if value is not None and value >= 0 else RED for value in values]))
    figure.update_layout(height=420, margin=dict(l=70, r=20, t=40, b=50), xaxis_title="Expiry",
                         yaxis_title="Signed delta flow (USD)", showlegend=False)
    return figure


def _figure_for(chart_key: str, result: OnChainAnalysisResult) -> go.Figure:
    market = result.market_wide
    if chart_key == "aggregate-gex":
        return _aggregate_figure(market, "gamma_exposure_holder", "dealer_gamma_exposure", "GEX (USD per 1% move)")
    if chart_key == "aggregate-dex":
        return _aggregate_figure(market, "delta_exposure_holder", "dealer_delta_exposure", "DEX (coins)")
    builders = {"gamma-rolloff": _rolloff_figure, "term-structure": _term_structure_figure, "skew-term": _skew_figure,
                "forward-vol": _forward_vol_figure, "vol-cone": _cone_figure, "futures-basis": _basis_figure}
    if chart_key in builders:
        return builders[chart_key](market)
    return _delta_flow_figure(result)


# ------------------------------------------------------------------ captions

def _caption(chart_key: str, result: OnChainAnalysisResult) -> str:
    market = result.market_wide
    note = paragraph_fragment(CHART_NOTES[chart_key], "note")
    facts: List[str] = []
    if chart_key in ("aggregate-gex", "aggregate-dex") and market.aggregate_gex_dex is not None:
        aggregate = market.aggregate_gex_dex
        levels = aggregate.key_levels
        facts.append(f"{text_or_missing(None if aggregate.expiration_count is None else str(aggregate.expiration_count))} expiries aggregated; "
                     f"call resistance {strike_text(levels.call_resistance.strike if levels.call_resistance else None)}, "
                     f"put support {strike_text(levels.put_support.strike if levels.put_support else None)}, "
                     f"cumulative GEX zero strike {strike_text(levels.cumulative_gex_zero_strike)}")
    elif chart_key == "gamma-rolloff" and market.gamma_rolloff is not None:
        rolloff = market.gamma_rolloff
        cliff = "more than 30% of the gamma expires within 7 days" if rolloff.gamma_cliff_7d else "no gamma cliff within 7 days"
        facts.append(f"{cliff}; share expiring within 7 days {percent_text(rolloff.cum_share_7d)}, within 30 days "
                     f"{percent_text(rolloff.cum_share_30d)} (of gross gamma {usd_text(rolloff.gross_total, False)})")
    elif chart_key == "term-structure" and market.term_structure is not None:
        term = market.term_structure
        facts.append(f"shape {term.shape}, spread {number_text(term.spread_signed, 2, ' vol points', True)}")
    elif chart_key == "skew-term" and market.skew_term_structure is not None:
        facts.append(f"risk reversal slope (back minus front) {number_text(market.skew_term_structure.rr_slope, 2, ' vol points', True)}")
    elif chart_key == "forward-vol" and market.forward_vol is not None and market.forward_vol.has_negative_variance:
        facts.append("DATA QUALITY: at least one pair has negative forward variance (stale IV on one leg), so its forward vol is not computed")
    elif chart_key == "delta-flow":
        total = next((bucket for bucket in result.delta_flow_buckets if bucket.expiration == "ALL"), None)
        window = f"{result.delta_flow_lookback_hours:g} hours, {result.delta_flow_hours_present} hourly rows present"
        facts.append(f"window {window}; all expiries: " + (
            NOT_COMPUTED if total is None else f"{usd_text(total.hiro_usd)} signed delta flow, {total.trade_count} trades, "
                                               f"{total.skipped_count} skipped"))
    return note + "".join(paragraph_fragment(fact) for fact in facts)


# ------------------------------------------------------------------ tables

def volatility_rows(market: MarketWideResult) -> List[List[str]]:
    """Rows [metric, value] for DVOL, VRP, realized vol and correlation."""
    rows = [["DVOL", number_text(market.dvol, 2)], ["IV percentile (365 days)", percent_text(market.iv_percentile_365d)]]
    vrp = market.variance_risk_premium
    rows.append(["Variance risk premium", NOT_COMPUTED if vrp is None else
                 f"{number_text(vrp.vrp, 1, ' vol points', True)} ({vrp.signal}); DVOL {number_text(vrp.dvol, 1)}, "
                 f"30-day realized vol {fraction_percent_text(vrp.rv_30d, 1)}"])
    realized = market.realized_volatility
    rows.append(["Realized vol, 10 / 20 / 30 days", NOT_COMPUTED if realized is None else
                 " / ".join(fraction_percent_text(value, 1) for value in (realized.rv_10d, realized.rv_20d, realized.rv_30d))])
    cone = market.volatility_cone
    rows.append(["Vol cone percentile, 10 / 20 / 30 days", NOT_COMPUTED if cone is None else
                 " / ".join(percent_text(value, 0) for value in (cone.cone_10d_pctile, cone.cone_20d_pctile, cone.cone_30d_pctile))])
    funding = market.perpetual_funding
    if funding is None:
        rows.append(["Perpetual funding", NOT_COMPUTED])
    else:
        rows += [["Perpetual open interest", number_text(funding.perp_open_interest, 0, " USD")],
                 ["Funding, current / 8 hours", f"{fraction_percent_text(funding.funding_rate, 8)} / {fraction_percent_text(funding.funding_8h, 4)}"],
                 ["Funding trend", f"{funding.funding_trend} ({funding.history_points} history points)"]]
    correlation = market.cross_asset_correlation
    rows.append([f"Correlation with {correlation.other_currency if correlation else 'other asset'}", NOT_COMPUTED if correlation is None else
                 f"price {number_text(correlation.price_correlation, 2)} ({correlation.sample_size} samples), DVOL "
                 f"{number_text(correlation.dvol_correlation, 2)}" + (f" (log changes, {correlation.dvol_correlation_observations} observations)"
                                                                      if correlation.dvol_correlation_observations is not None else "")])
    return rows


def _time_text(milliseconds: Optional[int]) -> str:
    if milliseconds is None:
        return NOT_COMPUTED
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def rolloff_table(market: MarketWideResult) -> str:
    """Gamma roll-off rows as a table."""
    if market.gamma_rolloff is None:
        return paragraph_fragment("Gamma roll-off not computed.", "note")
    rows = [[row.expiration, number_text(row.dte_days, 1), usd_text(row.net_gex), percent_text(row.share_pct),
             percent_text(row.cum_share_pct)] for row in market.gamma_rolloff.rows]
    return table_fragment(["Expiry", "Days to expiry", "Net GEX (signed)", "Share of gross gamma", "Cumulative share"], rows,
                          numeric_columns=(1, 2, 3, 4))


def skew_table(market: MarketWideResult) -> str:
    """Skew term structure rows as a table."""
    if market.skew_term_structure is None:
        return paragraph_fragment("Skew term structure not computed.", "note")
    rows = [[entry.expiration, number_text(entry.dte, 1), percent_text(entry.atm_iv_interp), number_text(entry.rr_25d, 2, signed=True),
             "p" + number_text(entry.rr_percentile_30d, 0) if entry.rr_percentile_30d is not None else NOT_COMPUTED,
             text_or_missing(entry.rr_regime_30d), number_text(entry.bf_25d, 2, signed=True),
             "p" + number_text(entry.bf_percentile_30d, 0) if entry.bf_percentile_30d is not None else NOT_COMPUTED]
            for entry in market.skew_term_structure.entries]
    return table_fragment(["Expiry", "Days", "ATM IV", "25d RR", "RR 30d percentile", "RR regime", "25d BF", "BF 30d percentile"],
                          rows, numeric_columns=(1, 2, 3, 4, 6, 7))


def forward_vol_table(market: MarketWideResult) -> str:
    """Forward vol rows as a table."""
    if market.forward_vol is None:
        return paragraph_fragment("Forward vol not computed.", "note")
    rows = [[f"{bucket.from_expiry} to {bucket.to_expiry}", number_text(bucket.t1_days, 1), number_text(bucket.t2_days, 1),
             percent_text(bucket.sigma1_pct, 2), percent_text(bucket.sigma2_pct, 2), percent_text(bucket.fwd_vol_pct, 2),
             number_text(bucket.event_premium, 2, signed=True), ", ".join(bucket.flags) if bucket.flags else ""]
            for bucket in market.forward_vol.buckets]
    return table_fragment(["Pair", "Days from", "Days to", "IV near", "IV far", "Forward vol", "Event premium", "Flags"], rows,
                          numeric_columns=(1, 2, 3, 4, 5, 6))


def basis_table(market: MarketWideResult) -> str:
    """Futures basis rows as a table."""
    if market.futures_basis is None:
        return paragraph_fragment("Futures basis not computed.", "note")
    rows = [[entry.instrument_name, number_text(entry.dte, 0), number_text(entry.mark_price, 0), number_text(entry.index_price, 0),
             percent_text(entry.annualized_premium_pct)] for entry in market.futures_basis.entries]
    return table_fragment(["Future", "Days", "Mark price", "Index price", "Annualized premium"], rows, numeric_columns=(1, 2, 3, 4))


def block_structure_text(block) -> str:
    """
    What a block is: the exchange's combo name when the legs were sent as a registered combo, else the legs themselves ("single leg"
    or "custom") -- a custom block has no combo name, which does not mean the structure is unknown. The block lists one instrument per
    trade row, so a leg filled in several prints is shown once; a block cut by the fetch window says "legs seen".
    """
    if block.combo_id:
        return block.combo_id
    names = list(dict.fromkeys(name for name in block.instruments if name))
    legs = [name.split("-", 1)[1] if name.count("-") >= 3 else name for name in names]       # option: 27NOV26-58000-C; else the full name
    if not legs:
        return NOT_COMPUTED
    shown = ", ".join(legs[:4]) + (f" and {len(legs) - 4} more" if len(legs) > 4 else "")
    if block.observed_leg_count != block.leg_count:
        return f"legs seen: {shown}"
    return f"{'single leg' if block.leg_count == 1 else 'custom'}: {shown}"


def block_tables(market: MarketWideResult) -> str:
    """Block trades and large prints as tables."""
    blocks = market.block_trades
    if blocks is None:
        return paragraph_fragment("Block trades not computed.", "note")
    tracked = blocks.tracked_since or NOT_COMPUTED
    text = paragraph_fragment(f"Block trade ids are tracked since {tracked}; earlier history is not available. "
                              f"{blocks.total_detected} large trades detected, notional threshold {number_text(blocks.notional_threshold, 0, ' USD')}.", "note")
    text += "<h4>Blocks</h4>" + (table_fragment(
        ["Block id", "Legs", "Structure", "Premium (USD)", "Time"],
        [[block.block_trade_id, str(block.leg_count) + ("" if block.leg_count == block.observed_leg_count
                                                         else f" ({block.observed_leg_count} seen)"),
          block_structure_text(block), number_text(block.combined_premium_usd, 0), _time_text(block.timestamp)]
         for block in blocks.blocks], numeric_columns=(1, 3)) if blocks.blocks else paragraph_fragment("No block trades in the window.", "note"))
    text += "<h4>Large prints (screen prints, not blocks)</h4>" + (table_fragment(
        ["Time", "Instrument", "Size", "Direction", "Notional (USD)", "IV"],
        [[_time_text(trade.timestamp), trade.instrument_name, number_text(trade.amount, 1), trade.direction,
          number_text(trade.notional, 0), percent_text(trade.implied_volatility)] for trade in blocks.trades],
        numeric_columns=(2, 4, 5)) if blocks.trades else paragraph_fragment("No large prints in the window.", "note"))
    return text


def delta_flow_table(result: OnChainAnalysisResult) -> str:
    """Delta-flow buckets as a table."""
    if not result.delta_flow_buckets:
        return paragraph_fragment("Delta flow not computed: no hourly flow rows in the window.", "note")
    rows = [[bucket.expiration, usd_text(bucket.hiro_usd), usd_text(bucket.premium_usd), usd_text(bucket.gross_delta_usd, False),
             number_text(bucket.net_contracts, 1, signed=True), str(bucket.trade_count), str(bucket.skipped_count),
             percent_text(None if bucket.skip_rate is None else bucket.skip_rate * 100)] for bucket in result.delta_flow_buckets]
    return table_fragment(["Expiry", "Signed delta flow", "Signed premium", "Gross delta", "Net contracts", "Trades", "Skipped",
                           "Skip rate"], rows, numeric_columns=(1, 2, 3, 4, 5, 6, 7))


def build_market_section(result: OnChainAnalysisResult) -> ReportSection:
    """The "Market-wide" tab."""
    market = result.market_wide
    section = ReportSection(key="market-wide", title="Market-wide",
                            description="Metrics across all expiries: aggregate exposure, term structures, volatility, funding, basis, blocks and flow.")
    if market.failed_sections:
        section.warnings.append("these market-wide sections failed and are not computed: " + ", ".join(market.failed_sections))
    controls = [SwitcherControl("chart", "Chart", list(CHARTS), CHARTS[0][0])]
    views: Dict[str, Dict[str, Any]] = {}
    for chart_key, _ in CHARTS:
        figure_id = f"market-{chart_key}"
        section.figures[figure_id] = figure_json(_figure_for(chart_key, result))
        views[view_key([chart_key])] = {"figure": figure_id, "caption": _caption(chart_key, result)}
    body = switcher_fragment(SWITCHER_ID, controls)
    section.switchers[SWITCHER_ID] = switcher_configuration(controls, views)
    body += "<h3>Volatility, funding and correlation</h3>" + table_fragment(["Metric", "Value"], volatility_rows(market))
    body += "<h3>Gamma roll-off</h3>" + rolloff_table(market)
    body += "<h3>Skew term structure</h3>" + skew_table(market)
    body += "<h3>Forward vol</h3>" + forward_vol_table(market)
    body += "<h3>Futures basis</h3>" + basis_table(market)
    body += "<h3>Block trades</h3>" + block_tables(market)
    body += "<h3>Delta flow</h3>" + delta_flow_table(result)
    section.body_html = body
    return section
