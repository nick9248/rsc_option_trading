"""Tests for Gate 1 — Liquidity Filter."""
import pytest
from coding.core.strategy.otm.signals.liquidity_gate import LiquidityGate
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)


@pytest.fixture
def gate(config):
    return LiquidityGate(config)


def _make_contract(**overrides) -> dict:
    """Return a contract dict that passes all Gate 1 checks by default."""
    base = {
        "asset": "BTC",
        "instrument_name": "BTC-28MAR25-95000-C",
        "delta": 0.28,
        "bid_iv": 0.60,
        "ask_iv": 0.63,       # spread = 0.03 vol pts, relative = 0.03/0.615 ≈ 4.9%
        "open_interest": 200,
        "volume_24h": 20,     # vol/OI = 0.10 > 0.05
        "mark_price": 0.004,  # in BTC
        "underlying_price": 87000.0,
        "contract_qty": 1,
    }
    base.update(overrides)
    return base


# ── Bid-ask spread: relative check ───────────────────────────────────────────

def test_passes_when_spread_within_both_thresholds(gate):
    passed, reason = gate.check(_make_contract())
    assert passed, reason


def test_fails_relative_spread_too_wide(gate):
    # relative = (0.65 - 0.50) / 0.575 = 26.1% > 8%
    passed, reason = gate.check(_make_contract(bid_iv=0.50, ask_iv=0.65))
    assert not passed
    assert "relative" in reason.lower()


def test_fails_absolute_spread_too_wide(gate):
    # bid_iv = 0.600, ask_iv = 0.645 → absolute = 0.045 * 100 = 4.5 vol pts > 4.0
    # relative: (0.645 - 0.600) / 0.6225 = 7.2% < 8% (passes relative)
    # absolute: 4.5 vol pts >= 4.0 (fails absolute)
    passed, reason = gate.check(_make_contract(bid_iv=0.600, ask_iv=0.645))
    assert not passed
    assert "absolute" in reason.lower()


def test_fails_when_bid_iv_is_none(gate):
    passed, reason = gate.check(_make_contract(bid_iv=None))
    assert not passed
    assert "null" in reason.lower() or "none" in reason.lower() or "missing" in reason.lower()


def test_fails_when_ask_iv_is_none(gate):
    passed, reason = gate.check(_make_contract(ask_iv=None))
    assert not passed


# ── Volume / OI ratio ─────────────────────────────────────────────────────────

def test_fails_volume_oi_ratio_too_low(gate):
    passed, reason = gate.check(_make_contract(volume_24h=2, open_interest=200))
    # ratio = 2/200 = 0.01 < 0.05
    assert not passed
    assert "volume" in reason.lower() or "oi" in reason.lower()


def test_fails_volume_oi_exactly_at_threshold(gate):
    passed, reason = gate.check(_make_contract(volume_24h=10, open_interest=200))
    # ratio = 10/200 = 0.05 — spec says > 0.05 (strictly), so exactly 0.05 should FAIL
    assert not passed
    assert "volume" in reason.lower() or "oi" in reason.lower()


# ── Minimum OI — asset-specific ───────────────────────────────────────────────

def test_fails_btc_min_oi(gate):
    passed, reason = gate.check(_make_contract(asset="BTC", open_interest=40))
    assert not passed
    assert "oi" in reason.lower() or "open interest" in reason.lower()


def test_fails_eth_min_oi(gate):
    passed, reason = gate.check(_make_contract(asset="ETH", open_interest=150))
    assert not passed


def test_passes_eth_min_oi_exactly(gate):
    passed, _ = gate.check(_make_contract(asset="ETH", open_interest=200,
                                           volume_24h=20))
    assert passed


# ── Transaction cost floor ────────────────────────────────────────────────────

def test_fails_tx_cost_floor(gate):
    # round_trip_fee = 2 × 0.0003 × 87000 × 1 = 52.2
    # required: 2 × entry_premium > 52.2 × 5 = 261 → premium must be > 130.5
    # mark_price in BTC: 0.0010 → entry_premium = 0.001 × 87000 = 87 USD — fails
    passed, reason = gate.check(_make_contract(mark_price=0.0010, underlying_price=87000.0))
    assert not passed
    assert "cost" in reason.lower() or "fee" in reason.lower() or "premium" in reason.lower()


def test_passes_tx_cost_floor(gate):
    # mark_price 0.004 → premium = 0.004 × 87000 = 348 USD
    # 2 × 348 = 696 > 261 ✓
    passed, _ = gate.check(_make_contract(mark_price=0.004, underlying_price=87000.0))
    assert passed


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_fails_zero_open_interest(gate):
    passed, _ = gate.check(_make_contract(open_interest=0))
    assert not passed


def test_passes_btc_exactly_min_oi(gate):
    passed, _ = gate.check(_make_contract(asset="BTC", open_interest=50, volume_24h=5))
    assert passed


def test_check_returns_tuple_of_bool_and_str(gate):
    result = gate.check(_make_contract())
    assert isinstance(result, tuple)
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


def test_fail_reason_is_informative(gate):
    _, reason = gate.check(_make_contract(open_interest=5))
    assert len(reason) > 10   # not empty


def test_btc_absolute_spread_threshold_in_vol_pts(gate):
    # Note: bid_iv and ask_iv are in decimal (0.60 = 60%)
    # absolute check uses (ask_iv - bid_iv) × 100 converted to vol pts
    # Spec says: (ask_iv − bid_iv) < 4.0 vol pts
    # Treating 1 vol pt = 1 percentage point of IV (i.e., raw decimal diff × 100 < 4.0)
    # bid=0.60, ask=0.63 → diff = 0.03 → 3 vol pts → PASS
    passed, _ = gate.check(_make_contract(bid_iv=0.60, ask_iv=0.63))
    assert passed
    # bid=0.60, ask=0.641 → diff = 0.041 → 4.1 vol pts → FAIL
    passed, reason = gate.check(_make_contract(bid_iv=0.60, ask_iv=0.641))
    assert not passed
    assert "absolute" in reason.lower()
