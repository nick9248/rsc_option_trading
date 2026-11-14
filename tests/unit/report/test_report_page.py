"""Tests of the report page assembly (pure: no database, no network)."""

import base64
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.report.report_page import (
    ReportHeader, ReportSection, SectionFailure, SwitcherControl, build_report_page, embeddable_page, format_utc,
    image_fragment, png_data_uri, sentence, switcher_configuration, switcher_fragment, table_fragment)

NOW = datetime(2026, 11, 14, 10, 29, tzinfo=timezone.utc)
SCRIPT_ATTACK = "<script>alert(1)</script>"
PLOTLY_MARKER = "/*PLOTLY-MARKER*/"


def make_header(facts=()):
    return ReportHeader("BTC", NOW, list(facts))


def make_figure(text="chart"):
    return {"data": [{"type": "bar", "x": [1, 2]}], "layout": {"title": {"text": text}}}


def make_section(key="one", title="One", **kwargs):
    return ReportSection(key=key, title=title, **kwargs)


def data_block(page):
    match = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_page_starts_with_doctype_and_shows_header_facts_and_generated_time_in_utc():
    page = build_report_page(make_header([("Newest stored trade", "2026-11-14 10:00 UTC")]), [make_section()], None)
    assert page.startswith("<!doctype html>")
    assert "<dt>Generated</dt><dd>2026-11-14 10:29 UTC</dd>" in page
    assert "<dt>Newest stored trade</dt><dd>2026-11-14 10:00 UTC</dd>" in page


def test_every_section_is_a_tab_with_matching_panel_ids():
    sections = [make_section("alpha", "Alpha"), make_section("beta-2", "Beta two")]
    page = build_report_page(make_header(), sections, None)
    assert len(re.findall(r'<button[^>]* role="tab"', page)) == 2
    for key, title in (("alpha", "Alpha"), ("beta-2", "Beta two")):
        assert f'id="tab-{key}"' in page and f'aria-controls="panel-{key}"' in page
        assert f'id="panel-{key}" aria-labelledby="tab-{key}"' in page
        assert f"{title}</button>" in page


def test_only_the_first_panel_is_visible_at_the_start():
    sections = [make_section("alpha", "Alpha"), make_section("beta", "Beta"), make_section("gamma", "Gamma")]
    page = build_report_page(make_header(), sections, None)
    panels = re.findall(r'<section class="panel"[^>]*>', page)
    assert len(panels) == 3
    assert [" hidden" in tag for tag in panels] == [False, True, True]
    assert 'id="tab-alpha" data-key="alpha" aria-controls="panel-alpha" aria-selected="true"' in page


def test_text_with_script_tags_is_escaped_everywhere():
    section = make_section(title=SCRIPT_ATTACK, description=SCRIPT_ATTACK, as_of=SCRIPT_ATTACK,
                           notes=[SCRIPT_ATTACK], warnings=[SCRIPT_ATTACK])
    page = build_report_page(make_header([(SCRIPT_ATTACK, SCRIPT_ATTACK)]), [section], None)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)" not in page


def test_failed_section_text_is_escaped():
    failure = SectionFailure(SCRIPT_ATTACK, SCRIPT_ATTACK)
    page = build_report_page(make_header(), [make_section(failure=failure)], None)
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


def test_data_block_cannot_be_closed_early_and_parses_back_to_the_original_text():
    nasty = "evil </script><!-- text"
    section = make_section(figures={"f1": make_figure(nasty)}, pages={"p1": f"<html>{nasty}</html>"})
    page = build_report_page(make_header(), [section], PLOTLY_MARKER)
    # two scripts of the template (data block, page script) plus plotly
    assert page.count("</script>") == 3
    assert "<!--" not in data_block(page)
    data = json.loads(data_block(page))
    assert data["figures"]["f1"]["layout"]["title"]["text"] == nasty
    assert data["pages"]["p1"] == f"<html>{nasty}</html>"


def test_plotly_is_inlined_once_when_two_sections_have_figures():
    sections = [make_section("one", figures={"f1": make_figure()}), make_section("two", figures={"f2": make_figure()})]
    page = build_report_page(make_header(), sections, PLOTLY_MARKER)
    assert page.count(PLOTLY_MARKER) == 1


def test_plotly_is_not_inlined_without_figures():
    page = build_report_page(make_header(), [make_section(body_html="<p>text</p>")], PLOTLY_MARKER)
    assert PLOTLY_MARKER not in page


def test_figures_without_plotly_javascript_are_refused():
    with pytest.raises(ValueError, match="plotly"):
        build_report_page(make_header(), [make_section(figures={"f1": make_figure()})], None)


def test_placeholder_text_inside_plotly_or_data_is_not_expanded():
    section = make_section(figures={"f1": make_figure("<!--__PANELS__-->")}, body_html="<p>body-marker</p>")
    page = build_report_page(make_header(), [section], "var x = '<!--__PANELS__-->'; /*__DATA__*/null")
    assert page.count("body-marker") == 1
    assert "var x = '<!--__PANELS__-->'; /*__DATA__*/null" in page
    assert json.loads(data_block(page))["figures"]["f1"]["layout"]["title"]["text"] == "<!--__PANELS__-->"


