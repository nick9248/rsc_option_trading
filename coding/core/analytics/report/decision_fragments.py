"""
HTML fragments of the "Decision" tab of the market report (pure; all text is escaped).

They only render what the decision engine returned (``DecisionReport``): nothing is scored a second time here. A missing
value is written as "not computed", never as zero. Amounts are written in USD, never with a dollar sign.
"""

import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.decision import decision_config
from coding.core.analytics.decision.decision_report import (
    BREAK_DOWN, BREAK_UP, INSIDE, ChangeTrigger, DecisionReport, ScenarioAssessment)
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.price_map import MapLevel
from coding.core.analytics.report.report_page import format_utc, paragraph_fragment, table_fragment
from coding.core.analytics.report.section_fragments import NOT_COMPUTED

UNCALIBRATED_WORDS = "uncalibrated, not a probability"
DECISIONS_NOTE = "Decision support, not orders, not targets."
GROUP_NOTE = ("Items of one input group are derived from the same data, so the engine counts a group once, however many "
              "items it holds.")
SCENARIO_WORDS = {INSIDE: "stays inside", BREAK_UP: "breaks above", BREAK_DOWN: "breaks below"}
NO_TOP_SCENARIO_WORDS = "no scenario with net support"


def _cell(text: str, css_class: str = "") -> str:
    attribute = f' class="{html.escape(css_class)}"' if css_class else ""
    return f"<td{attribute}>{html.escape(text)}</td>"


def _list_fragment(entries: Sequence[str]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(entry)}</li>" for entry in entries) + "</ul>"


def _ids_text(identifiers: Sequence[str]) -> str:
    return ", ".join(identifiers) if identifiers else "none"


def _small_value_text(value: float) -> str:
    """Two decimals; a positive value that would round to 0.00 is written "<0.01" (0.00 only for an exact 0)."""
    return "<0.01" if 0 < value < 0.005 else f"{value:.2f}"


def _price_text(price: Optional[float]) -> str:
    return f"{price:,.0f}" if price is not None else NOT_COMPUTED


def _scenario_of_top(report: DecisionReport) -> Optional[ScenarioAssessment]:
    return report.scenario(report.top_scenario) if report.top_scenario is not None else None


# ---------------------------------------------------------------- (1) header line

def decision_header_fragment(report: DecisionReport) -> str:
    """
    One line: the top scenario (the only highlighted word group of the tab), its confidence label and its evidence score,
    with the words that say the score is uncalibrated. Without a top scenario the line says so and gives the reason.
    """
    top = _scenario_of_top(report)
    if top is None:
        reason = "; ".join(report.confidence_reasons) or NOT_COMPUTED
        return ('<p class="verdict"><mark>No scenario has net support</mark>. '
                f'Confidence: {html.escape(report.confidence)}. Reason: {html.escape(reason)}.</p>')
    return (f'<p class="verdict">Best-supported scenario: <mark>{html.escape(top.title)}</mark>. '
            f'Confidence: {html.escape(top.confidence)} (an ordinal label, built from the reasons below). '
            f'Evidence score {top.evidence_score:.1f} of 100 ({html.escape(UNCALIBRATED_WORDS)}).</p>')


# ---------------------------------------------------------------- (2) price map

def _distance_text(price: float, spot: float) -> str:
    return f"{(price - spot) / spot:+.1%}"


def _map_row(role: str, level: Optional[MapLevel], spot: float, unmapped_text: str) -> str:
    if level is None:
        return f'<tr><th scope="row">{html.escape(role)}</th>' + _cell(unmapped_text, "wrap") + _cell("") * 5 + "</tr>"
    band = _price_text(level.low) if level.low == level.high else f"{_price_text(level.low)} to {_price_text(level.high)}"
    return (f'<tr><th scope="row">{html.escape(role)}</th>' + _cell(_price_text(level.price), "num")
            + _cell(band, "num") + _cell(_distance_text(level.price, spot), "num") + _cell(_small_value_text(level.weight), "num")
            + _cell(", ".join(level.input_groups), "wrap") + _cell(", ".join(level.evidence_ids), "wrap") + "</tr>")


