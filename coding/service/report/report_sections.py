"""
The five sections of the market report. Each builder calls the existing service of its analysis (nothing is computed a
second way here), turns the result into a ``ReportSection`` and is testable on its own with a fake repository or service.

A builder raises when its analysis cannot run at all (e.g. no option-chain snapshot); ``ReportService`` turns that into a
section that explains what failed. Data that is merely thin (no trades in the window) is shown as such, never drawn as zero.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import plotly.graph_objects as go
import plotly.io as plotly_io

from coding.core.analytics.menthorq_chart_summary import data_notes, data_warnings
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY, GAMMA_UNDERLYING_INDEX, is_monthly_expiry, parse_expiry_settlement)
from coding.core.analytics.option_flow.level_overview_page import build_level_overview_page
from coding.core.analytics.report.figure_style import apply_paper_style
from coding.core.analytics.report.report_page import (
    ReportSection, SwitcherControl, embeddable_page, format_utc, image_fragment, page_frame_fragment, paragraph_fragment, png_data_uri,
    switcher_configuration, switcher_fragment, view_key)
from coding.core.analytics.report.section_fragments import dealer_levels_caption, option_flow_caption, taker_flow_table
from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.taker_flow_chart_service import DATA_FILE_NAME, TakerFlowChartService
from coding.service.futures_flow.taker_flow_settings import STALE_AFTER
from coding.service.on_chain.menthorq_replication_service import (
    CHART_LAYOUT_NESTED, MenthorqReplicationService, last_complete_utc_day, nested_date_folder)
from coding.service.option_flow.dealer_flow_levels_service import DealerFlowLevelsService
from coding.service.option_flow.level_overview_service import LevelOverviewService
from coding.service.option_flow.option_flow_service import OptionFlowService

logger = logging.getLogger(__name__)

OPTION_FLOW_HOURS = 24
MAXIMUM_OPTION_FLOW_EXPIRIES = 12        # single expirations precomputed (the most traded ones by notional)
OPTION_FLOW_UNITS: List[Tuple[str, str]] = [("notional", "Notional (USD)"), ("contracts", "Contracts"), ("trades", "Trades")]
ALL_EXPIRATIONS = "all"
DEALER_NEAREST_EXPIRIES = 5              # nearest expiries precomputed, plus the nearest monthly one
STALE_TRADE_HOURS = 3
TAKER_FLOW_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

MENTHORQ_CHARTS: List[Tuple[str, str]] = [("iv_times_open_interest", "IV x OI"), ("net_gex_panels", "Net GEX panels"),
                                          ("net_dex", "Net DEX")]
MENTHORQ_UNIVERSES: List[Tuple[str, str]] = [("", "Monthly expiries"), ("_all_listed_expiries", "All listed expiries")]


@dataclass
class SectionContext:
    """What every builder shares: the currency, the one instant the whole report is as of, and the database."""

    currency: str
    now: datetime
    repository: DatabaseRepository
    working_directory: Path              # scratch folder for chart files that are read back and embedded

    def clock(self) -> datetime:
        """The report instant (every service sees the same 'now')."""
        return self.now


def figure_json(figure: go.Figure) -> Dict[str, Any]:
    """A figure in paper style as plain JSON data (``data`` and ``layout``), without Plotly's default template."""
    data = json.loads(plotly_io.to_json(apply_paper_style(figure), validate=False))
    layout = data.get("layout", {})
    layout.pop("template", None)
    layout.pop("title", None)          # the report shows the scope above the chart already
    return {"data": data.get("data", []), "layout": data.get("layout", {})}


# ---------------------------------------------------------------- MenthorQ

