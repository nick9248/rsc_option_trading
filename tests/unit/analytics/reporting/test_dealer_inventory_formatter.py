"""
Unit tests for coding.core.analytics.reporting.dealer_inventory_formatter
(institutional_metrics_spec.md section 2 / task C3 -- report display).

D9 (BINDING): never render the inferred view for a gate-failed expiry, never
blend it with the assumed view. T2.2's report-level assertion is reproduced
here: the unavailable case's text must contain
"INFERRED DEALER VIEW UNAVAILABLE" and must NOT contain any inferred GEX
number.
"""

from datetime import datetime, timezone

from coding.core.analytics.reporting.dealer_inventory_formatter import (
    format_dealer_inventory_section,
)
from coding.core.analytics.results.dealer_inventory_results import (
    DealerInventoryKeyLevels,
    DealerInventoryLevel,
    DealerInventoryResult,
    DealerInventoryStrikeRow,
)
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
)

_T0_MS = int(datetime(2026, 4, 25, tzinfo=timezone.utc).timestamp() * 1000)


def _gex_dex_result(**overrides) -> GexDexResult:
    defaults = dict(
        strike_rows=(
            GexDexStrikeRow(
                strike=70000.0, call_gamma=1.0, put_gamma=0.5, call_delta=0.6, put_delta=-0.4,
                call_oi=500.0, put_oi=300.0, net_gex=163840.0, net_dex=0.2,
                net_gamma=0.5, cumulative_gex=163840.0, cumulative_dex=0.2,
            ),
        ),
        cumulative_gex={70000.0: 163840.0},
        cumulative_dex={70000.0: 0.2},
        key_levels=GexDexKeyLevels(
            call_resistance=GexDexLevel(strike=70000.0, net_gex=163840.0),
            put_support=GexDexLevel(strike=60000.0, net_gex=-50000.0),
            hvl=65000.0,
            gamma_flip=65000.0,
        ),
        spot_price=64000.0,
        total_net_gex=163840.0,
        total_net_dex=0.2,
        currency="BTC",
    )
    defaults.update(overrides)
    return GexDexResult(**defaults)


def _dealer_result(render_inferred, **overrides):
    defaults = dict(
        strike_rows=(
            DealerInventoryStrikeRow(
                strike=70000.0, dealer_net_c=100.0, dealer_net_p=100.0,
                inferred_gex=-81920.0, inferred_dex=40.0,
            ),
        ),
        key_levels=DealerInventoryKeyLevels(
            call_resistance=None,
            put_support=DealerInventoryLevel(strike=70000.0, inferred_gex=-81920.0),
            hvl=None,
        ),
        total_inferred_gex=-81920.0,
        total_inferred_dex=40.0,
        spot_price=64000.0,
        currency="BTC",
        t0_epoch_ms=_T0_MS,
        coverage=0.9995,
        violation_rate=0.027,
        n_signed_trades=1842113,
        render_inferred=render_inferred,
        unavailable_reason=None if render_inferred else "coverage 87.0%, violations 8.7%",
    )
    defaults.update(overrides)
    return DealerInventoryResult(**defaults)


class TestGateFailedRendersUnavailable:
    def test_contains_unavailable_marker_and_reason(self):
        dealer_result = _dealer_result(render_inferred=False)
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "INFERRED DEALER VIEW UNAVAILABLE" in text
        assert "coverage 87.0%, violations 8.7%" in text

    def test_does_not_contain_any_inferred_gex_number(self):
        """T2.2's report-level assertion: no inferred GEX number leaks into
        text when the gate fails, even though calculate() computed one."""
        dealer_result = _dealer_result(render_inferred=False, total_inferred_gex=-81920.0)
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "-81920" not in text
        assert "81,920" not in text

    def test_mentions_fallback_to_assumed_view(self):
        dealer_result = _dealer_result(render_inferred=False)
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")
        assert "ASSUMED" in text


class TestGatePassedRendersBothViews:
    def test_contains_both_labels(self):
        dealer_result = _dealer_result(render_inferred=True)
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "ASSUMED" in text
        assert "INFERRED" in text

    def test_contains_assumed_and_inferred_gex_totals(self):
        dealer_result = _dealer_result(render_inferred=True, total_inferred_gex=-81920.0)
        gex_dex_result = _gex_dex_result()  # total_net_gex = 163840.0

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "163,840" in text
        assert "81,920" in text

    def test_contains_signed_trade_count_and_coverage_violation(self):
        dealer_result = _dealer_result(
            render_inferred=True, n_signed_trades=1842113, coverage=0.9995, violation_rate=0.027,
        )
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "1,842,113" in text
        assert "99.95" in text
        assert "2.7" in text

    def test_opposite_sign_from_assumed_view_both_present(self):
        """spec T2.1: same strike, opposite sign between the two views --
        both numbers must appear, neither overwrites the other."""
        dealer_result = _dealer_result(render_inferred=True, total_inferred_gex=-81920.0)
        gex_dex_result = _gex_dex_result(total_net_gex=163840.0)

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")
        assert "+163,840" in text or "163,840" in text
        assert "-81,920" in text

    def test_missing_gex_dex_result_does_not_crash(self):
        """Defensive: gex_dex_result is None (shouldn't happen in
        production wiring, but must not crash report generation)."""
        dealer_result = _dealer_result(render_inferred=True)
        text = format_dealer_inventory_section(dealer_result, None, "BTC")
        assert "DEALER POSITIONING" in text


class TestDataCompletenessDisclosure:
    """
    Task Wave-H-E: mirrors gex_dex_formatter.py's "DATA COMPLETENESS" line
    for GexDexResult.instruments_missing_gamma (Task G2-A) -- same
    disclosure convention, this calculator's own result.
    """

    def test_missing_gamma_renders_completeness_line(self):
        dealer_result = _dealer_result(
            render_inferred=True, instruments_missing_gamma=2, oi_missing_gamma=750.0,
        )
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "DATA COMPLETENESS" in text
        assert "2 leg(s)" in text
        assert "750.00 OI" in text
        assert "missing gamma/delta" in text

    def test_no_missing_gamma_omits_completeness_line(self):
        dealer_result = _dealer_result(
            render_inferred=True, instruments_missing_gamma=0, oi_missing_gamma=0.0,
        )
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "DATA COMPLETENESS" not in text

    def test_gate_failed_branch_never_shows_completeness_line(self):
        """D9: the gate-failed branch prints nothing but the UNAVAILABLE
        marker and the fallback note -- no numeric or diagnostic content,
        even a nonzero completeness gap, leaks into that branch's text."""
        dealer_result = _dealer_result(
            render_inferred=False, instruments_missing_gamma=3, oi_missing_gamma=999.0,
        )
        gex_dex_result = _gex_dex_result()

        text = format_dealer_inventory_section(dealer_result, gex_dex_result, "BTC")

        assert "DATA COMPLETENESS" not in text
        assert "INFERRED DEALER VIEW UNAVAILABLE" in text
