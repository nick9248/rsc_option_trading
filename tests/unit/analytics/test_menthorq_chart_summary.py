"""
Unit tests for menthorq_chart_summary on a hand-built service output (the
shape ``MenthorqReplicationService.run`` returns). Pure: no database.
"""

import pytest

from coding.core.analytics.menthorq_chart_summary import (
    CHART_ALL,
    CHART_IV_TIMES_OPEN_INTEREST,
    CHART_IV_TIMES_OPEN_INTEREST_EXPIRY,
    CHART_NET_DEX,
    CHART_NET_GEX_PANELS,
    chart_file_names,
    comparison_rows,
    data_warnings,
    expiry_choices,
    info_line,
    levels_rows,
)
from coding.core.analytics.menthorq_replication_calculator import iv_source_text


def _candidates(call_resistance, put_support, hvl):
    """compute_exposure-style level candidates with the drawn ones set."""
    return {
        "call_resistance": {"net_gex_argmax_positive": call_resistance, "call_gex_argmax": 1.0},
        "put_support": {"net_gex_argmin_negative": put_support, "put_gex_argmax": 2.0},
        "hvl": {"cumulative_net_gex_argmin": hvl, "spot_sweep_gex_argmin": 3.0},
    }


def _output(currency="BTC", iv_source_counts=None, comparison=None):
    replication = {
        "settings": {"expiry_universe": "monthly", "gamma_underlying": "index",
                     "expirations": ["27NOV26", "25DEC26"]},
        "iv_times_open_interest": {"levels": _candidates(80000.0, 75000.0, 79000.0)},
        "panels": {"absolute_net_gex": {"first_expiration": "27NOV26",
                                        "next_expiration": "25DEC26",
                                        "highest_gex": None, "second_highest_gex": None}},
        "gex_by_expiration": {
            "27NOV26": {"levels": _candidates(85000.0, 75000.0, 79000.0), "total_net_gex": -4.0},
            "25DEC26": {"levels": _candidates(None, None, None), "total_net_gex": None},
        },
        "gex_expiring_percent": {
            "27NOV26": {"net_over_absolute_net_sum": -100.0},
            "25DEC26": {"net_over_absolute_net_sum": None},
        },
    }
    all_expiries = {"gex_by_expiration": {
        "10NOV26": {"total_net_gex": None}, "27NOV26": {"total_net_gex": -4.0},
        "25DEC26": {"total_net_gex": None}}}
    return {
        "run_mode": "menthorq_daily_close",
        "currency": currency,
        "as_of_utc": "2026-11-06T23:59:00+00:00",
        "inputs": {
            "snapshot_captured_at_utc": "2026-11-06T23:48:59.536137+00:00",
            "open_interest_captured_at_utc": "2026-11-05T23:48:59+00:00",
            "index_price": 75755.64,
            "index_price_time_utc": "2026-11-06T23:58:04+00:00",
            "iv_source_counts": iv_source_counts or {"mark_iv": 940, "rebuilt_from_mark_price": 0,
                                                     "missing": 0},
        },
        "primary_configuration": "monthly__gamma_at_index",
        "charts": {"iv_times_open_interest": "iv.png", "net_gex_panels": "gex.png",
                   "net_dex": "dex.png", "net_dex_all_listed_expiries": "dex_all.png"},
        "per_expiry_iv_times_open_interest": [
            {"expiration": "10NOV26", "levels": {"call_resistance": None, "put_support": None,
                                                 "hvl": None},
             "chart": "iv_10NOV26.png", "rows_without_open_interest": 52},
            {"expiration": "27NOV26", "levels": {"call_resistance": 85000.0, "put_support": 75000.0,
                                                 "hvl": 79000.0},
             "chart": "iv_27NOV26.png", "rows_without_open_interest": 0},
        ],
        "configurations": {
            "monthly__gamma_at_index": {"replication": replication, "comparison": comparison},
            "all__gamma_at_index": {"replication": all_expiries, "comparison": None},
        },
    }


