"""Tests of the on-chain dashboard page: built from a real typed result (recorded fixture) and from hand-built results."""

import dataclasses
import json
import re
from datetime import datetime, timezone

import pytest

from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
from coding.core.analytics.onchain_dashboard.expiry_section import CHARTS
from coding.core.analytics.onchain_dashboard.formatting import ratio_text, strike_text
from coding.core.analytics.report.report_page import ReportHeader, ReportSection, build_report_page
from tests.unit.onchain_dashboard.conftest import FAKE_PLOTLY

TABS = ["overview", "expiries", "market-wide", "data-quality"]
HOSTILE = "</script><img src=x onerror=alert(1)>"


def _data_block(page: str) -> dict:
    """The JSON data block of the page, parsed strictly (NaN / Infinity are errors)."""
    text = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.S).group(1)

    def refuse(constant):
        raise AssertionError(f"{constant} in the page data")
    return json.loads(text, parse_constant=refuse)


def _visible_text(page: str) -> str:
    return re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.S))


@pytest.fixture(scope="module")
def real_page(real_result):
    return build_onchain_dashboard_page(real_result, FAKE_PLOTLY)


class TestRealResult:
    def test_page_has_every_tab_in_order(self, real_page):
        assert re.findall(r'role="tab" id="tab-([a-z-]+)"', real_page) == TABS

    def test_header_says_on_chain_dashboard(self, real_page):
        assert "<title>BTC on-chain dashboard 2026-07-25 18:34 UTC</title>" in real_page
        assert 'BTC</span> on-chain dashboard</h1>' in real_page
        assert "market report" not in real_page

    def test_key_numbers_from_the_result_are_shown(self, real_result, real_page):
        text = _visible_text(real_page)
        assert f"{real_result.underlying_price:,.2f} USD" in text
        first = sorted(real_result.expirations, key=lambda bundle: bundle.expiration)[0]
        assert strike_text(first.analysis.max_pain.max_pain_strike) in text
        call_wall = next(b.gex_dex.key_levels.call_resistance.strike for b in real_result.expirations
                         if b.gex_dex is not None and b.gex_dex.key_levels.call_resistance is not None)
        assert f"{call_wall:,.0f}" in text
        assert f"{real_result.market_metrics.dvol:,.2f}" in text

    def test_expiry_views_exist_for_every_expiry_and_chart(self, real_result, real_page):
        data = _data_block(real_page)
        views = data["switchers"]["expiries-main"]["views"]
        assert len(views) == len(real_result.expirations) * len(CHARTS)
        assert all(view["figure"] in data["figures"] for sw in data["switchers"].values() for view in sw["views"].values())

    def test_expiries_are_in_date_order(self, real_page):
        options = re.findall(r'<option value="([^"]+)"', real_page)
        assert options[0] == "26JUL26"
        assert options.index("7AUG26") < options.index("14AUG26") < options.index("25JUN27")

    def test_missing_pieces_read_not_computed(self, real_page):
        text = _visible_text(real_page)
        assert "not computed" in text            # the recorded fixture has no delta-flow rows and thin history
        assert "Delta flow not computed" in text

    def test_page_has_no_nan_or_infinity(self, real_page):
        _data_block(real_page)                   # strict parse
        assert "NaN" not in real_page and "Infinity" not in real_page

    def test_missing_values_are_not_shown_as_none(self, real_page):
        assert not re.search(r"\bNone\b", _visible_text(real_page))


