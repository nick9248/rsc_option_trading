"""
Assembles the market report: ONE self-contained HTML file (inline CSS, JS, images and plotly.js, no network) with a header
that states how fresh the data is and a tab per section.

Pure: everything comes from the ``ReportHeader`` and the ``ReportSection`` objects it is given. Text is escaped here; a
section's ``body_html`` is a fragment built with the helpers of this module (which escape their own text).

How a section's content gets into the page:

* images (PNG charts): inline as ``data:image/png;base64`` (``image_fragment``), or inside a switcher view;
* Plotly figures: as JSON in ONE data block; plotly.js is inlined once for the whole page and only when a figure is present;
  a figure is drawn when its tab is first shown (a hidden element has no width to draw into);
* a complete page of its own (the Levels page): kept as a string in the data block and loaded into an ``iframe`` through
  ``srcdoc`` when its tab is first shown;
* switchers: controls (buttons for a few choices, a list otherwise) that pick one precomputed view (a figure or an image,
  and a caption) by the combination of their values.
"""

import base64
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

TEMPLATE_PATH = Path(__file__).with_name("report_page.html")
MAXIMUM_BUTTON_CHOICES = 4          # a control with more choices is drawn as a list
VIEW_KEY_SEPARATOR = "|"
_PLACEHOLDER = re.compile(r"<!--__(TITLE|HEADER|TABS|PANELS|PLOTLY)__-->|/\*__DATA__\*/null")


@dataclass
class SectionFailure:
    """Why a section could not be built and what to do about it (plain text)."""

    what: str
    fix: str


@dataclass
class ReportSection:
    """
    One tab of the report.

    Attributes:
        key: Identifier used in element ids and the address fragment (letters, digits and hyphens).
        title: Tab label in sentence case.
        description: One or two sentences on what the section shows.
        as_of: When the data of the section is from (plain text), or None when not known.
        body_html: Content fragment (built with the helpers of this module).
        notes: Neutral remarks (plain text).
        warnings: Data problems the reader must know (plain text).
        failure: Set when the section could not be built; the body is then not shown.
        figures: Plotly figures by id, each as ``{"data": [...], "layout": {...}}``.
        switchers: Switcher configurations by id (see ``switcher_fragment``).
        pages: Complete HTML pages by id, shown in an iframe (see ``page_frame_fragment``).
    """

    key: str
    title: str
    description: str = ""
    as_of: Optional[str] = None
    body_html: str = ""
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    failure: Optional[SectionFailure] = None
    figures: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    switchers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pages: Dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        """True when the section could not be built."""
        return self.failure is not None


@dataclass
class ReportHeader:
    """The top of the report: currency, when it was made and how fresh its data is."""

    currency: str
    generated_at: datetime
    facts: List[Tuple[str, str]] = field(default_factory=list)      # (label, value), e.g. ("Newest stored trade", "...")


@dataclass
class SwitcherControl:
    """One control of a switcher: its name, label, choices as (value, label) and the value selected first."""

    name: str
    label: str
    choices: List[Tuple[str, str]]
    selected: str


def format_utc(instant: Optional[datetime], missing: str = "not known") -> str:
    """'2026-11-14 10:29 UTC' (``missing`` when there is no instant). Naive instants are refused."""
    if instant is None:
        return missing
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware (UTC)")
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def sentence(text: str) -> str:
    """Text with a capital first letter (messages of the services start in lower case)."""
    return text[:1].upper() + text[1:]


def embeddable_page(page: str) -> str:
    """A complete page made to sit inside the report: its own outer padding removed (the report already has one)."""
    override = "<style>main { padding: 4px 0 24px !important; }</style>"
    return page.replace("</head>", override + "</head>", 1) if "</head>" in page else override + page


