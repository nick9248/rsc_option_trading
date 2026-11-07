"""
Unit tests for the MenthorQ chart renderer: the dollar axis formatter and a
smoke render of every chart into a temporary folder. Pure: no database.
"""

from datetime import datetime, timedelta, timezone

from coding.core.analytics.menthorq_chart_renderer import (
    format_dollars,
    render_expiry_iv_times_open_interest_chart,
    render_gex_panels_chart,
    render_iv_times_open_interest_chart,
    render_net_dex_chart,
)
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_ALL,
    compute_expiry_iv_times_open_interest,
    replicate_menthorq,
)

AS_OF = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc) - timedelta(days=365)


def _row(expiration, strike, option_type, open_interest):
    return {"instrument_name": f"BTC-{expiration}-{strike}-{option_type}", "expiration": expiration,
            "strike": float(strike), "option_type": option_type, "open_interest": open_interest,
            "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None}


def test_format_dollars_uses_menthorq_suffixes():
    assert format_dollars(400e6) == "400M"
    assert format_dollars(-1.5e9) == "-1.5B"
    assert format_dollars(12000.0) == "12K"
    assert format_dollars(0.0) == "0"


def test_every_chart_renders(tmp_path):
    rows = [_row("27NOV26", 90, "P", 30.0), _row("27NOV26", 100, "C", 10.0),
            _row("25DEC26", 110, "C", 5.0), _row("25DEC26", 95, "P", 8.0)]
    replication = replicate_menthorq(rows, 100.0, AS_OF, EXPIRY_UNIVERSE_ALL)
    render_iv_times_open_interest_chart(replication, tmp_path / "iv.png", "label")
    render_gex_panels_chart(replication, tmp_path / "gex.png", "label")
    render_net_dex_chart(replication, tmp_path / "dex.png", "label")
    expiry = compute_expiry_iv_times_open_interest(
        rows, "25DEC26", replication["gex_by_expiration"]["25DEC26"])
    render_expiry_iv_times_open_interest_chart(expiry, 100.0, tmp_path / "expiry.png", "label")
    for name in ("iv.png", "gex.png", "dex.png", "expiry.png"):
        assert (tmp_path / name).stat().st_size > 0