def build_menthorq_section(context: SectionContext) -> ReportSection:
    """
    The MenthorQ replica charts of the last complete UTC day (MenthorQ's daily close: spot and IV at 23:59 UTC, open
    interest from 00:00 UTC), monthly and all listed expiries, gamma at the index, plus one IV x OI chart per expiry.
    """
    menthorq_date = last_complete_utc_day(context.now)
    output = MenthorqReplicationService(context.repository).run_menthorq_daily_close(
        context.currency, menthorq_date, context.working_directory, expiry_universe=EXPIRY_UNIVERSE_MONTHLY,
        gamma_underlying=GAMMA_UNDERLYING_INDEX, now=context.now, chart_layout=CHART_LAYOUT_NESTED)
    folder = nested_date_folder(context.working_directory, menthorq_date)
    charts: Dict[str, str] = output["charts"]

    controls = [SwitcherControl("chart", "Chart", [(key, label) for key, label in MENTHORQ_CHARTS], MENTHORQ_CHARTS[0][0]),
                SwitcherControl("universe", "Expiries", [(key or "monthly", label) for key, label in MENTHORQ_UNIVERSES],
                                "monthly")]
    views: Dict[str, Dict[str, Any]] = {}
    for chart_key, chart_label in MENTHORQ_CHARTS:
        for suffix, universe_label in MENTHORQ_UNIVERSES:
            relative = charts.get(f"{chart_key}{suffix}")
            if relative is None:
                continue
            views[view_key([chart_key, suffix or "monthly"])] = {
                "image": png_data_uri((folder / relative).read_bytes()),
                "alt": f"{context.currency} {chart_label}, {universe_label.lower()}, {menthorq_date.isoformat()}",
                "caption": ""}
    body = switcher_fragment("menthorq-main", controls)
    switchers = {"menthorq-main": switcher_configuration(controls, views)}

    per_expiry = output.get("per_expiry_iv_times_open_interest", [])
    if per_expiry:
        expiry_control = SwitcherControl("expiry", "Expiry", [(entry["expiration"], entry["expiration"]) for entry in per_expiry],
                                         per_expiry[0]["expiration"])
        expiry_views = {}
        for entry in per_expiry:
            levels = entry.get("levels") or {}
            parts = [f"{name} {levels[key]:,.0f}" for key, name in (("call_resistance", "call resistance"),
                                                                     ("put_support", "put support"), ("hvl", "HVL"))
                     if levels.get(key) is not None]
            expiry_views[view_key([entry["expiration"]])] = {
                "image": png_data_uri((folder / entry["chart"]).read_bytes()),
                "alt": f"{context.currency} IV x OI of {entry['expiration']}",
                "caption": paragraph_fragment(f"{entry['expiration']}: " + (", ".join(parts) if parts else "levels not computed") + ".")}
        body += "<h3>IV x OI of one expiry</h3>" + switcher_fragment("menthorq-expiry", [expiry_control])
        switchers["menthorq-expiry"] = switcher_configuration([expiry_control], expiry_views)

    inputs = output["inputs"]
    as_of = (f"Daily close of {menthorq_date.isoformat()}: spot and IV at 23:59 UTC (snapshot captured "
             f"{format_utc(datetime.fromisoformat(inputs['snapshot_captured_at_utc']))}), open interest captured "
             f"{format_utc(datetime.fromisoformat(inputs['open_interest_captured_at_utc']))}.")
    return ReportSection(
        key="menthorq", title="MenthorQ", as_of=as_of, body_html=body, switchers=switchers,
        description=("Replica of MenthorQ's charts from the stored Deribit chain: IV x OI, net GEX per expiry panel and net "
                     "DEX, with call resistance, put support and HVL. The daily close matches the instant MenthorQ prints. "
                     "Gamma at the index."),
        warnings=data_warnings(output), notes=data_notes(output) + [f"Index price {inputs['index_price']:,.2f}."])


# ---------------------------------------------------------------- Futures flow

def build_futures_flow_section(context: SectionContext) -> ReportSection:
    """The Binance perpetual taker buy/sell flow chart of the report currency (last 30 days) and its last days as a table."""
    symbol = TAKER_FLOW_SYMBOLS.get(context.currency)
    if symbol is None:
        raise LookupError(f"no Binance perpetual is configured for {context.currency}")
    result = TakerFlowChartService(context.repository, symbols=(symbol,), clock=context.clock).generate(context.working_directory)
    pictures = [path for path in result.files if path.suffix == ".png"]
    data = json.loads((result.folder / DATA_FILE_NAME).read_text(encoding="utf-8"))
    symbol_data = data["symbols"].get(symbol)
    if not pictures or symbol_data is None:
        raise LookupError("; ".join(result.warnings) or f"no taker flow stored for {symbol}")
    end = result.newest_hour_utc + timedelta(hours=1) if result.newest_hour_utc is not None else None
    body = (taker_flow_table(symbol_data["days"])
            + image_fragment(pictures[0].read_bytes(), f"{context.currency} perpetual taker buy/sell flow, last 30 days"))
    return ReportSection(
        key="futures-flow", title="Futures flow", as_of=f"Data up to {format_utc(end)}.", body_html=body,
        description=(f"Binance {symbol} perpetual: daily taker buy/sell ratio, daily net flow and the rolling 24-hour ratio "
                     "of the last 30 days. Futures market, not options; the taker is the side that crossed the book."),
        warnings=list(result.warnings),
        notes=[f"Stale when the newest hour is more than {STALE_AFTER.total_seconds() / 3600:.0f} hours old. "
               "A day with a missing hour is not drawn."])


