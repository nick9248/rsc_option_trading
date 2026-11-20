"""
Extractors: turn the outputs of the existing services into ``EvidenceItem`` values. Nothing is recomputed a second way:
each function takes what a service already produced (``DealerFlowLevels``, a ``StrikeFlow``, block legs, the rolling taker
ratio, the MenthorQ replica levels) and only translates it, adding the quality discounts that
the source itself reports (a failed plausibility check, a multi-leg block).

Input groups (items of one group are NOT independent):
* ``deribit_open_interest``: MenthorQ replica levels and open-interest dealer walls and their total GEX sign (all from
  the open interest of the Deribit chain snapshot);
* ``deribit_trades``: flow-based dealer walls and total GEX, option flow strikes and net taker flow (all from the stored
  Deribit trades);
* ``binance_perpetual_candles``: the futures taker ratio (taker volume of the Binance perpetual klines).

Every function returns items only for what was computed; a missing input gives no item, never a made-up one.
"""

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.evidence import (
    BEARISH, BULLISH, DIRECTIONAL, EXPANSION, NEUTRAL, PRICE_LEVEL, RANGE_BOUND, RANGE_REGIME, EvidenceItem)
from coding.core.analytics.menthorq_replication_calculator import is_monthly_expiry, parse_expiry_settlement
from coding.core.analytics.open_interest.expiry_open_interest import ExpiryOpenInterest
from coding.core.analytics.option_flow.block_structure import BlockTradeLeg, describe_blocks
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels
from coding.core.analytics.option_flow.strike_flow import StrikeFlow, series_key
from coding.service.option_flow.level_overview_service import top_strikes

OPEN_INTEREST_GROUP = "deribit_open_interest"
TRADES_GROUP = "deribit_trades"
BINANCE_CANDLES_GROUP = "binance_perpetual_candles"

MENTHORQ_SOURCE = "MenthorQ replica (monthly expiries)"
OPTION_FLOW_SOURCE = "Option flow (Deribit taker trades)"
TAKER_RATIO_SOURCE = "Futures taker ratio (Binance perpetual)"
OPEN_INTEREST_ASSUMPTION = "assumption-based: dealers assumed long every call and short every put of the open interest"
FLOW_ASSUMPTION = "assumption-based: every taker assumed a customer and every maker a dealer"


def _days_until(expiry: str, now: datetime) -> Optional[float]:
    settlement = parse_expiry_settlement(expiry)
    return max(0.0, (settlement - now).total_seconds() / 86400) if settlement is not None else None


def menthorq_evidence(drawn: Dict[str, Optional[float]], expirations: Sequence[str], captured_at: datetime,
                      now: datetime) -> List[EvidenceItem]:
    """
    The MenthorQ replica's call resistance, put support and HVL (``select_drawn_levels`` of ``replicate_menthorq``) as
    price levels. They are strike-axis points read from open interest, not regime boundaries.

    Args:
        drawn: ``select_drawn_levels`` output (call_resistance / put_support / hvl, None when not computed).
        expirations: The monthly expiries the replica used, soonest first (the horizon is the soonest).
        captured_at: The chain snapshot time.
        now: The report time.
    """
    horizon = _days_until(expirations[0], now) if expirations else None
    names = {"call_resistance": ("call resistance", 1.0), "put_support": ("put support", 1.0),
             "hvl": ("HVL", config.MENTHORQ_FLIP_STRENGTH)}
    items = []
    for key, (name, strength) in names.items():
        price = drawn.get(key)
        if price is None:
            continue
        items.append(EvidenceItem(
            id=f"menthorq.{key}", source=MENTHORQ_SOURCE, input_group=OPEN_INTEREST_GROUP, kind=PRICE_LEVEL,
            statement=(f"The MenthorQ replica puts its {name} at {price:,.0f}: a strike where open-interest gamma "
                       "concentrates (or, for the HVL, where its running sum changes sign). It does not say the price "
                       "will stop there, and it is a strike-axis point, not a regime boundary."),
            as_of=captured_at, strength=strength, quality=config.MENTHORQ_LEVEL_QUALITY, price=price, horizon_days=horizon,
            quality_reasons=("replica of a published open-interest method; open interest does not show who holds what",),
            details={"level": key, "expirations": list(expirations)}))
    return items