def test_chart_file_names_per_chart():
    output = _output()
    assert chart_file_names(output, CHART_IV_TIMES_OPEN_INTEREST) == ["iv.png"]
    assert chart_file_names(output, CHART_NET_GEX_PANELS) == ["gex.png"]
    assert chart_file_names(output, CHART_NET_DEX) == ["dex.png"]
    assert chart_file_names(output, CHART_IV_TIMES_OPEN_INTEREST_EXPIRY, "27NOV26") == ["iv_27NOV26.png"]
    assert chart_file_names(output, CHART_IV_TIMES_OPEN_INTEREST_EXPIRY, "1JAN30") == []
    assert chart_file_names(output, CHART_ALL) == [
        "iv.png", "gex.png", "dex.png", "iv_10NOV26.png", "iv_27NOV26.png"]
    assert expiry_choices(output) == ["10NOV26", "27NOV26"]


def test_all_expiry_levels_are_the_drawn_candidates_and_gex_percent_does_not_apply():
    (row,) = levels_rows(_output(), CHART_IV_TIMES_OPEN_INTEREST)
    assert row["scope"] == "All expiries (monthly, 2)"
    assert (row["call_resistance"], row["put_support"], row["hvl"]) == (80000.0, 75000.0, 79000.0)
    assert row["spot"] == 75755.64
    assert row["gex_expiring_percent_applies"] is False
    assert levels_rows(_output(), CHART_NET_DEX) == [row]


def test_panel_rows_keep_not_computed_as_none_never_zero():
    rows = levels_rows(_output(), CHART_NET_GEX_PANELS)
    assert [row["scope"] for row in rows] == [
        "First Expiration", "Next Expiration", "Expiration with Highest GEX",
        "Expiration with 2nd Highest GEX"]
    first, following, highest, _ = rows
    assert first["expiration"] == "27NOV26" and first["expiration_date"] == "2026-11-27"
    assert first["call_resistance"] == 85000.0 and first["gex_expiring_percent"] == -100.0
    assert following["call_resistance"] is None and following["gex_expiring_percent"] is None
    assert highest["expiration"] is None and highest["hvl"] is None
    assert all(row["gex_expiring_percent_applies"] for row in rows)


def test_single_expiry_rows_and_all_rows():
    (row,) = levels_rows(_output(), CHART_IV_TIMES_OPEN_INTEREST_EXPIRY, "10NOV26")
    assert row["call_resistance"] is None and row["scope"] == "This expiry only"
    assert len(levels_rows(_output(), CHART_ALL)) == 1 + 4 + 2


def test_info_line_names_every_input_instant():
    text = info_line(_output())
    assert "BTC MenthorQ daily close, as of 2026-11-06 23:59:00 UTC" in text
    assert "snapshot captured 2026-11-06 23:48:59 UTC" in text
    assert "index 75,755.64 from trade at 2026-11-06 23:58:04 UTC" in text
    assert "open interest from snapshot 2026-11-05 23:48:59 UTC" in text
    assert "IV source: mark_iv (all 940 rows)" in text


def test_iv_source_text_when_mixed_all_mark_iv_or_empty():
    # one shared helper (menthorq_replication_calculator) for the charts, the replay and the preview
    assert iv_source_text({"mark_iv": 1, "rebuilt_from_mark_price": 900, "missing": 3}) == (
        "mark_iv 1, rebuilt from mark_price 900, missing 3 (of 904 rows)")
    assert iv_source_text({"mark_iv": 940, "rebuilt_from_mark_price": 0, "missing": 0}) == \
        "mark_iv (all 940 rows)"
    assert iv_source_text({}) == "not computed"
    assert iv_source_text({"mark_iv": 0, "rebuilt_from_mark_price": 0, "missing": 0}) == "not computed"


