"""
The "How to read this" help of the market report, as structured data.

Every definition here is taken from the project's own code and documents (the file is named
in the text where it matters). Where the project's rule is a fitted replica of a vendor's
chart or an assumption (for example "dealers are long calls and short puts"), the help says
so. Nothing here claims that a level predicts price.

Every number in an example is derived by one of the small helper functions below, and the text
is written from the same numbers, so ``tests/unit/report/test_how_to_read.py`` can recompute it.

Text rules: plain words, no dollar sign (the report draws text through Plotly, where it would
turn into maths: write USD), no angle brackets.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import accumulate
from statistics import mean, pstdev
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HelpTerm:
    """One word of the help and what it means."""

    term: str
    meaning: str


@dataclass(frozen=True)
class HelpCase:
    """One situation you can meet, how to read it, and one tiny worked example with round numbers."""

    situation: str
    reading: str
    example: str


@dataclass(frozen=True)
class HelpTopic:
    """One chart or table: what you see, its terms, every case, and what it cannot tell."""

    title: str
    what_you_see: str
    terms: Tuple[HelpTerm, ...]
    cases: Tuple[HelpCase, ...]
    limits: Tuple[str, ...]


@dataclass(frozen=True)
class SectionHelp:
    """The help of one report tab (``section_key`` is the tab key)."""

    section_key: str
    summary: str
    topics: Tuple[HelpTopic, ...]


# --------------------------------------------------------------------------
# Constants copied from the code (the tests check them against the code)
# --------------------------------------------------------------------------

GEX_PER_ONE_PERCENT_MOVE = 0.01             # menthorq_replication_calculator.GEX_PER_ONE_PERCENT_MOVE
COVERAGE_GATE = 0.95                        # on_chain_analysis_service._DEALER_INVENTORY_COVERAGE_GATE
VIOLATION_GATE = 0.05                       # on_chain_analysis_service._DEALER_INVENTORY_VIOLATION_GATE
MAX_EXCLUSION_RATE = 0.20                   # on_chain_analysis_service._DEALER_INVENTORY_MAX_EXCLUSION_RATE
CONFLUENCE_BAND_SHARE = 0.01                # the Levels page: band width = 1 % of the spot
CONFLUENCE_MINIMUM_SOURCES = 3              # the Levels page: at least 3 different sources


# --------------------------------------------------------------------------
# Helpers the examples derive their numbers from
# --------------------------------------------------------------------------

def example_ratio(buy_usd: float, sell_usd: float) -> float:
    """Taker buy volume over taker sell volume (volume-weighted ratio). Raises ValueError without sell volume."""
    if sell_usd <= 0:
        raise ValueError("a ratio needs sell volume above zero")
    return buy_usd / sell_usd


def net_flow_usd(buy_usd: float, sell_usd: float) -> float:
    """Taker buy volume minus taker sell volume (positive = net aggressive buying)."""
    return buy_usd - sell_usd


def percent_from(price: float, reference: float) -> float:
    """How far ``price`` is from ``reference`` in percent of ``reference`` (positive = above)."""
    return (price - reference) / reference * 100.0


def notional_usd(contracts: float, strike: float) -> float:
    """Option-flow notional: contracts times the strike (Laevitas's definition, strike_flow.py)."""
    return contracts * strike


def bar_length_usd(blocked_usd: float, screen_usd: float) -> float:
    """Length of one option-flow bar: the blocked part plus the screen part."""
    return blocked_usd + screen_usd


def share_percent(part: float, total: float) -> float:
    """``part`` as a percentage of ``total``."""
    return part / total * 100.0


def share_by_part_percent(parts: Sequence[float]) -> List[float]:
    """Every part as a percentage of the sum of all parts."""
    total = sum(parts)
    return [share_percent(part, total) for part in parts]


def days_left(now: datetime, settlement: datetime) -> float:
    """Days from ``now`` to the settlement instant (Deribit settles at 08:00 UTC)."""
    return (settlement - now).total_seconds() / 86400.0


def open_interest_notional_usd(open_interest_coins: float, index_price: float) -> float:
    """Notional of open interest in the expiry table: open interest in coins times the index price."""
    return open_interest_coins * index_price


def iv_times_open_interest(implied_volatility_percent: float, open_interest_coins: float) -> float:
    """IV (as a decimal: percent divided by 100) times open interest in coins, for one option type at one strike."""
    return implied_volatility_percent / 100.0 * open_interest_coins


def net_gex_per_one_percent_usd(call_gamma_times_open_interest: float, put_gamma_times_open_interest: float,
                                spot: float) -> float:
    """
    Open-interest based net GEX at one strike, USD per 1 % move: (call gamma sum minus put gamma sum)
    times spot squared times 0.01 (gex_dex_methodology.md). The gamma sums are gamma times open interest.
    """
    return (call_gamma_times_open_interest - put_gamma_times_open_interest) * spot * spot * GEX_PER_ONE_PERCENT_MOVE


def dealer_position(taker_net_contracts: float) -> float:
    """Flow-based dealer position: minus the cumulative taker flow (dealer_flow_levels.md)."""
    return -taker_net_contracts


def flow_gex_per_one_percent_usd(dealer_calls: float, call_gamma: float, dealer_puts: float, put_gamma: float,
                                 spot: float) -> float:
    """
    Flow-based GEX at one strike, USD per 1 % move (dealer_inventory_calculator.py): (dealer calls times call gamma
    plus dealer puts times put gamma) times spot squared times 0.01. Gamma is added for both; the sign comes from
    the dealer position.
    """
    return (dealer_calls * call_gamma + dealer_puts * put_gamma) * spot * spot * GEX_PER_ONE_PERCENT_MOVE


def net_dex_usd(call_open_interest: float, call_delta: float, put_open_interest: float, put_delta: float,
                spot: float) -> float:
    """Net DEX at one strike (menthorq_replication_calculator.compute_net_delta_exposure): spot times the sum of delta times OI."""
    return spot * (call_delta * call_open_interest + put_delta * put_open_interest)


def gex_expiring_percent(net_gex: float, all_net_gex: Sequence[float]) -> float:
    """The drawn "GEX Expiring" candidate: net GEX of the expiry over the sum of the absolute net GEX of the expiries of the chart, in percent."""
    return 100.0 * net_gex / sum(abs(value) for value in all_net_gex)


def strike_of_largest_positive(net_gex_by_strike: Mapping[float, float]) -> Optional[float]:
    """Call resistance / call wall rule: the strike with the largest positive net GEX (None when none is positive)."""
    best = max(net_gex_by_strike.items(), key=lambda item: (item[1], -item[0]))
    return best[0] if best[1] > 0 else None


def strike_of_most_negative(net_gex_by_strike: Mapping[float, float]) -> Optional[float]:
    """Put support / put wall rule: the strike with the most negative net GEX (None when none is negative)."""
    worst = min(net_gex_by_strike.items(), key=lambda item: (item[1], item[0]))
    return worst[0] if worst[1] < 0 else None


def cumulative_net_gex(net_gex_by_strike: Mapping[float, float]) -> Dict[float, float]:
    """Running sum of net GEX over ascending strikes."""
    strikes = sorted(net_gex_by_strike)
    return dict(zip(strikes, accumulate(net_gex_by_strike[strike] for strike in strikes)))


def cumulative_minimum_strike(net_gex_by_strike: Mapping[float, float]) -> Optional[float]:
    """The HVL the MenthorQ replica draws today: the strike where the running net GEX is lowest (a tie goes to the lower strike)."""
    cumulative = cumulative_net_gex(net_gex_by_strike)
    if not cumulative:
        return None
    return min(cumulative.items(), key=lambda item: (item[1], item[0]))[0]


def sign_change_strike(net_gex_by_strike: Mapping[float, float]) -> Optional[float]:
    """
    The "gamma flip" of the dealer lanes: the highest strike where the running net GEX changes sign (or lands
    exactly on zero) between two strikes. None when it never does. (The flow-based view does not count an exact zero.)
    """
    flip = None
    previous = None
    for strike, running in cumulative_net_gex(net_gex_by_strike).items():
        if running == 0 or (previous is not None and previous * running < 0):
            flip = strike
        previous = running
    return flip


def violation_percent(violations: int, options_checked: int) -> float:
    """Share of the checked options whose dealer position exceeds their open interest, in percent."""
    return 100.0 * violations / options_checked


def flow_view_passes(coverage: float, violation_rate: float) -> bool:
    """The two numeric gates of the plausibility check: coverage at least 95 % and violations at most 5 %."""
    return coverage >= COVERAGE_GATE and violation_rate <= VIOLATION_GATE


def confluence_width(spot: float) -> float:
    """The width of the highlighter band: 1 % of the spot."""
    return CONFLUENCE_BAND_SHARE * spot


def zoom_window(spot: float, share: float) -> Tuple[float, float]:
    """The price range a zoom button shows: spot minus and plus ``share`` of the spot."""
    return spot * (1 - share), spot * (1 + share)


def simple_average(values: Sequence[float]) -> float:
    """Plain average (the idea of a moving average)."""
    return mean(values)


def population_standard_deviation(values: Sequence[float]) -> float:
    """Population standard deviation of a few values, as the band of the chart uses (taker_flow_figure.py: pstdev)."""
    return pstdev(values)


def index_notional_difference_percent(strike: float, index_price: float) -> float:
    """
    How much less contracts x index price is than contracts x strike, in percent of the strike:
    (strike - index) / strike. Negative means more (strikes below the index).
    """
    return (strike - index_price) / strike * 100.0


# Decision tab: constants copied from coding/core/analytics/decision/decision_config.py (the tests check them against it)
DECISION_SCORE_HALF_POINT = 1.0
DECISION_CONFLICT_PENALTY = 1.0
DECISION_GROUP_CONTRIBUTION_CAP = 1.0
DECISION_MINIMUM_LEVEL_WEIGHT = 0.3
DECISION_MAXIMUM_LEVEL_DISTANCE_SHARE = 0.15
DECISION_LEVEL_BAND_SHARE = 0.01
DECISION_FRESHNESS_LIMIT_HOURS = 3.0
DECISION_STALE_QUALITY_FACTOR = 0.5
DECISION_VERY_STALE_MULTIPLE = 4.0
DECISION_VERY_STALE_QUALITY_FACTOR = 0.1
DECISION_FAILED_CHECK_QUALITY_FACTOR = 0.2
DECISION_MINIMUM_AGREEING_CONTRIBUTION = 0.05
DECISION_HIGH_MINIMUM_GROUPS = 3
DECISION_LOW_MAXIMUM_GROUPS = 1
DECISION_HIGH_CONFLICT_SHARE = 0.35
DECISION_LOW_CONFLICT_SHARE = 0.10
DECISION_GOOD_MEAN_QUALITY = 0.6
DECISION_POOR_MEAN_QUALITY = 0.35
DECISION_WIDE_MARGIN_POINTS = 20.0
DECISION_NARROW_MARGIN_POINTS = 10.0
DECISION_HIGH_MINIMUM_POINTS = 2
DECISION_LOW_MAXIMUM_POINTS = -1
DECISION_TAKER_MINIMUM_POINTS = 240


def group_contribution_value(quality_and_value: Sequence[Tuple[float, float]]) -> float:
    """
    The contribution of ONE input group (independence.py): the quality-weighted mean value times the quality-weighted
    mean quality (group weight 1.0), clipped to plus or minus 1. Items are (quality, value) pairs.
    """
    total_quality = sum(quality for quality, _ in quality_and_value)
    mean_value = sum(quality * value for quality, value in quality_and_value) / total_quality
    mean_quality = sum(quality * quality for quality, _ in quality_and_value) / total_quality
    return max(-DECISION_GROUP_CONTRIBUTION_CAP, min(DECISION_GROUP_CONTRIBUTION_CAP, mean_value * mean_quality))


def evidence_score(support: float, opposition: float) -> float:
    """The evidence score (scenario_engine.py): net = support minus opposition (not below 0), score = 100 x net / (net + 1)."""
    net = max(0.0, support - DECISION_CONFLICT_PENALTY * opposition)
    return 100.0 * net / (net + DECISION_SCORE_HALF_POINT)


def quality_after_age(quality: float, age_hours: float) -> float:
    """Quality of an item of age ``age_hours`` (freshness limit 3 h): times 0.5 when older, times 0.1 when older than 4 limits."""
    if age_hours > DECISION_FRESHNESS_LIMIT_HOURS * DECISION_VERY_STALE_MULTIPLE:
        return quality * DECISION_VERY_STALE_QUALITY_FACTOR
    if age_hours > DECISION_FRESHNESS_LIMIT_HOURS:
        return quality * DECISION_STALE_QUALITY_FACTOR
    return quality


def confidence_points(agreeing_groups: int, conflict_share: float, mean_quality: float, stale_count: int,
                      margin: float) -> int:
    """
    The points of the five confidence criteria of the top scenario (scenario_engine._confidence), each +1, 0 or -1:
    agreeing input groups, conflict share, mean quality of the supporting items, stale supporting items, margin to the
    next scenario in score points.
    """
    points = 0
    if agreeing_groups >= DECISION_HIGH_MINIMUM_GROUPS:
        points += 1
    elif agreeing_groups <= DECISION_LOW_MAXIMUM_GROUPS:
        points -= 1
    if conflict_share > DECISION_HIGH_CONFLICT_SHARE:
        points -= 1
    elif conflict_share < DECISION_LOW_CONFLICT_SHARE:
        points += 1
    if mean_quality >= DECISION_GOOD_MEAN_QUALITY:
        points += 1
    elif mean_quality < DECISION_POOR_MEAN_QUALITY:
        points -= 1
    if stale_count:
        points -= 1
    if margin >= DECISION_WIDE_MARGIN_POINTS:
        points += 1
    elif margin < DECISION_NARROW_MARGIN_POINTS:
        points -= 1
    return points


def confidence_label(agreeing_groups: int, points: int, score: float, stale_count: int) -> str:
    """The label (scenario_engine._confidence): low, high or medium."""
    if agreeing_groups <= DECISION_LOW_MAXIMUM_GROUPS or points <= DECISION_LOW_MAXIMUM_POINTS or score <= 0:
        return "low"
    if agreeing_groups >= DECISION_HIGH_MINIMUM_GROUPS and points >= DECISION_HIGH_MINIMUM_POINTS and not stale_count:
        return "high"
    return "medium"


# --------------------------------------------------------------------------
# The numbers of the examples (one place, so the tests can recompute them)
# --------------------------------------------------------------------------

EXAMPLE_SPOT = 70_000.0
EXAMPLE_CALL_RESISTANCE = 75_000.0
EXAMPLE_PUT_SUPPORT = 62_000.0
EXAMPLE_HVL = 68_000.0

# Futures flow
FUTURES_BUY_USD = 5.2e9
FUTURES_SELL_USD = 5.7e9
FUTURES_ABOVE_BUY_USD = 6.0e9
FUTURES_ABOVE_SELL_USD = 5.0e9
FUTURES_EVEN_USD = 5.0e9
FUTURES_RATIO_VALUES = (0.9, 1.0, 1.1)

# Open interest and IV x OI
IV_OI_CALL_IV_PERCENT = 60.0
IV_OI_CALL_OPEN_INTEREST = 500.0
IV_OI_SECOND_IV_PERCENT = 50.0
IV_OI_SECOND_OPEN_INTEREST = 200.0

# GEX
GEX_CALL_GAMMA_SUM = 0.05           # gamma times open interest, calls: 1,000 contracts at gamma 0.00005
GEX_PUT_GAMMA_SUM = 0.015           # puts: 500 contracts at gamma 0.00003
GEX_EXAMPLE_NET_BY_STRIKE_MILLION = {60_000.0: -3.0, 65_000.0: -2.0, 70_000.0: -1.0, 75_000.0: 1.0, 80_000.0: 4.0}
GEX_FLIP_EXAMPLE_BY_STRIKE_MILLION = {60_000.0: 1.0, 65_000.0: -3.0, 70_000.0: 4.0, 75_000.0: -1.0}
GEX_EXPIRING_NETS_MILLION = (30.0, -10.0, 60.0)
DEALER_TAKER_NET_CONTRACTS = 100.0
DEALER_CALL_GAMMA = 0.00005
DEX_CALL_OPEN_INTEREST = 100.0
DEX_CALL_DELTA = 0.5
DEX_PUT_OPEN_INTEREST = 100.0
DEX_PUT_DELTA = -0.3

# Expiry table
EXPIRY_NOW = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)
EXPIRY_SETTLEMENT = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
EXPIRY_FAR_SETTLEMENT = datetime(2027, 2, 11, 8, 0, tzinfo=timezone.utc)
EXPIRY_CALLS = 600.0
EXPIRY_PUTS = 400.0
EXPIRY_ALL_TOTAL = 4000.0