# ---------------------------------------------------------------- Option flow

def _option_flow_warnings(report: Any, now: datetime) -> List[str]:
    """Data-age and block-coverage warnings of one option-flow report (same rules as the former Option Flow tab)."""
    warnings = []
    if report.newest_trade is None:
        warnings.append("no stored trades for this currency; sync the database")
    elif now - report.newest_trade > timedelta(hours=STALE_TRADE_HOURS):
        warnings.append(f"the newest stored trade is from {format_utc(report.newest_trade)}, older than the end of the "
                        "window; sync the database")
    if report.first_block_trade is None or report.window_start < report.first_block_trade:
        warnings.append("block trades were not stored for the whole window, so some blocks appear as screen volume")
    return warnings


def build_option_flow_section(context: SectionContext, service: Optional[OptionFlowService] = None) -> ReportSection:
    """
    Buy/sell volume per strike of the last 24 hours: all expirations and up to 12 single expirations (the most traded by
    notional), each in notional, contracts and trades. Every view is the figure ``OptionFlowService`` draws.
    """
    service = service or OptionFlowService(context.repository, context.clock)
    hours = OPTION_FLOW_HOURS
    everything = service.strike_flow(context.currency, hours, None, end=context.now)
    reports = {ALL_EXPIRATIONS: everything}
    traded = service.traded_expirations(context.currency, hours, end=context.now)
    single = {label: service.strike_flow(context.currency, hours, label, end=context.now) for label in traded}
    ranked = sorted(single, key=lambda label: -sum(cell.notional_usd for cells in single[label].flow.cells.values()
                                                   for cell in cells.values()))
    kept = [label for label in traded if label in ranked[:MAXIMUM_OPTION_FLOW_EXPIRIES]]   # soonest first
    reports.update({label: single[label] for label in kept})

    figures: Dict[str, Dict[str, Any]] = {}
    views: Dict[str, Dict[str, Any]] = {}
    warnings = _option_flow_warnings(everything, context.now)
    for scope, report in reports.items():
        scope_label = "All expirations" if scope == ALL_EXPIRATIONS else scope
        caption = option_flow_caption(report.flow, scope_label, report.window_start, report.window_end)
        for unit, _ in OPTION_FLOW_UNITS:
            figure_id = f"option-flow-{scope}-{unit}"
            if report.flow.trades_used:
                figures[figure_id] = figure_json(service.build_figure(report, unit))
                views[view_key([scope, unit])] = {"figure": figure_id, "caption": caption}
            else:
                views[view_key([scope, unit])] = {"caption": caption}
    expiry_choices = [(ALL_EXPIRATIONS, "All expirations")] + [(label, label) for label in kept]
    controls = [SwitcherControl("expiry", "Expiry", expiry_choices, ALL_EXPIRATIONS),
                SwitcherControl("unit", "Unit", OPTION_FLOW_UNITS, "notional")]
    notes = [f"Window {format_utc(everything.window_start)} to {format_utc(everything.window_end)}. "
             f"{len(traded)} expirations traded in it"
             + (f"; the {len(kept)} most traded by notional can be picked one by one, the rest only within all expirations."
                if len(traded) > len(kept) else "; each can be picked on its own.")
             + " Buy/sell is the taker's side (aggressor); blocked = Deribit block trades, drawn next to the axis.",
             "The window is fixed at 24 hours in the report; other windows: scripts/option_flow_by_strike.py."]
    return ReportSection(
        key="option-flow", title="Option flow", as_of=f"Last {hours} hours up to {format_utc(everything.window_end)}.",
        body_html=switcher_fragment("option-flow", controls),
        figures=figures, switchers={"option-flow": switcher_configuration(controls, views)}, warnings=warnings, notes=notes,
        description=("Buy and sell volume per strike of the Deribit option trades: calls to the left, puts to the right; "
                     "each bar is the blocked part next to the axis plus the screen part. Hover a strike for its eight "
                     "numbers."))


# ---------------------------------------------------------------- Dealer levels