def test_failed_section_explains_itself_and_its_content_is_not_in_the_data_block():
    failed = make_section("broken", "Broken", failure=SectionFailure("Broken: no snapshot.", "Sync the database."),
                          body_html="<p>HIDDEN-BODY</p>", figures={"f-broken": make_figure()},
                          switchers={"s-broken": {"views": {}}}, pages={"p-broken": "<html></html>"})
    page = build_report_page(make_header(), [make_section("fine", "Fine"), failed], None)
    assert "This section could not be built" in page
    assert "Broken: no snapshot." in page and "Sync the database." in page
    assert '<span class="flag">not built</span>' in page
    assert "HIDDEN-BODY" not in page
    data = json.loads(data_block(page))
    assert data == {"figures": {}, "switchers": {}, "pages": {}}
    assert "PLOTLY" not in page


def test_failed_sections_figures_do_not_cause_plotly_to_be_required():
    failed = make_section(failure=SectionFailure("what", "fix"), figures={"f1": make_figure()})
    page = build_report_page(make_header(), [failed], None)
    assert "report-data" in page


def test_no_sections_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        build_report_page(make_header(), [], None)


def test_duplicate_section_keys_are_refused():
    with pytest.raises(ValueError, match="unique"):
        build_report_page(make_header(), [make_section("same"), make_section("same", "Other")], None)


@pytest.mark.parametrize("key", ["Upper", "has space", "under_score", ""])
def test_malformed_section_key_is_refused(key):
    with pytest.raises(ValueError, match="unique"):
        build_report_page(make_header(), [make_section(key)], None)


def test_duplicate_figure_ids_across_sections_are_refused():
    sections = [make_section("one", figures={"same": make_figure()}), make_section("two", figures={"same": make_figure()})]
    with pytest.raises(ValueError, match="twice"):
        build_report_page(make_header(), sections, PLOTLY_MARKER)


def test_warnings_and_notes_start_with_a_capital_letter():
    section = make_section(warnings=["no stored trades"], notes=["window is fixed"])
    page = build_report_page(make_header(), [section], None)
    assert "<li>No stored trades</li>" in page
    assert "<p>Window is fixed</p>" in page


def test_sentence_capitalises_only_the_first_letter_and_accepts_empty_text():
    assert sentence("iV rebuilt") == "IV rebuilt"
    assert sentence("") == ""


def test_format_utc_refuses_a_naive_time():
    with pytest.raises(ValueError, match="timezone"):
        format_utc(datetime(2026, 11, 14, 10, 29))


def test_format_utc_of_none_is_the_missing_text():
    assert format_utc(None) == "not known"
    assert format_utc(None, missing="none stored") == "none stored"


def test_format_utc_converts_another_zone_to_utc():
    plus_two = timezone(timedelta(hours=2))
    assert format_utc(datetime(2026, 11, 14, 12, 29, tzinfo=plus_two)) == "2026-11-14 10:29 UTC"


def test_switcher_with_four_choices_uses_buttons_and_marks_the_selected_one():
    control = SwitcherControl("unit", "Unit", [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")], "b")
    fragment = switcher_fragment("sw", [control])
    assert fragment.count("<button") == 4 and "<select" not in fragment
    assert 'data-value="b" aria-pressed="true"' in fragment
    assert fragment.count('aria-pressed="false"') == 3


def test_switcher_with_five_choices_uses_a_list_with_the_selected_option():
    control = SwitcherControl("expiry", "Expiry", [(str(index), f"E{index}") for index in range(5)], "3")
    fragment = switcher_fragment("sw", [control])
    assert "<select" in fragment and "<button" not in fragment
    assert '<option value="3" selected>E3</option>' in fragment
    assert fragment.count(" selected") == 1


def test_switcher_configuration_holds_controls_selection_and_views():
    controls = [SwitcherControl("a", "A", [("x", "X")], "x"), SwitcherControl("b", "B", [("y", "Y")], "y")]
    views = {"x|y": {"figure": "f1"}}
    assert switcher_configuration(controls, views) == {
        "controls": ["a", "b"], "selected": {"a": "x", "b": "y"}, "views": views}


def test_switcher_configuration_refuses_a_first_selection_without_a_view():
    controls = [SwitcherControl("a", "A", [("x", "X")], "x")]
    with pytest.raises(ValueError, match="first selection"):
        switcher_configuration(controls, {"other": {}})


def test_png_data_uri_round_trips_the_bytes():
    payload = bytes(range(256))
    uri = png_data_uri(payload)
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == payload


def test_image_fragment_embeds_the_picture_and_escapes_alt_and_caption():
    fragment = image_fragment(b"\x89PNG fake", 'alt "<b>"', "caption <i>")
    assert png_data_uri(b"\x89PNG fake") in fragment
    assert 'alt="alt &quot;&lt;b&gt;&quot;"' in fragment and "<figcaption>caption &lt;i&gt;</figcaption>" in fragment


def test_embeddable_page_inserts_the_override_style_before_head_end_once():
    result = embeddable_page("<html><head></head><body></body></html>")
    assert result.count("<style>main {") == 1
    assert result.index("<style>main {") < result.index("</head>")
    assert embeddable_page("<head></head><head></head>").count("<style>main {") == 1


def test_embeddable_page_without_head_puts_the_style_in_front():
    assert embeddable_page("<p>x</p>").endswith("<p>x</p>") and embeddable_page("<p>x</p>").startswith("<style>")


def test_table_fragment_escapes_cells_and_marks_numeric_columns():
    fragment = table_fragment(["", "Value"], [["<b>row</b>", "1,000"]], numeric_columns=(1,))
    assert "&lt;b&gt;row&lt;/b&gt;" in fragment and "<b>row</b>" not in fragment
    assert '<td class="num">1,000</td>' in fragment and '<th scope="col" class="num">Value</th>' in fragment
    assert '<th scope="row">' in fragment