class TestHandBuiltResults:
    def test_empty_result_builds_all_tabs_with_not_computed(self, empty_result):
        page = build_onchain_dashboard_page(empty_result, FAKE_PLOTLY)
        assert re.findall(r'role="tab" id="tab-([a-z-]+)"', page) == TABS
        text = _visible_text(page)
        assert "No expiry was analyzed" in text
        assert text.count("not computed") >= 10
        assert "NaN" not in page and "Infinity" not in page and not re.search(r"\bNone\b", text)
        _data_block(page)

    def test_naive_generated_at_is_read_as_utc(self, empty_result):
        naive = dataclasses.replace(empty_result, generated_at=datetime(2026, 1, 1))
        assert "2026-01-01 00:00 UTC" in build_onchain_dashboard_page(naive, FAKE_PLOTLY)

    def test_plotly_javascript_is_required(self, empty_result):
        with pytest.raises(ValueError):
            build_onchain_dashboard_page(empty_result, None)

    def test_bundle_with_every_optional_part_missing(self, real_result):
        bare = dataclasses.replace(real_result.expirations[0], gex_dex=None, flow=None, vol_surface=None, oi_changes=None,
                                   iv_percentile=None, trend=None, dealer_inventory=None, exposure_profile=None,
                                   fixed_strike_vol=None)
        result = dataclasses.replace(real_result, expirations=(bare,), normalized_metrics={}, normalized_metrics_front_month=None)
        page = build_onchain_dashboard_page(result, FAKE_PLOTLY)
        text = _visible_text(page)
        data = _data_block(page)
        gex_figure = data["figures"][f"expiry-{bare.expiration}-gex"]
        assert gex_figure["data"] == [] and "Net GEX not computed" in json.dumps(gex_figure["layout"])
        caption = data["switchers"]["expiries-main"]["views"][f"{bare.expiration}|gex"]["caption"]
        assert "no Greeks result" in caption and "not computed" in caption
        assert "Historical context not computed" in text

    def test_infinite_ratios_are_text_never_infinity(self, real_result):
        bundle = real_result.expirations[0]
        analysis = dataclasses.replace(
            bundle.analysis,
            put_call_ratio=dataclasses.replace(bundle.analysis.put_call_ratio, ratio=float("inf")),
            volume_stats=dataclasses.replace(bundle.analysis.volume_stats, volume_ratio=float("inf")))
        result = dataclasses.replace(real_result, expirations=(dataclasses.replace(bundle, analysis=analysis),))
        page = build_onchain_dashboard_page(result, FAKE_PLOTLY)
        assert "no calls" in page and "Infinity" not in page
        assert ratio_text(float("inf")) == "no calls"
        _data_block(page)

    def test_nan_values_become_gaps_in_figures(self, real_result):
        bundle = real_result.expirations[0]
        rows = ((dataclasses.replace(bundle.analysis.strike_rows[0], call_oi=float("nan"), put_volume=float("inf")),)
                + bundle.analysis.strike_rows[1:])
        result = dataclasses.replace(real_result, expirations=(dataclasses.replace(
            bundle, analysis=dataclasses.replace(bundle.analysis, strike_rows=rows)),))
        page = build_onchain_dashboard_page(result, FAKE_PLOTLY)
        data = _data_block(page)
        figure = data["figures"][f"expiry-{bundle.expiration}-open-interest"]
        assert figure["data"][0]["y"][0] is None
        assert "NaN" not in page and "Infinity" not in page

    def test_stale_flags_are_shown_as_warnings(self, real_result):
        stale = dataclasses.replace(real_result, normalized_metrics_stale_since=datetime(2026, 7, 25, 10, 0),
                                    delta_flow_stale_since=datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc))
        text = _visible_text(build_onchain_dashboard_page(stale, FAKE_PLOTLY))
        assert "historical context is stale" in text.lower() and "2026-07-25 10:00 UTC" in text
        assert "delta flow is stale" in text.lower() and "2026-07-25 11:00 UTC" in text

    def test_failed_sections_are_listed(self, real_result):
        market = dataclasses.replace(real_result.market_wide, failed_sections=("forward_vol",), forward_vol=None)
        page = build_onchain_dashboard_page(dataclasses.replace(real_result, market_wide=market), FAKE_PLOTLY)
        assert "forward_vol" in _visible_text(page)
        assert "Forward vol not computed" in _visible_text(page)


class TestEscaping:
    def test_hostile_text_does_not_break_out(self, real_result):
        bundle = real_result.expirations[0]
        inventory = dataclasses.replace(bundle.dealer_inventory, unavailable_reason=HOSTILE, render_inferred=False)
        hostile = dataclasses.replace(bundle, expiration=HOSTILE, dealer_inventory=inventory,
                                      analysis=dataclasses.replace(bundle.analysis, expiration=HOSTILE))
        market = dataclasses.replace(real_result.market_wide, failed_sections=(HOSTILE,))
        result = dataclasses.replace(real_result, expirations=(hostile,), market_wide=market,
                                     normalized_metrics_front_month=HOSTILE)
        page = build_onchain_dashboard_page(result, FAKE_PLOTLY)
        assert HOSTILE not in page
        markup = re.sub(r"<script.*?</script>", "", page, flags=re.S)       # a tag inside a script element is inert text
        assert "<img src=x" not in markup and "&lt;img src=x" in markup
        data = _data_block(page)        # the data block still parses: the hostile text did not end its script element
        captions = [view["caption"] for sw in data["switchers"].values() for view in sw["views"].values()]
        assert any("&lt;img src=x" in caption for caption in captions)
        assert not any("<img" in caption or "<script" in caption for caption in captions)


class TestSharedInfrastructure:
    def test_market_report_header_is_unchanged(self):
        header = ReportHeader("BTC", datetime(2026, 1, 1, tzinfo=timezone.utc))
        page = build_report_page(header, [ReportSection(key="one", title="One")], None)
        assert "market report" in page and "on-chain dashboard" not in page

    def test_report_sections_still_exports_figure_json(self):
        from coding.core.analytics.report.figure_style import figure_json
        from coding.service.report.report_sections import figure_json as old_name
        assert old_name is figure_json
