"""
The levels overview as one self-contained HTML page (inline CSS, JS and SVG, no network): a price ladder with a lane per
source, bands where sources agree, and a ledger that lists every level so the numbers can be checked.

Pure: the page is built from the ``LevelOverview`` and the confluences it is given. The page does its own drawing, hover
linking and view range (zoom) in the browser; the numbers in it are exactly the ones in the data block.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from coding.core.analytics.option_flow.level_overview import (
    KIND_LABELS, Confluence, LevelMark, LevelOverview, find_confluences)

TEMPLATE_PATH = Path(__file__).with_name("level_overview_page.html")
DATA_PLACEHOLDER = "/*__DATA__*/null"
CHECK_FAILED_SUFFIX = ", check failed"


def describe_source(source: str) -> Tuple[str, str, bool]:
    """
    Split a source name into the lane title, its subtitle and whether its plausibility check failed.

    "MenthorQ replica (monthly expiries)" -> ("MenthorQ replica", "monthly expiries", False);
    "Dealer GEX 27NOV26, flow-based, check failed" -> ("Dealer GEX 27NOV26", "flow-based", True).
    """
    failed = source.endswith(CHECK_FAILED_SUFFIX)
    name = source[: -len(CHECK_FAILED_SUFFIX)] if failed else source
    if " (" in name and name.endswith(")"):
        title, subtitle = name[:-1].split(" (", 1)
    elif "," in name:
        title, subtitle = (part.strip() for part in name.split(",", 1))
    else:
        title, subtitle = name, ""
    return title, subtitle, failed


def page_data(overview: LevelOverview, confluences: Sequence[Confluence], band_percent: float,
              minimum_sources: int) -> Dict[str, Any]:
    """The data block the page draws from (JSON-serialisable)."""
    marks = sorted(overview.marks, key=lambda mark: -mark.price)
    index_of = {mark: position for position, mark in enumerate(marks)}
    source_names: List[str] = list(dict.fromkeys(mark.source for mark in overview.marks))
    sources = []
    for name in source_names:
        title, subtitle, failed = describe_source(name)
        sources.append({"key": name, "title": title, "subtitle": subtitle, "failed": failed})
    return {
        "currency": overview.currency, "expiry": overview.expiry, "spot": overview.spot,
        "bandPercent": band_percent, "minimumSources": minimum_sources, "notes": list(overview.notes),
        "kinds": {kind: label for kind, label in KIND_LABELS.items()},
        "sources": sources,
        "marks": [{"source": mark.source, "name": mark.name, "price": mark.price, "kind": mark.kind} for mark in marks],
        "confluences": [{"low": band.low, "high": band.high, "sources": band.sources,
                         "marks": sorted(index_of[mark] for mark in band.marks)} for band in confluences],
    }


def build_level_overview_page(overview: LevelOverview, band_percent: float = 1.0, minimum_sources: int = 3) -> str:
    """
    The complete page for ``overview``.

    Args:
        overview: The collected levels.
        band_percent: Width of a confluence band in percent of the spot.
        minimum_sources: Different sources needed in a band.
    """
    confluences = find_confluences(overview.marks, band_percent / 100 * overview.spot, minimum_sources)
    payload = json.dumps(page_data(overview, confluences, band_percent, minimum_sources), ensure_ascii=False)
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\u0021--")          # keep the data from closing the script tag
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if DATA_PLACEHOLDER not in template:
        raise ValueError("the page template has no data placeholder")
    return template.replace(DATA_PLACEHOLDER, payload)
