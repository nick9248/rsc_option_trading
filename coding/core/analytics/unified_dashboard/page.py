"""
The unified dashboard: the market report and the on-chain dashboard of one run merged into ONE themed HTML page.

Everything in both inputs is kept (figures, switchers, embedded level pages, tables); a new Overview tab is built from their
tables. The inputs are the HTML files the report generator and the on-chain dashboard write, so this module depends on their
structure: when one of them changes its markup, the tests of this package fail.
"""
import json
from pathlib import Path

from coding.core.analytics.unified_dashboard.overview import build_overview, esc, extract

PACKAGE_DIRECTORY = Path(__file__).parent
ASSET_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum"}
LOGOS = {"BTC": "₿", "ETH": "Ξ"}

TABS = [  # (key, label, source, source panel id)
    ("home", "Overview", None, None),
    ("levels", "Levels", "A", "levels"),
    ("decision", "Evidence", "A", "decision"),
    ("expiries", "Expiries", "B", "expiries"),
    ("menthorq", "MenthorQ", "A", "menthorq"),
    ("dealer-levels", "Dealer levels", "A", "dealer-levels"),
    ("option-flow", "Option flow", "A", "option-flow"),
    ("futures-flow", "Futures flow", "A", "futures-flow"),
    ("market-wide", "Market-wide", "B", "market-wide"),
    ("snapshot", "Snapshot tables", "B", "overview"),
    ("data-quality", "Data quality", "B", "data-quality"),
    ("guide", "Guide", "A", "__intro__"),
]


def build_unified_dashboard_page(market_report_html: str, onchain_dashboard_html: str, currency: str) -> str:
    """
    Merge the two pages of one run into the unified dashboard page.

    Args:
        market_report_html: The market report page (``ReportService`` output).
        onchain_dashboard_html: The on-chain dashboard page (``dashboard.html``).
        currency: "BTC" or "ETH"; used for titles and to shorten instrument names.

    Returns:
        The complete, self-contained HTML page.

    Raises:
        ValueError: when an input has no embedded report data, i.e. is not a page of the expected structure.
    """
    try:
        A, B = extract(market_report_html, "m"), extract(onchain_dashboard_html, "o")
    except AttributeError as error:
        raise ValueError('an input page has no embedded report data (script id="report-data"): it is not a market report '
                         'or on-chain dashboard page') from error
    plotly = A["plotly"] or B["plotly"] or ""
    data = {"figures": {**A["figures"], **B["figures"]}, "pages": {**A["pages"], **B["pages"]}, "switchers": {**A["switchers"], **B["switchers"]}}
    pa, pb = dict(A["panels"]), dict(B["panels"])
    overview = build_overview(A, B, currency)
    nav, panels = [], []
    for i, (key, label, src, pid) in enumerate(TABS):
        if src == "A":
            inner = A["intro"] if pid == "__intro__" else pa.get(pid)
        elif src == "B":
            inner = pb.get(pid)
        else:
            inner = overview
        if inner is None:
            continue
        cls = "panel ov" if key == "home" else "panel src"
        nav.append(f'<button type="button" role="tab" id="tab-{key}" data-key="{key}" aria-controls="panel-{key}" aria-selected="false" tabindex="-1">{esc(label)}</button>')
        if key == "guide":
            inner = f'<div class="panel-head"><h2>How to read this dashboard</h2></div>{inner}'
        panels.append(f'<section class="{cls}" role="tabpanel" id="panel-{key}" aria-labelledby="tab-{key}" tabindex="0" hidden>{inner}</section>')

    fa, fb = dict(A["facts"]), dict(B["facts"])
    facts = [("Built", fa.get("Generated") or fb.get("Generated", "")), ("Newest trade", fa.get("Newest stored trade", "")), ("Chain", fa.get("Chain snapshot", ""))]
    sync = next((v for k, v in A["facts"] if "sync" in (k + v).lower()), "")
    facts_html = "".join(f'<span class="fact"><span>{esc(k)}</span>{esc(v)}</span>' for k, v in facts if v)
    if sync:
        facts_html += f'<span class="fact fact-warn">{esc(sync)}</span>'
    title_date = (fa.get("Generated") or fb.get("Generated", ""))

    template = (PACKAGE_DIRECTORY / "page_template.html").read_text(encoding="utf-8")
    stylesheet = (PACKAGE_DIRECTORY / "dashboard.css").read_text(encoding="utf-8")
    application_script = (PACKAGE_DIRECTORY / "dashboard.js").read_text(encoding="utf-8")
    page = template.replace("%%NAV%%", "".join(nav)).replace("%%PANELS%%", "\n".join(panels)).replace("%%FACTS%%", facts_html) \
        .replace("%%TITLE%%", esc(f"{currency} Market Intelligence · {title_date}")).replace("%%CSS%%", stylesheet).replace("%%APP%%", application_script) \
        .replace("%%LOGO%%", LOGOS[currency]).replace("%%ASSET_NAME%%", ASSET_NAMES[currency]).replace("%%CURRENCY%%", currency)
    data_json = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return page.replace("%%DATA%%", data_json).replace("%%PLOTLY%%", plotly.replace("</script", "<\\/script").replace("\ufffd", "\\uFFFD"))
