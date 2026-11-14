"""The Dealer levels caption: what failed, the table of failing options, the gamma share and the reliability verdict."""

from datetime import datetime, timezone

from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, StrikeLevelRow, ViolatingOption
from coding.core.analytics.report.section_fragments import check_sentence, dealer_levels_caption

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
UNRELIABLE = "Treat the flow-based view of this expiry as unreliable; use the open-interest based view."


def make_levels(gate_reason, share_rows=((1.0, 30.0), (2.0, 70.0)), violating=(), passed=False, violations=10,
                options_checked=92, coverage=1.0):
    rows = [StrikeLevelRow(strike, 0.0, 0.0, gex, None, None, None) for strike, gex in share_rows]
    return DealerFlowLevels(
        "BTC", "27NOV26", 80_000.0, NOW, NOW, 5, rows, None, None, None, None, None, None, 0.0, None, coverage,
        violations / options_checked, violations, options_checked, 0, passed, gate_reason,
        violating_options=list(violating))


def test_sentence_of_a_violation_failure_names_counts_rate_and_limit():
    levels = make_levels("10.9% of options exceed their open interest (limit 5%)")
    assert check_sentence(levels) == ("Check failed: 10 of 92 options (10.9 %) show a dealer position larger than their "
                                      "open interest; the limit is 5 %.")


def test_sentence_of_a_coverage_failure_and_of_several_reasons():
    assert check_sentence(make_levels("trade coverage 82.7% is below 95%")) == (
        "Check failed: trade coverage 82.7 % is below 95 %: hours without stored trades, so the dealer position is incomplete.")
    both = check_sentence(make_levels("trade coverage 82.7% is below 95%; 3 of 10 traded options have no open interest to check against"))
    assert "82.7 % is below 95 %" in both and "3 of 10 traded options have no open interest value" in both
    assert check_sentence(make_levels("no stored trades for this expiry")) == "Check failed: no stored trades for this expiry."


def test_sentence_of_a_pass_is_short():
    assert check_sentence(make_levels(None, passed=True)).startswith("Check passed")


def test_caption_has_the_table_sorted_as_given_the_share_and_the_unreliable_verdict():
    violating = [ViolatingOption(2.0, "P", -4139.8, 174.0, 3965.8), ViolatingOption(1.0, "C", 6179.8, 2895.5, 3284.3)]
    caption = dealer_levels_caption(make_levels("10.9% of options exceed their open interest (limit 5%)", violating=violating))
    assert caption.index("Check failed") < caption.index("Every option that breaks the rule")
    assert caption.index("-4,139.8") < caption.index("6,179.8")                         # the order of the list is kept
    assert "3,965.8" in caption and "23.8 x" in caption                                # 4139.8 / 174.0
    assert "carry 100.0 % of the flow-based gamma" in caption                          # both strikes violate
    assert caption.count(UNRELIABLE) == 1
    assert "closing against a dealer" in caption


def test_a_small_share_says_less_affected_and_not_unreliable():
    violating = [ViolatingOption(1.0, "P", -9.0, 5.0, 4.0)]
    caption = dealer_levels_caption(make_levels("10.9% of options exceed their open interest (limit 5%)", violating=violating,
                                                share_rows=((1.0, 5.0), (2.0, 95.0))))
    assert "carry 5.0 % of the flow-based gamma" in caption
    assert UNRELIABLE not in caption and "less affected" in caption


def test_open_interest_zero_has_no_ratio_and_a_passed_view_has_no_table():
    zero = dealer_levels_caption(make_levels("x", violating=[ViolatingOption(1.0, "C", 7.0, 0.0, 7.0)]))
    assert "open interest is 0" in zero
    passed = dealer_levels_caption(make_levels(None, passed=True, violating=[ViolatingOption(1.0, "C", 7.0, 1.0, 6.0)]))
    assert "Every option that breaks the rule" not in passed and "Check passed" in passed


def test_a_failure_without_violating_options_shows_only_the_sentence():
    caption = dealer_levels_caption(make_levels("trade coverage 82.7% is below 95%", violations=0))
    assert "Check failed" in caption and "Every option that breaks the rule" not in caption and UNRELIABLE not in caption