def _share_strength(row: Optional[ExpiryOpenInterest]) -> Tuple[float, Optional[str]]:
    """Strength from the expiry's open-interest share, and a quality reason when the share is not computed."""
    if row is None or row.share is None:
        return 1.0, "open-interest share of the expiry not computed: strength not scaled by it"
    return min(1.0, row.share / config.OPEN_INTEREST_SHARE_FOR_FULL_STRENGTH), None


def dealer_evidence(levels: DealerFlowLevels, open_interest_row: Optional[ExpiryOpenInterest],
                    now: datetime) -> List[EvidenceItem]:
    """
    Dealer GEX of one expiry (``DealerFlowLevelsService.levels``): the open-interest and flow-based walls and gamma flips
    as price levels, and the sign of each total GEX as a weak, assumption-based regime hint. The expiry's share of all
    open interest scales the strength; its days left are the horizon. The flow-based items lose quality automatically when
    the service's plausibility check fails.
    """
    expiry = levels.expiry
    strength_scale, share_reason = _share_strength(open_interest_row)
    horizon = (open_interest_row.days_left if open_interest_row is not None and open_interest_row.days_left is not None
               else _days_until(expiry, now))
    share_text = (f"{open_interest_row.share:.0%} of all open interest" if open_interest_row is not None
                  and open_interest_row.share is not None else "share of open interest not computed")
    flow_quality, flow_reasons = config.FLOW_DEALER_LEVEL_QUALITY, [FLOW_ASSUMPTION]
    if not levels.gate_passed:
        flow_quality *= config.FAILED_CHECK_QUALITY_FACTOR
        flow_reasons.append(f"plausibility check failed ({levels.gate_reason}), quality x{config.FAILED_CHECK_QUALITY_FACTOR:g}")
    check = {"gate_passed": levels.gate_passed, "violation_rate": levels.violation_rate, "violations": levels.violations,
             "options_checked": levels.options_checked, "coverage": levels.coverage}
    items: List[EvidenceItem] = []
    views = (
        ("open_interest", "open interest", OPEN_INTEREST_GROUP, config.OPEN_INTEREST_DEALER_LEVEL_QUALITY,
         [OPEN_INTEREST_ASSUMPTION], levels.open_interest_call_wall, levels.open_interest_put_wall,
         levels.open_interest_gamma_flip, levels.total_open_interest_gex, config.OPEN_INTEREST_REGIME_QUALITY),
        ("flow", "flow-based", TRADES_GROUP, flow_quality, flow_reasons, levels.flow_call_wall, levels.flow_put_wall,
         levels.flow_gamma_flip, levels.total_flow_gex if levels.trades_used > 0 else None,
         config.FLOW_REGIME_QUALITY * (1.0 if levels.gate_passed else config.FAILED_CHECK_QUALITY_FACTOR)),
    )
    for view, view_name, group, quality, reasons, call_wall, put_wall, flip, total, regime_quality in views:
        source = f"Dealer GEX {expiry}, {view_name}"
        level_reasons = tuple(reasons + ([share_reason] if share_reason else []))
        for key, name, price, base_strength in (("call_wall", "call wall", call_wall, 1.0),
                                                ("put_wall", "put wall", put_wall, 1.0),
                                                ("gamma_flip", "gamma flip", flip, config.DEALER_FLIP_STRENGTH)):
            if price is None:
                continue
            items.append(EvidenceItem(
                id=f"dealer.{expiry}.{view}.{key}", source=source, input_group=group, kind=PRICE_LEVEL,
                statement=(f"The {view_name} dealer GEX of {expiry} ({share_text}) has its {name} at {price:,.0f}. "
                           "It is where the assumed dealer gamma concentrates; it does not say the price will stop there."),
                as_of=levels.chain_captured_at, strength=base_strength * strength_scale, quality=quality, price=price,
                horizon_days=horizon, quality_reasons=level_reasons, details={"expiry": expiry, "view": view, **check}))
        if total is None or not math.isfinite(total) or total == 0:
            continue
        regime = RANGE_BOUND if total > 0 else EXPANSION
        meaning = ("dealers would be long gamma, which damps moves" if total > 0
                   else "dealers would be short gamma, which can amplify moves")
        items.append(EvidenceItem(
            id=f"dealer.{expiry}.{view}.total_gex_sign", source=source, input_group=group, kind=RANGE_REGIME,
            statement=(f"The total {view_name} GEX of {expiry} is {total / 1e6:+,.1f}M USD per 1 %: under the stated "
                       f"assumption {meaning}. A weak hint; it says nothing about direction or levels."),
            as_of=levels.chain_captured_at, strength=config.REGIME_HINT_STRENGTH * strength_scale, quality=regime_quality,
            regime=regime, horizon_days=horizon, quality_reasons=level_reasons,
            details={"expiry": expiry, "view": view, "total_gex_usd": total, **check}))
    return items


