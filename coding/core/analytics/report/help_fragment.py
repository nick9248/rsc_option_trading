"""
Renders the "How to read this" help (``how_to_read.SectionHelp``) as a native, keyboard-usable disclosure panel
(``<details>``, closed by default). Pure; every text is escaped.
"""

import html

from coding.core.analytics.report.how_to_read import HelpTopic, SectionHelp


def _topic_html(topic: HelpTopic) -> str:
    """One topic: title, what you see, terms, the cases with their examples, and the limits."""
    parts = [f"<h4>{html.escape(topic.title)}</h4>",
             f'<p class="help-what">{html.escape(topic.what_you_see)}</p>']
    if topic.terms:
        parts.append('<dl class="help-terms">' + "".join(
            f"<div><dt>{html.escape(term.term)}</dt><dd>{html.escape(term.meaning)}</dd></div>" for term in topic.terms)
            + "</dl>")
    if topic.cases:
        rows = "".join(
            f'<tr><th scope="row">{html.escape(case.situation)}</th><td>{html.escape(case.reading)}</td>'
            f'<td class="help-example">{html.escape(case.example)}</td></tr>' for case in topic.cases)
        parts.append('<div class="help-cases"><table><thead><tr><th scope="col">Situation</th>'
                     '<th scope="col">How to read it</th><th scope="col">Example</th></tr></thead>'
                     f"<tbody>{rows}</tbody></table></div>")
    if topic.limits:
        parts.append('<p class="help-limits-title">What it cannot tell</p><ul class="help-limits">'
                     + "".join(f"<li>{html.escape(limit)}</li>" for limit in topic.limits) + "</ul>")
    return '<div class="help-topic">' + "".join(parts) + "</div>"


def help_fragment(section_help: SectionHelp, label: str = "How to read this tab") -> str:
    """The closed disclosure panel with the whole help of one tab (or of the report)."""
    topics = "".join(_topic_html(topic) for topic in section_help.topics)
    return (f'<details class="how-to-read"><summary>{html.escape(label)}</summary>'
            f'<div class="help-body"><p class="help-summary">{html.escape(section_help.summary)}</p>{topics}</div></details>')