# Option flow
FLOW_CONTRACTS = 10.0
FLOW_STRIKE = 80_000.0
FLOW_FAR_STRIKE = 140_000.0
FLOW_INDEX_STRIKES = (77_000.0, 108_000.0, 60_000.0)      # index price: EXAMPLE_SPOT
FLOW_BLOCKED_USD = 3_000_000.0
FLOW_SCREEN_USD = 1_000_000.0
FLOW_EXPIRY_PARTS_USD = (6_000_000.0, 3_000_000.0, 1_000_000.0)

# Plausibility check
CHECK_OPTIONS_CHECKED = 100
CHECK_VIOLATIONS_FAILING = 6
CHECK_VIOLATIONS_PASSING = 4
CHECK_COVERAGE_LOW = 0.90

# Levels page
LEVELS_AGREE_MARKS = (("MenthorQ replica", 75_000.0), ("Dealer GEX, open interest", 75_200.0),
                      ("Option flow", 75_400.0))
LEVELS_TOO_FAR_MARKS = (("MenthorQ replica", 75_000.0), ("Dealer GEX, open interest", 75_500.0),
                        ("Option flow", 76_000.0))
LEVELS_SAME_SOURCE_MARKS = (("MenthorQ replica", 75_000.0), ("Option flow", 75_300.0), ("Option flow", 75_600.0))
LEVELS_ZOOM_SHARE = 0.05
STRATEGY_LONG_STRIKE = 70_000.0
STRATEGY_SHORT_STRIKE = 75_000.0

# Decision tab (spot: EXAMPLE_SPOT)
DECISION_ITEM_QUALITY = 0.6
DECISION_ITEM_VALUE = 0.8
DECISION_PUT_WALL = 66_000.0                # open-interest group, strength 1.0, quality 0.6
DECISION_CALL_WALL = 74_000.0               # trades group, strength 1.0, quality 0.6
DECISION_WALL_STRENGTH = 1.0
DECISION_WALL_QUALITY = 0.6
DECISION_STALE_AGE_HOURS = 5.0
DECISION_VERY_STALE_AGE_HOURS = 13.0
DECISION_HVL_STRENGTH = 0.5
DECISION_HVL_QUALITY = 0.7
DECISION_WEAK_FLIP_STRENGTH = 0.05
DECISION_WEAK_FLIP_QUALITY = 0.6
DECISION_HIGH_EXAMPLE = (3, 0.08, 0.55, 0, 25.0)       # agreeing groups, conflict share, mean quality, stale items, margin
DECISION_FLOW_STRIKE_QUALITY = 0.35         # an option-flow strike as a level (strength 1.0 for the top strike)
DECISION_DEALER_FLIP_STRENGTH = 0.5         # a dealer gamma flip at full open-interest share
DECISION_CAPPED_EXAMPLE = (3, 0.05, 0.55, 1, 25.0)
DECISION_CONFLICT_EXAMPLE = (3, 0.40, 0.60, 0, 12.0)
DECISION_ONE_GROUP_EXAMPLE = (1, 0.0, 0.70, 0, 30.0)
DECISION_TAKER_POINTS_FOUND = 120


def _int(value: float) -> str:
    """A whole number with thousands separators."""
    return f"{value:,.0f}"


def _one(value: float) -> str:
    """One decimal place."""
    return f"{value:,.1f}"


def _two(value: float) -> str:
    """Two decimal places."""
    return f"{value:,.2f}"


def _billions(value: float) -> str:
    """USD amount in billions, one decimal."""
    return f"{value / 1e9:.1f}B USD"


def _millions(value: float) -> str:
    """USD amount in millions, no decimals unless needed."""
    return f"{value / 1e6:,.2f}M USD".replace(".00M", "M")


_RATIO_BELOW = example_ratio(FUTURES_BUY_USD, FUTURES_SELL_USD)
_RATIO_ABOVE = example_ratio(FUTURES_ABOVE_BUY_USD, FUTURES_ABOVE_SELL_USD)
_RATIO_EVEN = example_ratio(FUTURES_EVEN_USD, FUTURES_EVEN_USD)
_RATIO_SPREAD = population_standard_deviation(FUTURES_RATIO_VALUES)
_INDEX_LESS = [index_notional_difference_percent(strike, EXAMPLE_SPOT) for strike in FLOW_INDEX_STRIKES]
_CALL_IV_OI = iv_times_open_interest(IV_OI_CALL_IV_PERCENT, IV_OI_CALL_OPEN_INTEREST)
_SECOND_IV_OI = iv_times_open_interest(IV_OI_SECOND_IV_PERCENT, IV_OI_SECOND_OPEN_INTEREST)
_NET_GEX = net_gex_per_one_percent_usd(GEX_CALL_GAMMA_SUM, GEX_PUT_GAMMA_SUM, EXAMPLE_SPOT)
_EXPIRY_TOTAL = EXPIRY_CALLS + EXPIRY_PUTS
_EXPIRY_NOTIONAL = open_interest_notional_usd(_EXPIRY_TOTAL, EXAMPLE_SPOT)
_EXPIRY_SHARE = share_percent(_EXPIRY_TOTAL, EXPIRY_ALL_TOTAL)
_EXPIRY_DAYS = days_left(EXPIRY_NOW, EXPIRY_SETTLEMENT)
_FAR_DAYS = days_left(EXPIRY_NOW, EXPIRY_FAR_SETTLEMENT)
_FLOW_NEAR = notional_usd(FLOW_CONTRACTS, FLOW_STRIKE)
_FLOW_FAR = notional_usd(FLOW_CONTRACTS, FLOW_FAR_STRIKE)
_FLOW_BAR = bar_length_usd(FLOW_BLOCKED_USD, FLOW_SCREEN_USD)
_FLOW_SHARES = share_by_part_percent(FLOW_EXPIRY_PARTS_USD)
_DEALER_POSITION = dealer_position(DEALER_TAKER_NET_CONTRACTS)
_FLOW_GEX = flow_gex_per_one_percent_usd(_DEALER_POSITION, DEALER_CALL_GAMMA, 0.0, 0.0, EXAMPLE_SPOT)
_DEX = net_dex_usd(DEX_CALL_OPEN_INTEREST, DEX_CALL_DELTA, DEX_PUT_OPEN_INTEREST, DEX_PUT_DELTA, EXAMPLE_SPOT)
_GEX_EXAMPLE_CALL_WALL = strike_of_largest_positive(GEX_EXAMPLE_NET_BY_STRIKE_MILLION)
_GEX_EXAMPLE_PUT_WALL = strike_of_most_negative(GEX_EXAMPLE_NET_BY_STRIKE_MILLION)
_GEX_EXAMPLE_HVL = cumulative_minimum_strike(GEX_EXAMPLE_NET_BY_STRIKE_MILLION)
_GEX_EXAMPLE_CUMULATIVE = cumulative_net_gex(GEX_EXAMPLE_NET_BY_STRIKE_MILLION)
_FLIP_EXAMPLE = sign_change_strike(GEX_FLIP_EXAMPLE_BY_STRIKE_MILLION)
_FLIP_CUMULATIVE = cumulative_net_gex(GEX_FLIP_EXAMPLE_BY_STRIKE_MILLION)
_CHECK_FAILING = violation_percent(CHECK_VIOLATIONS_FAILING, CHECK_OPTIONS_CHECKED)
_CHECK_PASSING = violation_percent(CHECK_VIOLATIONS_PASSING, CHECK_OPTIONS_CHECKED)
_BAND_WIDTH = confluence_width(EXAMPLE_SPOT)
_ZOOM_LOW, _ZOOM_HIGH = zoom_window(EXAMPLE_SPOT, LEVELS_ZOOM_SHARE)
_DECISION_ONE_ITEM = group_contribution_value([(DECISION_ITEM_QUALITY, DECISION_ITEM_VALUE)])
_DECISION_TWO_ITEMS_ONE_GROUP = group_contribution_value([(DECISION_ITEM_QUALITY, DECISION_ITEM_VALUE)] * 2)
_DECISION_TWO_GROUPS = _DECISION_ONE_ITEM + _DECISION_ONE_ITEM
_DECISION_WALL = group_contribution_value([(DECISION_WALL_QUALITY, DECISION_WALL_STRENGTH)])
_DECISION_INSIDE_SUPPORT = _DECISION_WALL + _DECISION_WALL            # one wall of each of two groups
_DECISION_INSIDE_SCORE = evidence_score(_DECISION_INSIDE_SUPPORT, 0.0)
_DECISION_BREAK_UP_SCORE = evidence_score(0.0, _DECISION_WALL)        # the call wall is the obstacle: all opposition
_DECISION_MEDIUM_POINTS = confidence_points(2, 0.0, DECISION_WALL_QUALITY, 0, _DECISION_INSIDE_SCORE - _DECISION_BREAK_UP_SCORE)
_DECISION_HIGH_POINTS = confidence_points(*DECISION_HIGH_EXAMPLE)
_DECISION_CAPPED_POINTS = confidence_points(*DECISION_CAPPED_EXAMPLE)
_DECISION_CONFLICT_POINTS = confidence_points(*DECISION_CONFLICT_EXAMPLE)
_DECISION_ONE_GROUP_POINTS = confidence_points(*DECISION_ONE_GROUP_EXAMPLE)
_DECISION_STALE_QUALITY = quality_after_age(DECISION_ITEM_QUALITY, DECISION_STALE_AGE_HOURS)
_DECISION_VERY_STALE_QUALITY = quality_after_age(DECISION_ITEM_QUALITY, DECISION_VERY_STALE_AGE_HOURS)
_DECISION_HVL_WEIGHT = group_contribution_value([(DECISION_HVL_QUALITY, DECISION_HVL_STRENGTH)])
_DECISION_WEAKENED_WEIGHT = group_contribution_value([(DECISION_HVL_QUALITY, DECISION_HVL_STRENGTH),
                                                      (DECISION_WEAK_FLIP_QUALITY, DECISION_WEAK_FLIP_STRENGTH)])
_DECISION_FLOW_FAILED_QUALITY = DECISION_WALL_QUALITY * DECISION_FAILED_CHECK_QUALITY_FACTOR
_DECISION_FLOW_STRIKE_WEIGHT = group_contribution_value([(DECISION_FLOW_STRIKE_QUALITY, DECISION_WALL_STRENGTH)])
_DECISION_FLIP_WEIGHT = group_contribution_value([(DECISION_WALL_QUALITY, DECISION_DEALER_FLIP_STRENGTH)])
_DECISION_STALE_HVL_WEIGHT = group_contribution_value(
    [(DECISION_HVL_QUALITY * DECISION_STALE_QUALITY_FACTOR, DECISION_HVL_STRENGTH)])
_DECISION_FAILED_WALL_WEIGHT = group_contribution_value([(_DECISION_FLOW_FAILED_QUALITY, DECISION_WALL_STRENGTH)])
_DECISION_WINDOW_LOW, _DECISION_WINDOW_HIGH = zoom_window(EXAMPLE_SPOT, DECISION_MAXIMUM_LEVEL_DISTANCE_SHARE)


def _cumulative_text(cumulative: Mapping[float, float]) -> str:
    """"-3, -5, -6" style list of the running sums, in millions."""
    return ", ".join(f"{value:+.0f}" for value in cumulative.values())


def _net_text(net_by_strike: Mapping[float, float]) -> str:
    """"60,000: -3; 65,000: -2" style list of net GEX in millions of USD."""
    return "; ".join(f"{_int(strike)}: {value:+.0f}M" for strike, value in sorted(net_by_strike.items()))


# --------------------------------------------------------------------------
# Report level
# --------------------------------------------------------------------------

