"""
Point-in-time replication of MenthorQ's BTC (Deribit) "IV * OI" chart and
"NET GEX Multi Expirations" panels from one stored option-chain snapshot.

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
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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


# --------------------------------------------------------------------------
# Expiry handling
# --------------------------------------------------------------------------

def parse_expiry_settlement(expiration: str) -> Optional[datetime]:
    """
    Deribit expiry label (e.g. "27NOV26" or "7NOV26") -> 08:00 UTC
    settlement instant. None when the label does not parse.
    """
    try:
        expiry_date = datetime.strptime(expiration, "%d%b%y")
    except (TypeError, ValueError):
        return None
    return expiry_date.replace(
        hour=DERIBIT_SETTLEMENT_HOUR_UTC, minute=0, second=0, microsecond=0,
        tzinfo=timezone.utc,
    )


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
    instruments: Sequence[Dict[str, Any]], index_price: float, as_of: datetime
) -> Dict[str, Any]:
    """
    Per-strike call/put/net GEX and every level candidate for one set of
    priced instruments, using ``GexDexCalculator`` for the GEX arithmetic.

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
    """
    gex_result = GexDexCalculator(
        list(instruments), index_price, currency="BTC", as_of=as_of
    ).calculate()
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


def compute_gex_expiring_shares(
    totals_by_expiration: Dict[str, Dict[str, float]]
) -> Dict[str, Dict[str, Optional[float]]]:
    """
    "GEX Expiring %" candidates for every expiry, in percent.

    Args:
        totals_by_expiration: {expiration: {"net": total net GEX,
            "gross": total |call| + |put| GEX}}.

    Returns:
        {expiration: {"net_over_net_sum", "net_over_absolute_net_sum",
                      "gross_over_gross_sum"}}; a candidate is None when its
        denominator is zero.
    """
    net_sum = sum(totals["net"] for totals in totals_by_expiration.values())
    absolute_net_sum = sum(abs(totals["net"]) for totals in totals_by_expiration.values())
    gross_sum = sum(totals["gross"] for totals in totals_by_expiration.values())

    def share(numerator: float, denominator: float) -> Optional[float]:
        return 100.0 * numerator / denominator if denominator else None

    return {
        expiration: {
            "net_over_net_sum": share(totals["net"], net_sum),
            "net_over_absolute_net_sum": share(totals["net"], absolute_net_sum),
            "gross_over_gross_sum": share(totals["gross"], gross_sum),
        }
        for expiration, totals in totals_by_expiration.items()
    }


def select_panel_expirations(
    chronological_expirations: Sequence[str],
    totals_by_expiration: Dict[str, Dict[str, float]],
    ranking: str = PANEL_RANKING_ABSOLUTE_NET,
) -> Dict[str, Optional[str]]:
    """
    The four MenthorQ panels: first and next expiry (chronological), then
    the highest and second-highest GEX among the remaining expiries.

    ``ranking`` is "absolute_net_gex" (|net GEX|) or "gross_gex" -- a
    candidate, since MenthorQ does not say which "GEX" it ranks by. The
    first/next expiries are excluded from the ranking (otherwise a panel
    would repeat).
    """
    if ranking == PANEL_RANKING_ABSOLUTE_NET:
        def size(expiration: str) -> float:
            return abs(totals_by_expiration[expiration]["net"])
    elif ranking == PANEL_RANKING_GROSS:
        def size(expiration: str) -> float:
            return totals_by_expiration[expiration]["gross"]
    else:
        raise ValueError(f"Unknown panel ranking {ranking!r}")

    first = chronological_expirations[0] if len(chronological_expirations) > 0 else None
    following = chronological_expirations[1] if len(chronological_expirations) > 1 else None
    remaining = sorted(chronological_expirations[2:], key=size, reverse=True)
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

    Returns:
        JSON-serialisable dict with "settings", "iv_times_open_interest"
        (per-strike candidates and chart levels) and "gex_by_expiration",
        "gex_expiring_percent", "panels" for the GEX panels chart.
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
    all_expiries_exposure = compute_exposure(all_instruments, index_price, as_of)
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
            expiry_instruments, index_price, as_of
        )

    totals = {
        expiration: {"net": exposure["total_net_gex"], "gross": exposure["total_gross_gex"]}
        for expiration, exposure in gex_by_expiration.items()
    }
    return {
        "settings": {
            "as_of_utc": as_of.isoformat(),
            "index_price": index_price,
            "expiry_universe": expiry_universe,
            "gamma_underlying": gamma_underlying,
            "expirations": expirations,
            "gex_units": "USD per 1% move: gamma * OI(BTC) * S^2 * 0.01",
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
    scale = sum(o * t for o, t in zip(ours, theirs)) / sum_ours_squared
    residuals = [t - scale * o for o, t in zip(ours, theirs)]
    mean_theirs = sum(theirs) / len(theirs)
    total_variance = sum((t - mean_theirs) ** 2 for t in theirs)
    residual_variance = sum(residual * residual for residual in residuals)
    relative_errors = sorted(
        abs(residual) / abs(t) for residual, t in zip(residuals, theirs) if t != 0
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
