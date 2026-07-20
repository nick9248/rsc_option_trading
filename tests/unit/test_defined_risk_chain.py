"""Tests for the shared liquid-chain builder."""
from coding.service.scanner.defined_risk_chain import build_liquid_chain


def _contract(strike, option_type, bid_price, ask_price, oi):
    return {
        "strike": strike, "option_type": option_type,
        "bid_price": bid_price, "ask_price": ask_price, "open_interest": oi,
        "bid_usd": bid_price * 65000.0 if bid_price else None,
        "ask_usd": ask_price * 65000.0 if ask_price else None,
    }


class TestBuildLiquidChain:
    def test_pairs_both_legs_at_same_strike(self):
        contracts = [
            _contract(65000, "C", 0.01, 0.0102, 100),
            _contract(65000, "P", 0.01, 0.0102, 100),
        ]
        liquid = build_liquid_chain(contracts, index_price=65000.0)
        assert 65000 in liquid
        assert liquid[65000]["call_ok"] is True
        assert liquid[65000]["put_ok"] is True

    def test_strike_missing_one_leg_excluded(self):
        contracts = [_contract(65000, "C", 0.01, 0.0102, 100)]
        liquid = build_liquid_chain(contracts, index_price=65000.0)
        assert liquid == {}

    def test_illiquid_leg_marked_not_ok_but_still_present(self):
        contracts = [
            _contract(65000, "C", 0.01, 0.0102, 100),
            _contract(65000, "P", 0.01, 0.02, 100),  # wide spread -> put_ok False
        ]
        liquid = build_liquid_chain(contracts, index_price=65000.0)
        assert liquid[65000]["call_ok"] is True
        assert liquid[65000]["put_ok"] is False