def test_warnings_for_rebuilt_iv_missing_gex_open_interest_currency_and_reference_date():
    output = _output(currency="ETH", iv_source_counts={"mark_iv": 0, "rebuilt_from_mark_price": 900,
                                                       "missing": 3})
    warnings = data_warnings(output, {"timestamp_utc": "2026-10-12T23:59:00Z"})
    assert warnings[0].startswith("IV rebuilt from mark_price (Black-76) on 900 of 903 rows")
    assert warnings[1].startswith("3 rows have no IV at all")
    assert warnings[2].startswith("GEX not computed for 2 expiries (10NOV26, 25DEC26)")
    assert warnings[3].startswith("52 rows have no open interest")
    assert warnings[4].startswith("ETH: the formulas were fitted on MenthorQ's BTC charts only")
    assert warnings[5] == "The reference JSON is for 2026-10-12, the charts are for 2026-11-06"


def test_no_warnings_for_clean_btc_inputs():
    output = _output()
    output["per_expiry_iv_times_open_interest"][0]["rows_without_open_interest"] = 0
    output["configurations"]["all__gamma_at_index"]["replication"]["gex_by_expiration"] = {
        "27NOV26": {"total_net_gex": -4.0}}
    assert data_warnings(output, {"timestamp_utc": "2026-11-06T20:59:00Z"}) == []


def test_comparison_rows_use_drawn_candidates_and_hand_calculated_differences():
    comparison = {
        "iv_times_open_interest": {"levels": {
            "call_resistance": {"reference": 80000, "candidates": {
                "net_gex_argmax_positive": {"ours": 80000.0}, "call_gex_argmax": {"ours": 1.0}}},
            "hvl": {"reference": 83000, "candidates": {
                "cumulative_net_gex_argmin": {"ours": 79000.0}}},
        }},
        "gex_panels": {
            "first_expiration": {
                "our_panel_choice": {"absolute_net_gex": "27NOV26", "gross_gex": "27NOV26"},
                "call_resistance": {"candidates": {"net_gex_argmax_positive": {"ours": 85000.0}}},
                "put_support": {"candidates": {"net_gex_argmin_negative": {"ours": 75000.0}}},
                "hvl": {"candidates": {"cumulative_net_gex_argmin": {"ours": 79000.0}}},
                "gex_expiring_percent": {"candidates": {"net_over_absolute_net_sum": -40.38}},
            },
            # Reference date not in our universe: no levels on our side.
            "next_expiration": {"our_panel_choice": {"absolute_net_gex": "25DEC26"}},
        },
    }
    reference = {
        "spot_price": 75752,
        "gex_panels": {
            "first_expiration": {"expiration_date": "2026-11-27", "call_resistance": 85000,
                                 "put_support": 75000, "hvl": 79000, "gex_expiring_percent": -36.25},
            "next_expiration": {"expiration_date": "2026-12-18", "call_resistance": 80000},
        },
    }
    rows = {row["item"]: row for row in comparison_rows(_output(comparison=comparison), reference)}

    assert rows["Spot"]["difference"] == pytest.approx(75755.64 - 75752)       # +3.64
    assert rows["All expiries: Call Resistance"]["difference"] == 0.0
    assert rows["All expiries: HVL"]["difference"] == 79000.0 - 83000           # -4000
    first = "First Expiration (2026-11-27)"
    assert rows[f"{first}: expiry"]["difference"] == "same"
    assert rows[f"{first}: Put Support"]["ours"] == 75000.0
    assert rows[f"{first}: GEX Expiring %"]["difference"] == pytest.approx(-40.38 - (-36.25))  # -4.13
    following = "Next Expiration (2026-12-18)"
    assert rows[f"{following}: expiry"] == {"item": f"{following}: expiry", "ours": "2026-12-25",
                                            "reference": "2026-12-18", "difference": "different"}
    assert rows[f"{following}: Call Resistance"]["ours"] is None
    assert rows[f"{following}: Call Resistance"]["difference"] is None
    assert rows[f"{following}: Put Support"]["reference"] is None


def test_no_reference_means_no_comparison_rows():
    assert comparison_rows(_output(), None) == []