def price_map_fragment(report: DecisionReport) -> str:
    """
    The price map as a table (next level above, resistance, spot, support, next level below, bands at the spot) and a plain
    sentence for every side that is unmapped. A one-sided map is stated as such; no level is invented.
    """
    price_map = report.price_map
    spot = price_map.spot
    distance = f"{price_map.maximum_distance / spot:.0%}"
    unmapped = f"unmapped: no meaningful level within {distance} of the spot"
    rows = [_map_row("Next level above", price_map.next_above, spot, "none within the distance"),
            _map_row("Resistance (nearest meaningful level above)", price_map.resistance, spot, unmapped),
            f'<tr><th scope="row">Spot</th>{_cell(_price_text(spot), "num")}{_cell("") * 5}</tr>',
            _map_row("Support (nearest meaningful level below)", price_map.support, spot, unmapped),
            _map_row("Next level below", price_map.next_below, spot, "none within the distance")]
    rows += [_map_row("At the spot (the band contains the spot)", band, spot, "") for band in price_map.at_spot]
    headers = ["Role", "Price", "Band (low to high)", "From the spot", "Weight", "Input groups", "Evidence"]
    head = "".join(f'<th scope="col"{" class=\"num\"" if index in (1, 2, 3, 4) else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    table = ('<div class="table-wrap"><table class="price-map"><caption>Levels from the evidence, nearest meaningful band '
             f'on each side of the spot</caption><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    sentences = []
    if price_map.support is None and price_map.resistance is None:
        sentences.append(f"Neither side is mapped: no meaningful level within {distance} of the spot, so no scenario can be built.")
    elif price_map.resistance is None:
        sentences.append(f"One-sided map: the upside is unmapped (no meaningful level above the spot within {distance}); "
                         "only the support bounds the scenarios.")
    elif price_map.support is None:
        sentences.append(f"One-sided map: the downside is unmapped (no meaningful level below the spot within {distance}); "
                         "only the resistance bounds the scenarios.")
    meaningful = sum(1 for level in price_map.levels if level.meaningful)
    counts = (f"{len(price_map.levels)} bands within {distance} of the spot, {meaningful} meaningful (weight at least "
              f"{decision_config.MINIMUM_LEVEL_WEIGHT:g}); {len(price_map.left_out)} price levels beyond that distance are left out.")
    return table + "".join(paragraph_fragment(text, "verdict-failed") for text in sentences) + paragraph_fragment(counts, "note")


# ---------------------------------------------------------------- (3) scenarios

def _contributions_text(contributions: Dict[str, float]) -> str:
    return "; ".join(f"{group} {value:+.2f}" for group, value in contributions.items()) or "none"


def scenarios_fragment(report: DecisionReport) -> str:
    """One row per scenario, best score first: score, label, supporting and opposing evidence ids, reasons for the label."""
    if not report.scenarios:
        return paragraph_fragment("No scenario: there is no mapped level on either side of the spot.")
    headers = ["Scenario", "Evidence score", "Confidence", "Supporting evidence", "Opposing evidence",
               "Input group contributions", "Reasons for the label"]
    head = "".join(f'<th scope="col"{" class=\"num\"" if index == 1 else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    body = "".join(
        f'<tr><th scope="row" class="wrap">{html.escape(scenario.title)}</th>'
        + _cell(f"{scenario.evidence_score:.1f}", "num") + _cell(scenario.confidence)
        + _cell(_ids_text(scenario.supporting_evidence_ids), "wrap") + _cell(_ids_text(scenario.opposing_evidence_ids), "wrap")
        + _cell(_contributions_text(scenario.group_contributions), "wrap")
        + f'<td class="wrap">{_list_fragment(scenario.confidence_reasons)}</td></tr>'
        for scenario in report.scenarios)
    table = ('<div class="table-wrap"><table class="scenarios"><caption>'
             f'Evidence score: {html.escape(report.score_scale)}; the scores of different scenarios do not add up to 100'
             f'</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
    return table


# ---------------------------------------------------------------- (4) decisions

def decisions_fragment(report: DecisionReport) -> str:
    """The decision-support lines exactly as the engine produced them, under the note that they are not orders or targets."""
    lines = "".join(f"<li><strong>{html.escape(item.kind.replace('_', ' '))}</strong> {html.escape(item.text)}</li>"
                    for item in report.decisions)
    return paragraph_fragment(DECISIONS_NOTE, "note") + f'<ul class="decision-lines">{lines}</ul>'


# ---------------------------------------------------------------- (5) evidence

def _lean_text(item: EvidenceItem) -> str:
    return item.direction or (item.regime.replace("_", "-") if item.regime else None) or "not applicable"


def _evidence_row(item: EvidenceItem) -> str:
    return (f'<tr><th scope="row">{html.escape(item.id)}</th>' + _cell(item.source, "wrap") + _cell(item.kind.replace("_", " "))
            + _cell(_lean_text(item)) + _cell(_price_text(item.price) if item.price is not None else "not applicable", "num")
            + _cell(_small_value_text(item.strength), "num") + _cell(_small_value_text(item.quality), "num")
            + _cell(f"{item.horizon_days:.1f}" if item.horizon_days is not None else NOT_COMPUTED, "num")
            + _cell("; ".join(item.quality_reasons) or "none", "wrap") + _cell(format_utc(item.as_of))
            + _cell(item.statement, "wrap") + "</tr>")


def evidence_fragment(report: DecisionReport) -> str:
    """
    The full evidence table, one group of rows per input group (sorted by group name) so the independence logic can be
    checked: id, source, kind, direction or regime, price, strength, quality, horizon, quality reasons, as-of (UTC), and what the item
    says and does not say.
    """
    if not report.evidence:
        return paragraph_fragment("No evidence was computed.")
    headers = ["Id", "Source", "Kind", "Direction or regime", "Price", "Strength", "Quality", "Horizon (days)",
               "Quality reasons", "As of (UTC)", "Says / does not say"]
    head = "".join(f'<th scope="col"{" class=\"num\"" if index in (4, 5, 6, 7) else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    groups: Dict[str, List[EvidenceItem]] = {}
    for item in report.evidence:
        groups.setdefault(item.input_group, []).append(item)
    body = []
    for group in sorted(groups):
        items = groups[group]
        body.append(f'<tr class="group-row"><th scope="colgroup" colspan="{len(headers)}">{html.escape(group)}: '
                    f'{len(items)} item{"" if len(items) == 1 else "s"}, counted once</th></tr>')
        body.extend(_evidence_row(item) for item in items)
    return (paragraph_fragment(GROUP_NOTE, "note")
            + '<div class="table-wrap"><table class="evidence"><caption>Every item after the freshness check, grouped by input '
              f'group</caption><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


# ---------------------------------------------------------------- (6) what would change this

def change_triggers_fragment(report: DecisionReport) -> str:
    """The items whose flip or removal alone would change the top scenario, and what would then lead."""
    if not report.what_would_change:
        return paragraph_fragment("No single item's flip or removal changes the top scenario.")
    rows = [[trigger.evidence_id, trigger.change, _new_top_text(trigger)] for trigger in report.what_would_change]
    return table_fragment(["Item", "Change", "Top scenario then"], rows,
                          caption="Each item is changed alone and the whole report is recomputed")


def _new_top_text(trigger: ChangeTrigger) -> str:
    if trigger.new_top_scenario is None:
        return NO_TOP_SCENARIO_WORDS
    return SCENARIO_WORDS.get(trigger.new_top_scenario, trigger.new_top_scenario)


# ---------------------------------------------------------------- (7) caveats and freshness

def _context_time(report: DecisionReport, key: str) -> Optional[datetime]:
    """A time stored in ``report.context`` as ISO 8601 text; None when absent, unreadable or without a time zone."""
    value = report.context.get(key)
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _age_text(instant: Optional[datetime], now: datetime) -> str:
    return f"{(now - instant).total_seconds() / 3600:.1f} h" if instant is not None else NOT_COMPUTED


def data_ages(report: DecisionReport) -> List[Tuple[str, Optional[datetime]]]:
    """(data, as-of) pairs: the spot, the chain snapshot and the newest item of every evidence source."""
    ages: List[Tuple[str, Optional[datetime]]] = [("Index price (spot)", _context_time(report, "spot_time")),
                                                  ("Option chain snapshot", _context_time(report, "chain_snapshot"))]
    newest: Dict[str, datetime] = {}
    for item in report.evidence:
        newest[item.source] = max(item.as_of, newest.get(item.source, item.as_of))
    ages.extend((source, newest[source]) for source in sorted(newest))
    return ages


def caveats_fragment(report: DecisionReport) -> str:
    """Sources not computed and why, the data caveats, and the age of every input at the report time."""
    caveats = report.context.get("caveats")
    caveat_list: List[Any] = list(caveats) if isinstance(caveats, list) else []
    parts = ["<h4>Sources not computed</h4>"
             + (_list_fragment(report.not_computed) if report.not_computed else paragraph_fragment("Every source was computed.")),
             "<h4>Data caveats</h4>"
             + (_list_fragment([str(text) for text in caveat_list]) if caveat_list else paragraph_fragment("No data caveats.")),
             "<h4>Data ages</h4>"]
    rows = [[name, format_utc(instant, missing=NOT_COMPUTED), _age_text(instant, report.as_of)]
            for name, instant in data_ages(report)]
    parts.append(table_fragment(["Data", "As of (UTC)", f"Age at {format_utc(report.as_of)}"], rows, numeric_columns=(2,)))
    parts.append(paragraph_fragment("Items past their freshness limit have lowered quality; the reason is in the quality "
                                    "reasons of the evidence table.", "note"))
    return "".join(parts)


# ---------------------------------------------------------------- the whole tab

def decision_body_fragment(report: DecisionReport) -> str:
    """The seven parts of the Decision tab in reading order, each under its own heading (the first is the header line)."""
    return (decision_header_fragment(report)
            + "<h3>Price map</h3>" + price_map_fragment(report)
            + "<h3>Scenarios</h3>" + scenarios_fragment(report)
            + "<h3>Decisions</h3>" + decisions_fragment(report)
            + "<h3>Evidence</h3>" + evidence_fragment(report)
            + "<h3>What would change this</h3>" + change_triggers_fragment(report)
            + "<h3>Data caveats and freshness</h3>" + caveats_fragment(report))