def _flow_as_of(newest_trade: Optional[datetime], window_end: datetime) -> Optional[datetime]:
    """The data time of the flow: the newest stored trade, never later than the window end (point in time)."""
    if newest_trade is None:
        return None
    return min(newest_trade, window_end)


def flow_strike_evidence(flow: StrikeFlow, newest_trade: Optional[datetime], window_end: datetime,
                         hours: int = config.OPTION_FLOW_WINDOW_HOURS,
                         per_side: int = config.FLOW_STRIKES_PER_SIDE) -> List[EvidenceItem]:
    """
    The strikes with the most taker buying and selling of calls and puts (``top_strikes``, the Levels page's rule) as
    weak price levels. Strength = the strike's notional / the largest notional among them.
    """
    as_of = _flow_as_of(newest_trade, window_end)
    if as_of is None or flow.trades_used == 0:
        return []
    picked = []
    for option_type, direction, label in (("C", "buy", "calls bought"), ("C", "sell", "calls sold"),
                                          ("P", "buy", "puts bought"), ("P", "sell", "puts sold")):
        for strike, notional in top_strikes(flow, option_type, direction, per_side):
            picked.append((option_type, direction, label, strike, notional))
    largest = max((entry[4] for entry in picked), default=0.0)
    if largest <= 0:
        return []
    return [EvidenceItem(
        id=f"option_flow.strike.{option_type}_{direction}.{strike:g}", source=OPTION_FLOW_SOURCE, input_group=TRADES_GROUP,
        kind=PRICE_LEVEL,
        statement=(f"{notional / 1e6:,.0f}M USD (contracts x strike) of {label} by takers at the {strike:,.0f} strike in "
                   f"{hours} h. It shows where interest is; it does not say whether positions were opened or closed, nor "
                   "that the price will react there."),
        as_of=as_of, strength=notional / largest, quality=config.FLOW_STRIKE_LEVEL_QUALITY, price=strike,
        quality_reasons=("a traded strike is where volume was, not a barrier",),
        details={"option_type": option_type, "taker_side": direction, "notional_usd": notional, "window_hours": hours})
        for option_type, direction, label, strike, notional in picked]


def _imbalance(call_buy: float, call_sell: float, put_buy: float, put_sell: float) -> Optional[float]:
    """(calls bought - calls sold - puts bought + puts sold) / all, in contracts; None without any volume."""
    total = call_buy + call_sell + put_buy + put_sell
    if total <= 0:
        return None
    return (call_buy - call_sell - put_buy + put_sell) / total


def _directional(item_id: str, source: str, imbalance: float, contracts: float, what: str, as_of: datetime,
                 quality: float, reasons: Tuple[str, ...], details: Dict) -> EvidenceItem:
    """A directional item from a call/put imbalance (bullish/bearish beyond the threshold, else balanced)."""
    threshold = config.DIRECTIONAL_IMBALANCE_THRESHOLD
    if abs(imbalance) >= threshold:
        direction = BULLISH if imbalance > 0 else BEARISH
        strength = min(1.0, abs(imbalance))
        lean = "leans bullish (more calls bought or puts sold)" if imbalance > 0 else "leans bearish (more puts bought or calls sold)"
    else:
        direction, strength = NEUTRAL, 1.0 - abs(imbalance) / threshold
        lean = "is balanced"
    return EvidenceItem(
        id=item_id, source=source, input_group=TRADES_GROUP, kind=DIRECTIONAL,
        statement=(f"The net taker flow of {what} {lean}: imbalance {imbalance:+.2f} over {contracts:,.0f} contracts. "
                   "It does not say whether the trades opened or closed positions, nor what they hedge."),
        as_of=as_of, strength=strength, quality=quality, direction=direction, quality_reasons=reasons,
        details={**details, "imbalance": imbalance, "contracts": contracts})


