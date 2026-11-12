"""
Point-in-time replication of MenthorQ's BTC (Deribit) "IV * OI" chart,
"NET GEX Multi Expirations" panels and "Net DEX All Expirations" chart from
one stored option-chain snapshot.

MenthorQ publishes only the DEFINITIONS of its levels, not formulas, units
or the expiry set:
  - Net GEX = call gamma minus put gamma.
  - Call Resistance = strike with the most call gamma (all expiries).
  - Put Support = strike with the most put gamma.
  - HVL = inflection of the GEX profile.
  - GEX Expiring % = share of GEX expiring at that date.
  - IV * OI = implied volatility times open interest per strike.

Because the formulas are unknown, this module computes several CANDIDATE
formulas side by side instead of silently picking one, so a comparison
against MenthorQ's published numbers can show which candidate matches.

Conventions (stated, not hidden):
  - Black-Scholes gamma with r = q = 0 (the convention of the rest of this
    package); Deribit settles at 08:00 UTC on the expiry date.
  - ``mark_iv`` is stored in PERCENT; it is divided by 100 for pricing.
    Where it is NULL (BTC snapshots before 2026-08-25 15:11 UTC) the
    service may rebuild it from ``mark_price`` (``fill_missing_implied_volatility``);
    every row then says which source its IV came from (``iv_source``).
  - Contract multiplier is 1 BTC, so open interest is in BTC.
  - Net GEX per strike = (sum call gamma*OI - sum put gamma*OI) * S^2 * 0.01
    (USD per 1% move), exactly the existing ``GexDexCalculator`` formula,
    which is reused here rather than re-implemented.
  - The gamma of each leg is evaluated at either the index price (the
    default, consistent with ``GexDexCalculator``) or the expiry's forward
    (``underlying_price`` in ``snapshots``; Deribit implies mark IV against
    that forward). Both are offered because the choice is not published.
  - Everything is computed as of an explicit ``as_of`` instant -- never the
    wall clock -- so a replay is reproducible and has no lookahead.

Pure: no database or network access. The service layer loads the snapshot.
"""

import calendar
import logging
import math
import re
from datetime import datetime, time, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.gamma_profile_calculator import GammaProfileCalculator
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.market_wide_calculator import DERIBIT_SETTLEMENT_HOUR_UTC

logger = logging.getLogger(__name__)

SECONDS_PER_YEAR = 365.0 * 86400.0
GEX_PER_ONE_PERCENT_MOVE = 0.01
FRIDAY = 4
QUARTERLY_MONTHS = (3, 6, 9, 12)

EXPIRY_UNIVERSE_ALL = "all"
EXPIRY_UNIVERSE_MONTHLY = "monthly"
EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH = "quarterly_plus_front_month"
EXPIRY_UNIVERSES = (
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSE_QUARTERLY_PLUS_FRONT_MONTH,
)

GAMMA_UNDERLYING_INDEX = "index"
GAMMA_UNDERLYING_FORWARD = "forward"
GAMMA_UNDERLYINGS = (GAMMA_UNDERLYING_INDEX, GAMMA_UNDERLYING_FORWARD)

IMPLIED_VOLATILITY_UNIT_DECIMAL = "decimal"
IMPLIED_VOLATILITY_UNIT_PERCENT = "percent"
IV_OI_AGGREGATION_SUM = "sum"
IV_OI_AGGREGATION_MAXIMUM = "maximum"

PANEL_FIRST_EXPIRATION = "first_expiration"
PANEL_NEXT_EXPIRATION = "next_expiration"
PANEL_HIGHEST_GEX = "highest_gex"
PANEL_SECOND_HIGHEST_GEX = "second_highest_gex"
PANEL_NAMES = (
    PANEL_FIRST_EXPIRATION,
    PANEL_NEXT_EXPIRATION,
    PANEL_HIGHEST_GEX,
    PANEL_SECOND_HIGHEST_GEX,
)
PANEL_RANKING_ABSOLUTE_NET = "absolute_net_gex"
PANEL_RANKING_GROSS = "gross_gex"

# Which candidate each drawn level uses, chosen by comparison with
# MenthorQ's charts labelled 2026-11-06 and 2026-10-12 (tuning), replayed at
# 23:59 UTC with open interest from 00:00 UTC (a consistent choice; the
# open-interest instant is only constrained to roughly 21:00 UTC of the
# previous day to about 00:20 UTC): the net-GEX extremes and the
# cumulative-net-GEX minimum matched 24 of 24 per-expiry panel levels and
# 3 of 3 all-expiry levels on each date, but only with a different expiry
# set per date (monthly or quarterly + front month on 2026-11-06, all
# expiries on 2026-10-12; no single set fits both). The call-only /
# put-only gamma maxima (MenthorQ's published wording) matched 8 of 16
# per-expiry levels. The 2026-08-24 "hold-out" (3 of 3 levels on two
# charts) is weak evidence: snapshots.mark_iv is NULL there, so it needs the
# IV rebuilt from mark_price, and the rules were written after its legend
# had been seen. The JSON output carries every candidate.
DEFAULT_LEVEL_CANDIDATES = {
    "call_resistance": "net_gex_argmax_positive",
    "put_support": "net_gex_argmin_negative",
    "hvl": "cumulative_net_gex_argmin",
}
# GEX Expiring % candidate drawn on the panels (and listed by the GUI): net
# GEX of the expiry / sum of |net GEX| over the universe; approximate, see
# ``render_gex_panels_chart``.
DEFAULT_GEX_EXPIRING_CANDIDATE = "net_over_absolute_net_sum"
LEVEL_RULE_DESCRIPTION = (
    "Call Resistance = strike with the highest positive net GEX; Put Support = strike "
    "with the lowest negative net GEX; HVL = strike where cumulative net GEX (summed "
    "from the lowest strike up) is lowest"
)
DELTA_UNDERLYING_FORWARD = "forward"
PROFILE_RESCALE_RULE = (
    "cumulative sum over ascending strikes of the whole chain, multiplied so that its "
    "largest |value| over the whole chain equals the largest |net DEX bar| over the whole chain"
)

IV_SOURCE_MARK_IV = "mark_iv"
IV_SOURCE_REBUILT_FROM_MARK_PRICE = "rebuilt_from_mark_price"
IV_SOURCE_MISSING = "missing"
IMPLIED_VOLATILITY_SEARCH_LOW = 1e-4     # decimal, 0.01% vol
IMPLIED_VOLATILITY_SEARCH_HIGH = 10.0    # decimal, 1000% vol
IMPLIED_VOLATILITY_TOLERANCE = 1e-10     # decimal vol

