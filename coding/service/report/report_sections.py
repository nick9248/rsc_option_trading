"""
The five sections of the market report. Each builder calls the existing service of its analysis (nothing is computed a
second way here), turns the result into a ``ReportSection`` and is testable on its own with a fake repository or service.

A builder raises when its analysis cannot run at all (e.g. no option-chain snapshot); ``ReportService`` turns that into a
section that explains what failed. Data that is merely thin (no trades in the window) is shown as such, never drawn as zero.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import plotly.graph_objects as go
import psycopg2
import plotly.io as plotly_io

from coding.core.analytics.futures_flow.taker_flow_figure import build_taker_flow_figure
from coding.core.analytics.menthorq_chart_summary import data_notes, data_warnings
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY, GAMMA_UNDERLYING_INDEX, is_monthly_expiry, parse_expiry_settlement)
from coding.core.analytics.open_interest.expiry_open_interest import ExpiryOpenInterest
from coding.core.analytics.option_flow.level_overview_page import build_level_overview_page
from coding.core.analytics.option_flow.block_structure import bar_expiry_shares
from coding.core.analytics.report.figure_style import apply_paper_style
from coding.core.analytics.report.report_page import (
    ReportSection, SwitcherControl, embeddable_page, format_utc, page_frame_fragment, paragraph_fragment, png_data_uri,
    switcher_configuration, switcher_fragment, view_key)
from coding.core.analytics.report.section_fragments import (
    bar_expiry_table, block_structure_table, dealer_levels_caption, expiry_choice_label, open_interest_table, option_flow_caption, taker_flow_table)
from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.taker_flow_chart_service import CHART_DAYS, TakerFlowChartService
from coding.service.futures_flow.taker_flow_settings import STALE_AFTER
from coding.service.on_chain.expiry_open_interest_service import ExpiryOpenInterestReport, ExpiryOpenInterestService
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
TOP_OPEN_INTEREST_EXPIRIES = 4           # expiries with the most open interest that get their own view in option flow and levels
BAR_TABLE_ROWS = 8                       # biggest bars whose expiries are listed
BLOCK_TABLE_ROWS = 10                    # largest blocks listed
BLOCK_EXPLANATION = ("A block is negotiated off the order book and often has several legs; the taker side of one leg of a "
                     "multi-leg block is not a directional view on its own.")
OPTION_FLOW_TABLE_ROWS = 8              # rows of the open interest table in the option flow tab
DEALER_NEAREST_EXPIRIES = 5             # nearest expiries precomputed, plus the nearest monthly one
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
    _open_interest: Optional[ExpiryOpenInterestReport] = field(default=None, init=False, repr=False)
    _open_interest_failure: Optional[str] = field(default=None, init=False, repr=False)
    _open_interest_tried: bool = field(default=False, init=False, repr=False)

    def clock(self) -> datetime:
        """The report instant (every service sees the same 'now')."""
        return self.now

    def open_interest(self) -> Tuple[Optional[ExpiryOpenInterestReport], Optional[str]]:
        """
        Open interest per expiry, computed once per report (every section gets the same object). Returns
        ``(report, None)``, or ``(None, reason)`` when it cannot be computed: that must not fail a section whose own data
        is fine, so the reason is shown as a note instead.
        """
        if not self._open_interest_tried:
            self._open_interest_tried = True
            try:
                self._open_interest = ExpiryOpenInterestService(self.repository, self.clock).open_interest(self.currency)
            except (LookupError, psycopg2.Error) as error:      # data missing or database down: show the reason, keep the section
                logger.warning("open interest per expiry not available: %s", error, exc_info=True)
                self._open_interest_failure = str(error) or type(error).__name__
        return self._open_interest, self._open_interest_failure


def open_interest_fragment(context: SectionContext, limit: Optional[int] = None) -> str:
    """The "Open interest per expiry" heading and table (first ``limit`` rows), or a one-line note why it is missing."""
    report, failure = context.open_interest()
    if report is None:
        return paragraph_fragment(f"Open interest per expiry not available: {failure}", "note")
    html_text = "<h3>Open interest per expiry</h3>" + open_interest_table(report.rows, report.captured_at, report.index_price, limit)
    rest = report.rows[limit:] if limit is not None else []
    if rest:
        known = [row.share for row in rest if row.share is not None]
        remaining = f" hold the remaining {sum(known) * 100:.0f}%." if known else " are not shown."
        html_text += paragraph_fragment(f"{len(rest)} more expiries{remaining}", "note")
    return html_text


def open_interest_order(report: Optional[ExpiryOpenInterestReport]) -> Dict[str, ExpiryOpenInterest]:
    """The open interest rows by expiry label (empty without a report); insertion order = largest first."""
    return {row.expiry: row for row in report.rows} if report is not None else {}


def order_by_open_interest(labels: List[str], rows: Dict[str, ExpiryOpenInterest]) -> List[str]:
    """``labels`` with an open interest row first (largest first), then the others soonest settlement first."""
    with_row = [label for label in rows if label in labels]
    far = datetime.max.replace(tzinfo=timezone.utc)
    others = sorted((label for label in labels if label not in rows), key=lambda label: parse_expiry_settlement(label) or far)
    return with_row + others


def choice_label(label: str, rows: Dict[str, ExpiryOpenInterest], now: datetime) -> str:
    """The expiry choice text: with its open interest when known, "<label>, expired" when it already settled."""
    if label in rows:
        return expiry_choice_label(rows[label])
    settlement = parse_expiry_settlement(label)
    return f"{label}, expired" if settlement is not None and settlement <= now else label


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
    body += open_interest_fragment(context)
    if context.open_interest()[0] is not None:
        body += paragraph_fragment(
            "This table and the order of the per-expiry charts below use the newest chain snapshot (time in the table "
            f"caption); the charts themselves are the daily close of {menthorq_date.isoformat()}, so an expiry that settled "
            "since then is listed last as expired.", "note")
    if per_expiry:
        rows = open_interest_order(context.open_interest()[0])
        order = order_by_open_interest([entry["expiration"] for entry in per_expiry], rows)
        per_expiry = sorted(per_expiry, key=lambda entry: order.index(entry["expiration"]))      # largest open interest first
        expiry_control = SwitcherControl("expiry", "Expiry", [(entry["expiration"], choice_label(entry["expiration"], rows, context.now))
                                                              for entry in per_expiry], per_expiry[0]["expiration"])
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
    """
    The Binance perpetual rolling 24-hour taker buy/sell ratio of the report currency (last 30 days) as an interactive
    chart (the daily ratio and daily net flow are in its hover) and the last complete days as a table.
    """
    symbol = TAKER_FLOW_SYMBOLS.get(context.currency)
    if symbol is None:
        raise LookupError(f"no Binance perpetual is configured for {context.currency}")
    data = TakerFlowChartService(context.repository, symbols=(symbol,), clock=context.clock).figure_data(symbol)
    if not data.days or not data.rolling_points:
        raise LookupError("; ".join(data.warnings) or f"no taker flow stored for {symbol}")
    end = data.newest_hour_utc + timedelta(hours=1) if data.newest_hour_utc is not None else None
    values = [ratio for _, ratio in data.rolling_points]
    figure_id = "futures-flow-rolling-ratio"
    figures = {figure_id: figure_json(build_taker_flow_figure(data.label, data.rolling_points, data.days, ""))}
    controls: List[SwitcherControl] = []          # one view, nothing to pick: the switcher only draws the figure
    day_rows = [{"day": day.day.isoformat(), "taker_buy_usd": day.taker_buy_usd, "taker_sell_usd": day.taker_sell_usd,
                 "ratio": day.ratio} for day in data.days]
    body = (switcher_fragment("futures-flow", controls)
            + paragraph_fragment(
                "No trend lines are drawn: the ratio swings around 1.0 inside a band (about "
                f"{min(values):.2f} to {max(values):.2f} over these {CHART_DAYS} days), so a straight line through it would "
                "suggest a direction the data does not have; the 7-day average and the band show where it sits instead.", "note")
            + taker_flow_table(day_rows))
    return ReportSection(
        key="futures-flow", title="Futures flow", as_of=f"Data up to {format_utc(end)}.", body_html=body, figures=figures,
        switchers={"futures-flow": switcher_configuration(controls, {view_key([]): {"figure": figure_id}})},
        description=(f"Binance {symbol} perpetual: the rolling 24-hour taker buy/sell ratio of the last {CHART_DAYS} days, hour "
                     "by hour; zoom to hours or pick dates, and hover for that day's ratio and net flow. Futures market, not "
                     "options; the taker is the side that crossed the book."),
        warnings=list(data.warnings),
        notes=[f"Stale when the newest hour is more than {STALE_AFTER.total_seconds() / 3600:.0f} hours old. "
               "A day with a missing hour has no daily numbers and the line breaks where an hour is missing.",
               "The 7-day average is the plain mean of the hourly values of the last 168 hours (fewer at the start of the "
               "window); the band is the mean plus and minus one standard deviation of all values shown."])


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


@dataclass
class OptionFlowExpiries:
    """The expiries picked one by one in the option-flow section and why: by open interest or by traded notional."""

    by_open_interest: List[str]
    by_notional: List[str]

    @property
    def kept(self) -> List[str]:
        """Every expiry that can be picked one by one."""
        return self.by_open_interest + self.by_notional


def option_flow_expiries(ranked: List[str], rows: Dict[str, ExpiryOpenInterest],
                         maximum: int = MAXIMUM_OPTION_FLOW_EXPIRIES) -> OptionFlowExpiries:
    """
    The single expiries to precompute, at most ``maximum``: the traded ones among the ``TOP_OPEN_INTEREST_EXPIRIES`` with the
    most open interest always, the rest filled in the order of ``ranked`` (traded expiries, most traded notional first).
    """
    top = [label for label, row in rows.items() if row.total_open_interest is not None][:TOP_OPEN_INTEREST_EXPIRIES]
    required = [label for label in top if label in ranked]
    return OptionFlowExpiries(required, [label for label in ranked if label not in required][:max(maximum - len(required), 0)])


def _option_flow_pick_sentence(chosen: OptionFlowExpiries, traded_count: int, has_open_interest: bool) -> str:
    """What the option-flow expiry list really contains: how many were required by open interest, how many filled by notional."""
    kept_count = len(chosen.kept)
    order = "ordered by open interest" if has_open_interest else "ordered by settlement (open interest not available)"
    if not has_open_interest:
        origin = f"the {len(chosen.by_notional)} most traded by notional"
    elif chosen.by_notional:
        origin = (f"{len(chosen.by_open_interest)} required by open interest (traded among the {TOP_OPEN_INTEREST_EXPIRIES} "
                  f"with the most) and {len(chosen.by_notional)} filled by traded notional")
    else:
        origin = f"{len(chosen.by_open_interest)} required by open interest (traded among the {TOP_OPEN_INTEREST_EXPIRIES} with the most)"
    if traded_count > kept_count:
        return f"{kept_count} can be picked one by one ({origin}), the rest only within all expirations; the list is {order}."
    return f"each can be picked on its own ({origin}); the list is {order}."


def _days_left(label: str, rows: Dict[str, ExpiryOpenInterest], now: datetime) -> Optional[float]:
    """Days to settlement from the open interest row, else from the label; None when neither is known."""
    if label in rows and rows[label].days_left is not None:
        return rows[label].days_left
    settlement = parse_expiry_settlement(label)
    return (settlement - now).total_seconds() / 86400 if settlement is not None else None


def _bar_expiry_fragment(context: SectionContext, single: Dict[str, Any], everything: Any, kept: List[str],
                         rows: Dict[str, ExpiryOpenInterest]) -> str:
    """
    "Which expiry carries the largest bars": ``single`` must hold EVERY traded expiry, else the bars cannot be explained.
    A pure computation over in-memory flows: a ``LookupError`` (data missing) is shown as a one-line note, a programming
    error raises and fails the section.
    """
    heading = "<h3>Which expiry carries the largest bars</h3>"
    try:
        bars = bar_expiry_shares({label: report.flow for label, report in single.items()}, everything.flow, BAR_TABLE_ROWS)
    except LookupError as error:
        logger.warning("expiry shares of the biggest bars not available: %s", error, exc_info=True)
        return heading + paragraph_fragment(f"Not available: {str(error) or type(error).__name__}", "note")
    if not bars:
        return heading + paragraph_fragment("No trades in the window, so there are no bars.", "note")
    days = {item.expiry: _days_left(item.expiry, rows, context.now) for bar in bars for item in bar.expiries}
    return heading + bar_expiry_table(bars, days, set(kept))


def _block_structure_fragment(context: SectionContext, service: Any, hours: int) -> str:
    """The largest blocks of the window with a plain-language sentence; a failure is a one-line note (see above)."""
    heading = "<h3>Largest blocks of the window</h3>"
    try:
        report = service.block_structures(context.currency, hours, end=context.now, top=BLOCK_TABLE_ROWS)
        if not report.blocks:
            return heading + paragraph_fragment("No block trades are stored in the window.", "note")
        return (heading + paragraph_fragment(BLOCK_EXPLANATION)
                + block_structure_table(report.blocks)
                + paragraph_fragment(f"{report.legs_in_window:,} block legs in the window; the {len(report.blocks)} "
                                     "largest blocks are shown.", "note"))
    except (LookupError, psycopg2.Error) as error:
        logger.warning("block structures not available: %s", error, exc_info=True)
        return heading + paragraph_fragment(f"Not available: {str(error) or type(error).__name__}", "note")


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
    open_interest_rows = open_interest_order(context.open_interest()[0])
    ranked = sorted(single, key=lambda label: -sum(cell.notional_usd for cells in single[label].flow.cells.values()
                                                   for cell in cells.values()))
    chosen = option_flow_expiries(ranked, open_interest_rows)
    kept = chosen.kept
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
    ordered = order_by_open_interest(kept, open_interest_rows)
    expiry_choices = [(ALL_EXPIRATIONS, "All expirations")] + [(label, choice_label(label, open_interest_rows, context.now))
                                                               for label in ordered]
    controls = [SwitcherControl("expiry", "Expiry", expiry_choices, ALL_EXPIRATIONS),
                SwitcherControl("unit", "Unit", OPTION_FLOW_UNITS, "notional")]
    notes = [f"Window {format_utc(everything.window_start)} to {format_utc(everything.window_end)}. "
             f"{len(traded)} expirations traded in it; "
             + _option_flow_pick_sentence(chosen, len(traded), bool(open_interest_rows))
             + " Buy/sell is the taker's side (aggressor); blocked = Deribit block trades, drawn next to the axis.",
             "The window is fixed at 24 hours in the report; other windows: scripts/option_flow_by_strike.py."]
    below_switcher = (_bar_expiry_fragment(context, single, everything, kept, open_interest_rows)
                      + _block_structure_fragment(context, service, hours))
    return ReportSection(
        key="option-flow", title="Option flow", as_of=f"Last {hours} hours up to {format_utc(everything.window_end)}.",
        body_html=(open_interest_fragment(context, OPTION_FLOW_TABLE_ROWS) + switcher_fragment("option-flow", controls)
                   + below_switcher),
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

def levels_expiries(rows: Dict[str, ExpiryOpenInterest], now: datetime) -> Tuple[List[str], Optional[str]]:
    """
    The expiries that get a Levels view: the ``TOP_OPEN_INTEREST_EXPIRIES`` with the most open interest plus the nearest
    monthly one (no duplicates, largest open interest first), and the one to show first (the nearest monthly, else the first).
    """
    ranked = [label for label, row in rows.items() if row.total_open_interest is not None]
    chosen = ranked[:TOP_OPEN_INTEREST_EXPIRIES]
    monthly = sorted((row.settlement, label) for label, row in rows.items()
                     if row.settlement is not None and row.settlement > now and is_monthly_expiry(row.settlement))
    nearest_monthly = monthly[0][1] if monthly else None
    if nearest_monthly is not None and nearest_monthly not in chosen:
        chosen.append(nearest_monthly)
    return chosen, nearest_monthly or (chosen[0] if chosen else None)


def build_levels_section(context: SectionContext, service: Optional[LevelOverviewService] = None) -> ReportSection:
    """
    Every source's levels on one price line (the Levels page), 24-hour option flow. The dealer lanes are computed for the
    expiries with the most open interest and the nearest monthly one (one complete page each, picked in a switcher); the
    other lanes do not depend on the expiry. Without open interest per expiry: one page, nearest monthly expiry.
    """
    service = service or LevelOverviewService(context.repository, context.clock)
    report, failure = context.open_interest()
    rows = open_interest_order(report)
    expiries, first = levels_expiries(rows, context.now)
    if not expiries:
        overview = service.overview(context.currency, None, OPTION_FLOW_HOURS)
        note = paragraph_fragment(f"Open interest per expiry not available: {failure or 'no expiry has open interest'}", "note")
        return _levels_section(overview, note + page_frame_fragment("levels-page", f"{context.currency} levels of every source"),
                               {"levels-page": embeddable_page(build_level_overview_page(overview))}, {})

    overviews = {}
    skipped: List[str] = []
    for expiry in expiries:
        try:
            overviews[expiry] = service.overview(context.currency, expiry, OPTION_FLOW_HOURS)
        except LookupError as error:
            skipped.append(f"{expiry}: {error}")
    if not overviews:
        raise LookupError("no expiry could be computed: " + "; ".join(skipped))
    pages = {f"levels-page-{expiry}": embeddable_page(build_level_overview_page(overview)) for expiry, overview in overviews.items()}
    views = {view_key([expiry]): {"page": f"levels-page-{expiry}"} for expiry in overviews}
    selected = first if first in overviews else next(iter(overviews))
    control = SwitcherControl("expiry", "Dealer expiry", [(expiry, choice_label(expiry, rows, context.now)) for expiry in overviews],
                              selected)
    body = (open_interest_fragment(context, OPTION_FLOW_TABLE_ROWS)
            + paragraph_fragment(f"Dealer lanes are shown for the {TOP_OPEN_INTEREST_EXPIRIES} expiries with the most open "
                                 "interest and the nearest monthly expiry; the other lanes do not depend on the expiry.", "note")
            + switcher_fragment("levels", [control]))
    section = _levels_section(overviews[selected], body, pages, {"levels": switcher_configuration([control], views)})
    section.notes += [f"Not computed: {text}" for text in skipped]
    return section


def _levels_section(overview: Any, body_html: str, pages: Dict[str, str], switchers: Dict[str, Any]) -> ReportSection:
    """The Levels section around the page(s); the header facts are those of the page shown first."""
    sources = len({mark.source for mark in overview.marks})
    return ReportSection(
        key="levels", title="Levels",
        as_of=f"Spot {overview.spot:,.0f}; dealer expiry {overview.expiry} shown first; option flow of the last {OPTION_FLOW_HOURS} hours.",
        body_html=body_html, pages=pages, switchers=switchers,
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
