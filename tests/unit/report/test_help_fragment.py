"""Tests of the "How to read this" panel (``help_fragment``), its place in the report page and in ``ReportService``.

No database, no network: the service test uses fakes like ``test_report_service.py``.
"""

import html
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.report.help_fragment import help_fragment
from coding.core.analytics.report.how_to_read import (
    REPORT_HELP, SECTION_HELP, HelpCase, HelpTerm, HelpTopic, SectionHelp)
from coding.core.analytics.report.report_page import ReportHeader, ReportSection, SectionFailure, build_report_page
from coding.service.report.report_service import ReportService

NOW = datetime(2026, 11, 14, 10, 29, 5, tzinfo=timezone.utc)
PANEL_OPENING = '<details class="how-to-read"><summary>'
REPORT_LABEL_TAG = "<summary>How to read this report</summary>"
TAB_LABEL_TAG = "<summary>How to read this tab</summary>"


def small_help() -> SectionHelp:
    """A hand-made help with every kind of text, including markup that must be escaped."""
    topic = HelpTopic(
        title="Topic <b>one</b> & \"two\"",
        what_you_see="What you see: a bar & a line.",
        terms=(HelpTerm("Term alpha", "Meaning alpha."), HelpTerm("Term \"beta\"", "Meaning beta.")),
        cases=(HelpCase("Situation gamma", "Reading gamma.", "Example gamma 1 & 2."),),
        limits=("Limit delta.", "Limit <epsilon>."),
    )
    return SectionHelp(section_key="small", summary="Summary of the small help.", topics=(topic,))


def panel_for(page: str, key: str) -> str:
    """The whole ``<section>`` element of one tab."""
    start = page.index(f'id="panel-{key}"')
    return page[page.rfind("<section", 0, start):page.index("</section>", start)]


# ---------------------------------------------------------------------------
# 1. The fragment itself
# ---------------------------------------------------------------------------

def test_panel_is_a_closed_details_element_with_the_label():
    output = help_fragment(small_help(), "How to read this tab")
    assert output.startswith('<details class="how-to-read">')
    opening_tag = output[: output.index(">") + 1]
    assert " open" not in opening_tag
    assert "<summary>How to read this tab</summary>" in output


def test_default_label_is_how_to_read_this_tab():
    assert "<summary>How to read this tab</summary>" in help_fragment(small_help())


def test_every_text_of_the_small_help_appears_escaped():
    help_text = small_help()
    output = help_fragment(help_text, "Label")
    topic = help_text.topics[0]
    expected = [help_text.summary, topic.title, topic.what_you_see, *(t for term in topic.terms for t in (term.term, term.meaning)),
                *(text for case in topic.cases for text in (case.situation, case.reading, case.example)), *topic.limits]
    for text in expected:
        assert html.escape(text) in output, text


def test_markup_and_quotes_in_the_help_are_escaped():
    hostile = SectionHelp(
        section_key="hostile",
        summary="<script>alert(\"summary\")</script>",
        topics=(HelpTopic(title="<script>alert(\"t\")</script>", what_you_see="\"quoted\" <img src=x>",
                          terms=(), cases=(), limits=("<script>alert('limit')</script>",)),),
    )
    output = help_fragment(hostile, "Label")
    assert "<script>" not in output
    assert "&lt;script&gt;alert(&quot;summary&quot;)&lt;/script&gt;" in output
    assert "&quot;quoted&quot;" in output


# ---------------------------------------------------------------------------
# 2. Every real help renders a non-empty panel with every topic title
# ---------------------------------------------------------------------------

HELP_CASES = [pytest.param(REPORT_HELP, id="report"), *(pytest.param(value, id=key) for key, value in SECTION_HELP.items())]


@pytest.mark.parametrize("section_help", HELP_CASES)
def test_real_help_renders_a_non_empty_panel_with_every_topic_title(section_help):
    output = help_fragment(section_help)
    assert output.startswith(PANEL_OPENING) and output.endswith("</details>")
    assert section_help.topics, "every help has at least one topic"
    for topic in section_help.topics:
        assert html.escape(topic.title) in output, topic.title


# ---------------------------------------------------------------------------
# 3. Placement in the report page
# ---------------------------------------------------------------------------

def test_section_panels_carry_their_own_help_before_the_description(tmp_path):
    sections = [
        ReportSection(key="menthorq", title="MenthorQ", description="The MenthorQ description.",
                      body_html="<p>menthorq body</p>", help=SECTION_HELP["menthorq"]),
        ReportSection(key="levels", title="Levels", description="The levels description.",
                      failure=SectionFailure("Nothing stored.", "Sync and try again."), help=SECTION_HELP["levels"]),
    ]
    page = build_report_page(ReportHeader("BTC", NOW, help=REPORT_HELP), sections, None)

    for key, section in (("menthorq", sections[0]), ("levels", sections[1])):
        panel = panel_for(page, key)
        assert panel.count(PANEL_OPENING) == 1
        assert html.escape(section.help.topics[0].title) in panel
        assert panel.index(PANEL_OPENING) < panel.index('<p class="description">')
        assert html.escape(section.description) in panel

    assert "<p>menthorq body</p>" in panel_for(page, "menthorq")
    levels = panel_for(page, "levels")
    assert "This section could not be built" in levels
    assert html.escape(SECTION_HELP["levels"].topics[0].title) in levels
    assert levels.index(PANEL_OPENING) < levels.index("This section could not be built")


def test_header_help_appears_once_when_set_and_not_at_all_when_none():
    with_help = build_report_page(ReportHeader("BTC", NOW, help=REPORT_HELP), [ReportSection(key="one", title="One")], None)
    assert with_help.count(REPORT_LABEL_TAG) == 1
    assert with_help.count(PANEL_OPENING) == 1

    without_help = build_report_page(ReportHeader("BTC", NOW, help=None), [ReportSection(key="one", title="One")], None)
    assert REPORT_LABEL_TAG not in without_help
    assert "How to read this report" not in without_help
    assert PANEL_OPENING not in without_help


# ---------------------------------------------------------------------------
# 4. ReportService.generate attaches the help and writes it into the file
# ---------------------------------------------------------------------------

class FakeRepository:
    def get_latest_trade_time(self, currency):
        return NOW - timedelta(minutes=3)

    def get_latest_option_chain_capture_at_or_before(self, currency, instant):
        return NOW - timedelta(minutes=9)


class SynchronizerMustNotRun:
    def sync_if_behind(self, table_names=(), progress_callback=None, should_stop=None):
        raise AssertionError("skip_sync=True must not contact the synchronizer")


def test_generate_writes_the_report_help_once_and_the_tab_help_for_each_tab_with_help(tmp_path):
    def menthorq_builder(context):
        return ReportSection(key="menthorq", title="MenthorQ", body_html="<p>menthorq body</p>")

    def levels_builder(context):
        raise LookupError("No BTC option-chain snapshot is stored")

    service = ReportService(
        repository_factory=FakeRepository, synchronizer=SynchronizerMustNotRun(), output_directory=tmp_path,
        clock=lambda: NOW, builders=[("menthorq", "MenthorQ", menthorq_builder), ("levels", "Levels", levels_builder)],
        plotly_javascript=lambda: "/*js*/")

    result = service.generate("BTC", skip_sync=True)
    page = result.path.read_text(encoding="utf-8")

    assert [outcome.built for outcome in result.sections] == [True, False]
    assert page.count(REPORT_LABEL_TAG) == 1
    assert page.count(TAB_LABEL_TAG) == 2
    assert "This section could not be built" in page
    assert html.escape(SECTION_HELP["levels"].topics[0].title) in page