def png_data_uri(png: bytes) -> str:
    """A PNG as a ``data:`` URI."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def image_fragment(png: bytes, alternative_text: str, caption: Optional[str] = None) -> str:
    """One PNG chart, inline, with an optional caption."""
    caption_html = f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
    return (f'<figure class="chart-image"><img src="{png_data_uri(png)}" alt="{html.escape(alternative_text)}" '
            f'loading="lazy">{caption_html}</figure>')


def paragraph_fragment(text: str, css_class: str = "") -> str:
    """A paragraph of plain text."""
    class_attribute = f' class="{html.escape(css_class)}"' if css_class else ""
    return f"<p{class_attribute}>{html.escape(text)}</p>"


def table_fragment(headers: Sequence[str], rows: Sequence[Sequence[str]], caption: Optional[str] = None,
                   numeric_columns: Sequence[int] = ()) -> str:
    """A small table of plain-text cells; ``numeric_columns`` are right-aligned in the numeral font."""
    caption_html = f"<caption>{html.escape(caption)}</caption>" if caption else ""
    head = "".join(f'<th scope="col"{" class=\"num\"" if index in numeric_columns else ""}>{html.escape(text)}</th>'
                   for index, text in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(
            (f'<th scope="row">{html.escape(cell)}</th>' if index == 0 else
             f'<td{" class=\"num\"" if index in numeric_columns else ""}>{html.escape(cell)}</td>')
            for index, cell in enumerate(row)) + "</tr>"
        for row in rows)
    return f'<div class="table-wrap"><table>{caption_html}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def view_key(values: Sequence[str]) -> str:
    """The key of a switcher view: the control values joined in control order."""
    return VIEW_KEY_SEPARATOR.join(values)


def switcher_fragment(switcher_id: str, controls: Sequence[SwitcherControl]) -> str:
    """
    The controls, caption and drawing area of a switcher. Its views go into ``ReportSection.switchers[switcher_id]``
    as ``{"controls": [names...], "selected": {name: value}, "views": {view_key: {"figure": id} or
    {"image": data URI, "alt": text}, plus "caption": HTML}}`` (see ``switcher_configuration``).
    """
    parts = []
    for control in controls:
        name = html.escape(control.name)
        if len(control.choices) <= MAXIMUM_BUTTON_CHOICES:
            buttons = "".join(
                f'<button type="button" data-control="{name}" data-value="{html.escape(value)}" '
                f'aria-pressed="{"true" if value == control.selected else "false"}">{html.escape(label)}</button>'
                for value, label in control.choices)
            parts.append(f'<div class="control"><span class="control-label" id="{html.escape(switcher_id)}-{name}-label">'
                         f'{html.escape(control.label)}</span><span role="group" class="choice-buttons" '
                         f'aria-labelledby="{html.escape(switcher_id)}-{name}-label">{buttons}</span></div>')
        else:
            options = "".join(
                f'<option value="{html.escape(value)}"{" selected" if value == control.selected else ""}>{html.escape(label)}</option>'
                for value, label in control.choices)
            parts.append(f'<label class="control"><span class="control-label">{html.escape(control.label)}</span>'
                         f'<select data-control="{name}">{options}</select></label>')
    return (f'<div class="switcher" data-switcher="{html.escape(switcher_id)}">'
            f'<div class="controls">{"".join(parts)}</div>'
            f'<div class="switcher-caption" aria-live="polite"></div>'
            f'<div class="switcher-plot"></div><img class="switcher-image" alt="" hidden></div>')


def switcher_configuration(controls: Sequence[SwitcherControl], views: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    The data of a switcher for ``ReportSection.switchers``.

    Raises:
        ValueError: when the first selection has no view (the reader would start on an empty panel).
    """
    selected = {control.name: control.selected for control in controls}
    if view_key([control.selected for control in controls]) not in views:
        raise ValueError("the switcher has no view for its first selection")
    return {"controls": [control.name for control in controls], "selected": selected, "views": views}


def page_frame_fragment(page_id: str, title: str) -> str:
    """An iframe that shows the complete page ``ReportSection.pages[page_id]`` once its tab is opened."""
    return f'<iframe class="page-frame" data-page="{html.escape(page_id)}" title="{html.escape(title)}"></iframe>'