def dealer_expiries(expirations: List[str], nearest: int = DEALER_NEAREST_EXPIRIES) -> Tuple[List[str], Optional[str]]:
    """
    The expiries to precompute (the ``nearest`` soonest plus the nearest monthly, soonest first) and the one to show first
    (the nearest monthly, else the soonest; None when there is none). ``expirations`` must be sorted soonest first.
    """
    monthly = next((label for label in expirations
                    if (settlement := parse_expiry_settlement(label)) is not None and is_monthly_expiry(settlement)), None)
    chosen = expirations[:nearest]
    if monthly is not None and monthly not in chosen:
        chosen.append(monthly)
    return chosen, monthly or (chosen[0] if chosen else None)


def build_dealer_levels_section(context: SectionContext, service: Optional[DealerFlowLevelsService] = None) -> ReportSection:
    """Flow-based next to open-interest based dealer GEX for the nearest expiries and the nearest monthly one."""
    service = service or DealerFlowLevelsService(context.repository, context.clock)
    expirations = service.expirations(context.currency)
    if not expirations:
        raise LookupError(f"no {context.currency} option-chain snapshot with an unexpired expiry is stored")
    chosen, first = dealer_expiries(expirations)
    figures: Dict[str, Dict[str, Any]] = {}
    views: Dict[str, Dict[str, Any]] = {}
    labels: List[Tuple[str, str]] = []
    skipped: List[str] = []
    chain_time = None
    for expiry in chosen:
        try:
            levels = service.levels(context.currency, expiry)
        except LookupError as error:
            skipped.append(f"{expiry}: {error}")
            continue
        chain_time = levels.chain_captured_at
        figure_id = f"dealer-levels-{expiry}"
        figures[figure_id] = figure_json(service.build_figure(levels))
        views[view_key([expiry])] = {"figure": figure_id, "caption": dealer_levels_caption(levels)}
        labels.append((expiry, expiry + ("" if levels.gate_passed else " (check failed)")))
    if not views:
        raise LookupError("no expiry could be computed: " + "; ".join(skipped))
    selected = first if first is not None and view_key([first]) in views else labels[0][0]
    control = SwitcherControl("expiry", "Expiry", labels, selected)
    notes = [f"Precomputed in this file: the {DEALER_NEAREST_EXPIRIES} nearest expiries and the nearest monthly one "
             f"({', '.join(label for label, _ in labels)}); {len(expirations)} are listed. Others: scripts/dealer_flow_levels.py.",
             "Dealer = the other side of every taker. There is no ground truth for either view."]
    notes += [f"Not computed: {text}" for text in skipped]
    return ReportSection(
        key="dealer-levels", title="Dealer levels", as_of=f"Chain snapshot {format_utc(chain_time)}; trades up to the report time.",
        body_html=switcher_fragment("dealer-levels", [control]), figures=figures,
        switchers={"dealer-levels": switcher_configuration([control], views)}, notes=notes,
        description=("Dealer gamma levels of one expiry two ways: from the trades (the dealer holds minus the cumulative taker "
                     "flow since listing) and from open interest (dealers long calls, short puts). The check tells whether "
                     "the flow-based view is plausible."))


# ---------------------------------------------------------------- Levels

def build_levels_section(context: SectionContext, service: Optional[LevelOverviewService] = None) -> ReportSection:
    """Every source's levels on one price line (the Levels page), nearest monthly expiry, 24-hour option flow."""
    service = service or LevelOverviewService(context.repository, context.clock)
    overview = service.overview(context.currency, None, OPTION_FLOW_HOURS)
    page = build_level_overview_page(overview)
    sources = len({mark.source for mark in overview.marks})
    return ReportSection(
        key="levels", title="Levels",
        as_of=f"Spot {overview.spot:,.0f}; dealer expiry {overview.expiry}; option flow of the last {OPTION_FLOW_HOURS} hours.",
        body_html=page_frame_fragment("levels-page", f"{context.currency} levels of every source"),
        pages={"levels-page": embeddable_page(page)},
        description=(f"{len(overview.marks)} levels from {sources} sources on one price line. A highlighted band marks a "
                     "price where at least 3 sources agree within 1 % of the spot. The sources are not independent, so "
                     "agreement is a coincidence of methods, not proof."))


SECTION_BUILDERS: List[Tuple[str, str, Callable[[SectionContext], ReportSection]]] = [
    ("menthorq", "MenthorQ", build_menthorq_section),
    ("futures-flow", "Futures flow", build_futures_flow_section),
    ("option-flow", "Option flow", build_option_flow_section),
    ("dealer-levels", "Dealer levels", build_dealer_levels_section),
    ("levels", "Levels", build_levels_section),
]