# The data instant (UTC time of day) of a MenthorQ-style daily close and of a
# MenthorQ chart labelled with that date. Shared by the replay, the backtest
# and the monitor.
DAILY_CLOSE_DATA_TIME_UTC = time(23, 59)
# The three drawn levels (keys of ``select_drawn_levels``).
LEVEL_NAMES = ("call_resistance", "put_support", "hvl")


def iv_source_text(iv_source_counts: Mapping[str, int]) -> str:
    """
    Where the IVs came from, for display (the chart summary, the replay
    report, the monitor preview):
    {} or no rows at all -> "not computed";
    {"mark_iv": 940, ...0} -> "mark_iv (all 940 rows)";
    {"mark_iv": 900, "rebuilt_from_mark_price": 3, "missing": 1} ->
    "mark_iv 900, rebuilt from mark_price 3, missing 1 (of 904 rows)".
    """
    total = sum(iv_source_counts.values())
    if total == 0:
        return "not computed"
    if iv_source_counts.get(IV_SOURCE_MARK_IV, 0) == total:
        return f"mark_iv (all {total:,} rows)"
    return (f"mark_iv {iv_source_counts.get(IV_SOURCE_MARK_IV, 0):,}, "
            f"rebuilt from mark_price {iv_source_counts.get(IV_SOURCE_REBUILT_FROM_MARK_PRICE, 0):,}, "
            f"missing {iv_source_counts.get(IV_SOURCE_MISSING, 0):,} (of {total:,} rows)")


# --------------------------------------------------------------------------
# Expiry handling
# --------------------------------------------------------------------------

# English month names of Deribit labels; strptime's %b would follow the process locale.
EXPIRY_MONTH_NUMBERS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
EXPIRY_LABEL_PATTERN = re.compile(r"(\d{1,2})([A-Za-z]{3})(\d{2})")