def option_flow_evidence(flow: StrikeFlow, block_legs: Sequence[BlockTradeLeg], newest_trade: Optional[datetime],
                         window_end: datetime, hours: int = config.OPTION_FLOW_WINDOW_HOURS) -> List[EvidenceItem]:
    """
    Net taker flow of calls against puts, in contracts (strike notional would inflate far out-of-the-money calls), as
    three directional items: screen trades (``StrikeFlow`` series without blocks), single-leg blocks, and multi-leg
    block structures (``describe_blocks`` labels; the side of one leg of a structure is not a view, so they keep only
    ``MULTI_LEG_BLOCK_QUALITY_FACTOR`` of the quality). A part without volume gives no item.
    """
    as_of = _flow_as_of(newest_trade, window_end)
    if as_of is None:
        return []
    items = []
    screen = [sum(flow.cell(strike, series_key(option_type, direction, False)).contracts for strike in flow.strikes)
              for option_type, direction in (("C", "buy"), ("C", "sell"), ("P", "buy"), ("P", "sell"))]
    screen_imbalance = _imbalance(*screen)
    if screen_imbalance is not None:
        items.append(_directional("option_flow.screen_net", OPTION_FLOW_SOURCE, screen_imbalance, sum(screen),
                                  f"screen (order-book) option trades in {hours} h", as_of, config.OPTION_FLOW_QUALITY,
                                  ("taker side known, intent (open, close, hedge) unknown",), {"window_hours": hours}))

    structures = {structure.block_trade_id: structure for structure in describe_blocks(list(block_legs), top=len(block_legs))}
    sums = {True: [0.0, 0.0, 0.0, 0.0], False: [0.0, 0.0, 0.0, 0.0]}      # multi-leg? -> call buy, call sell, put buy, put sell
    counts = {True: 0, False: 0}
    position = {("C", "buy"): 0, ("C", "sell"): 1, ("P", "buy"): 2, ("P", "sell"): 3}
    multi_leg_by_block = {block_trade_id: structure.legs_in_window > 1 or (structure.declared_leg_count or 1) > 1
                          for block_trade_id, structure in structures.items()}
    for multi_leg in multi_leg_by_block.values():
        counts[multi_leg] += 1
    for leg in block_legs:
        if (leg.option_type, leg.direction) in position:
            sums[multi_leg_by_block[leg.block_trade_id]][position[(leg.option_type, leg.direction)]] += leg.amount
    for multi_leg, key, what, quality, reasons in (
            (False, "option_flow.single_leg_blocks_net", "single-leg block trades", config.OPTION_FLOW_QUALITY,
             ("taker side known, intent (open, close, hedge) unknown",)),
            (True, "option_flow.multi_leg_blocks_net", "multi-leg block structures",
             config.OPTION_FLOW_QUALITY * config.MULTI_LEG_BLOCK_QUALITY_FACTOR,
             ("taker side known, intent (open, close, hedge) unknown",
              f"multi-leg structures: the side of one leg is not a view, quality x{config.MULTI_LEG_BLOCK_QUALITY_FACTOR:g}"))):
        imbalance = _imbalance(*sums[multi_leg])
        if imbalance is None:
            continue
        items.append(_directional(key, OPTION_FLOW_SOURCE, imbalance, sum(sums[multi_leg]),
                                  f"{what} in {hours} h ({counts[multi_leg]} blocks)", as_of, quality, reasons,
                                  {"window_hours": hours, "blocks": counts[multi_leg]}))
    return items


