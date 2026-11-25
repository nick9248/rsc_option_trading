"""
The facts of one expiry at one instant, in one fixed shape that can be rebuilt for any past instant from stored data (pure).

The facts of the strategy pipeline are the numbers the collector stores every hour (levels and totals of the chain, volatility, flow), plus the
spot, the DVOL and the realised volatility. They are rendered as text for the model. Because every field can be read back as of any past
instant, a historical run and a live run show the model the same kind of text, and the model can be tested on the past. A field the store
did not have at that time is None and is written as "not recorded", never as 0.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from coding.core.analytics.report.report_page import format_utc

MAX_SNAPSHOT_AGE_HOURS = 3.0
"""A stored hourly snapshot older than this at the instant of the facts is not used (the collector writes one an hour)."""
MINIMUM_FLOW_HOURS = 12
"""Fewer hourly flow rows than this in the last 24 hours are not summed: the sum would describe a part of the day only."""


@dataclass(frozen=True)
class FactsV1:
    """Everything the model is told about one expiry. Optional fields are None when they were not recorded or were too old."""

    currency: str
    expiration: str
    as_of: datetime
    spot: float
    days_to_expiry: float
    forward: Optional[float] = None
    # market
    dvol: Optional[float] = None
    dvol_percentile_365d: Optional[float] = None
    realised_vol_10d: Optional[float] = None
    realised_vol_30d: Optional[float] = None
    # chain levels and totals (hourly analysis snapshot)
    analysis_hour: Optional[datetime] = None
    max_pain_strike: Optional[float] = None
    put_call_ratio_oi: Optional[float] = None
    put_call_ratio_volume: Optional[float] = None
    total_call_oi: Optional[float] = None
    total_put_oi: Optional[float] = None
    total_net_gex_usd: Optional[float] = None
    total_net_dex: Optional[float] = None
    call_resistance_strike: Optional[float] = None
    put_support_strike: Optional[float] = None
    hvl_level: Optional[float] = None
    itm_call_oi_pct: Optional[float] = None
    itm_put_oi_pct: Optional[float] = None
    # implied volatility of the full chain (hourly skew history)
    chain_volatility_hour: Optional[datetime] = None
    atm_iv_pct: Optional[float] = None
    skew_25d: Optional[float] = None
    put_25d_iv_pct: Optional[float] = None
    call_25d_iv_pct: Optional[float] = None
    vrp_absolute_points: Optional[float] = None
    """At-the-money implied volatility of this expiry minus the 30-day realised volatility, in vol points."""
    # other volatility figures (hourly volatility snapshot)
    volatility_hour: Optional[datetime] = None
    iv_percentile_365d: Optional[float] = None
    expected_daily_move_usd: Optional[float] = None
    expected_weekly_move_usd: Optional[float] = None
    pc_atm_ratio: Optional[float] = None
    pc_near_otm_ratio: Optional[float] = None
    pc_far_otm_ratio: Optional[float] = None
    net_vanna: Optional[float] = None
    net_charm: Optional[float] = None
    # flow of the last 24 hours (hourly flow rows)
    flow_hours: int = 0
    flow_hiro_usd: Optional[float] = None
    flow_premium_usd: Optional[float] = None
    flow_gross_delta_usd: Optional[float] = None
    flow_trade_count: Optional[int] = None


def _line(label: str, value: Optional[float], unit: str = "", digits: int = 2) -> str:
    return f"{label}: " + ("not recorded" if value is None else f"{value:,.{digits}f}{unit}")


def render_facts(facts: FactsV1) -> str:
    """The facts as text for the model; what is missing says so."""
    lines: List[str] = [f"# {facts.currency} options, expiry {facts.expiration}, facts as of {format_utc(facts.as_of)}", ""]
    lines += ["## Market",
              _line("Spot", facts.spot), _line("Forward of this expiry", facts.forward), _line("Days to expiry", facts.days_to_expiry, "", 1),
              _line("DVOL (30-day implied volatility index)", facts.dvol), _line("DVOL percentile over 365 days", facts.dvol_percentile_365d, "%", 0),
              _line("Realised volatility, 10 days", None if facts.realised_vol_10d is None else facts.realised_vol_10d * 100, "%", 1),
              _line("Realised volatility, 30 days", None if facts.realised_vol_30d is None else facts.realised_vol_30d * 100, "%", 1)]
    if facts.dvol is not None and facts.realised_vol_30d is not None:
        lines.append(f"DVOL minus 30-day realised volatility: {facts.dvol - facts.realised_vol_30d * 100:+.1f} vol points")
    lines.append("")

    lines.append(f"## Chain of this expiry (stored hourly analysis of {'not available' if facts.analysis_hour is None else format_utc(facts.analysis_hour)})")
    lines += [_line("Max pain strike", facts.max_pain_strike, "", 0), _line("Put/call ratio, open interest", facts.put_call_ratio_oi),
              _line("Put/call ratio, volume", facts.put_call_ratio_volume), _line("Call open interest (coins)", facts.total_call_oi, "", 0),
              _line("Put open interest (coins)", facts.total_put_oi, "", 0),
              _line("Call resistance (assumed dealer positioning)", facts.call_resistance_strike, "", 0),
              _line("Put support (assumed dealer positioning)", facts.put_support_strike, "", 0),
              _line("Cumulative GEX zero strike", facts.hvl_level, "", 0),
              _line("Net gamma exposure, assumed dealer (convention, not measured)", facts.total_net_gex_usd, " USD", 0),
              _line("Net delta exposure (coins)", facts.total_net_dex, "", 0),
              _line("Calls in the money, share of open interest", facts.itm_call_oi_pct, "%", 1),
              _line("Puts in the money, share of open interest", facts.itm_put_oi_pct, "%", 1), ""]

    lines.append(f"## Implied volatility of this expiry (full chain, last finished hour {'not available' if facts.chain_volatility_hour is None else format_utc(facts.chain_volatility_hour)})")
    lines += [_line("ATM implied volatility", facts.atm_iv_pct, "%", 1),
              _line("25-delta skew (put IV minus call IV, vol points)", facts.skew_25d, "", 1),
              _line("25-delta put IV", facts.put_25d_iv_pct, "%", 1), _line("25-delta call IV", facts.call_25d_iv_pct, "%", 1),
              _line("Variance risk premium (this ATM implied volatility minus 30-day realised, vol points)", facts.vrp_absolute_points, "", 1), ""]

    lines.append(f"## Other volatility figures of this expiry (stored hourly analysis of {'not available' if facts.volatility_hour is None else format_utc(facts.volatility_hour)})")
    lines += [_line("DVOL percentile over 365 days at that hour (whole market, not this expiry)", facts.iv_percentile_365d, "%", 0),
              _line("Expected daily move (from DVOL, whole market)", facts.expected_daily_move_usd, " USD", 0),
              _line("Expected weekly move (from DVOL, whole market)", facts.expected_weekly_move_usd, " USD", 0),
              _line("Put/call ratio at the money (instruments traded in that hour only)", facts.pc_atm_ratio),
              _line("Put/call ratio, 5-15% out of the money (traded only)", facts.pc_near_otm_ratio),
              _line("Put/call ratio, more than 15% out of the money (traded only)", facts.pc_far_otm_ratio),
              _line("Net vanna, holder side (USD per vol point, traded instruments only)", facts.net_vanna, "", 0),
              _line("Net charm, holder side (USD per day, traded instruments only)", facts.net_charm, "", 0), ""]

    lines.append("## Option flow of the last 24 hours (delta-adjusted)")
    if facts.flow_hours < MINIMUM_FLOW_HOURS or facts.flow_hiro_usd is None:
        lines.append(f"not recorded ({facts.flow_hours} of the last 24 hourly rows exist)")
    else:
        lines += [f"Hourly rows present: {facts.flow_hours} of 24", _line("Signed delta flow (HIRO)", facts.flow_hiro_usd, " USD", 0),
                  _line("Signed premium", facts.flow_premium_usd, " USD", 0), _line("Gross delta notional", facts.flow_gross_delta_usd, " USD", 0),
                  f"Trades: {facts.flow_trade_count if facts.flow_trade_count is not None else 'not recorded'}"]
    return "\n".join(lines)