def parse_expiry_settlement(expiration: str) -> Optional[datetime]:
    """
    Deribit expiry label DDMONYY (e.g. "27NOV26" or "7NOV26", English month
    names, ``EXPIRY_MONTH_NUMBERS``; the year is 2000 + YY) -> 08:00 UTC
    settlement instant. None when the label does not parse (not a string,
    another shape, an unknown month, or a day the month does not have).
    """
    if not isinstance(expiration, str):
        return None
    match = EXPIRY_LABEL_PATTERN.fullmatch(expiration)
    if match is None:
        return None
    day_text, month_text, year_text = match.groups()
    month = EXPIRY_MONTH_NUMBERS.get(month_text.upper())
    if month is None:
        return None
    try:
        return datetime(2000 + int(year_text), month, int(day_text),
                        DERIBIT_SETTLEMENT_HOUR_UTC, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_utc_timestamp(text: str) -> datetime:
    """ISO timestamp -> UTC-aware datetime; a timestamp without offset is taken as UTC."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def start_of_utc_day(instant: datetime) -> datetime:
    """
    00:00 UTC of the UTC calendar day that contains ``instant``.

    MenthorQ's BTC charts take open interest about one day older than their
    spot and IV: compared on 2026-08-24, 2026-10-12 and 2026-11-06 (data
    instant 23:59 UTC), open interest from the snapshot at or before 00:00
    UTC of the same day matched their bars and levels best. The data only
    constrain that instant to roughly 21:00 UTC of the previous day to
    about 00:20 UTC; 00:00 is a consistent choice, not a proven one. This
    instant is always at or before ``instant``, so using it adds no
    lookahead.

    Raises:
        ValueError: when ``instant`` is naive.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    return instant.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def is_monthly_expiry(settlement: datetime) -> bool:
    """True when the date is the last Friday of its month (Deribit monthly)."""
    days_in_month = calendar.monthrange(settlement.year, settlement.month)[1]
    return settlement.weekday() == FRIDAY and settlement.day + 7 > days_in_month


def is_quarterly_expiry(settlement: datetime) -> bool:
    """True for the last Friday of March, June, September or December."""
    return is_monthly_expiry(settlement) and settlement.month in QUARTERLY_MONTHS


def select_expiry_universe(
    expirations: Iterable[str], universe: str, as_of: datetime
) -> List[str]:
    """
    Expirations still alive at ``as_of`` that belong to ``universe``, in
    chronological order.

    Universes (a candidate, because MenthorQ does not publish its set):
      - "all": every listed expiry (dailies, weeklies, monthlies, quarterlies).
      - "monthly": last-Friday-of-month expiries only.
      - "quarterly_plus_front_month": quarterly expiries plus the nearest
        monthly one.
    """
    if universe not in EXPIRY_UNIVERSES:
        raise ValueError(f"Unknown expiry universe {universe!r}")
    alive: List[Tuple[datetime, str]] = []
    for expiration in set(expirations):
        settlement = parse_expiry_settlement(expiration)
        if settlement is None:
            logger.warning("Skipping unparseable expiry label %r", expiration)
            continue
        if settlement <= as_of:
            continue
        alive.append((settlement, expiration))
    alive.sort()

    if universe == EXPIRY_UNIVERSE_ALL:
        return [expiration for _, expiration in alive]
    monthly = [(settlement, expiration) for settlement, expiration in alive
               if is_monthly_expiry(settlement)]
    if universe == EXPIRY_UNIVERSE_MONTHLY:
        return [expiration for _, expiration in monthly]
    selected = [expiration for settlement, expiration in monthly
                if is_quarterly_expiry(settlement)]
    if monthly and monthly[0][1] not in selected:
        selected.insert(0, monthly[0][1])
    return selected


# --------------------------------------------------------------------------
# Greeks
# --------------------------------------------------------------------------

def compute_leg_greeks(
    underlying_price: Optional[float],
    strike: Optional[float],
    time_to_expiry_years: Optional[float],
    implied_volatility_decimal: Optional[float],
    option_type: Optional[str],
) -> Optional[Dict[str, float]]:
    """
    Black-Scholes gamma and delta (r = q = 0) for one leg.

    Returns None when any input is missing or non-positive -- never a
    fabricated 0.0 (``BlackScholesCalculator`` itself would substitute an IV
    of 0.01 for a non-positive IV, so the inputs are validated here first).

    Args:
        underlying_price: Price the gamma is evaluated at (index or forward).
        strike: Option strike.
        time_to_expiry_years: Years from ``as_of`` to 08:00 UTC settlement.
        implied_volatility_decimal: e.g. 0.45 for 45%.
        option_type: "C" or "P".

    Returns:
        {"gamma": ..., "delta": ...} or None.
    """
    if (underlying_price is None or strike is None or time_to_expiry_years is None
            or implied_volatility_decimal is None):
        return None
    if min(underlying_price, strike, time_to_expiry_years, implied_volatility_decimal) <= 0:
        return None
    kind = (option_type or "").upper()
    if kind not in ("C", "P"):
        return None
    greeks = BlackScholesCalculator(risk_free_rate=0.0).calculate_greeks(
        underlying_price, strike, time_to_expiry_years, implied_volatility_decimal,
        "call" if kind == "C" else "put",
    )
    return {"gamma": greeks["gamma"], "delta": greeks["delta"]}


# --------------------------------------------------------------------------
# Implied volatility fallback (snapshots without mark_iv)
# --------------------------------------------------------------------------

def _standard_normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def black76_price(
    forward: float, strike: float, time_to_expiry_years: float,
    volatility_decimal: float, option_type: str,
) -> float:
    """
    Undiscounted Black-76 option price (r = 0) in the forward's currency
    (USD for BTC). The put comes from put-call parity: P = C - F + K.
    """
    volatility_root_time = volatility_decimal * math.sqrt(time_to_expiry_years)
    d1 = math.log(forward / strike) / volatility_root_time + 0.5 * volatility_root_time
    d2 = d1 - volatility_root_time
    call = forward * _standard_normal_cdf(d1) - strike * _standard_normal_cdf(d2)
    return call if option_type.upper() == "C" else call - forward + strike


def implied_volatility_from_mark_price(
    mark_price_in_underlying: Optional[float],
    forward: Optional[float],
    strike: Optional[float],
    time_to_expiry_years: Optional[float],
    option_type: Optional[str],
) -> Optional[float]:
    """
    Implied volatility in PERCENT (the unit of ``mark_iv``) that reproduces
    a Deribit mark price, by bisection on ``black76_price``.

    Deribit quotes option mark prices in the underlying (BTC), so the USD
    price is ``mark_price_in_underlying * forward`` (the expiry's forward,
    ``snapshots.underlying_price``; Deribit implies mark IV against it).

    Returns None -- never a default vol -- when an input is missing or
    non-positive, or the price is outside the no-arbitrage range
    (intrinsic, forward for a call / strike for a put).
    """
    if (mark_price_in_underlying is None or forward is None or strike is None
            or time_to_expiry_years is None):
        return None
    if min(mark_price_in_underlying, forward, strike, time_to_expiry_years) <= 0:
        return None
    kind = (option_type or "").upper()
    if kind not in ("C", "P"):
        return None
    target = mark_price_in_underlying * forward
    intrinsic = max(0.0, forward - strike) if kind == "C" else max(0.0, strike - forward)
    upper_bound = forward if kind == "C" else strike
    if not intrinsic < target < upper_bound:
        return None

    def price(volatility_decimal: float) -> float:
        return black76_price(forward, strike, time_to_expiry_years, volatility_decimal, kind)

    low, high = IMPLIED_VOLATILITY_SEARCH_LOW, IMPLIED_VOLATILITY_SEARCH_HIGH
    if not price(low) <= target <= price(high):
        return None
    while high - low > IMPLIED_VOLATILITY_TOLERANCE:
        middle = 0.5 * (low + high)
        if price(middle) < target:
            low = middle
        else:
            high = middle
    return 100.0 * 0.5 * (low + high)


def fill_missing_implied_volatility(
    chain_rows: Sequence[Dict[str, Any]], priced_at: datetime
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Explicit, labelled fallback for snapshots without ``mark_iv`` (BTC
    ``snapshots.mark_iv`` is NULL until 2026-08-25 15:11 UTC, while
    ``mark_price`` and ``underlying_price`` are stored).

    A row whose ``mark_iv`` is None, 0 or negative (``compute_leg_greeks``
    gives no gamma for it) but whose ``mark_price`` (BTC) and forward are
    present gets the IV inverted from that price
    (``implied_volatility_from_mark_price``). Every returned row carries
    ``iv_source``: "mark_iv", "rebuilt_from_mark_price" or "missing" (no
    usable IV either way; ``mark_iv`` is set to None).

    Args:
        chain_rows: Snapshot rows (mark_iv in percent, mark_price in BTC).
        priced_at: The instant the mark prices refer to (the snapshot's
            capture time); time to expiry is measured from it.

    Returns:
        (new rows, {"mark_iv": n, "rebuilt_from_mark_price": n, "missing": n}).
    """
    counts = {IV_SOURCE_MARK_IV: 0, IV_SOURCE_REBUILT_FROM_MARK_PRICE: 0, IV_SOURCE_MISSING: 0}
    filled: List[Dict[str, Any]] = []
    for row in chain_rows:
        row = dict(row)
        mark_iv = row.get("mark_iv")
        if mark_iv is not None and mark_iv > 0:
            row["iv_source"] = IV_SOURCE_MARK_IV
        else:
            settlement = parse_expiry_settlement(row.get("expiration", ""))
            rebuilt = implied_volatility_from_mark_price(
                row.get("mark_price"), row.get("underlying_price"), row.get("strike"),
                (settlement - priced_at).total_seconds() / SECONDS_PER_YEAR
                if settlement is not None else None,
                row.get("option_type"),
            )
            row["mark_iv"] = rebuilt
            row["iv_source"] = (IV_SOURCE_REBUILT_FROM_MARK_PRICE if rebuilt is not None
                                else IV_SOURCE_MISSING)
        counts[row["iv_source"]] += 1
        filled.append(row)
    return filled, counts


def build_priced_instruments(
    chain_rows: Sequence[Dict[str, Any]],
    expirations: Iterable[str],
    index_price: float,
    as_of: datetime,
    gamma_underlying: str,
) -> List[Dict[str, Any]]:
    """
    Attach Black-Scholes gamma/delta to the chain rows of ``expirations``,
    in the instrument-dict shape ``GexDexCalculator`` consumes.

    A row whose gamma cannot be computed (missing IV, OI or forward) keeps
    ``gamma``/``delta`` = None, which ``GexDexCalculator`` counts in
    ``instruments_missing_gamma`` instead of treating as zero exposure.
    """
    if gamma_underlying not in GAMMA_UNDERLYINGS:
        raise ValueError(f"Unknown gamma underlying {gamma_underlying!r}")
    wanted = set(expirations)
    instruments: List[Dict[str, Any]] = []
    for row in chain_rows:
        if row.get("expiration") not in wanted:
            continue
        settlement = parse_expiry_settlement(row["expiration"])
        time_to_expiry_years = (
            (settlement - as_of).total_seconds() / SECONDS_PER_YEAR
            if settlement is not None else None
        )
        mark_iv_percent = row.get("mark_iv")
        underlying = (
            index_price if gamma_underlying == GAMMA_UNDERLYING_INDEX
            else row.get("underlying_price")
        )
        greeks = compute_leg_greeks(
            underlying, row.get("strike"), time_to_expiry_years,
            mark_iv_percent / 100.0 if mark_iv_percent is not None else None,
            row.get("option_type"),
        )
        instruments.append({
            "expiration": row["expiration"],
            "strike": row.get("strike"),
            "option_type": (row.get("option_type") or "").upper(),
            "open_interest": row.get("open_interest"),
            "mark_iv": mark_iv_percent,
            "gamma": greeks["gamma"] if greeks else None,
            "delta": greeks["delta"] if greeks else None,
        })
    return instruments


# --------------------------------------------------------------------------
# IV * OI
# --------------------------------------------------------------------------

def compute_iv_times_open_interest(
    chain_rows: Sequence[Dict[str, Any]],
    expirations: Iterable[str],
    implied_volatility_unit: str = IMPLIED_VOLATILITY_UNIT_DECIMAL,
    aggregation: str = IV_OI_AGGREGATION_SUM,
) -> Dict[float, Dict[str, Optional[float]]]:
    """
    IV * OI per strike, calls and puts separately, over ``expirations``.

    Args:
        chain_rows: Snapshot rows (mark_iv in percent, open_interest in BTC).
        expirations: Expiry labels to include.
        implied_volatility_unit: "decimal" (mark_iv / 100) or "percent"
            (mark_iv as stored).
        aggregation: "sum" adds each expiry's IV*OI at a strike; "maximum"
            keeps the largest single-expiry value (what overlapping,
            unstacked per-expiry bars would visually show).

    Returns:
        {strike: {"call": value or None, "put": value or None}}. None means
        no instrument of that type with both IV and OI exists at the strike.
    """
    if implied_volatility_unit == IMPLIED_VOLATILITY_UNIT_DECIMAL:
        divisor = 100.0
    elif implied_volatility_unit == IMPLIED_VOLATILITY_UNIT_PERCENT:
        divisor = 1.0
    else:
        raise ValueError(f"Unknown implied volatility unit {implied_volatility_unit!r}")
    if aggregation not in (IV_OI_AGGREGATION_SUM, IV_OI_AGGREGATION_MAXIMUM):
        raise ValueError(f"Unknown aggregation {aggregation!r}")

    wanted = set(expirations)
    result: Dict[float, Dict[str, Optional[float]]] = {}
    for row in chain_rows:
        if row.get("expiration") not in wanted:
            continue
        strike = row.get("strike")
        mark_iv = row.get("mark_iv")
        open_interest = row.get("open_interest")
        side = {"C": "call", "P": "put"}.get((row.get("option_type") or "").upper())
        if strike is None or side is None or mark_iv is None or open_interest is None:
            continue
        value = mark_iv / divisor * open_interest
        entry = result.setdefault(strike, {"call": None, "put": None})
        if entry[side] is None:
            entry[side] = value
        elif aggregation == IV_OI_AGGREGATION_SUM:
            entry[side] += value
        else:
            entry[side] = max(entry[side], value)
    return dict(sorted(result.items()))


# --------------------------------------------------------------------------
# Level pickers
# --------------------------------------------------------------------------

def strike_of_maximum(
    values_by_strike: Mapping[float, Optional[float]], require_positive: bool = False
) -> Optional[float]:
    """
    Strike with the largest value (None values ignored). With
    ``require_positive`` a maximum that is not > 0 yields None. Ties go to
    the lower strike.
    """
    best_strike, best_value = None, None
    for strike in sorted(values_by_strike):
        value = values_by_strike[strike]
        if value is None:
            continue
        if best_value is None or value > best_value:
            best_strike, best_value = strike, value
    if best_value is None or (require_positive and best_value <= 0):
        return None
    return best_strike


def strike_of_minimum(
    values_by_strike: Mapping[float, Optional[float]], require_negative: bool = False
) -> Optional[float]:
    """
    Strike with the smallest value (None values ignored). With
    ``require_negative`` a minimum that is not < 0 yields None. Ties go to
    the lower strike.
    """
    negated = {strike: (-value if value is not None else None)
               for strike, value in values_by_strike.items()}
    return strike_of_maximum(negated, require_positive=require_negative)


def cumulative_by_strike(values_by_strike: Dict[float, float]) -> Dict[float, float]:
    """Running sum of the values over ascending strikes."""
    running = 0.0
    cumulative: Dict[float, float] = {}
    for strike in sorted(values_by_strike):
        running += values_by_strike[strike]
        cumulative[strike] = running
    return cumulative


def strike_of_cumulative_minimum(net_gex_by_strike: Dict[float, float]) -> Optional[float]:
    """
    HVL candidate (i): the strike where cumulative net GEX (summed from the
    lowest strike upward) is lowest -- the last strike before the
    cumulative curve turns back up, i.e. the turning point of the profile.
    """
    if not net_gex_by_strike:
        return None
    return strike_of_minimum(cumulative_by_strike(net_gex_by_strike))


def nearest_strike(value: Optional[float], strikes: Iterable[float]) -> Optional[float]:
    """Listed strike closest to ``value`` (None when either is missing)."""
    strike_list = list(strikes)
    if value is None or not strike_list:
        return None
    return min(strike_list, key=lambda strike: abs(strike - value))


def compute_spot_sweep_profile(
    instruments: Sequence[Dict[str, Any]], index_price: float, as_of: datetime
) -> Dict[str, Any]:
    """
    HVL candidate (ii): re-price every leg's gamma across a grid of
    hypothetical spots (sticky strike, ``GammaProfileCalculator``) and
    return the net GEX profile, its argmin and its zero crossing.

    Reuses ``GexDexCalculator._build_gamma_legs`` (with the explicit
    ``as_of``) so leg filtering is identical to the live calculator.
    ``GexDexResult`` does not expose the profile grid, which is why it is
    recomputed here rather than read from the GEX result.

    Returns:
        {"profile": [(spot, net_gex_usd_per_1pct)], "argmin_spot": float|None,
         "zero_gamma_level": float|None, "zero_gamma_crossings": [...]}
    """
    legs = GexDexCalculator._build_gamma_legs(list(instruments), as_of=as_of)
    profile_result = GammaProfileCalculator(legs, index_price).calculate()
    profile = [
        (spot, net_gamma * spot * spot * GEX_PER_ONE_PERCENT_MOVE)
        for spot, net_gamma in profile_result["gamma_profile"]
    ]
    argmin_spot = min(profile, key=lambda point: point[1])[0] if profile else None
    return {
        "profile": profile,
        "argmin_spot": argmin_spot,
        "zero_gamma_level": profile_result["zero_gamma_level"],
        "zero_gamma_crossings": list(profile_result["zero_gamma_crossings"]),
    }


# --------------------------------------------------------------------------
# Exposure for one set of instruments (one expiry, or all expiries)
# --------------------------------------------------------------------------

def compute_exposure(
    instruments: Sequence[Dict[str, Any]], index_price: float, as_of: datetime,
    currency: str = "BTC",
) -> Dict[str, Any]:
    """
    Per-strike call/put/net GEX and every level candidate for one set of
    priced instruments, using ``GexDexCalculator`` for the GEX arithmetic.
    ``currency`` is passed to ``GexDexCalculator``, which uses it only as a
    label (GEX is USD whatever the currency); the numbers do not depend on it.

    Returns:
        {
          "strikes": [{"strike", "call_open_interest", "put_open_interest",
                       "call_gex", "put_gex", "net_gex", "cumulative_net_gex"}],
          "total_net_gex", "total_gross_gex", "instruments_missing_gamma",
          "levels": {
            "call_resistance": {"call_gex_argmax", "net_gex_argmax_positive"},
            "put_support": {"put_gex_argmax", "net_gex_argmin_negative"},
            "hvl": {"cumulative_net_gex_argmin", "spot_sweep_gex_argmin",
                    "spot_sweep_gex_argmin_nearest_strike",
                    "spot_sweep_zero_crossing_nearest_spot",
                    "cumulative_sign_change_existing"},
          },
          "spot_sweep_profile": [(spot, net_gex)],
        }
        All GEX values are USD per 1% move.

        When NO instrument has both a gamma and an open interest (e.g. a
        snapshot without mark_iv, or an expiry listed after the
        open-interest snapshot), every GEX value, total and level is None
        ("not computed"): ``GexDexCalculator`` adds a missing gamma or open
        interest as 0, so the pickers would otherwise return the lowest
        strike as a level.
    """
    gex_result = GexDexCalculator(
        list(instruments), index_price, currency=currency, as_of=as_of
    ).calculate()
    has_exposure = any(item.get("gamma") is not None and item.get("open_interest") is not None
                       for item in instruments)
    if not has_exposure:
        return {
            "strikes": [
                {
                    "strike": row.strike,
                    "call_open_interest": row.call_oi,
                    "put_open_interest": row.put_oi,
                    "call_gex": None,
                    "put_gex": None,
                    "net_gex": None,
                    "cumulative_net_gex": None,
                }
                for row in gex_result.strike_rows
            ],
            "total_net_gex": None,
            "total_gross_gex": None,
            "instruments_missing_gamma": gex_result.instruments_missing_gamma,
            "levels": {
                "call_resistance": {"call_gex_argmax": None, "net_gex_argmax_positive": None},
                "put_support": {"put_gex_argmax": None, "net_gex_argmin_negative": None},
                "hvl": {
                    "cumulative_net_gex_argmin": None,
                    "spot_sweep_gex_argmin": None,
                    "spot_sweep_gex_argmin_nearest_strike": None,
                    "spot_sweep_zero_crossing_nearest_spot": None,
                    "cumulative_sign_change_existing": None,
                },
            },
            "spot_sweep_profile": [],
        }
    gex_scale = index_price * index_price * GEX_PER_ONE_PERCENT_MOVE

    strikes = [
        {
            "strike": row.strike,
            "call_open_interest": row.call_oi,
            "put_open_interest": row.put_oi,
            "call_gex": row.call_gamma * gex_scale,
            "put_gex": row.put_gamma * gex_scale,
            "net_gex": row.net_gex,
            "cumulative_net_gex": row.cumulative_gex,
        }
        for row in gex_result.strike_rows
    ]
    call_gex = {row["strike"]: row["call_gex"] for row in strikes}
    put_gex = {row["strike"]: row["put_gex"] for row in strikes}
    net_gex = {row["strike"]: row["net_gex"] for row in strikes}

    sweep = compute_spot_sweep_profile(instruments, index_price, as_of)
    key_levels = gex_result.key_levels

    return {
        "strikes": strikes,
        "total_net_gex": gex_result.total_net_gex,
        "total_gross_gex": sum(call_gex.values()) + sum(put_gex.values()),
        "instruments_missing_gamma": gex_result.instruments_missing_gamma,
        "levels": {
            "call_resistance": {
                "call_gex_argmax": strike_of_maximum(call_gex, require_positive=True),
                "net_gex_argmax_positive": (
                    key_levels.call_resistance.strike
                    if key_levels.call_resistance is not None else None
                ),
            },
            "put_support": {
                "put_gex_argmax": strike_of_maximum(put_gex, require_positive=True),
                "net_gex_argmin_negative": (
                    key_levels.put_support.strike
                    if key_levels.put_support is not None else None
                ),
            },
            "hvl": {
                "cumulative_net_gex_argmin": strike_of_cumulative_minimum(net_gex),
                "spot_sweep_gex_argmin": sweep["argmin_spot"],
                "spot_sweep_gex_argmin_nearest_strike": nearest_strike(
                    sweep["argmin_spot"], net_gex.keys()
                ),
                "spot_sweep_zero_crossing_nearest_spot": sweep["zero_gamma_level"],
                "cumulative_sign_change_existing": key_levels.hvl,
            },
        },
        "spot_sweep_profile": sweep["profile"],
    }


def select_drawn_levels(
    level_candidates: Mapping[str, Mapping[str, Optional[float]]]
) -> Dict[str, Optional[float]]:
    """
    The level drawn for each of call_resistance / put_support / hvl, picked
    from a ``compute_exposure`` "levels" dict by ``DEFAULT_LEVEL_CANDIDATES``
    (the net-GEX rules, ``LEVEL_RULE_DESCRIPTION``). None stays None.
    """
    return {
        level_name: level_candidates[level_name].get(candidate_name)
        for level_name, candidate_name in DEFAULT_LEVEL_CANDIDATES.items()
    }


def expiries_without_any_open_interest(
    chain_rows: Sequence[Dict[str, Any]], expirations: Iterable[str]
) -> List[str]:
    """
    The expiries of ``expirations`` (order kept) that have chain rows but
    not one row with open interest: listed AFTER the open-interest snapshot
    was taken (e.g. a new weekly), so their GEX cannot be computed. This is
    expected, not a data fault. An expiry where only SOME rows lack open
    interest is not returned.
    """
    has_rows: Set[Optional[str]] = set()
    has_open_interest: Set[Optional[str]] = set()
    for row in chain_rows:
        expiration = row.get("expiration")
        has_rows.add(expiration)
        if row.get("open_interest") is not None:
            has_open_interest.add(expiration)
    return [expiration for expiration in expirations
            if expiration in has_rows and expiration not in has_open_interest]


def expiry_not_in_open_interest_snapshot_text(expiration: str) -> str:
    """The note (log line and GUI) for an expiry of ``expiries_without_any_open_interest``."""
    return (f"Expiry {expiration} is not in the open-interest snapshot (listed after it): no open "
            "interest, left out of IV*OI and GEX; its levels show as not computed")


def compute_expiry_iv_times_open_interest(
    chain_rows: Sequence[Dict[str, Any]], expiration: str, exposure: Dict[str, Any]
) -> Dict[str, Any]:
    """
    IV*OI of one expiry with that expiry's own levels.

    Args:
        chain_rows: Snapshot rows (mark_iv in percent, open_interest in BTC).
        expiration: The expiry label, e.g. "27NOV26".
        exposure: ``compute_exposure`` of this expiry's instruments only; its
            levels are selected by the net-GEX rules (``select_drawn_levels``).

    Returns:
        {"expiration", "by_strike" (IV decimal * OI, as
        ``compute_iv_times_open_interest``), "levels", "level_rule"}. Levels
        are None when this expiry's GEX was not computed.
    """
    return {
        "expiration": expiration,
        "by_strike": compute_iv_times_open_interest(chain_rows, [expiration]),
        "levels": select_drawn_levels(exposure["levels"]),
        "level_rule": f"from this expiry's own net GEX only: {LEVEL_RULE_DESCRIPTION}",
    }


def compute_net_delta_exposure(
    instruments: Sequence[Dict[str, Any]], spot: float
) -> Dict[str, Any]:
    """
    Net DEX per strike: spot * sum over legs of delta * OI (BTC), with the
    put delta negative (Black-Scholes sign). Checked against MenthorQ's
    "Net DEX All Expirations" charts of 2026-10-12 and 2026-08-24 with the
    delta evaluated at each expiry's forward: same signs and magnitudes
    (e.g. 80000: 381M ours vs about 370M read off the 2026-10-12 chart).

    A leg without delta or open interest is left out and counted in
    ``instruments_missing_delta``; a strike where no leg contributes gets
    None, never 0.

    Returns:
        {"net_dex_by_strike": {strike: USD or None}, "total_net_dex": USD or
        None (None when no leg contributes), "instruments_missing_delta": n}.
    """
    net_by_strike: Dict[float, Optional[float]] = {}
    missing = 0
    for instrument in instruments:
        strike = instrument.get("strike")
        if strike is None:
            missing += 1
            continue
        net_by_strike.setdefault(strike, None)
        delta = instrument.get("delta")
        open_interest = instrument.get("open_interest")
        if delta is None or open_interest is None:
            missing += 1
            continue
        net_by_strike[strike] = (net_by_strike[strike] or 0.0) + delta * open_interest * spot
    present = [value for value in net_by_strike.values() if value is not None]
    return {
        "net_dex_by_strike": dict(sorted(net_by_strike.items())),
        "total_net_dex": sum(present) if present else None,
        "instruments_missing_delta": missing,
    }


def profile_rescale_factor(
    profile_values: Iterable[Optional[float]], bar_values: Iterable[Optional[float]]
) -> Optional[float]:
    """
    Factor that makes the largest |profile value| equal the largest |bar|
    (None values ignored). None when either side has no non-zero value.
    """
    largest_profile = max((abs(value) for value in profile_values if value is not None), default=0.0)
    largest_bar = max((abs(value) for value in bar_values if value is not None), default=0.0)
    if largest_profile == 0 or largest_bar == 0:
        return None
    return largest_bar / largest_profile


def build_net_dex_chart_data(
    net_dex_by_strike: Mapping[float, Optional[float]],
    net_gex_by_strike: Mapping[float, Optional[float]],
) -> List[Dict[str, Optional[float]]]:
    """
    Rows of the "Net DEX All Expirations" chart over the union of strikes:
    net DEX bar, DEX profile and GEX profile (both ``PROFILE_RESCALE_RULE``:
    cumulative over the whole chain, rescaled to the largest |net DEX bar|).
    A strike without a value contributes nothing to a cumulative sum; a
    profile is None everywhere when it cannot be rescaled (no non-zero value).
    """
    strikes = sorted(set(net_dex_by_strike) | set(net_gex_by_strike))
    cumulative_dex: Dict[float, float] = cumulative_by_strike(
        {strike: net_dex_by_strike.get(strike) or 0.0 for strike in strikes})
    cumulative_gex: Dict[float, float] = cumulative_by_strike(
        {strike: net_gex_by_strike.get(strike) or 0.0 for strike in strikes})
    bars = list(net_dex_by_strike.values())
    has_dex = any(value is not None for value in net_dex_by_strike.values())
    has_gex = any(value is not None for value in net_gex_by_strike.values())
    dex_factor = profile_rescale_factor(cumulative_dex.values(), bars) if has_dex else None
    gex_factor = profile_rescale_factor(cumulative_gex.values(), bars) if has_gex else None
    return [
        {
            "strike": strike,
            "net_dex": net_dex_by_strike.get(strike),
            "cumulative_net_dex": cumulative_dex[strike] if has_dex else None,
            "dex_profile": cumulative_dex[strike] * dex_factor if dex_factor is not None else None,
            "gex_profile": cumulative_gex[strike] * gex_factor if gex_factor is not None else None,
        }
        for strike in strikes
    ]


def compute_gex_expiring_shares(
    totals_by_expiration: Dict[str, Dict[str, Optional[float]]]
) -> Dict[str, Dict[str, Optional[float]]]:
    """
    "GEX Expiring %" candidates for every expiry, in percent.

    Args:
        totals_by_expiration: {expiration: {"net": total net GEX,
            "gross": total |call| + |put| GEX}}.

    Returns:
        {expiration: {"net_over_net_sum", "net_over_absolute_net_sum",
                      "gross_over_gross_sum"}}; a candidate is None when its
        denominator is zero. An expiry whose totals are None (not
        computed) gets None shares and is left out of the denominators.
    """
    computed: Dict[str, Tuple[float, float]] = {}
    for expiration, totals in totals_by_expiration.items():
        net, gross = totals["net"], totals["gross"]
        if net is not None and gross is not None:
            computed[expiration] = (net, gross)
    net_sum = sum(net for net, _ in computed.values())
    absolute_net_sum = sum(abs(net) for net, _ in computed.values())
    gross_sum = sum(gross for _, gross in computed.values())

    def share(numerator: float, denominator: float) -> Optional[float]:
        return 100.0 * numerator / denominator if denominator else None

    shares: Dict[str, Dict[str, Optional[float]]] = {}
    for expiration in totals_by_expiration:
        if expiration not in computed:
            shares[expiration] = {"net_over_net_sum": None, "net_over_absolute_net_sum": None,
                                  "gross_over_gross_sum": None}
            continue
        net, gross = computed[expiration]
        shares[expiration] = {
            "net_over_net_sum": share(net, net_sum),
            "net_over_absolute_net_sum": share(net, absolute_net_sum),
            "gross_over_gross_sum": share(gross, gross_sum),
        }
    return shares


def select_panel_expirations(
    chronological_expirations: Sequence[str],
    totals_by_expiration: Dict[str, Dict[str, Optional[float]]],
    ranking: str = PANEL_RANKING_ABSOLUTE_NET,
) -> Dict[str, Optional[str]]:
    """
    The four MenthorQ panels: first and next expiry (chronological), then
    the highest and second-highest GEX among the remaining expiries.

    ``ranking`` is "absolute_net_gex" (|net GEX|) or "gross_gex" -- a
    candidate, since MenthorQ does not say which "GEX" it ranks by. The
    first/next expiries are excluded from the ranking (otherwise a panel
    would repeat). Expiries whose totals are None (GEX not computed) are
    skipped for every panel.
    """
    if ranking not in (PANEL_RANKING_ABSOLUTE_NET, PANEL_RANKING_GROSS):
        raise ValueError(f"Unknown panel ranking {ranking!r}")

    size_by_expiration: Dict[str, float] = {}
    for expiration in chronological_expirations:
        net = totals_by_expiration[expiration]["net"]
        gross = totals_by_expiration[expiration]["gross"]
        if net is None or gross is None:
            continue
        size_by_expiration[expiration] = abs(net) if ranking == PANEL_RANKING_ABSOLUTE_NET else gross

    computed = [expiration for expiration in chronological_expirations
                if expiration in size_by_expiration]
    first = computed[0] if len(computed) > 0 else None
    following = computed[1] if len(computed) > 1 else None
    remaining = sorted(computed[2:], key=lambda expiration: size_by_expiration[expiration],
                       reverse=True)
    return {
        PANEL_FIRST_EXPIRATION: first,
        PANEL_NEXT_EXPIRATION: following,
        PANEL_HIGHEST_GEX: remaining[0] if len(remaining) > 0 else None,
        PANEL_SECOND_HIGHEST_GEX: remaining[1] if len(remaining) > 1 else None,
    }


# --------------------------------------------------------------------------
# Top-level replication
# --------------------------------------------------------------------------

def replicate_menthorq(
    chain_rows: Sequence[Dict[str, Any]],
    index_price: float,
    as_of: datetime,
    expiry_universe: str = EXPIRY_UNIVERSE_MONTHLY,
    gamma_underlying: str = GAMMA_UNDERLYING_INDEX,
    currency: str = "BTC",
) -> Dict[str, Any]:
    """
    Compute everything both MenthorQ charts show, with candidate formulas.

    Args:
        chain_rows: One snapshot's rows (see
            ``DatabaseRepository.get_option_chain_snapshot_at_or_before``).
        index_price: Index (spot) price at or before ``as_of``.
        as_of: Timezone-aware UTC evaluation instant.
        expiry_universe: Which expiries count (see ``select_expiry_universe``).
        gamma_underlying: "index" or "forward" (see module docstring).
        currency: Underlying ("BTC", "ETH"): the unit labels
            (``settings``) and ``GexDexCalculator``'s label; no number
            depends on it.

    Returns:
        JSON-serialisable dict with "settings", "iv_times_open_interest"
        (per-strike candidates and chart levels), "net_dex_all_expiries"
        (net DEX bars and profiles, see ``compute_net_delta_exposure``) and
        "gex_by_expiration", "gex_expiring_percent", "panels" for the GEX
        panels chart.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    expirations = select_expiry_universe(
        (row["expiration"] for row in chain_rows), expiry_universe, as_of
    )

    iv_times_open_interest = {
        f"{unit}_{aggregation}": compute_iv_times_open_interest(
            chain_rows, expirations, unit, aggregation
        )
        for unit in (IMPLIED_VOLATILITY_UNIT_DECIMAL, IMPLIED_VOLATILITY_UNIT_PERCENT)
        for aggregation in (IV_OI_AGGREGATION_SUM, IV_OI_AGGREGATION_MAXIMUM)
    }
    primary_iv_oi = iv_times_open_interest[
        f"{IMPLIED_VOLATILITY_UNIT_DECIMAL}_{IV_OI_AGGREGATION_SUM}"
    ]

    all_instruments = build_priced_instruments(
        chain_rows, expirations, index_price, as_of, gamma_underlying
    )
    all_expiries_exposure = compute_exposure(all_instruments, index_price, as_of, currency)
    all_expiries_levels = all_expiries_exposure["levels"]
    all_expiries_levels["call_resistance"]["call_iv_oi_argmax"] = strike_of_maximum(
        {strike: values["call"] for strike, values in primary_iv_oi.items()},
        require_positive=True,
    )
    all_expiries_levels["put_support"]["put_iv_oi_argmax"] = strike_of_maximum(
        {strike: values["put"] for strike, values in primary_iv_oi.items()},
        require_positive=True,
    )

    gex_by_expiration: Dict[str, Dict[str, Any]] = {}
    for expiration in expirations:
        expiry_instruments = [item for item in all_instruments
                              if item["expiration"] == expiration]
        gex_by_expiration[expiration] = compute_exposure(
            expiry_instruments, index_price, as_of, currency
        )

    totals = {
        expiration: {"net": exposure["total_net_gex"], "gross": exposure["total_gross_gex"]}
        for expiration, exposure in gex_by_expiration.items()
    }

    forward_priced_instruments = build_priced_instruments(
        chain_rows, expirations, index_price, as_of, DELTA_UNDERLYING_FORWARD
    )
    net_delta = compute_net_delta_exposure(forward_priced_instruments, index_price)
    net_dex_chart_rows = build_net_dex_chart_data(
        net_delta["net_dex_by_strike"],
        {row["strike"]: row["net_gex"] for row in all_expiries_exposure["strikes"]},
    )
    return {
        "settings": {
            "as_of_utc": as_of.isoformat(),
            "index_price": index_price,
            "expiry_universe": expiry_universe,
            "gamma_underlying": gamma_underlying,
            "expirations": expirations,
            "currency": currency,
            "gex_units": f"USD per 1% move: gamma * OI({currency}) * S^2 * 0.01",
            "dex_units": f"USD: delta at the expiry's forward * OI({currency}) * index price",
        },
        "net_dex_all_expiries": {
            "delta_underlying": DELTA_UNDERLYING_FORWARD,
            "total_net_dex": net_delta["total_net_dex"],
            "instruments_missing_delta": net_delta["instruments_missing_delta"],
            "levels": select_drawn_levels(all_expiries_levels),
            "level_rule": f"all-expiry net GEX: {LEVEL_RULE_DESCRIPTION}",
            "profile_rule": PROFILE_RESCALE_RULE,
            "strikes": net_dex_chart_rows,
        },
        "iv_times_open_interest": {
            "by_candidate": iv_times_open_interest,
            "levels": all_expiries_levels,
            "all_expiries_exposure": {
                "total_net_gex": all_expiries_exposure["total_net_gex"],
                "total_gross_gex": all_expiries_exposure["total_gross_gex"],
                "strikes": all_expiries_exposure["strikes"],
                "instruments_missing_gamma": all_expiries_exposure["instruments_missing_gamma"],
            },
        },
        "gex_by_expiration": gex_by_expiration,
        "gex_expiring_percent": compute_gex_expiring_shares(totals),
        "panels": {
            PANEL_RANKING_ABSOLUTE_NET: select_panel_expirations(
                expirations, totals, PANEL_RANKING_ABSOLUTE_NET
            ),
            PANEL_RANKING_GROSS: select_panel_expirations(
                expirations, totals, PANEL_RANKING_GROSS
            ),
        },
    }


# --------------------------------------------------------------------------
# Comparison with a reference (e.g. numbers read off MenthorQ screenshots)
# --------------------------------------------------------------------------

def fit_scale_factor(
    ours: Sequence[float], theirs: Sequence[float]
) -> Optional[Dict[str, Optional[float]]]:
    """
    Least-squares scale through the origin, theirs ~= scale * ours.

    Returns:
        {"scale", "r_squared", "root_mean_square_error",
         "median_absolute_relative_error", "point_count"} or None when there
        are fewer than two points or ``ours`` is all zero.
    """
    if len(ours) != len(theirs):
        raise ValueError("ours and theirs must have the same length")
    if len(ours) < 2:
        return None
    sum_ours_squared = sum(value * value for value in ours)
    if sum_ours_squared == 0:
        return None
    scale = sum(our_value * their_value for our_value, their_value in zip(ours, theirs)) / sum_ours_squared
    residuals = [their_value - scale * our_value for our_value, their_value in zip(ours, theirs)]
    mean_theirs = sum(theirs) / len(theirs)
    total_variance = sum((their_value - mean_theirs) ** 2 for their_value in theirs)
    residual_variance = sum(residual * residual for residual in residuals)
    relative_errors = sorted(
        abs(residual) / abs(their_value) for residual, their_value in zip(residuals, theirs) if their_value != 0
    )
    median_relative = (
        relative_errors[len(relative_errors) // 2] if relative_errors else None
    )
    return {
        "scale": scale,
        "r_squared": 1.0 - residual_variance / total_variance if total_variance else None,
        "root_mean_square_error": math.sqrt(residual_variance / len(ours)),
        "median_absolute_relative_error": median_relative,
        "point_count": len(ours),
    }


def _expiration_for_reference_date(
    reference_date: str, expirations: Iterable[str]
) -> Optional[str]:
    """Our expiry label whose settlement date equals an ISO date like "2026-11-27"."""
    for expiration in expirations:
        settlement = parse_expiry_settlement(expiration)
        if settlement is not None and settlement.date().isoformat() == reference_date:
            return expiration
    return None


def compare_with_reference(
    replication: Dict[str, Any], reference: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Side-by-side comparison of every candidate with a reference.

    ``reference`` shape (all numbers as read off the MenthorQ charts):
        {"iv_times_open_interest": {"levels": {"call_resistance", "put_support",
             "hvl"}, "bars": {"<strike>": {"call": x, "put": y or null}}},
         "gex_panels": {"<panel name>": {"expiration_date": "YYYY-MM-DD",
             "call_resistance", "put_support", "hvl", "gex_expiring_percent",
             "net_gex_bars": {"<strike>": value}}}}

    Returns:
        {"iv_times_open_interest": {"levels": {...}, "scale_fits": {...}},
         "gex_panels": {panel: {...}}}. Each level entry lists every
        candidate's value and whether it equals the reference strike.
    """
    comparison: Dict[str, Any] = {}

    iv_reference = reference.get("iv_times_open_interest", {})
    iv_levels = replication["iv_times_open_interest"]["levels"]
    comparison_levels = {}
    for level_name, reference_value in iv_reference.get("levels", {}).items():
        candidates = iv_levels.get(level_name, {})
        comparison_levels[level_name] = {
            "reference": reference_value,
            "candidates": {
                name: {"ours": value, "matches": value == reference_value}
                for name, value in candidates.items()
            },
        }
    scale_fits = {}
    for candidate_name, by_strike in replication["iv_times_open_interest"]["by_candidate"].items():
        ours_values: List[float] = []
        reference_values: List[float] = []
        points_missing_ours = 0
        for strike_text, sides in iv_reference.get("bars", {}).items():
            ours_sides = by_strike.get(float(strike_text), {"call": None, "put": None})
            for side in ("call", "put"):
                if sides.get(side) is None:
                    continue
                if ours_sides[side] is None:
                    points_missing_ours += 1
                    continue
                ours_values.append(ours_sides[side])
                reference_values.append(sides[side])
        scale_fits[candidate_name] = {
            "fit": fit_scale_factor(ours_values, reference_values),
            "reference_points_without_our_value": points_missing_ours,
        }
    comparison["iv_times_open_interest"] = {
        "levels": comparison_levels, "scale_fits": scale_fits,
    }

    panels_comparison = {}
    expiring = replication["gex_expiring_percent"]
    panel_choice = replication["panels"][PANEL_RANKING_ABSOLUTE_NET]
    for panel_name, panel_reference in reference.get("gex_panels", {}).items():
        expiration = _expiration_for_reference_date(
            panel_reference["expiration_date"], replication["gex_by_expiration"]
        )
        entry: Dict[str, Any] = {
            "reference_expiration_date": panel_reference["expiration_date"],
            "our_expiration_for_that_date": expiration,
            "our_panel_choice": {
                ranking: replication["panels"][ranking].get(panel_name)
                for ranking in replication["panels"]
            },
            "panel_choice_matches": expiration is not None
            and panel_choice.get(panel_name) == expiration,
        }
        if expiration is not None:
            exposure = replication["gex_by_expiration"][expiration]
            for level_name in ("call_resistance", "put_support", "hvl"):
                reference_value = panel_reference.get(level_name)
                entry[level_name] = {
                    "reference": reference_value,
                    "candidates": {
                        name: {"ours": value, "matches": value == reference_value}
                        for name, value in exposure["levels"][level_name].items()
                    },
                }
            entry["gex_expiring_percent"] = {
                "reference": panel_reference.get("gex_expiring_percent"),
                "candidates": expiring[expiration],
            }
            net_by_strike = {row["strike"]: row["net_gex"] for row in exposure["strikes"]}
            bars = panel_reference.get("net_gex_bars", {})
            ours_bars = [net_by_strike.get(float(strike_text)) for strike_text in bars]
            present_bars = [value for value in ours_bars if value is not None]
            if bars and len(present_bars) == len(ours_bars):
                entry["net_gex_bars"] = {
                    "reference": bars,
                    "ours_usd_per_1pct": dict(zip(bars, present_bars)),
                    "scale_fit_to_usd_per_1pct": fit_scale_factor(
                        present_bars, list(bars.values())
                    ),
                }
        panels_comparison[panel_name] = entry
    comparison["gex_panels"] = panels_comparison
    return comparison
