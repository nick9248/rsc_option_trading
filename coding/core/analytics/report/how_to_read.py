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
from statistics import mean, stdev
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
MAINTENANCE_MARGIN_RATE = 0.004             # HeatmapSettings.maintenance_margin_rate (default)


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
    """The drawn "GEX Expiring" candidate: net GEX of the expiry over the sum of the absolute net GEX of all expiries, in percent."""
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


def long_liquidation_price(entry: float, leverage: int) -> float:
    """Model liquidation price of a long (liquidation_heatmap_model.py): entry times (1 - 1/leverage + 0.004)."""
    return entry * (1 - 1 / leverage + MAINTENANCE_MARGIN_RATE)


def short_liquidation_price(entry: float, leverage: int) -> float:
    """Model liquidation price of a short: entry times (1 + 1/leverage - 0.004)."""
    return entry * (1 + 1 / leverage - MAINTENANCE_MARGIN_RATE)


def simple_average(values: Sequence[float]) -> float:
    """Plain average (the idea of a moving average)."""
    return mean(values)


def sample_standard_deviation(values: Sequence[float]) -> float:
    """Standard deviation of a few values (how far they usually stray from their average)."""
    return stdev(values)


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
LIQUIDATION_ENTRY = 70_000.0
LIQUIDATION_LEVERAGE = 25
STRATEGY_LONG_STRIKE = 70_000.0
STRATEGY_SHORT_STRIKE = 75_000.0
HEDGE_CLUSTER_PRICE = 66_000.0


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
_RATIO_SPREAD = sample_standard_deviation(FUTURES_RATIO_VALUES)
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
_LONG_LIQUIDATION = long_liquidation_price(LIQUIDATION_ENTRY, LIQUIDATION_LEVERAGE)
_SHORT_LIQUIDATION = short_liquidation_price(LIQUIDATION_ENTRY, LIQUIDATION_LEVERAGE)


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
                                  "taker volume or estimated liquidations. It is a reference price, not a forecast."),
                HelpTerm("Source", "One way of finding levels, for example the MenthorQ replica or the flow-based "
                                   "dealer view. The Levels tab draws one row (lane) per source."),
                HelpTerm("Assumption", "Something the data cannot show, so the method takes it as given. The main one: "
                                       "dealers are long calls and short puts (open-interest view), or the dealer is "
                                       "the other side of every taker (flow view). Deribit does not say who is who."),
                HelpTerm("Replica", "A rebuild of a vendor's chart (MenthorQ). Its formulas are fitted to a few "
                                    "MenthorQ charts, on BTC only. It is not the vendor's own formula."),
                HelpTerm("Forward test", "Testing a model on data that arrives after the model was fixed, so it cannot "
                                         "have been tuned on it. The liquidation estimate is still in a forward test."),
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
                    "The MenthorQ tab uses the close of the last complete UTC day; the Levels tab uses the newest "
                    "chain. A call resistance of 75,000 on one and 76,000 on the other can simply be 14 hours apart."),
                HelpCase(
                    "Several sources show a level at the same price",
                    "It is a coincidence of methods, not a proof. The sources are not independent: several read the "
                    "same open interest or the same trades, so some overlap is built in.",
                    "Three of five lanes at 75,000 sounds strong. If two of those lanes use the same open interest, "
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
        "Replica of MenthorQ's charts, rebuilt from the stored Deribit option chain: IV x OI, net GEX per expiry and "
        "net DEX, with three drawn levels (call resistance, put support, HVL). It is a fitted replica, not MenthorQ's "
        "own formula, and it was fitted on BTC only. The data is the daily close of the last complete UTC day."),
    topics=(
        HelpTopic(
            title="IV x OI chart and its three lines",
            what_you_see=(
                "Horizontal bars per strike: calls filled green, puts as orange outlines, with three dashed lines "
                "(call resistance, put support, HVL) and the spot price. Default: the monthly expiries added together; "
                "a switch shows all listed expiries."),
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
                                "(cumulative_net_gex_argmin). It is the turning point of that running sum. In the "
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
                    "The usual vendor story calls this the calmer zone (positive gamma side). That story is a common "
                    "reading, not something this report tested. The line is a reference for how far the next level is.",
                    f"HVL {_int(EXAMPLE_HVL)}, call resistance {_int(EXAMPLE_CALL_RESISTANCE)}, price 72,000: "
                    f"{_int(EXAMPLE_CALL_RESISTANCE - 72_000)} points below call resistance and "
                    f"{_int(72_000 - EXAMPLE_HVL)} above HVL."),
                HelpCase(
                    "Price is between put support and HVL",
                    "The usual vendor story calls this the faster zone (negative gamma side). Again a common reading "
                    "that depends on the dealer assumption.",
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
                    "Positioning at that strike is largest around price right now. The report does not say what price "
                    "does there. Treat it as a reference price.",
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
                    "This tab uses the last complete UTC day: spot and IV at 23:59 UTC, open interest from 00:00 UTC. "
                    "The Levels tab uses the newest chain. Different instants give different levels.",
                    "Close of 12 Nov (23:59 UTC) against a chain from 13 Nov 14:00 UTC is about 14 hours apart. "
                    "A level of 75,000 versus 76,000 is a timing difference, not an error."),
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
                "Four panels: the first expiry, the next expiry, the expiry with the highest absolute net GEX and the "
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
                HelpTerm("Positive gamma, long gamma", "Dealers hedge against the move: they sell into rises and buy "
                                                       "into falls. That tends to dampen moves."),
                HelpTerm("Negative gamma, short gamma", "Dealers hedge with the move: they buy into rises and sell into "
                                                        "falls. That tends to amplify moves."),
                HelpTerm("Profile line", "The running sum of net GEX over strikes, rescaled so its largest value spans "
                                         "the largest bar. The rescale is an approximation (within 10 % on 11 of 12 "
                                         "transcribed MenthorQ profiles)."),
                HelpTerm("GEX Expiring", "Net GEX of the expiry over the sum of the absolute net GEX of all expiries, in "
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
                    "Under the assumption, dealers are net long gamma at that strike. Their hedging works against "
                    "price moves around it. This is why call resistance, the largest green bar, is called resistance. "
                    "It is a tendency, not a barrier.",
                    f"Net GEX {_int(_NET_GEX)} USD per 1 % move: about that many USD of hedging for a 1 % move at that strike."),
                HelpCase(
                    "A red bar (negative net GEX at a strike)",
                    "Under the assumption, dealers are net short gamma at that strike. Their hedging works with price "
                    "moves around it, which tends to amplify them. Put support is the largest red bar.",
                    f"Puts weigh more than calls: calls 0.02, puts 0.05, spot {_int(EXAMPLE_SPOT)}: "
                    f"(0.02 - 0.05) x {_int(EXAMPLE_SPOT)} squared x 0.01 = "
                    f"{_int(net_gex_per_one_percent_usd(0.02, 0.05, EXAMPLE_SPOT))} USD per 1 % move."),
                HelpCase(
                    "The total net GEX of an expiry is positive or negative",
                    "Positive total: dealers net long gamma in that expiry (under the assumption), a dampening "
                    "environment. Negative total: net short gamma, an amplifying one. One expiry is not the whole "
                    "market: another expiry can point the other way.",
                    "A weekly with a total of -3 and a monthly with +8 (millions of USD) add to +5, but the weekly "
                    "can still behave as short gamma near its strikes."),
                HelpCase(
                    "Which panel is shown",
                    "The first two panels are the next two expiries in time. The last two are the biggest by absolute "
                    "net GEX among the remaining expiries. A panel can say no expiry when there are too few.",
                    "Four expiries in time order with absolute net GEX 30, 10, 60 and 20 (millions): panels one and two "
                    "are the first two expiries (30 and 10); panel three is the biggest of the rest (60), panel four the next (20)."),
                HelpCase(
                    "GEX Expiring in the panel title",
                    "How big this expiry's net GEX is compared with all expiries together (by absolute size). Higher "
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
                "Net DEX bars per strike over all expiries (green positive, red negative), an orange DEX profile, a "
                "yellow GEX profile, the three all-expiry level lines and the spot."),
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
                    "They are the all-expiry net GEX levels (same rule as the IV x OI chart), not a DEX rule.",
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
                "One IV x OI chart per expiry with that expiry's own levels, and a table of open interest per expiry: "
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
                    "Takers sold more than they bought: sellers were the more aggressive side. In the stored 30 days the "
                    "real ratio stayed roughly between 0.8 and 1.15.",
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
                    f"For the three values {', '.join(str(value) for value in FUTURES_RATIO_VALUES)} the spread is "
                    f"{_one(_RATIO_SPREAD)}, so the band is about 1.0 plus or minus {_one(_RATIO_SPREAD)}."),
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
                HelpTerm("Blocked and screen", "Blocked = a block trade (block_trade_id set; in practice 25 BTC or more per "
                                               "leg), negotiated off the screen. Screen = a normal order-book trade."),
                HelpTerm("Notional", "Contracts times the strike, in USD (Laevitas's definition). One contract is 1 BTC. "
                                     "Contracts times the index price would give about 4 % less at a strike of 77,000 and 35 % less at 108,000."),
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
                    "protection, as spread legs, or to sell premium. Notional also inflates far strikes.",
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
                HelpTerm("Label 'single leg'", "The block has one leg."),
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
                "put wall, gamma flip), the totals and a plausibility check. Pick the expiry with the switcher."),
            terms=(
                HelpTerm("Dealer assumption (flow view)", "Every trade has a taker and a maker. The maker is assumed to be a "
                                                          "dealer. So the dealer position of an option is minus the cumulative "
                                                          "taker flow since the start of the flow (Glassnode's taker-flow method). "
                                                          "This is an assumption: Deribit does not say who is a customer or a dealer."),
                HelpTerm("Dealer assumption (OI view)", "Dealers are long calls and short puts over the whole open interest. Also an assumption."),
                HelpTerm("Flow start", "The first stored trade of the expiry, but not before 2026-04-25, when the complete trade "
                                       "history starts. Positions held before that are not known."),
                HelpTerm("GEX per 1 % move", "Flow view: (dealer calls x call gamma + dealer puts x put gamma) x spot squared x 0.01. "
                                             "OI view: (call gamma x OI minus put gamma x OI) x spot squared x 0.01. "
                                             "Positive = dealers long gamma (they trade against moves), negative = short gamma (they chase moves)."),
                HelpTerm("Call wall", "The strike with the largest positive GEX."),
                HelpTerm("Put wall", "The strike with the most negative GEX."),
                HelpTerm("Gamma flip", "The strike where the running sum of GEX over ascending strikes changes sign. If it "
                                       "changes sign several times, the highest such strike is kept. It is not computed "
                                       "when it never changes sign. The OI view also counts landing exactly on zero. This is a "
                                       "different rule from the HVL on the MenthorQ tab (lowest point of the running sum)."),
                HelpTerm("Agreement and disagreement", "When both views put a level at the same strike, two different assumptions "
                                                       "point to the same place. When they differ, the dealer assumption matters "
                                                       "more than the data."),
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
                    "The usual story: net long gamma side, dampening, with the call wall as the next reference. A common "
                    "reading that depends on the assumption.",
                    f"Flip {_int(EXAMPLE_HVL)}, call wall {_int(EXAMPLE_CALL_RESISTANCE)}, spot 72,000: spot is "
                    f"{_int(72_000 - EXAMPLE_HVL)} above the flip and {_int(EXAMPLE_CALL_RESISTANCE - 72_000)} below the wall."),
                HelpCase(
                    "Price is below the gamma flip and above the put wall",
                    "The usual story: net short gamma side, amplifying moves, with the put wall as the next reference.",
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
                    "The regime story is at its turning point; small moves change which side spot is on. Treat the flip as an approximate zone.",
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
                    "Common. The dealer assumption changes the answer. There is no ground truth to pick a winner.",
                    f"Flow call wall 73,000 and OI call wall 77,000: {_one(percent_from(77_000, 73_000))} % apart. One real sample "
                    "(2026-11-13) had totals of -3.4M and -69.2M USD per 1 %."),
            ),
            limits=(
                "No ground truth for either view. Dealer positioning is not published.",
                "Flow is read up to the report time but the chain snapshot is the newest stored one. The snapshot time is shown "
                "under the chart; check how old it is.",
                "Only one expiry at a time. Other expiries: scripts/dealer_flow_levels.py.",
                "The flow view assumes zero dealer position at the flow start.",
            ),
        ),
        HelpTopic(
            title="The plausibility check",
            what_you_see=(
                "A line under the chart that says whether the flow-based view passed its check, and if not, why. The "
                "expiry switcher marks a failed expiry with 'check failed'."),
            terms=(
                HelpTerm("Plausibility check", "A dealer cannot hold more than the open interest of an option. The check counts "
                                               "the options whose dealer position, in absolute value, exceeds their open interest."),
                HelpTerm("Coverage", "The share of hours since the flow start for which trades are stored. It must be at least 95 %."),
                HelpTerm("Violations", "Options whose dealer position exceeds their open interest, as a share of the options "
                                       "checked. At most 5 % are allowed."),
                HelpTerm("Options without open interest", "Traded options that have no open interest to check against. More than "
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
                    "Failures are tiny strikes",
                    "Many failures are far strikes with small numbers. The check counts options, not exposure, so a tiny "
                    "option fails as much as a large one. Look at which strikes failed before discarding the view. Then "
                    "weigh that lane low.",
                    "7 contracts of flow against 0 open interest at a far strike is one failure, the same as a big strike "
                    "with 700 against 500."),
                HelpCase(
                    "What to do with a failed check",
                    "Do not delete the view and do not trust it. Weigh the flow-based lane low, rely on the OI-based lane "
                    "less than usual too (it also has no ground truth), and keep the check result in mind.",
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
                "Up to five rows. Each level is a diamond at its price, with its name and price next to it. A source that "
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
                HelpTerm("Liquidation estimate (Binance)", "A research model, not data. The 2 strongest estimated long liquidation "
                                                           "levels below the reference price and the 2 strongest short levels above "
                                                           "it from the newest stored snapshot, at least 1 % apart. Still in a forward test."),
                HelpTerm("Shared inputs", "The MenthorQ replica and the OI-based dealer lane read the same open interest. The "
                                          "flow-based lane and the option flow lane read the same stored trades. So overlap "
                                          "is partly built in. Only the liquidation estimate uses a different market "
                                          "(Binance futures)."),
                HelpTerm("Diamond", "One level of one source, at its exact price."),
                HelpTerm("Hollow mark", "That source failed its plausibility check. Read it with less weight."),
            ),
            cases=(
                HelpCase(
                    "A source is missing",
                    "It could not be computed (no data, or the rule gave no answer). It is left out with a note. It is not drawn as zero.",
                    "With the liquidation snapshot older than 24 hours, the liquidation lane is not drawn and a note says so."),
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
                "Only prices within 15 % of the spot are drawn in the figure; the footer names the others.",
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
                HelpTerm("3 of 5", "Three of the five lanes have a level in that band. If a lane is missing, the second number is "
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
                    "Many levels, only one source",
                    "Several levels of the same source do not make a band. A band needs 3 different sources.",
                    f"Levels at {', '.join(_int(price) for _, price in LEVELS_SAME_SOURCE_MARKS)} from two sources only. No band."),
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
            title="The liquidation estimate",
            what_you_see=(
                "Up to four diamonds in the liquidation lane: the strongest estimated long liquidation levels below the "
                "reference price and the strongest short liquidation levels above it, with their size in millions of USD."),
            terms=(
                HelpTerm("Liquidation level", "A price where leveraged positions would be closed by force. No exchange "
                                              "publishes them, so they are modelled."),
                HelpTerm("The model", "Each 5-minute rise in Binance perpetual open interest is treated as new positions at "
                                      "that price, spread over leverage tiers. A liquidation price is the entry price "
                                      "adjusted by 1 / leverage and a maintenance margin of 0.4 %. A level is removed when price "
                                      "reaches it. Every step is an assumption."),
                HelpTerm("Long liquidation levels", "Below the price. A long that is liquidated is a forced sell."),
                HelpTerm("Short liquidation levels", "Above the price. A short that is liquidated is a forced buy."),
                HelpTerm("Reference price", "The price of the newest snapshot. It can differ slightly from the spot line."),
            ),
            cases=(
                HelpCase(
                    "A long liquidation level below the price",
                    "If price falls to it, longs are forced to sell. That is fuel for a drop, not a floor. It can act as a "
                    "magnet for price. It gives no direction.",
                    f"A long at 25x opened at {_int(LIQUIDATION_ENTRY)}: {_int(LIQUIDATION_ENTRY)} x (1 - 1/25 + 0.004) = "
                    f"{_int(_LONG_LIQUIDATION)}."),
                HelpCase(
                    "A short liquidation level above the price",
                    "If price rises to it, shorts are forced to buy. That is fuel for a rise, not a ceiling. It can act as "
                    "a magnet. It gives no direction.",
                    f"A short at 25x opened at {_int(LIQUIDATION_ENTRY)}: {_int(LIQUIDATION_ENTRY)} x (1 + 1/25 - 0.004) = "
                    f"{_int(_SHORT_LIQUIDATION)}."),
                HelpCase(
                    "Both sides have strong levels",
                    "Price sits between two pools of forced orders. Neither side is favoured by the model.",
                    f"Long levels near {_int(_LONG_LIQUIDATION)} and short levels near {_int(_SHORT_LIQUIDATION)} around spot {_int(LIQUIDATION_ENTRY)}."),
                HelpCase(
                    "The size label",
                    "The size is the estimated USD at the level's price. It is a model's number, not a count of real positions.",
                    "'longs 300M USD' means 300 million USD of estimated long positions with a liquidation price in that bin."),
            ),
            limits=(
                "A research model with assumed leverage tiers and simplifications (no fees, no maintenance tiers). Still "
                "in a forward test, so treat it as unproven.",
                "A snapshot keeps the bins within 10 % of the reference price.",
                "Liquidation levels act like magnets and fuel. Neither side gives direction.",
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
                    "Hedging a long position",
                    "A cluster of long liquidations below price shows where forced selling may sit. You may want a hedge "
                    "that already works before it, not at it.",
                    f"Long 1 coin at {_int(EXAMPLE_SPOT)} and a long-liquidation cluster at {_int(HEDGE_CLUSTER_PRICE)}, "
                    f"{_one(abs(percent_from(HEDGE_CLUSTER_PRICE, EXAMPLE_SPOT)))} % below. A put struck above "
                    f"{_int(HEDGE_CLUSTER_PRICE)} would already be in the money there."),
                HelpCase(
                    "Placing a stop",
                    "Many other orders sit at obvious levels. A stop exactly on a level shares the price with them. "
                    "Use a level as a reference and add your own margin.",
                    f"Put support at {_int(EXAMPLE_PUT_SUPPORT)}: a stop placed 1 % beyond is at "
                    f"{_int(EXAMPLE_PUT_SUPPORT * 0.99)}."),
                HelpCase(
                    "Levels you do not trust",
                    "Hollow marks, lanes under a forward test and single-source levels deserve less weight than a band of "
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


SECTION_HELP: Dict[str, SectionHelp] = {
    "menthorq": _MENTHORQ_HELP,
    "futures-flow": _FUTURES_HELP,
    "option-flow": _OPTION_FLOW_HELP,
    "dealer-levels": _DEALER_HELP,
    "levels": _LEVELS_HELP,
}


def all_help() -> Tuple[SectionHelp, ...]:
    """The report help followed by the help of every tab."""
    return (REPORT_HELP,) + tuple(SECTION_HELP.values())