REPORT_HELP = SectionHelp(
    section_key="report",
    summary=(
        "The report shows where positioning and liquidity sit in the BTC or ETH options and futures "
        "markets. It does not forecast. A level is a price where a lot of open interest, trading or estimated "
        "forced selling or buying is concentrated. There is no ground truth that tells you a level is right, and "
        "no level in this report is claimed to predict price. Each tab has its own How to read this."),
    topics=(
        HelpTopic(
            title="What the report is, and what it is not",
            what_you_see=(
                "Five tabs, one currency per file: MenthorQ (replica charts), Futures flow (Binance perpetual), "
                "Option flow (Deribit trades per strike), Dealer levels (gamma levels of one expiry, two ways) and "
                "Levels (every source on one price line)."),
            terms=(
                HelpTerm("Level", "A price where something is concentrated: open interest, estimated dealer hedging, "
                                  "or taker volume. It is a reference price, not a forecast."),
                HelpTerm("Source", "One way of finding levels, for example the MenthorQ replica or the flow-based "
                                   "dealer view. The Levels tab draws one row (lane) per source."),
                HelpTerm("Assumption", "Something the data cannot show, so the method takes it as given. The main one: "
                                       "dealers are long calls and short puts (open-interest view), or the dealer is "
                                       "the other side of every taker (flow view). Deribit does not say who is who."),
                HelpTerm("Replica", "A rebuild of a vendor's chart (MenthorQ). Its formulas are fitted to a few "
                                    "MenthorQ charts, on BTC only. It is not the vendor's own formula."),
                HelpTerm("Not computed", "The value is missing: the data or the rule gave no answer. It never means "
                                         "zero."),
            ),
            cases=(
                HelpCase(
                    "A level is drawn on a chart",
                    "Read it as where positioning sits. Do not read it as a forecast of where the market goes or stops.",
                    f"A call wall at {_int(EXAMPLE_CALL_RESISTANCE)} with spot at {_int(EXAMPLE_SPOT)} is "
                    f"{_one(percent_from(EXAMPLE_CALL_RESISTANCE, EXAMPLE_SPOT))} % above spot "
                    f"({_int(EXAMPLE_CALL_RESISTANCE)} minus {_int(EXAMPLE_SPOT)}, divided by {_int(EXAMPLE_SPOT)}). "
                    "That distance is all the level tells you."),
                HelpCase(
                    "Two charts or two tabs show different levels",
                    "They may use different data instants or different rules. Check the as-of line of each tab before "
                    "you call it a contradiction.",
                    "The MenthorQ charts use the close of the last complete UTC day (open interest from 00:00 UTC of that "
                    "day, spot and IV at 23:59 UTC); the Levels tab uses the newest chain. With the close of 12 Nov "
                    "and a chain from 13 Nov 14:00 UTC, the spot and IV are about 14 hours apart and the open "
                    "interest about 38 hours. A call resistance of 75,000 on one and 76,000 on the other can simply be "
                    "that time gap."),
                HelpCase(
                    "Several sources show a level at the same price",
                    "It is a coincidence of methods, not a proof. The sources are not independent: several read the "
                    "same open interest or the same trades, so some overlap is built in.",
                    "Three of four lanes at 75,000 sounds strong. If two of those lanes use the same open interest, "
                    "they were always going to be close."),
                HelpCase(
                    "A value says not computed",
                    "The value is missing. Do not treat it as zero or as a level at the lowest strike.",
                    "A put wall that says not computed means no strike gave a negative net value, or the data was "
                    "missing. It does not mean a put wall of 0."),
                HelpCase(
                    "The as-of line is old",
                    "Old data describes an old market. The tab header says when its data ends. Futures flow and "
                    "option flow warn when the newest stored data is more than 3 hours old.",
                    "Futures flow data up to 14:00 UTC in a report built at 18:00 UTC is 4 hours old, which is past "
                    "the 3-hour limit, so the tab shows a warning."),
            ),
            limits=(
                "No ground truth exists for dealer positioning. Each view is a model.",
                "The sources are not independent, so counting sources overstates how much evidence you have.",
                "The report describes where positioning is. It does not say which way price will move.",
            ),
        ),
        HelpTopic(
            title="Data freshness and the sync",
            what_you_see=(
                "A header with the currency, the build time and the database state, and an as-of line on every tab."),
            terms=(
                HelpTerm("As of", "The instant the tab's data refers to. It is different for each tab: a daily close, "
                                  "the newest stored hour, the last 24 hours or the newest chain."),
                HelpTerm("Sync", "Copying the newest collector data from the VPS to the local database before the "
                                 "report is built. If the check cannot be made (no connection), the report is built "
                                 "from local data and the header says so."),
            ),
            cases=(
                HelpCase(
                    "The header says the report was built from local data",
                    "The sync did not run. The data may be hours or days behind the market. Read the as-of line of each tab.",
                    "A local copy last synced 6 hours ago gives option flow up to 6 hours old, so the option flow tab "
                    "shows its stale-data warning."),
                HelpCase(
                    "A tab says it could not be built",
                    "That tab failed; the others are fine. The tab says what failed.",
                    "Four tabs built and one failed: the status line says 4 of 5 sections were built and names the "
                    "missing one."),
            ),
            limits=(
                "A row changed in place on the VPS is not seen by the sync check.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# MenthorQ
# --------------------------------------------------------------------------

_MENTHORQ_HELP = SectionHelp(
    section_key="menthorq",
    summary=(
        "Replica of MenthorQ's charts, rebuilt from the stored Deribit option chain as interactive charts (hover a bar "
        "or a line for its exact values): IV x OI, net GEX per expiry and "
        "net DEX, with three drawn levels (call resistance, put support, HVL), for three expiry sets (monthly, all "
        "listed, quarterly plus front month). It is a fitted replica, not MenthorQ's "
        "own formula, and it was fitted on BTC only. The charts use the daily close of the last complete UTC day; the "
        "open-interest table uses the newest chain at report time."),
    topics=(
        HelpTopic(
            title="IV x OI chart and its three lines",
            what_you_see=(
                "Horizontal bars per strike: calls filled green, puts as orange outlines, with three dashed lines "
                "(call resistance, put support, HVL) and the spot price, each a labelled line. Hover a bar for the strike, "
                "the call part, the put part and the total; hover a line for the level and its rule. Default: the monthly "
                "expiries added together; the Expiries switch shows all listed expiries or the quarterly expiries plus the "
                "front month."),
            terms=(
                HelpTerm("IV x OI", "Per strike and option type: the mark implied volatility written as a decimal "
                                    "(60 % is 0.6) times the open interest in coins, added over the expiries in the chart. "
                                    "A long bar means a lot of open interest or a high IV at that strike. "
                                    "It does not say who is long or short."),
                HelpTerm("Open interest (OI)", "The number of contracts that are open and not yet closed or expired."),
                HelpTerm("Implied volatility (IV)", "The volatility that the option's price implies."),
                HelpTerm("Call resistance", "The strike with the highest positive net GEX. This is the rule drawn today "
                                            "(DEFAULT_LEVEL_CANDIDATES in menthorq_replication_calculator.py: "
                                            "net_gex_argmax_positive). It is not taken from the IV x OI bars."),
                HelpTerm("Put support", "The strike with the lowest (most negative) net GEX. Drawn today: "
                                        "net_gex_argmin_negative. Also not taken from the bars."),
                HelpTerm("HVL", "The strike where the running sum of net GEX, added from the lowest strike up, is lowest "
                                "(cumulative_net_gex_argmin). It is a point on the strike axis, the turning point of that "
                                "running sum, not a price level re-priced for a change of spot. In the "
                                "code this is one of several candidates for HVL; see the limits below."),
            ),
            cases=(
                HelpCase(
                    "How the three lines are found (worked example)",
                    "Net GEX per strike is needed first (see the Net GEX topic). Call resistance is the largest "
                    "positive value, put support the most negative one, HVL the lowest point of the running sum.",
                    f"Net GEX in millions of USD: {_net_text(GEX_EXAMPLE_NET_BY_STRIKE_MILLION)}. Running sums: "
                    f"{_cumulative_text(_GEX_EXAMPLE_CUMULATIVE)}. Call resistance {_int(_GEX_EXAMPLE_CALL_WALL)}, "
                    f"put support {_int(_GEX_EXAMPLE_PUT_WALL)}, HVL {_int(_GEX_EXAMPLE_HVL)} (running sum -6)."),
                HelpCase(
                    "Reading one bar",
                    "A long call bar at a strike means a lot of IV times open interest in calls there. A long put bar "
                    "means the same for puts. Both can be long.",
                    f"A call IV of {_int(IV_OI_CALL_IV_PERCENT)} % and open interest of {_int(IV_OI_CALL_OPEN_INTEREST)} "
                    f"coins give {_int(IV_OI_CALL_IV_PERCENT)} / 100 x {_int(IV_OI_CALL_OPEN_INTEREST)} = "
                    f"{_int(_CALL_IV_OI)}. A second expiry with IV {_int(IV_OI_SECOND_IV_PERCENT)} % and "
                    f"{_int(IV_OI_SECOND_OPEN_INTEREST)} coins adds {_int(_SECOND_IV_OI)}, so the bar is "
                    f"{_int(_CALL_IV_OI + _SECOND_IV_OI)}."),
                HelpCase(
                    "Price is above call resistance",
                    "Price has moved beyond the strike where assumed dealer long gamma peaked. Some people call that "
                    "a broken ceiling. The report does not test that. Nothing here says price continues or comes back.",
                    f"Call resistance {_int(EXAMPLE_CALL_RESISTANCE)}, price 78,000: price is "
                    f"{_one(percent_from(78_000, EXAMPLE_CALL_RESISTANCE))} % above the line."),
                HelpCase(
                    "Price is between HVL and call resistance",
                    "Price sits between two levels on the strike axis. Being above HVL says only where price is, not "
                    "whether the summed exposure is positive or negative: that is the sign of the total net GEX "
                    "(per 1 % move) of the chart, under the dealer assumption. The vendor story of a calmer zone here "
                    "is a common reading that this report did not test. Use the lines as distances to the next levels.",
                    f"HVL {_int(EXAMPLE_HVL)}, call resistance {_int(EXAMPLE_CALL_RESISTANCE)}, price 72,000: "
                    f"{_int(EXAMPLE_CALL_RESISTANCE - 72_000)} points below call resistance and "
                    f"{_int(72_000 - EXAMPLE_HVL)} above HVL."),
                HelpCase(
                    "Price is between put support and HVL",
                    "Price sits between two levels on the strike axis. Being below HVL does not tell whether the summed "
                    "exposure is positive or negative; the sign of the chart's total net GEX does, under the dealer "
                    "assumption. The vendor story of a faster zone here is a common reading that this report did not "
                    "test. Use the lines as distances.",
                    f"Put support {_int(EXAMPLE_PUT_SUPPORT)}, HVL {_int(EXAMPLE_HVL)}, price 65,000: "
                    f"{_int(EXAMPLE_HVL - 65_000)} below HVL and {_int(65_000 - EXAMPLE_PUT_SUPPORT)} above put support."),
                HelpCase(
                    "Price is below put support",
                    "Price has moved beyond the strike with the most negative net GEX. Nothing here says it keeps "
                    "falling or bounces. It is the lowest of the three lines.",
                    f"Put support {_int(EXAMPLE_PUT_SUPPORT)}, price 60,000: price is "
                    f"{_one(abs(percent_from(60_000, EXAMPLE_PUT_SUPPORT)))} % below the line."),
                HelpCase(
                    "Price is at a level",
                    "Price equals the level; the distance is 0 %. What the level means depends on its rule (see above). "
                    "The report does not say what price does there. Treat it as a reference price.",
                    f"Price {_int(EXAMPLE_CALL_RESISTANCE)} equals call resistance {_int(EXAMPLE_CALL_RESISTANCE)}: "
                    "the distance is 0 %."),
                HelpCase(
                    "The lines are in an unusual order, or one is missing",
                    "The three rules are independent, so any order can occur. Read each line on its own. A "
                    "missing line says not computed.",
                    f"HVL at 80,000 above call resistance at {_int(EXAMPLE_CALL_RESISTANCE)} is possible. The cases "
                    "above assume put support, then HVL, then call resistance."),
                HelpCase(
                    "This tab and the Levels tab show different levels",
                    "The charts use the last complete UTC day: spot and IV at 23:59 UTC, open interest from 00:00 UTC. "
                    "The Levels tab uses the newest chain. Different instants give different levels.",
                    "Close of 12 Nov (23:59 UTC) against a chain from 13 Nov 14:00 UTC: the spot and IV are about 14 "
                    "hours apart, the open interest (from 00:00 UTC of 12 Nov) about 38 hours. A level of 75,000 versus "
                    "76,000 is a timing difference, not an error."),
                HelpCase(
                    "The open-interest table and the charts differ",
                    "On this tab the open-interest table uses the newest chain at report time, while the charts use "
                    "the previous UTC day's close. Totals and shares can therefore differ between them.",
                    "A table built from the 13 Nov 14:00 UTC chain next to a chart with open interest from 12 Nov "
                    "00:00 UTC compares data about 38 hours apart."),
            ),
            limits=(
                "Which rule each line uses today: call resistance = highest positive net GEX; put support = lowest "
                "negative net GEX; HVL = lowest point of the running net GEX (menthorq_replication_calculator.py, "
                "DEFAULT_LEVEL_CANDIDATES). These were chosen by comparison with MenthorQ charts of 2026-11-06 and "
                "2026-10-12. They matched all 24 per-expiry panel levels and 3 of 3 all-expiry levels on each date, but "
                "only with a different expiry set per date. The check on 2026-08-24 is weak evidence.",
                "Other candidates exist in the code and are not drawn: highest call-only gamma strike and highest "
                "put-only gamma strike (MenthorQ's published wording; matched 8 of 16 per-expiry levels), the highest "
                "call and put IV x OI strike, the lowest point of the net GEX re-priced over a grid of spot prices "
                "(spot sweep), the zero crossing of that spot-sweep profile, and the strike where the running net GEX "
                "changes sign. The dealer lanes of the Levels tab use that sign-change rule, so the same word HVL or "
                "gamma flip can mean two different strikes.",
                "The dealer assumption (dealers long calls, short puts) is an assumption, not an observation.",
                "Per-expiry charts draw the levels from that expiry's own net GEX only.",
            ),
        ),
        HelpTopic(
            title="Net GEX panels",
            what_you_see=(
                "One figure with four panels, within the expiries of the chart (monthly by default; all listed or quarterly "
                "plus front month with the switch; hover a bar for the call part, the put part and both units): the "
                "first expiry of that set, the next expiry, the expiry with the highest absolute net GEX and the "
                "one with the second highest (among the rest). Bars are net GEX per strike (green positive, red "
                "negative), a yellow profile line, the three level lines, the spot, and the share 'GEX Expiring' in the title."),
            terms=(
                HelpTerm("Gamma", "How fast an option's delta changes when price moves. It is largest for options "
                                  "near the strike and close to expiry."),
                HelpTerm("GEX (gamma exposure)", "Gamma times open interest times spot squared times 0.01, in USD per 1 % "
                                                 "move of the spot (gex_dex_methodology.md). Roughly the size in USD of "
                                                 "the hedge trade a 1 % move would require."),
                HelpTerm("Net GEX", "Call gamma exposure minus put gamma exposure. This takes the assumption that "
                                    "dealers are long calls and short puts (the published SqueezeMetrics and SpotGamma "
                                    "convention). It is an assumption: Deribit does not say who holds what."),
                HelpTerm("Positive gamma, long gamma", "If dealers are long gamma and delta-hedge, their hedges trade "
                                                       "against the move: they sell into rises and buy into falls. This "
                                                       "report does not test whether that dampens or amplifies price."),
                HelpTerm("Negative gamma, short gamma", "If dealers are short gamma and delta-hedge, their hedges trade "
                                                        "with the move: they buy into rises and sell into falls. This "
                                                        "report does not test whether that dampens or amplifies price."),
                HelpTerm("Profile line", "The running sum of net GEX over strikes, rescaled so its largest value spans "
                                         "the largest bar. The rescale is an approximation (within 10 % on 11 of 12 "
                                         "transcribed MenthorQ profiles)."),
                HelpTerm("GEX Expiring", "Net GEX of the expiry over the sum of the absolute net GEX of the expiries of "
                                         "the chart (monthly by default; all listed with the switch), in "
                                         "percent. Approximate: off by up to about 3 to 5 percentage points against "
                                         "MenthorQ's numbers."),
                HelpTerm("Bar unit", "The bars are drawn in MenthorQ's unit, gamma x OI x spot (the stored net GEX divided "
                                     "by spot x 0.01). The code's GEX per 1 % move is the other unit."),
            ),
            cases=(
                HelpCase(
                    "Net GEX of one strike (the formula)",
                    "Weight gamma by open interest for calls and puts, take the difference, multiply by spot squared "
                    "times 0.01.",
                    f"Spot {_int(EXAMPLE_SPOT)}. Calls: 1,000 contracts x gamma 0.00005 = "
                    f"{GEX_CALL_GAMMA_SUM:g}. Puts: 500 x 0.00003 = {GEX_PUT_GAMMA_SUM:g}. Net {GEX_CALL_GAMMA_SUM - GEX_PUT_GAMMA_SUM:g}"
                    f" x {_int(EXAMPLE_SPOT)} squared x 0.01 = {_int(_NET_GEX)} USD per 1 % move."),
                HelpCase(
                    "A green bar (positive net GEX at a strike)",
                    "Under the assumption, dealers are net long gamma at that strike. If they delta-hedge, their hedges "
                    "trade against the move. This is why call resistance, the largest green bar, is called resistance. "
                    "This report does not test whether that dampens or amplifies price.",
                    f"Net GEX {_int(_NET_GEX)} USD per 1 % move: about that many USD of hedging for a 1 % move at that strike."),
                HelpCase(
                    "A red bar (negative net GEX at a strike)",
                    "Under the assumption, dealers are net short gamma at that strike. If they delta-hedge, their hedges "
                    "trade with the move. Put support is the largest red bar. This report does not test whether "
                    "that amplifies price.",
                    f"Puts weigh more than calls: calls 0.02, puts 0.05, spot {_int(EXAMPLE_SPOT)}: "
                    f"(0.02 - 0.05) x {_int(EXAMPLE_SPOT)} squared x 0.01 = "
                    f"{_int(net_gex_per_one_percent_usd(0.02, 0.05, EXAMPLE_SPOT))} USD per 1 % move."),
                HelpCase(
                    "The total net GEX of an expiry is positive or negative",
                    "Positive total: dealers net long gamma in that expiry (under the assumption); if they "
                    "delta-hedge, their hedges trade against the move. Negative total: net short gamma, hedges "
                    "trade with the move. This report does not test whether that dampens or amplifies price. One "
                    "expiry is not the whole market: another expiry can point the other way.",
                    "A weekly with a total of -3 and a monthly with +8 (millions of USD) add to +5, but the weekly "
                    "can still behave as short gamma near its strikes."),
                HelpCase(
                    "Which panel is shown",
                    "The first two panels are the first two expiries of the selected set (monthly by default), in time "
                    "order. The last two are the biggest by absolute "
                    "net GEX among the remaining expiries of that set. A panel can say no expiry when there are too few.",
                    "Four expiries in time order with absolute net GEX 30, 10, 60 and 20 (millions): panels one and two "
                    "are the first two expiries (30 and 10); panel three is the biggest of the rest (60), panel four the next (20)."),
                HelpCase(
                    "GEX Expiring in the panel title",
                    "How big this expiry's net GEX is compared with the expiries of the chart together (by absolute "
                    "size; monthly by default, all listed with the switch). Higher "
                    "means more of the gamma sits in this expiry.",
                    f"Net GEX in millions of USD for three expiries: {', '.join(f'{value:+.0f}' for value in GEX_EXPIRING_NETS_MILLION)}. "
                    f"First expiry: {_int(GEX_EXPIRING_NETS_MILLION[0])} / ({_int(sum(abs(v) for v in GEX_EXPIRING_NETS_MILLION))})"
                    f" = {_one(gex_expiring_percent(GEX_EXPIRING_NETS_MILLION[0], GEX_EXPIRING_NETS_MILLION))} %."),
                HelpCase(
                    "The profile line crosses zero or turns",
                    "The profile is the running sum over strikes. Its lowest point is where HVL is drawn. It shows "
                    "how net GEX accumulates along the strike axis, not how it would change if price moved.",
                    f"Running sums {_cumulative_text(_GEX_EXAMPLE_CUMULATIVE)} reach their lowest value, -6, at "
                    f"{_int(_GEX_EXAMPLE_HVL)}."),
            ),
            limits=(
                "Net GEX rests on the assumption that dealers are long calls and short puts. If customers are "
                "net sellers of calls, the sign of a bar flips. Nobody can check this on Deribit.",
                "Gamma is computed with Black-Scholes from the mark IV, with the index price as underlying.",
                "GEX Expiring and the profile rescale are approximations of MenthorQ's numbers, not their formula.",
            ),
        ),
        HelpTopic(
            title="Net DEX",
            what_you_see=(
                "Net DEX bars per strike over the expiries of the chart (monthly by default; all listed or quarterly "
                "plus front month with the switch; green positive, red negative; hover for the exact value), an orange DEX profile, a "
                "yellow GEX profile, the three level lines of those expiries and the spot."),
            terms=(
                HelpTerm("Delta", "How much an option's price changes for a one-unit move of the underlying: about 0 to 1 "
                                  "for calls, 0 to -1 for puts."),
                HelpTerm("Net DEX", "Spot times the sum of delta x open interest over the calls and puts at a strike. Put "
                                    "delta is negative. This is the holders' side (open interest has a buyer and a seller "
                                    "for every contract; the assumed dealer side is the opposite sign)."),
                HelpTerm("DEX profile, GEX profile", "Running sums over strikes, rescaled so the largest value matches the "
                                                     "largest bar (PROFILE_RESCALE_RULE). They are drawn on the bar axis."),
            ),
            cases=(
                HelpCase(
                    "A positive DEX bar",
                    "The open interest at that strike has more call-like delta than put-like delta. It does not say "
                    "who is long or short, and it is not a view on direction.",
                    f"100 calls with delta 0.5 and 100 puts with delta -0.3 at spot {_int(EXAMPLE_SPOT)}: "
                    f"{_int(EXAMPLE_SPOT)} x (100 x 0.5 - 100 x 0.3) = {_int(_DEX)} USD."),
                HelpCase(
                    "A negative DEX bar",
                    "More put-like delta than call-like delta at that strike.",
                    f"100 calls with delta 0.2 and 100 puts with delta -0.5 at spot {_int(EXAMPLE_SPOT)}: "
                    f"{_int(net_dex_usd(100, 0.2, 100, -0.5, EXAMPLE_SPOT))} USD."),
                HelpCase(
                    "The DEX profile rises or falls from left to right",
                    "It is the running sum of the bars from the lowest strike up. A rise means positive bars are being "
                    "added there. It is rescaled, so only its shape matters, not its height.",
                    "Bars +2, -1, +3 in millions give a running sum of +2, +1, +4."),
                HelpCase(
                    "The level lines on this chart",
                    "They are the net GEX levels over the expiries of the chart (same rule as the IV x OI chart), not a DEX rule.",
                    "Call resistance, put support and HVL here come from net GEX, even though the bars are DEX."),
            ),
            limits=(
                "Net DEX is the holder side of open interest. Calling it bullish or bearish positioning goes beyond "
                "what the data shows (the older text in gex_dex_methodology.md does this).",
                "The strike window is 0.75 to 1.35 times spot, widened to include the level lines. MenthorQ's window "
                "is not published.",
                "Delta is evaluated at each expiry's forward. Checked against two MenthorQ DEX charts "
                "(2026-10-12 and 2026-08-24): same signs and magnitudes.",
            ),
        ),
        HelpTopic(
            title="IV x OI per expiry and the open-interest table",
            what_you_see=(
                "One interactive IV x OI chart per expiry of the all-listed set (the expiry switch under it), with that "
                "expiry's own levels, and a table of open interest per expiry: "
                "expiry, days left, calls, puts, total OI in coins, notional and a share with a bar."),
            terms=(
                HelpTerm("Expiry", "The date the options settle (Deribit: 08:00 UTC)."),
                HelpTerm("Days left", "Time from now to the settlement instant, in days."),
                HelpTerm("Calls, puts, total OI", "Open interest of each type in coins, and their sum."),
                HelpTerm("Notional", "Total OI times the index price, in USD. The size of the open positions in USD terms."),
                HelpTerm("Share", "This expiry's total OI as a percentage of the total OI of all listed expiries. The bar "
                                  "draws that share."),
            ),
            cases=(
                HelpCase(
                    "Reading one table row",
                    "Add calls and puts for the total, multiply by the index price for the notional, divide by the "
                    "grand total for the share.",
                    f"Calls {_int(EXPIRY_CALLS)} plus puts {_int(EXPIRY_PUTS)} = {_int(_EXPIRY_TOTAL)} coins. At index "
                    f"{_int(EXAMPLE_SPOT)}: {_millions(_EXPIRY_NOTIONAL)}. With {_int(EXPIRY_ALL_TOTAL)} coins in all "
                    f"expiries: share {_int(_EXPIRY_SHARE)} %."),
                HelpCase(
                    "One expiry has a big share",
                    "More contracts are tied to that date. When it settles those positions, and the hedges tied to "
                    "them, go away. That is why the largest expiries deserve a look. It does not say which way price moves.",
                    f"A share of {_int(_EXPIRY_SHARE)} % means about one in four open contracts expires on that date."),
                HelpCase(
                    "Few days left",
                    "An option close to expiry reacts more sharply to price moves (higher gamma near the strike), so "
                    "a near expiry can matter more than its share suggests.",
                    f"Now 13 Nov 08:00 UTC, settlement 20 Nov 08:00 UTC: {_int(_EXPIRY_DAYS)} days left."),
                HelpCase(
                    "Many days left",
                    "Its open interest is far from expiry. It still counts, but gamma per contract is lower.",
                    f"Now 13 Nov 08:00 UTC, settlement 11 Feb 08:00 UTC: {_int(_FAR_DAYS)} days left, against "
                    f"{_int(_EXPIRY_DAYS)} for the 20 Nov expiry."),
                HelpCase(
                    "An expiry has no open interest in the chart",
                    "It was listed after the open-interest snapshot. Its levels say not computed. That is expected.",
                    "A weekly listed at 06:00 UTC has no open interest in a snapshot taken at 00:00 UTC."),
            ),
            limits=(
                "Per-expiry levels come from that expiry's own net GEX, not from the IV x OI bars.",
                "The table uses the newest chain at report time; the charts use the previous UTC day's close (open "
                "interest from 00:00 UTC of that day). The two can be more than a day apart.",
                "OI is the number of open contracts, not a view on direction.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Futures flow
# --------------------------------------------------------------------------

_FUTURES_HELP = SectionHelp(
    section_key="futures-flow",
    summary=(
        "One interactive chart of the Binance perpetual future of the report currency: the taker buy/sell ratio over "
        "the stored 30 days. This is the futures market, not options. It shows how aggressive buyers and sellers were, "
        "not where price goes."),
    topics=(
        HelpTopic(
            title="Taker buy/sell ratio chart",
            what_you_see=(
                "The hourly rolling 24-hour taker buy/sell ratio over the stored 30 days, with range buttons (1d, 3d, 7d, "
                "all) and a range slider, a dashed baseline at 1.0, a 7-day moving average, a band of plus or minus one "
                "standard deviation of the ratio over the whole window, and a hover that shows that day's daily ratio "
                "and daily net flow in USD."),
            terms=(
                HelpTerm("Taker", "The side of a trade that crossed the order book (an aggressive buyer or seller). The "
                                  "data is Binance's perpetual klines: total volume and the volume bought by takers; "
                                  "taker sell = total minus taker buy. Volumes are in USD (USDT)."),
                HelpTerm("Taker buy/sell ratio", "Taker buy volume divided by taker sell volume. Over several hours or days "
                                                 "it is total buy over total sell (volume-weighted), never an average of ratios."),
                HelpTerm("Rolling 24-hour ratio", "For each hour, the ratio of the 24 hours ending with that hour. A window "
                                                  "with a missing hour gives no point: the line has a gap."),
                HelpTerm("Daily ratio and net flow", "The ratio of one complete UTC day, and buy volume minus sell volume "
                                                     "of that day. A day with a missing hour is left out."),
                HelpTerm("Baseline 1.0", "Buys equal sells."),
                HelpTerm("7-day mean", "For each hour, the plain average of the hourly rolling-ratio values of the 7 days "
                                       "(168 hours) up to that hour. It smooths the hour-to-hour wiggle. At the start of "
                                       "the window fewer than 7 days exist, so the first part averages fewer values."),
                HelpTerm("Band", "The average of all ratio values shown, plus and minus one standard deviation of them "
                                 "(population standard deviation): a measure of how far the ratio usually strays. A point "
                                 "outside is unusual for this window, not a signal."),
                HelpTerm("Day in the hover", "The UTC day of the last hour of that point's 24-hour window. A point drawn at "
                                             "00:00 belongs to the day before. 'Day not complete' means that day has a "
                                             "missing hour or has not ended."),
                HelpTerm("Range buttons and slider", "They only zoom the view. They change no number."),
            ),
            cases=(
                HelpCase(
                    "Ratio above 1",
                    "Takers bought more than they sold: buyers were the more aggressive side.",
                    f"Buys {_billions(FUTURES_ABOVE_BUY_USD)}, sells {_billions(FUTURES_ABOVE_SELL_USD)}: ratio "
                    f"{_two(_RATIO_ABOVE)} (net flow {_billions(net_flow_usd(FUTURES_ABOVE_BUY_USD, FUTURES_ABOVE_SELL_USD))})."),
                HelpCase(
                    "Ratio about 1",
                    "Aggressive buying and selling were about equal. This is the usual state.",
                    f"Buys {_billions(FUTURES_EVEN_USD)}, sells {_billions(FUTURES_EVEN_USD)}: ratio {_one(_RATIO_EVEN)}."),
                HelpCase(
                    "Ratio below 1",
                    "Takers sold more than they bought: sellers were the more aggressive side. The note under the chart "
                    "prints the live minimum and maximum of the ratio over the stored days.",
                    f"Buys {_billions(FUTURES_BUY_USD)}, sells {_billions(FUTURES_SELL_USD)}: ratio "
                    f"{_two(_RATIO_BELOW)} ({FUTURES_BUY_USD / 1e9:.1f} / {FUTURES_SELL_USD / 1e9:.1f} = "
                    f"{_RATIO_BELOW:.3f}); net flow {_billions(net_flow_usd(FUTURES_BUY_USD, FUTURES_SELL_USD))}."),
                HelpCase(
                    "The line is above its 7-day moving average",
                    "Taker buying is stronger than in the last week. It is a comparison with its own recent past.",
                    "A ratio of 1.10 now against a 7-day average of 1.00 is 0.10 above that average."),
                HelpCase(
                    "The line leaves the band",
                    "The ratio is unusual compared with this 30-day window. It is not a signal by itself: a window with "
                    "a calm month has a narrow band, so smaller moves look unusual.",
                    f"For the three values {', '.join(str(value) for value in FUTURES_RATIO_VALUES)} the population "
                    f"standard deviation is {_RATIO_SPREAD:.3f} (prints as about {_two(_RATIO_SPREAD)}), so the band is "
                    f"about 1.0 plus or minus {_two(_RATIO_SPREAD)}. The real band is computed over all 30 days, not "
                    "only the part you have zoomed to."),
                HelpCase(
                    "The line crosses 1.0",
                    "The 24-hour balance changed from net buying to net selling or back. It is a change of balance, "
                    "not a forecast.",
                    "A rolling ratio of 1.02 one hour and 0.99 a few hours later has crossed the baseline."),
                HelpCase(
                    "A gap in the line, or a missing day",
                    "An hour or a day of data is missing. The chart leaves a gap rather than guessing.",
                    "One missing hour removes the 24 rolling windows that contain it from the line."),
                HelpCase(
                    "The hover shows a daily ratio and a daily net flow",
                    "The day's ratio and the day's buy minus sell volume in USD. The ratio says which side dominated, "
                    "the net flow says by how much.",
                    f"Buys {_billions(FUTURES_BUY_USD)} and sells {_billions(FUTURES_SELL_USD)}: ratio "
                    f"{_two(_RATIO_BELOW)}, net flow {_billions(net_flow_usd(FUTURES_BUY_USD, FUTURES_SELL_USD))}."),
            ),
            limits=(
                "No trend lines are drawn on the ratio. It oscillates around 1 within a band, so a trend line would "
                "show a direction the data does not have.",
                "It cannot tell the direction of price. A ratio above 1 can come with a falling price.",
                "It is the futures market (Binance USD-M perpetual), not options. Other exchanges, spot and "
                "coin-margined futures are not included.",
                "The data is stale after 3 hours. The history starts when the collector first ran (30 days).",
                "Checked against CryptoQuant on 29 days: mean absolute difference of the daily ratio 0.0028; "
                "our total volume is about 1 % higher.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Option flow
# --------------------------------------------------------------------------

_OPTION_FLOW_HELP = SectionHelp(
    section_key="option-flow",
    summary=(
        "Buy and sell volume per strike of the Deribit option trades of the last 24 hours, in the manner of Laevitas's "
        "chart. Calls on the left, puts on the right. It shows what takers did, not what positions exist now."),
    topics=(
        HelpTopic(
            title="Buy and sell bars per strike",
            what_you_see=(
                "For each strike that traded, calls to the left and puts to the right. Each side has two thin bars, Buy and "
                "Sell. Each bar is the blocked part next to the axis plus the screen part. Hover a strike for its eight numbers."),
            terms=(
                HelpTerm("Taker side (buy or sell)", "Deribit's direction is the side of the aggressor, the one who crossed "
                                                     "the spread. A buy is a taker who bought. Checked on the data: buys print "
                                                     "above the mark price (median +0.14 %), sells below (-0.58 %)."),
                HelpTerm("Blocked and screen", "Blocked = a block trade (block_trade_id set; for BTC in practice 25 BTC or "
                                               "more per leg; the BTC minimum is 25 BTC), negotiated off the screen. "
                                               "Screen = a normal order-book trade."),
                HelpTerm("Notional", "Contracts times the strike, in USD (Laevitas's definition). One contract is 1 coin "
                                     "(BTC or ETH). Contracts times the index price differs from it by (strike minus "
                                     f"index) / strike. At index {_int(EXAMPLE_SPOT)}: {_int(_INDEX_LESS[0])} % less at a "
                                     f"strike of {_int(FLOW_INDEX_STRIKES[0])}, {_int(_INDEX_LESS[1])} % less at "
                                     f"{_int(FLOW_INDEX_STRIKES[1])}, and more for strikes below the index "
                                     f"({_int(-_INDEX_LESS[2])} % more at {_int(FLOW_INDEX_STRIKES[2])})."),
                HelpTerm("Contracts and trades", "Contracts: the size in coins. Trades: how many separate trades. One big "
                                                 "block is one trade; many small orders are many trades."),
                HelpTerm("Unit switch", "Notional (USD), contracts or trades. The same trades, counted three ways."),
                HelpTerm("Expiry switcher", "All expirations, or one of up to 12 single expiries, each labelled with days "
                                            "left, open interest in USD and its share, for example '25DEC26, 41 days, 9.65B "
                                            "USD OI, 40%'. The list is ordered by open interest, largest first; an expiry "
                                            "without open interest in the chain (for example one that has already "
                                            "settled) comes last. The expiries among the 4 largest by open interest that "
                                            "traded in the window are always offered; the rest are the most traded by notional."),
            ),
            cases=(
                HelpCase(
                    "Calls bought (taker bought calls)",
                    "Takers paid up for calls. It can be a directional bet up, but also a hedge, the call leg of a "
                    "spread, a trade that closes an earlier short call, or a market maker lifting an offer.",
                    f"{_int(FLOW_CONTRACTS)} contracts at strike {_int(FLOW_STRIKE)}: notional {_int(FLOW_CONTRACTS)} x "
                    f"{_int(FLOW_STRIKE)} = {_int(_FLOW_NEAR)} USD."),
                HelpCase(
                    "Calls sold (taker sold calls)",
                    "Takers hit bids for calls. It can be a bet that price stays below the strike, covered call selling, "
                    "a closing of long calls, or one leg of a spread.",
                    f"{_int(FLOW_CONTRACTS)} contracts at {_int(FLOW_STRIKE)} sold: the same {_int(_FLOW_NEAR)} USD, drawn in the Sell bar."),
                HelpCase(
                    "Puts bought (taker bought puts)",
                    "Takers paid up for puts. It can be protection for a long position (a hedge, not a bearish view), "
                    "a bearish bet, a closing of short puts, or a spread leg.",
                    f"{_int(FLOW_CONTRACTS)} puts at strike 65,000: {_int(notional_usd(FLOW_CONTRACTS, 65_000))} USD."),
                HelpCase(
                    "Puts sold (taker sold puts)",
                    "Takers hit bids for puts. It can be a bet that price stays above the strike, income selling, a "
                    "closing of long puts, or a spread leg.",
                    f"{_int(FLOW_CONTRACTS)} puts at 65,000 sold: {_int(notional_usd(FLOW_CONTRACTS, 65_000))} USD in the Sell bar on the put side."),
                HelpCase(
                    "A bar has a blocked part and a screen part",
                    "The bar's length is blocked plus screen volume of that type and side. The blocked part sits next "
                    "to the axis. Blocks are negotiated and often belong to a multi-leg structure.",
                    f"Blocked {_millions(FLOW_BLOCKED_USD)} plus screen {_millions(FLOW_SCREEN_USD)}: the bar is "
                    f"{_millions(_FLOW_BAR)} long."),
                HelpCase(
                    "Notional, contracts and trades give different pictures",
                    "Notional weighs high strikes more (it multiplies by the strike). Contracts count coins. Trades count "
                    "orders. Switch the unit to see which strikes stand out each way.",
                    f"The same {_int(FLOW_CONTRACTS)} contracts are {_int(_FLOW_NEAR)} USD at {_int(FLOW_STRIKE)} but "
                    f"{_int(_FLOW_FAR)} USD at {_int(FLOW_FAR_STRIKE)}. One block of 100 contracts is 1 trade; 100 "
                    "orders of 1 contract are 100 trades."),
                HelpCase(
                    "Large flow far out of the money",
                    "Far out-of-the-money flow is not optimism by itself. Cheap far strikes are used for tail "
                    "protection, as spread legs, or to sell premium. Notional also inflates far out-of-the-money calls "
                    "(high strikes); far puts (low strikes) are deflated.",
                    f"{_int(FLOW_CONTRACTS)} calls at {_int(FLOW_FAR_STRIKE)} with spot at {_int(EXAMPLE_SPOT)}: the strike is "
                    f"{_int(percent_from(FLOW_FAR_STRIKE, EXAMPLE_SPOT))} % above spot."),
                HelpCase(
                    "Both buy and sell bars are large at one strike",
                    "Heavy two-way trading at a strike. It can be a roll or spread traded through that strike, or two "
                    "sides of a market. The chart cannot say which.",
                    "Calls bought 4M USD and calls sold 4M USD at 80,000 give a net of 0, with 8M USD of activity."),
                HelpCase(
                    "Reading the bar stack across the window",
                    "The chart covers the last 24 hours only. An older position that is still open does not appear.",
                    "A 100-contract trade 30 hours ago is not in the chart, though its position may still be open."),
            ),
            limits=(
                "It cannot say whether a trade opens or closes a position, nor whether the taker was a customer or a "
                "market maker. A sell is sell-side volume, not 'they are now short'.",
                "Blocks are negotiated, average 3.8 legs (measured over 24 hours on 2026-11-13) and more than half of "
                "the legs belong to a combo, so a single leg is not necessarily a view.",
                "The window is fixed at 24 hours in the report. It is not a forecast.",
                "Block trades may not be stored for the whole window; then some blocks show as screen volume (the tab warns).",
            ),
        ),
        HelpTopic(
            title="Block-structure table and the expiry table",
            what_you_see=(
                "A block-structure table with the largest blocks of the window (legs, expiries, legs bought against sold, "
                "the biggest legs and a label), and a 'which expiry carries it' table: the largest bars of the "
                "all-expiries chart and their share by expiry with days left."),
            terms=(
                HelpTerm("Block structure", "The legs of one block trade. A block can be one leg or several legs traded "
                                            "together, such as a spread."),
                HelpTerm("Label 'single leg'", "One leg in the window. A 4-leg block with only 1 leg in the window reads "
                                               "'single leg (only 1 of 4 legs in the window)'."),
                HelpTerm("Label 'two legs'", "The block has two legs, for example a spread or a risk reversal."),
                HelpTerm("Label 'multi-leg structure over N expiries'", "More than two legs over N expiries. The direction of one "
                                                                       "leg is not a view on its own."),
                HelpTerm("Legs in the window against declared legs", "Deribit stores how many legs a block has. When some legs "
                                                                     "were traded just outside the 24-hour window, the label "
                                                                     "adds '(only K of M legs in the window)'."),
                HelpTerm("Which expiry carries it", "A bar in the all-expiries chart adds up all expiries. This table takes the "
                                                    "8 largest bars (one bar = one strike, calls or puts, bought or sold, "
                                                    "blocked plus screen) and splits each by expiry: the 3 largest expiries "
                                                    "with share and days left, the rest as 'others', and the blocked share of "
                                                    "the bar. An expiry name that is a button opens that expiry in the chart "
                                                    "above. 'Not explained' would mean the per-expiry numbers do not add up "
                                                    "to the bar; normally it does not appear."),
            ),
            cases=(
                HelpCase(
                    "A single-leg block",
                    "One option bought or sold. It is the closest to a directional view, but still can be a hedge or a closing trade.",
                    "A block of 50 call contracts at one strike and one expiry: legs 1, expiries 1."),
                HelpCase(
                    "A two-leg block",
                    "Often a spread. The buy leg and the sell leg belong together; read the pair, not either leg alone.",
                    "Buy 100 calls at 70,000 and sell 100 calls at 75,000 in one block: legs bought 1, sold 1."),
                HelpCase(
                    "A multi-leg structure over several expiries",
                    "A calendar, a butterfly or a larger structure. The direction of one leg is not a view on its own.",
                    "Four legs over 2 expiries with 2 bought and 2 sold: label multi-leg structure over 2 expiries."),
                HelpCase(
                    "One expiry carries most of a big bar",
                    "A bar in the all-expiries chart can come from one expiry. A short-dated expiry says something "
                    "different from a distant one.",
                    f"A bar of {_millions(sum(FLOW_EXPIRY_PARTS_USD))} splits into {_millions(FLOW_EXPIRY_PARTS_USD[0])}, "
                    f"{_millions(FLOW_EXPIRY_PARTS_USD[1])} and {_millions(FLOW_EXPIRY_PARTS_USD[2])}: shares "
                    f"{_int(_FLOW_SHARES[0])} %, {_int(_FLOW_SHARES[1])} % and {_int(_FLOW_SHARES[2])} %."),
                HelpCase(
                    "The expiry carrying a bar is close to settlement",
                    "Days left says how long the trade has to run. Trades in the last days before expiry react to "
                    "price more sharply.",
                    f"A bar whose largest share sits in an expiry {_int(_EXPIRY_DAYS)} days away (13 Nov to 20 Nov, 08:00 UTC) "
                    "is mostly short-dated."),
            ),
            limits=(
                "The tables describe the window's largest blocks only. They are not a position report.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Dealer levels
# --------------------------------------------------------------------------

_DEALER_HELP = SectionHelp(
    section_key="dealer-levels",
    summary=(
        "Gamma levels of one expiry, calculated two ways: from the trades (flow-based) and from open interest (OI-based). "
        "Both rest on an assumption about who the dealer is, and neither has a ground truth. A plausibility check tells "
        "whether the flow-based view is believable."),
    topics=(
        HelpTopic(
            title="Flow-based and OI-based GEX",
            what_you_see=(
                "GEX bars per strike for one expiry (green positive, red negative), the levels of both views (call wall, "
                "put wall, gamma flip), the totals and a plausibility check. Pick the expiry with the switcher: it offers the 5 "
                "nearest expiries, the nearest monthly one and the one with the most open interest."),
            terms=(
                HelpTerm("Dealer assumption (flow view)", "Every trade has a taker and a maker. The maker is assumed to be a "
                                                          "dealer. So the dealer position of an option is minus the cumulative "
                                                          "taker flow since the start of the flow (Glassnode's taker-flow method). "
                                                          "This is an assumption: Deribit does not say who is a customer or a dealer."),
                HelpTerm("Dealer assumption (OI view)", "Dealers are long calls and short puts over the whole open interest. Also an assumption."),                HelpTerm("Flow start", "The first stored trade of the expiry, but not before 2026-04-25, when the complete trade "
                                       "history starts. Positions held before that are not known."),
                HelpTerm("GEX per 1 % move", "Flow view: (dealer calls x call gamma + dealer puts x put gamma) x spot squared x 0.01. "
                                             "OI view: (call gamma x OI minus put gamma x OI) x spot squared x 0.01. "
                                             "Positive = dealers long gamma, negative = short gamma, under the assumption. If "
                                             "dealers are long gamma and delta-hedge, their hedges trade against the move "
                                             "(short gamma: with the move). This report does not test whether that dampens "
                                             "or amplifies price."),
                HelpTerm("Call wall", "The strike with the largest positive GEX."),
                HelpTerm("Put wall", "The strike with the most negative GEX."),
                HelpTerm("Gamma flip", "The strike where the running sum of GEX over ascending strikes changes sign. If it "
                                       "changes sign several times, the highest such strike is kept. It is not computed "
                                       "when it never changes sign. The OI view also counts landing exactly on zero. This is a "
                                       "different rule from the HVL on the MenthorQ tab (lowest point of the running sum). "
                                       "It is a point on the strike axis, not a re-priced zero-gamma level, and it can sit "
                                       "on the opposite side of spot from one (gex_dex_calculator.py)."),
                HelpTerm("Agreement and disagreement", "When both views put a level at the same strike, two different assumptions "
                                                       "point to the same place. When they differ, the gap can come from the "
                                                       "assumption or from the different data each view reads (trades versus "
                                                       "open interest)."),
            ),
            cases=(
                HelpCase(
                    "Dealer position in the flow view",
                    "If takers bought, the dealer sold. The dealer position is minus the net taker flow.",
                    f"Takers net bought {_int(DEALER_TAKER_NET_CONTRACTS)} calls: dealers are {_int(_DEALER_POSITION)} (short 100)."),
                HelpCase(
                    "Flow-based GEX of one strike",
                    "Gamma is added for calls and puts; the sign comes from the dealer position, so a short dealer gives a red bar.",
                    f"Dealers short 100 calls, gamma 0.00005, spot {_int(EXAMPLE_SPOT)}: 100 x 0.00005 = 0.005; "
                    f"-0.005 x {_int(EXAMPLE_SPOT)} squared x 0.01 = {_int(_FLOW_GEX)} USD per 1 % move."),
                HelpCase(
                    "OI-based GEX of one strike",
                    "Calls minus puts, with the assumption that dealers are long calls and short puts.",
                    f"Call OI 1,000 at gamma 0.00005 and put OI 500 at gamma 0.00003, spot {_int(EXAMPLE_SPOT)}: "
                    f"{_int(net_gex_per_one_percent_usd(GEX_CALL_GAMMA_SUM, GEX_PUT_GAMMA_SUM, EXAMPLE_SPOT))} USD per 1 % move."),
                HelpCase(
                    "Price is above the gamma flip and below the call wall",
                    "Spot is above the flip on the strike axis, with the call wall as the next reference above. Being "
                    "above the flip does not tell whether the summed exposure is positive or negative: the sign of the "
                    "total net GEX (per 1 % move) of the view does, under the assumption.",
                    f"Flip {_int(EXAMPLE_HVL)}, call wall {_int(EXAMPLE_CALL_RESISTANCE)}, spot 72,000: spot is "
                    f"{_int(72_000 - EXAMPLE_HVL)} above the flip and {_int(EXAMPLE_CALL_RESISTANCE - 72_000)} below the wall."),
                HelpCase(
                    "Price is below the gamma flip and above the put wall",
                    "Spot is below the flip on the strike axis, with the put wall as the next reference below. Being "
                    "below the flip does not tell whether the summed exposure is positive or negative; the sign of the "
                    "view's total net GEX does, under the assumption.",
                    f"Flip {_int(EXAMPLE_HVL)}, put wall 65,000, spot 66,000: {_int(EXAMPLE_HVL - 66_000)} below the flip and "
                    f"{_int(66_000 - 65_000)} above the put wall."),
                HelpCase(
                    "Price is above the call wall",
                    "Spot has passed the strike with the largest positive GEX. The wall is behind you. Nothing says what happens next.",
                    f"Call wall {_int(EXAMPLE_CALL_RESISTANCE)}, spot 78,000: {_one(percent_from(78_000, EXAMPLE_CALL_RESISTANCE))} % above the wall."),
                HelpCase(
                    "Price is below the put wall",
                    "Spot has passed the strike with the most negative GEX. Nothing says what happens next.",
                    f"Put wall 65,000, spot 62,000: {_one(abs(percent_from(62_000, 65_000)))} % below the wall."),
                HelpCase(
                    "Price is at the gamma flip",
                    "Price equals the flip; the distance is 0 %. Small moves change which side of the flip spot is on, "
                    "which says nothing about the sign of the exposure. Treat the flip as an approximate point.",
                    f"Flip {_int(EXAMPLE_HVL)} and spot {_int(EXAMPLE_HVL)}: 0 % apart."),
                HelpCase(
                    "The gamma flip says not computed",
                    "The running sum never changed sign, so there is no flip. The code does not invent one.",
                    "Net GEX +1, +2, +3 (millions): running sums +1, +3, +6, never negative, so no flip."),
                HelpCase(
                    "The running sum changes sign more than once",
                    "The highest strike where it does is kept as the flip.",
                    f"Net GEX in millions: {_net_text(GEX_FLIP_EXAMPLE_BY_STRIKE_MILLION)}. Running sums "
                    f"{_cumulative_text(_FLIP_CUMULATIVE)}: it changes sign at 65,000 and at 70,000. Flip {_int(_FLIP_EXAMPLE)}."),
                HelpCase(
                    "The two views agree",
                    "Two different assumptions put the wall at the same strike. That is a little more reason to look at it, "
                    "not proof.",
                    "Flow call wall 75,000 and OI call wall 75,000."),
                HelpCase(
                    "The two views disagree",
                    "Common. The assumption, or the different data each view reads (trades versus open interest), can "
                    "change the answer. There is no ground truth to pick a winner.",
                    f"Flow call wall 73,000 and OI call wall 77,000: {_one(percent_from(77_000, 73_000))} % apart. One real sample "
                    "(2026-11-13) had totals of -3.4M and -69.2M USD per 1 %."),
            ),
            limits=(
                "No ground truth for either view. Dealer positioning is not published.",
                "Flow is read up to the report time but the chain snapshot is the newest stored one. The snapshot time is shown "
                "above the chart; check how old it is.",
                "Only one expiry at a time. Other expiries: scripts/dealer_flow_levels.py.",
                "The flow view assumes zero dealer position at the flow start.",
            ),
        ),
        HelpTopic(
            title="The plausibility check",
            what_you_see=(
                "Under the level table, a sentence that says whether the flow-based view passed its check and, if not, what "
                "failed. A failed check also shows a table of every failing option, the share of the flow-based gamma on "
                "those strikes, and why. The expiry switcher marks a failed expiry with 'check failed'."),
            terms=(
                HelpTerm("Plausibility check", "A dealer cannot hold more than the open interest of an option. The check counts "
                                               "the options whose dealer position, in absolute value, exceeds their open interest."),
                HelpTerm("Coverage", "The share of hours since the flow start for which trades are stored (hours with any "
                                     "trade of the currency, not only this expiry). It must be at least 95 %."),
                HelpTerm("Violations", "Options whose dealer position exceeds their open interest, as a share of the options "
                                       "checked. At most 5 % are allowed."),
                HelpTerm("Options without an open interest value", "Traded options that are not in the chain snapshot or have "
                                                                   "no open interest value, so there is nothing to check against. An "
                                                                   "option with open interest 0 is checked, and any flow against it "
                                                                   "counts as a violation. More than "
                                                                   "20 % of those seen, or all of them, fails the check."),
                HelpTerm("Other reasons", "No valid index price, or no stored trades for the expiry."),
            ),
            cases=(
                HelpCase(
                    "The check passes",
                    "The flow view is plausible by this one test. It is still a model with an assumption.",
                    f"{CHECK_VIOLATIONS_PASSING} of {CHECK_OPTIONS_CHECKED} options exceed their open interest: "
                    f"{_int(_CHECK_PASSING)} %, at most 5 %, with coverage of 100 %. Passes."),
                HelpCase(
                    "Too many violations",
                    "The check failed. The flow view is built on trade history that does not fit the open interest: "
                    "positions before the flow start, a different dealer mix, or a missing hour.",
                    f"{CHECK_VIOLATIONS_FAILING} of {CHECK_OPTIONS_CHECKED} options exceed their open interest: "
                    f"{_int(_CHECK_FAILING)} %, above the 5 % limit. Check failed."),
                HelpCase(
                    "Coverage too low",
                    "Hours of trades are missing, so the dealer position is wrong by an unknown amount.",
                    f"Coverage {_int(CHECK_COVERAGE_LOW * 100)} % is below the 95 % gate."),
                HelpCase(
                    "The table of failing options and the gamma share",
                    "Under a failed check the report lists every option whose dealer position is larger than its open "
                    "interest (excess = |position| - open interest, and |position| / open interest), and the share of the "
                    "expiry's flow-based gamma that sits on those strikes (sum of |flow GEX| at the failing strikes divided "
                    "by the sum of |flow GEX| at all strikes). Failures are not only small far strikes: on one real sample "
                    "(2026-11-14) the failing strikes of the 27NOV26 expiry carried 48.6 % of the flow-based gamma, "
                    "with a put at 65,000 at an inferred dealer position of -4,139.8 contracts against an open interest "
                    "of 174.0 (23.8 x). On that date the check failed on every precomputed BTC expiry, and most had a "
                    "large share: 15NOV26 83.7 %, 18NOV26 100 %, 27NOV26 48.6 %, 25DEC26 31.5 %, 20NOV26 7.3 %.",
                    "|Flow GEX| of the failing strikes adds up to 30 (millions), over all strikes to 80: 30 / 80 = 37.5 %."),
                HelpCase(
                    "Why the flow view fails, and a large share",
                    "The flow method assumes the maker of every trade is a dealer, so the dealer position is minus the "
                    "net taker flow. When customers trade with each other (for example one customer closing against another "
                    "customer's resting order) or a dealer takes a customer's resting order, the trade is given to the wrong "
                    "side. Trades before the flow start or in hours without stored trades are also missing. Then the net "
                    "taker flow at a strike can exceed what is still open, and the inferred dealer position there is wrong "
                    "by an unknown amount. When the failing strikes carry more than 25 % of the gamma (a display threshold "
                    "chosen for this report, not a calibrated limit), the report calls the flow-based levels of that expiry "
                    "unreliable. A gamma-weighted version of the check with the same 5 % limit would fail these expiries "
                    "too (48.6 %, 31.5 %, 7.3 %), so 'less affected' means relatively, not fine.",
                    "On 2026-11-14 the 27NOV26 expiry had 48.6 %: unreliable. The 20NOV26 expiry had 7.3 %: less affected, "
                    "still read with care."),
                HelpCase(
                    "What to do with a failed check",
                    "Do not delete the view and do not trust it blindly. Look at the share of gamma on the failing strikes: "
                    "a large share means treat the flow-based levels of this expiry as unreliable (the open-interest based view "
                    "does not have this problem, but it rests on its own unverified assumption: dealers long calls, short "
                    "puts); a small share means the flow-based levels are less affected, relatively, but still need care.",
                    "On the Levels tab a failed lane is drawn with hollow marks."),
            ),
            limits=(
                "The check tests plausibility, not correctness. A pass does not prove that the dealer assumption is right.",
                "The thresholds are the project's decision D9: coverage at least 95 %, violations at most 5 %, "
                "options without open interest at most 20 %.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Levels
# --------------------------------------------------------------------------

_LEVELS_HELP = SectionHelp(
    section_key="levels",
    summary=(
        "Every source's levels on one price line, one row (lane) per source, with the spot as a black line and a "
        "highlighter band where several sources agree. It brings the other tabs together. Agreement is a coincidence of "
        "methods, not a proof, and no level here predicts price."),
    topics=(
        HelpTopic(
            title="The lanes (sources)",
            what_you_see=(
                "Up to four rows. Each level is a diamond at its price, with its name and price next to it. A source that "
                "cannot be computed is left out with a note, never drawn as a made-up level."),
            terms=(
                HelpTerm("MenthorQ replica (monthly expiries)", "Call resistance, put support and HVL of the monthly expiries "
                                                                "from the newest chain, by the same rules as the MenthorQ tab "
                                                                "(net GEX argmax and argmin; HVL = lowest running net GEX). "
                                                                "Open interest based."),
                HelpTerm("Dealer GEX, open interest", "Call wall, put wall and gamma flip of one expiry, dealers assumed long "
                                                      "calls and short puts. The gamma flip here is a sign change of the "
                                                      "running sum. The expiry selector offers the 4 expiries with the most "
                                                      "open interest and the nearest monthly one (at most 5); the nearest "
                                                      "monthly is shown first."),
                HelpTerm("Dealer GEX, flow-based", "The same three levels from the taker flow. Hollow marks mean its plausibility "
                                                   "check failed."),
                HelpTerm("Option flow (top strikes)", "For each of calls bought, calls sold, puts bought and puts sold over the last "
                                                      "24 hours, the 2 strikes with the most notional (screen plus blocked, "
                                                      "contracts x strike)."),
                HelpTerm("Shared inputs", "The MenthorQ replica and the OI-based dealer lane read the same open interest. The "
                                          "flow-based lane and the option flow lane read the same stored trades. So overlap "
                                          "is partly built in."),
                HelpTerm("Diamond", "One level of one source, at its exact price."),
                HelpTerm("Hollow mark", "That source failed its plausibility check. If the Dealer levels tab says the failing "
                                        "strikes carry a large share of the gamma, treat the flow-based lane as unreliable; "
                                        "otherwise read it with less weight. The Dealer levels tab lists only the 5 nearest "
                                        "expiries, the nearest monthly one and the one with the most open interest, the Levels tab the 4 with the most open interest "
                                        "and the nearest monthly one, so the share is shown there only for the expiries the "
                                        "Dealer levels tab lists."),
            ),
            cases=(
                HelpCase(
                    "A source is missing",
                    "It could not be computed (no data, or the rule gave no answer). It is left out with a note. It is not drawn as zero.",
                    "With no option trades in the last 24 hours, the option flow lane is not drawn and a note says so."),
                HelpCase(
                    "Two levels of one source at the same price",
                    "Their labels are merged so they do not print over each other.",
                    "Put wall and gamma flip both at 68,000 in one lane read 'put wall / gamma flip 68,000'."),
                HelpCase(
                    "Hollow diamonds in a lane",
                    "The lane's plausibility check failed. The levels are still drawn so you can see them.",
                    f"A flow-based lane with {CHECK_VIOLATIONS_FAILING} of {CHECK_OPTIONS_CHECKED} violations "
                    f"({_int(_CHECK_FAILING)} %, above 5 %) is drawn hollow."),
                HelpCase(
                    "The expiry selector changes the dealer lanes",
                    "Choosing an expiry recomputes the two dealer lanes for that expiry. The other lanes stay unchanged.",
                    "Switching from the nearest monthly to another expiry in the list moves the two dealer lanes only."),
                HelpCase(
                    "The zoom buttons",
                    "The buttons plus or minus 5 %, 10 %, 15 % and All only zoom. They change no level. A level beyond the "
                    "zoom is listed at the edge of its row.",
                    f"Zoom plus or minus {_int(LEVELS_ZOOM_SHARE * 100)} % at spot {_int(EXAMPLE_SPOT)} shows "
                    f"{_int(_ZOOM_LOW)} to {_int(_ZOOM_HIGH)}."),
                HelpCase(
                    "The ruler",
                    "A vertical line follows the pointer and shows the price and its distance from spot.",
                    f"The pointer at 75,000 with spot at {_int(EXAMPLE_SPOT)} reads 75,000 (+{_one(percent_from(75_000, EXAMPLE_SPOT))}%)."),
            ),
            limits=(
                "The sources are not independent. Counting lanes overstates the evidence.",
                "The page opens at plus or minus 15 % of the spot. Levels beyond the zoom are listed at the edge of "
                "their row; All levels shows every one, and the ledger lists them all.",
            ),
        ),
        HelpTopic(
            title="Where sources agree (confluence)",
            what_you_see=(
                "A highlighter band over a price range, with the number of sources in it, and a ledger 'Where sources agree' "
                "with every band and the levels in it."),
            terms=(
                HelpTerm("Confluence band", "A price range of at most 1 % of the spot that holds levels from at least 3 "
                                            "different sources. Two levels of one source count once."),
                HelpTerm("3 of 4", "Three of the four lanes have a level in that band. If a lane is missing, the second number is "
                                   "lower."),
                HelpTerm("Band width", "Measured from the lowest level of the band. A band closes when the next level is "
                                       "farther from its first level than the width. Every level is in at most one band."),
            ),
            cases=(
                HelpCase(
                    "Three different sources within 1 % of spot",
                    "A band is drawn. It is a coincidence of methods, not a proof.",
                    f"Spot {_int(EXAMPLE_SPOT)}, so the width is {_int(_BAND_WIDTH)}. Levels at "
                    f"{', '.join(_int(price) for _, price in LEVELS_AGREE_MARKS)} from three lanes: the farthest is "
                    f"{_int(LEVELS_AGREE_MARKS[-1][1] - LEVELS_AGREE_MARKS[0][1])} from the first, within {_int(_BAND_WIDTH)}. One band."),
                HelpCase(
                    "Levels too far apart",
                    "The third level is too far from the first, so the band has two sources and is not drawn.",
                    f"Levels at {', '.join(_int(price) for _, price in LEVELS_TOO_FAR_MARKS)}: the last is "
                    f"{_int(LEVELS_TOO_FAR_MARKS[-1][1] - LEVELS_TOO_FAR_MARKS[0][1])} from the first, more than "
                    f"{_int(_BAND_WIDTH)}. No band."),
                HelpCase(
                    "Many levels, fewer than 3 sources",
                    "Several levels of the same source do not make a band. A band needs 3 different sources.",
                    f"Levels at {', '.join(_int(price) for _, price in LEVELS_SAME_SOURCE_MARKS)} from two sources only "
                    "(one level of the MenthorQ replica, two of option flow). No band."),
                HelpCase(
                    "A band that includes a hollow mark",
                    "One of the agreeing lanes failed its check. Treat the agreement as weaker.",
                    "A band of 3 sources where one lane is hollow is 2 trusted lanes and 1 doubtful lane."),
                HelpCase(
                    "No band at all",
                    "No price has levels from 3 sources within the width. That is a normal result.",
                    "The page says: no price has levels from 3 sources within 1.0 % of the spot."),
            ),
            limits=(
                "Agreement of methods is not agreement with the market.",
                "The band width (1 %) and the number of sources (3) are fixed in the report. Other settings: scripts/levels_overview.py.",
            ),
        ),
        HelpTopic(
            title="Using the levels for a strategy or a hedge",
            what_you_see=(
                "Nothing new on the page. This topic is about what to do with it."),
            terms=(
                HelpTerm("Reference price", "A price you use to place something else (a strike, a stop, a hedge). It is not a target."),
                HelpTerm("Strategy", "A trade built to profit from a view, for example a spread."),
                HelpTerm("Hedge", "A trade built to reduce a risk you already have."),
            ),
            cases=(
                HelpCase(
                    "Structuring a spread",
                    "A level near your idea can help you pick strikes. It does not make the trade work. Whether levels help "
                    "is not shown by this report.",
                    f"A bull call spread of long {_int(STRATEGY_LONG_STRIKE)} and short {_int(STRATEGY_SHORT_STRIKE)} calls, "
                    f"with call resistance at {_int(STRATEGY_SHORT_STRIKE)}, is {_int(STRATEGY_SHORT_STRIKE - STRATEGY_LONG_STRIKE)} "
                    "points wide. The level only helped choose the short strike."),
                HelpCase(
                    "Placing a stop",
                    "Other traders may place orders at the same obvious levels. A stop exactly on a level shares the price with them. "
                    "Use a level as a reference and add your own margin.",
                    f"Put support at {_int(EXAMPLE_PUT_SUPPORT)}: a stop placed 1 % beyond is at "
                    f"{_int(EXAMPLE_PUT_SUPPORT * 0.99)}."),
                HelpCase(
                    "Levels you do not trust",
                    "Hollow marks and single-source levels deserve less weight than a band of "
                    "several lanes. None of them is a target.",
                    "A band of 3 lanes with one hollow is weaker than a band of 3 lanes without one."),
            ),
            limits=(
                "Levels are reference prices for structuring (strikes, stops, hedges), never price targets.",
                "Nothing in this report predicts price.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------

def _points_text(example: Tuple[int, float, float, int, float]) -> str:
    """The five inputs of a confidence example in words."""
    groups, conflict, quality, stale, margin = example
    return (f"{groups} agreeing input groups, {conflict * 100:.0f} % conflict, mean quality {_two(quality)}, "
            f"{stale} stale supporting items, {margin:.0f} points ahead of the next scenario")


_DECISION_HELP = SectionHelp(
    section_key="decision",
    summary=(
        "The decision maker turns selected signals of the other tabs (listed in the evidence table) into evidence items, "
        "builds a price map around the spot "
        "and scores the scenarios that map allows. It says which scenario the evidence supports most, with an uncalibrated "
        "evidence score and an ordinal confidence label that comes with its reasons. It is decision support: not a "
        "forecast, not an order, not a price target. Nothing on this tab is recorded; the report only shows it."),
    topics=(
        HelpTopic(
            title="Evidence and input groups",
            what_you_see=(
                "The evidence table: one row per item, grouped by input group. Each row names its source, its kind, its "
                "direction or regime, its strength and quality (0 to 1, with the reasons that lowered the quality), the time "
                "of its data, and one sentence of what it says and does not say."),
            terms=(
                HelpTerm("Evidence item", "One piece of evidence produced from one of the other tabs' services. Every source "
                                          "is converted into the same form, so the engine treats them alike."),
                HelpTerm("Selection", "Not every signal of the other tabs becomes an item. Dealer levels: the nearest monthly "
                                      "expiry and the one with the most open interest (walls, gamma flip and the sign of the "
                                      "total GEX of each view). MenthorQ replica: call resistance, put support and HVL. Option "
                                      "flow: the top 2 strikes for each of calls bought, calls sold, puts bought and puts sold, "
                                      "and the net flow of screen trades, single-leg blocks and multi-leg blocks. Futures: the 24 hour taker ratio."),
                HelpTerm("Kind: price_level", "A price where a source puts a level (MenthorQ replica levels, dealer walls and "
                                              "gamma flips, option-flow strikes). Only these shape the price map. Support or "
                                              "resistance follows only from the side of the spot the price is on."),
                HelpTerm("Kind: directional", "A lean up (bullish) or down (bearish), or balanced (neutral): net option flow "
                                              "and the futures taker ratio."),
                HelpTerm("Kind: range_regime", "Moves are damped (range-bound) or amplified (expansion). It has no direction. "
                                               "It comes from the sign of the total dealer GEX, a weak, assumption-based hint."),
                HelpTerm("Input group", "The underlying data an item is derived from: deribit_open_interest, deribit_trades or "
                                        "binance_perpetual_candles. Items of one group are derived from the same data and are "
                                        "not independent, so the engine counts each group once."),
                HelpTerm("Strength", "0 to 1: how strongly the item says what it says. A dealer level is scaled by the share "
                                     "of all open interest its expiry holds (full strength at 25 % or more)."),
                HelpTerm("Quality", "0 to 1: how much the item can be trusted. The quality reasons name what lowered it "
                                    "(an assumption, a stale time, a failed check, a model under test)."),
                HelpTerm("As of", "The time of the underlying data, in UTC. An item newer than the report time makes the "
                                  "engine raise an error (a guard against lookahead). The service does not catch it, so "
                                  "the whole tab shows 'This section could not be built'."),
                HelpTerm("Taker band", "The 30-day mean of the hourly rolling 24 hour taker ratio, plus and minus 1 standard "
                                       "deviation (the sample standard deviation). The Futures flow chart draws a band with "
                                       "the population standard deviation, so the two bands differ slightly. Outside the "
                                       "band the item is bullish (above) or bearish (below) with strength |z| / 3, capped at "
                                       "1, where z is the distance from the mean in standard deviations. Inside it is "
                                       "balanced with strength 1 - |z|."),
            ),
            cases=(
                HelpCase(
                    "Two items from one input group",
                    "They count once: the engine takes the quality-weighted mean of their values and multiplies it by the "
                    "quality-weighted mean of their quality. A second item of the same data does not add on top. It moves the "
                    "group's mean (quality-weighted) up or down, and supporting and opposing items of one group cancel inside it.",
                    f"Two items, each with quality {_two(DECISION_ITEM_QUALITY)} and value {_two(DECISION_ITEM_VALUE)}: "
                    f"contribution {_two(_DECISION_TWO_ITEMS_ONE_GROUP)}, the same as one item alone "
                    f"({_two(_DECISION_ONE_ITEM)})."),
                HelpCase(
                    "Items from different input groups",
                    "Each group adds its own contribution (at most 1 per group), so two groups say more than one.",
                    f"The same item in two groups: {_two(_DECISION_ONE_ITEM)} + {_two(_DECISION_ONE_ITEM)} = "
                    f"{_two(_DECISION_TWO_GROUPS)}."),
                HelpCase(
                    "An item is older than its freshness limit",
                    "Its quality is halved, with the reason in the table. Older than four limits, it keeps a tenth. The limit "
                    "is 3 hours for all three input groups (6 hours for a group the settings do not list).",
                    f"Quality {_two(DECISION_ITEM_QUALITY)}, {DECISION_STALE_AGE_HOURS:.0f} hours old: "
                    f"{_two(DECISION_ITEM_QUALITY)} x {DECISION_STALE_QUALITY_FACTOR:g} = {_two(_DECISION_STALE_QUALITY)}. "
                    f"At {DECISION_VERY_STALE_AGE_HOURS:.0f} hours (more than 12): "
                    f"{_two(DECISION_ITEM_QUALITY)} x {DECISION_VERY_STALE_QUALITY_FACTOR:g} = {_two(_DECISION_VERY_STALE_QUALITY)}."),
                HelpCase(
                    "A source is not computed",
                    "The engine runs without it and lists it under 'Sources not computed' with the reason (no stored data, "
                    "too few points for a band). Its items are simply absent; nothing is filled in for them.",
                    f"The taker ratio needs at least {DECISION_TAKER_MINIMUM_POINTS} hourly points in 30 days; with "
                    f"{DECISION_TAKER_POINTS_FOUND} it is listed as not computed."),
                HelpCase(
                    "A source produces no items without an error",
                    "A source can run without error and still have nothing to say: the replica draws no level or every dealer "
                    "level is not computed. It is then neither in the evidence "
                    "table nor under 'Sources not computed', because nothing failed. (No option trades at all in the last "
                    "24 hours is different: that is listed as not computed.)",
                    "A replica that draws no call resistance, put support or HVL adds no row and no line in the list of "
                    "sources not computed."),
                HelpCase(
                    "The flow-based dealer lane failed its check",
                    "When the plausibility check of the Dealer levels tab fails, every flow-based dealer item (walls, gamma "
                    "flip, total-GEX sign) keeps a fifth of its quality, with the reason in the table. The open-interest based "
                    "items are not changed, and neither are the option-flow strikes and the net flow.",
                    f"A flow-based wall of quality {_two(DECISION_WALL_QUALITY)} becomes {_two(DECISION_WALL_QUALITY)} x "
                    f"{DECISION_FAILED_CHECK_QUALITY_FACTOR:g} = {_two(_DECISION_FLOW_FAILED_QUALITY)}."),
            ),
            limits=(
                "Distinct input groups are not independent evidence: open interest is the accumulated result of the trades, "
                "and all three groups react to the same price moves.",
                "Strength and quality are priors chosen for the project. No recorded decision has been compared with what the "
                "price did, so none of them is calibrated.",
                "There are at most three input groups today.",
                "The taker ratio is read as aggressor pressure in its own direction (above its band is bullish), not as a "
                "contrarian signal. It is known to revert to its mean within days.",
                "Two of the three dominant level sources (the MenthorQ replica and the open-interest dealer levels) share the "
                "open interest, so they are counted as one group.",
            ),
        ),
        HelpTopic(
            title="The price map",
            what_you_see=(
                "A table of the nearest meaningful level above and below the spot (support and resistance), the next level "
                "beyond each, the spot, and what is unmapped. A sentence says so when only one side is mapped."),
            terms=(
                HelpTerm("Band", "Price levels within 15 % of the spot are grouped, lowest first, into bands: a band closes "
                                 "when the next price is more than 1 % of the spot from the first price of the band."),
                HelpTerm("Weight", "The sum over a band's input groups of the group's contribution (quality-weighted mean "
                                   "strength times quality-weighted mean quality, at most 1 per group)."),
                HelpTerm("Band price", "The mean of the prices of the band's items, weighted by strength times quality."),
                HelpTerm("Meaningful", f"A band weighing at least {DECISION_MINIMUM_LEVEL_WEIGHT:g} (exactly "
                                       f"{DECISION_MINIMUM_LEVEL_WEIGHT:g} counts). A lone item weighs its strength times its "
                                       f"quality. So the top option-flow strike (strength {_two(DECISION_WALL_STRENGTH)} x quality "
                                       f"{_two(DECISION_FLOW_STRIKE_QUALITY)} = {_two(_DECISION_FLOW_STRIKE_WEIGHT)}) and a dealer "
                                       f"gamma flip at full open-interest share ({_two(DECISION_DEALER_FLIP_STRENGTH)} x "
                                       f"{_two(DECISION_WALL_QUALITY)} = {_two(_DECISION_FLIP_WEIGHT)}) are meaningful alone. A "
                                       f"flow-based wall that failed its check ({_two(_DECISION_FAILED_WALL_WEIGHT)}) and a stale "
                                       f"HVL ({_two(DECISION_HVL_STRENGTH)} x {_two(DECISION_HVL_QUALITY * DECISION_STALE_QUALITY_FACTOR)}"
                                       f" = {_DECISION_STALE_HVL_WEIGHT:.3f}) are not."),
                HelpTerm("Support and resistance", "The nearest meaningful band below the spot, and the nearest above it. "
                                                   "Only the side of the spot decides which is which."),
                HelpTerm("Next level", "The next meaningful band beyond the support or the resistance."),
                HelpTerm("At the spot", "A meaningful band whose price range contains the spot. It is reported, but it "
                                        "bounds neither side."),
                HelpTerm("Unmapped", "No meaningful band on that side within 15 % of the spot. The report says so; it never "
                                     "invents a level."),
            ),
            cases=(
                HelpCase(
                    "One side is unmapped",
                    "The map is one-sided. The scenarios become 'stays above the support' (or 'below the resistance') and the "
                    "break through the mapped side. The unmapped side has no scenario.",
                    f"Spot {_int(EXAMPLE_SPOT)}: levels count within {_int(_DECISION_WINDOW_LOW)} to {_int(_DECISION_WINDOW_HIGH)}. "
                    f"A meaningful support at {_int(DECISION_PUT_WALL)} and nothing meaningful up to "
                    f"{_int(_DECISION_WINDOW_HIGH)}: 'stays above {_int(DECISION_PUT_WALL)}' and 'breaks below "
                    f"{_int(DECISION_PUT_WALL)}'; no break above."),
                HelpCase(
                    "Neither side is mapped",
                    "No scenario can be built. The top line says that no scenario has net support and the confidence is low.",
                    f"No meaningful band between {_int(_DECISION_WINDOW_LOW)} and {_int(_DECISION_WINDOW_HIGH)} around spot "
                    f"{_int(EXAMPLE_SPOT)}."),
                HelpCase(
                    "A weak item of the same group joins a band",
                    "The group's weight is a quality-weighted mean, so a weak item that agrees with a stronger one of its own "
                    "group can lower the band's weight and push it under 0.3. This is a known property of the rule.",
                    f"An HVL (strength {_two(DECISION_HVL_STRENGTH)}, quality {_two(DECISION_HVL_QUALITY)}) weighs "
                    f"{_two(_DECISION_HVL_WEIGHT)}: meaningful. A flip of strength {_two(DECISION_WEAK_FLIP_STRENGTH)} and "
                    f"quality {_two(DECISION_WEAK_FLIP_QUALITY)} from the same group in the same band brings it to "
                    f"{_two(_DECISION_WEAKENED_WEIGHT)}: not meaningful."),
                HelpCase(
                    "A failed flow-based wall alone in a band",
                    "With only a fifth of its quality it does not reach 0.3 by itself, so it is not mapped. It still appears in "
                    "the evidence table.",
                    f"A flow-based wall of strength {_two(DECISION_WALL_STRENGTH)} and quality {_two(_DECISION_FLOW_FAILED_QUALITY)} "
                    f"weighs {_two(group_contribution_value([(_DECISION_FLOW_FAILED_QUALITY, DECISION_WALL_STRENGTH)]))}."),
                HelpCase(
                    "The spot is inside a band",
                    "The price is at a mapped level now. That band is listed 'at the spot' and is neither support nor resistance. "
                    "When it is the only meaningful band, both sides are unmapped and there is no scenario.",
                    f"A band from {_int(EXAMPLE_SPOT - 200)} to {_int(EXAMPLE_SPOT + 200)} at spot {_int(EXAMPLE_SPOT)}."),
            ),
            limits=(
                "A level is where a source puts a level, not a barrier. Whether price reacts to it is not shown by this report.",
                "The 15 % distance, the 1 % band and the weight of 0.3 are priors, not calibrated values.",
                "The map uses only the evidence computed for this report; a source that is not computed leaves no level.",
            ),
        ),
        HelpTopic(
            title="Scenarios, evidence score and confidence label",
            what_you_see=(
                "The line at the top (the best-supported scenario, its label and its score) and a table of every scenario the "
                "map allows with its score, label, supporting and opposing evidence, and the reasons for the label."),
            terms=(
                HelpTerm("Scenario", "Generated from the map: stays between support and resistance (or above the support, or "
                                     "below the resistance when one side is unmapped), breaks above the resistance, breaks below "
                                     "the support. No level on either side gives no scenario."),
                HelpTerm("How an item counts", "Levels in the support or resistance band support 'inside' and oppose the break "
                                               "through them. A bullish lean supports the break up and opposes the break down "
                                               "(a bearish lean the other way round). It also opposes 'inside' at half strength, "
                                               "unless it points toward the unmapped side. Balanced evidence supports 'inside'. "
                                               "A range-bound hint supports 'inside' and opposes each break at half strength. An "
                                               "expansion hint supports each break at half strength; it opposes 'inside' fully "
                                               "when both sides are mapped and at half strength on a one-sided map. A"),
                HelpTerm("Evidence score", "0 to 100. Within each input group the supporting and opposing items net into one "
                                           "contribution (at most plus or minus 1). Support is the sum of the positive group "
                                           "contributions, opposition the sum of the negative ones. Net = support minus "
                                           "opposition (not below 0); score = 100 x net / (net + 1). With three groups net is at "
                                           "most 3, so the score is at most 75 today. Uncalibrated relative support on a stated "
                                           "scale. It is not a probability, and the scores of different scenarios do not add up "
                                           "to 100."),
                HelpTerm("Agreeing groups", "Distinct input groups whose contribution to the scenario is above 0.05. Three or "
                                            "more: +1 point. One or none: -1. Two: 0."),
                HelpTerm("Conflict", "Quality x size of the opposing items over that of all items speaking to the scenario, per "
                                     "item. Above 35 %: -1. Below 10 %: +1. No evidence speaking to it: -1."),
                HelpTerm("Mean quality", "Of the supporting items. At least 0.6: +1. Below 0.35: -1. A scenario with no "
                                         "supporting items skips this criterion and the stale one."),
                HelpTerm("Stale support", "Any supporting item older than its freshness limit: -1."),
                HelpTerm("Margin", "For the top scenario, its score minus the next one: at least 20 points +1, below 10: -1. A "
                                   "scenario that is not on top gets -1. A top scenario with no other scenario: 0."),
                HelpTerm("Confidence label", "Low when at most one input group agrees, the points are -1 or less, or the score "
                                             "is 0. High when at least three groups agree, the points are 2 or more and no "
                                             "supporting item is stale. Otherwise medium. The reasons are listed with the label."),
            ),
            cases=(
                HelpCase(
                    "Two groups agree, wide margin",
                    "The label is medium: high needs three agreeing groups however good the rest looks. With both bounds "
                    "mapped, 'inside' is favoured because the bounding levels support it and oppose each break.",
                    f"Spot {_int(EXAMPLE_SPOT)}, put wall {_int(DECISION_PUT_WALL)} (open interest) and call wall "
                    f"{_int(DECISION_CALL_WALL)} (trades), each strength {_two(DECISION_WALL_STRENGTH)} and quality "
                    f"{_two(DECISION_WALL_QUALITY)}: each contributes {_two(_DECISION_WALL)}, 'inside' has support "
                    f"{_two(_DECISION_INSIDE_SUPPORT)}, score 100 x {_two(_DECISION_INSIDE_SUPPORT)} / "
                    f"({_two(_DECISION_INSIDE_SUPPORT)} + 1) = {_one(_DECISION_INSIDE_SCORE)}. The break up has only opposition "
                    f"({_two(_DECISION_WALL)}), net 0, score {_one(_DECISION_BREAK_UP_SCORE)}. Points: 0 groups (2 of 3), +1 "
                    f"conflict, +1 quality, 0 stale, +1 margin = {_DECISION_MEDIUM_POINTS:+d}: medium."),
                HelpCase(
                    "Three groups agree",
                    "High needs three agreeing groups, 2 points or more and fresh supporting items.",
                    f"{_points_text(DECISION_HIGH_EXAMPLE)}: +1, +1, 0, 0, +1 = {_DECISION_HIGH_POINTS:+d}: "
                    f"{confidence_label(DECISION_HIGH_EXAMPLE[0], _DECISION_HIGH_POINTS, 50.0, DECISION_HIGH_EXAMPLE[3])}."),
                HelpCase(
                    "A supporting item is stale",
                    "The label is capped at medium and the reasons say so: high should not rest on old data.",
                    f"{_points_text(DECISION_CAPPED_EXAMPLE)}: +1, +1, 0, -1, +1 = {_DECISION_CAPPED_POINTS:+d}, but one stale "
                    f"item: "
                    f"{confidence_label(DECISION_CAPPED_EXAMPLE[0], _DECISION_CAPPED_POINTS, 50.0, DECISION_CAPPED_EXAMPLE[3])}."),
                HelpCase(
                    "The evidence conflicts",
                    "A large opposing share costs one point. Here that leaves +1: medium. If every other criterion earns its "
                    "point, high is still possible.",
                    f"{_points_text(DECISION_CONFLICT_EXAMPLE)}: +1, -1, +1, 0, 0 = {_DECISION_CONFLICT_POINTS:+d}: "
                    f"{confidence_label(DECISION_CONFLICT_EXAMPLE[0], _DECISION_CONFLICT_POINTS, 50.0, DECISION_CONFLICT_EXAMPLE[3])}."),
                HelpCase(
                    "Only one input group supports the scenario",
                    "The label is low whatever else holds: one data source is one opinion.",
                    f"{_points_text(DECISION_ONE_GROUP_EXAMPLE)}: {_DECISION_ONE_GROUP_POINTS:+d} points, but only one group: "
                    f"{confidence_label(DECISION_ONE_GROUP_EXAMPLE[0], _DECISION_ONE_GROUP_POINTS, 50.0, DECISION_ONE_GROUP_EXAMPLE[3])}."),
                HelpCase(
                    "No scenario has net support",
                    "The top line says so, the label is low, and no reference decision follows from this evidence. The "
                    "reference range and hedge level, if mapped, are still listed; they describe the map.",
                    "Every scenario has at least as much opposition as support: net 0, score 0."),
                HelpCase(
                    "Two scenarios have the same score",
                    "Equal scores are ordered: inside, then break up, then break down. A tied top scenario has a margin of 0, "
                    "which costs a point (below 10 points).",
                    "Two scenarios at score 40.0: the one listed first is the top scenario, its margin is 0.0 points and the "
                    "margin criterion gives -1."),
            ),
            limits=(
                "Not a forecast: the top scenario is the one the current evidence supports most, not what happens next.",
                "Not calibrated: no recorded decision has been scored against the price path, so the score is not a "
                "probability and the label is ordinal only.",
                "In a quiet market the absence of a signal counts as agreement: balanced flow and a taker ratio inside its "
                "band support 'inside', and can supply the third group for a high label.",
                "With a mapped range, 'inside' is favoured unless directional evidence is strong.",
                "Distinct groups are not independent, so 'three groups agree' is weaker than it sounds.",
            ),
        ),
        HelpTopic(
            title="Decisions, what would change this, and data problems",
            what_you_see=(
                "The decision-support lines exactly as the engine wrote them, the items whose change would change the top "
                "scenario, and the sources that were not computed, the caveats and the age of every input."),
            terms=(
                HelpTerm("Decision line", "A reference range for a strategy, a reference level for a hedge, a condition to "
                                          "watch, or a caveat. It is not an order and not a price target."),
                HelpTerm("What would change this", "Each item is reversed (bullish or bearish items, range hints) or removed "
                                                   "(balanced items, levels) alone and the whole report is recomputed. The items that change the top "
                                                   "scenario are listed with what would then lead."),
                HelpTerm("Sources not computed", "Sources that could not be computed, with the reason. They are left out of the "
                                                 "scores."),
                HelpTerm("Data ages", "The time of the spot, the chain snapshot and the newest item of each source, and their "
                                      "age at the report time."),
            ),
            cases=(
                HelpCase(
                    "One item would change the top scenario",
                    "The top scenario rests on that item. Check its quality reasons and its age before relying on it.",
                    "A row 'if futures.taker_ratio_24h turned bearish' with 'breaks below' as the top scenario then: with that "
                    "one item reversed, another scenario would lead."),
                HelpCase(
                    "No single item changes the top scenario",
                    "The result does not hang on one item. It can still change if several items change together.",
                    "The tab says: no single item's flip or removal changes the top scenario."),
                HelpCase(
                    "The spot is old",
                    "Every distance is measured from the spot the map was built around. An old spot is listed as a data caveat "
                    "when it is more than 3 hours old.",
                    f"A spot of {_int(EXAMPLE_SPOT)} from 5 hours ago: the map is centred on {_int(EXAMPLE_SPOT)}, not on the current "
                    "price."),
            ),
            limits=(
                "The tab shows one run at one instant. This report does not record the decision; scripts/decision_maker.py "
                "with --save stores a snapshot in decision_snapshots.",
                "Changing one item at a time does not show what several changes together would do.",
                "The reference range and the hedge level are listed whatever the top scenario is: they describe the map, not "
                "a recommendation.",
            ),
        ),
    ),
)


SECTION_HELP: Dict[str, SectionHelp] = {
    "menthorq": _MENTHORQ_HELP,
    "futures-flow": _FUTURES_HELP,
    "option-flow": _OPTION_FLOW_HELP,
    "dealer-levels": _DEALER_HELP,
    "levels": _LEVELS_HELP,
    "decision": _DECISION_HELP,
}


def all_help() -> Tuple[SectionHelp, ...]:
    """The report help followed by the help of every tab."""
    return (REPORT_HELP,) + tuple(SECTION_HELP.values())