def taker_ratio_evidence(symbol: str, rolling_points: Sequence[Tuple[datetime, float]], now: datetime,
                         band_days: int = 30) -> Tuple[Optional[EvidenceItem], Optional[str]]:
    """
    The latest rolling 24 h taker buy/sell ratio (``rolling_ratio`` of ``TakerFlowChartService.figure_data``) against
    its own band over the last ``band_days`` days (mean +- ``TAKER_BAND_STANDARD_DEVIATIONS`` sample standard
    deviations of the hourly points). Outside the band: directional, strength |z| / ``TAKER_FULL_STRENGTH_...``; inside:
    balanced. Weak and mean-reverting, horizon 1 to 3 days.

    Returns:
        The item, or None and the reason it was not computed.
    """
    start = now - timedelta(days=band_days)
    # a point's 24 h window ends one hour after its time: keep only windows closed by ``now`` (point in time)
    points = sorted((time, ratio) for time, ratio in rolling_points if start <= time and time + timedelta(hours=1) <= now)
    if len(points) < config.TAKER_BAND_MINIMUM_POINTS:
        return None, (f"{symbol} taker ratio: only {len(points)} rolling points in {band_days} days "
                      f"(at least {config.TAKER_BAND_MINIMUM_POINTS} needed for the band)")
    ratios = [ratio for _, ratio in points]
    mean, deviation = statistics.mean(ratios), statistics.stdev(ratios)
    if deviation <= 0:
        return None, f"{symbol} taker ratio: the band has no width (all ratios equal)"
    latest_time, latest = points[-1]
    z = (latest - mean) / deviation
    width = config.TAKER_BAND_STANDARD_DEVIATIONS
    if abs(z) > width:
        direction = BULLISH if z > 0 else BEARISH
        strength = min(1.0, abs(z) / config.TAKER_FULL_STRENGTH_STANDARD_DEVIATIONS)
        where = "above" if z > 0 else "below"
        lean = f"{where} its band: takers {'bought' if z > 0 else 'sold'} more aggressively than usual"
    else:
        direction, strength = NEUTRAL, 1.0 - abs(z) / width
        lean = "inside its band: no unusual aggressor imbalance"
    low, high = mean - width * deviation, mean + width * deviation
    item = EvidenceItem(
        id="futures.taker_ratio_24h", source=TAKER_RATIO_SOURCE, input_group=BINANCE_CANDLES_GROUP, kind=DIRECTIONAL,
        statement=(f"The 24 h taker buy/sell ratio of {symbol} is {latest:.3f}, {lean} ({low:.3f} to {high:.3f}, "
                   f"{band_days}-day mean +- {width:g} sd; z {z:+.2f}). Weak: the ratio reverts to its mean within days and "
                   "it shows one venue's aggressors, not positioning."),
        as_of=latest_time + timedelta(hours=1), strength=strength, quality=config.TAKER_RATIO_QUALITY, direction=direction,
        horizon_days=config.TAKER_HORIZON_DAYS,
        quality_reasons=("one venue, aggressor side only; mean-reverting within days",),
        details={"symbol": symbol, "ratio": latest, "mean": mean, "standard_deviation": deviation, "z": z,
                 "points": len(points)})
    return item, None


def open_interest_context(rows: Sequence[ExpiryOpenInterest], limit: int = 6) -> List[Dict]:
    """The open interest per expiry for the reader (largest first), JSON-serialisable; not used in the scores."""
    return [{"expiry": row.expiry, "days_left": row.days_left, "share": row.share,
             "total_open_interest": row.total_open_interest, "notional_usd": row.notional_usd} for row in rows[:limit]]


def chosen_expiries(rows: Sequence[ExpiryOpenInterest]) -> List[str]:
    """
    The expiries whose dealer GEX becomes evidence: the nearest monthly and the one with the most open interest (one when
    they coincide), soonest first. ``rows`` is ``open_interest_by_expiry`` output (largest first, unexpired only).
    """
    monthly = sorted((row.settlement, row.expiry) for row in rows if row.settlement is not None and is_monthly_expiry(row.settlement))
    chosen = []
    if monthly:
        chosen.append(monthly[0][1])
    largest = next((row.expiry for row in rows if row.total_open_interest is not None), None)
    if largest is not None and largest not in chosen:
        chosen.append(largest)
    far = datetime.max.replace(tzinfo=timezone.utc)
    settlement = {row.expiry: row.settlement or far for row in rows}
    return sorted(chosen, key=lambda label: settlement[label])