def _script_safe_json(value: Any) -> str:
    """JSON that cannot end the script element it sits in."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def _header_html(header: ReportHeader) -> str:
    """Title and the data-freshness facts."""
    facts = "".join(f"<div><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>" for label, value in header.facts)
    return (f'<h1><span class="currency">{html.escape(header.currency)}</span> market report</h1>'
            f'<dl class="facts"><div><dt>Generated</dt><dd>{html.escape(format_utc(header.generated_at))}</dd></div>{facts}</dl>')


def _tabs_html(sections: Sequence[ReportSection]) -> str:
    """The tab bar (the first section is selected)."""
    buttons = []
    for index, section in enumerate(sections):
        key = html.escape(section.key)
        flag = '<span class="flag">not built</span>' if section.failed else ""
        buttons.append(
            f'<button type="button" role="tab" id="tab-{key}" data-key="{key}" aria-controls="panel-{key}" '
            f'aria-selected="{"true" if index == 0 else "false"}" tabindex="{0 if index == 0 else -1}">'
            f'{html.escape(section.title)}{flag}</button>')
    return f'<nav class="tabs" role="tablist" aria-label="Report sections">{"".join(buttons)}</nav>'


def _section_html(section: ReportSection, index: int) -> str:
    """One tab panel."""
    key = html.escape(section.key)
    as_of = f'<p class="as-of">{html.escape(section.as_of)}</p>' if section.as_of else ""
    description = f'<p class="description">{html.escape(section.description)}</p>' if section.description else ""
    if section.failure is not None:
        body = (f'<div class="failure" role="status"><h3>This section could not be built</h3>'
                f'<p>{html.escape(section.failure.what)}</p><p class="fix">{html.escape(section.failure.fix)}</p></div>')
    else:
        body = section.body_html
    warnings = ("<ul class=\"warnings\">" + "".join(f"<li>{html.escape(sentence(text))}</li>" for text in section.warnings)
                + "</ul>" if section.warnings else "")
    notes = ("<div class=\"notes\"><h3>About this data</h3>"
             + "".join(f"<p>{html.escape(sentence(text))}</p>" for text in section.notes) + "</div>" if section.notes else "")
    hidden = "" if index == 0 else " hidden"
    return (f'<section class="panel" role="tabpanel" id="panel-{key}" aria-labelledby="tab-{key}" tabindex="0"{hidden}>'
            f'<header class="panel-head"><h2>{html.escape(section.title)}</h2>{as_of}</header>{description}'
            f'{warnings}{body}{notes}</section>')


def build_report_page(header: ReportHeader, sections: Sequence[ReportSection], plotly_javascript: Optional[str]) -> str:
    """
    The complete report page.

    Args:
        header: Title and data freshness.
        sections: The tabs, in order; at least one.
        plotly_javascript: The plotly.js source, inlined once. Needed only when a section has a figure.

    Raises:
        ValueError: for no sections, duplicate or malformed keys, or figures without plotly.js.
    """
    if not sections:
        raise ValueError("a report needs at least one section")
    keys = [section.key for section in sections]
    if len(set(keys)) != len(keys) or not all(re.fullmatch(r"[a-z0-9-]+", key) for key in keys):
        raise ValueError(f"section keys must be unique and use a-z, 0-9 and hyphens: {keys}")
    figures: Dict[str, Any] = {}
    switchers: Dict[str, Any] = {}
    pages: Dict[str, str] = {}
    for section in sections:
        if section.failed:
            continue                                         # nothing of a failed section is drawn
        for target, source in ((figures, section.figures), (switchers, section.switchers), (pages, section.pages)):
            duplicates = set(target) & set(source)
            if duplicates:
                raise ValueError(f"ids used twice: {sorted(duplicates)}")
            target.update(source)
    if figures and not plotly_javascript:
        raise ValueError("the report has figures but no plotly.js to draw them")
    plotly_html = f"<script>{plotly_javascript}</script>" if figures and plotly_javascript else ""
    replacements = {
        "TITLE": html.escape(f"{header.currency} market report {format_utc(header.generated_at)}"),
        "HEADER": _header_html(header),
        "TABS": _tabs_html(sections),
        "PANELS": "".join(_section_html(section, index) for index, section in enumerate(sections)),
        "PLOTLY": plotly_html,
        "DATA": _script_safe_json({"figures": figures, "switchers": switchers, "pages": pages}),
    }
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    found = {match.group(1) or "DATA" for match in _PLACEHOLDER.finditer(template)}
    if found != set(replacements):
        raise ValueError(f"the page template is missing placeholders: {sorted(set(replacements) - found)}")
    # One pass: inserted text (plotly.js, data) is never searched for placeholders again.
    return _PLACEHOLDER.sub(lambda match: replacements[match.group(1) or "DATA"], template)
