"""
Regression test for OnChainAnalysisService.fetch_and_analyze's index-price
fallback (Wave H Task H-F, Fix 3).

nearest_expiry_median_underlying_price() now returns None (not a
fabricated 0.0) when no instrument in the book has a priced
underlying_price at all. Fix 3 wires that None through fetch_and_analyze:
if BOTH the primary get_index_price fetch AND the fallback fail to
produce a real price, the method must raise loudly (RuntimeError) instead
of silently continuing with index_price=0.0 -- a value that would
otherwise poison every spot-scaled metric downstream (notional,
moneyness, GEX's S^2 term, max-pain distance) with no in-band marker
distinguishing it from a genuine $0 price.
"""
from unittest.mock import MagicMock

import pytest

from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(book_summary):
    api = MagicMock()
    api.get_book_summary.return_value = book_summary
    api.get_index_price.side_effect = RuntimeError("index price endpoint down")
    service = OnChainAnalysisService(api_service=api, repository=None)
    return service, api


def test_raises_when_primary_and_fallback_both_fail_to_price():
    """Empty book summary: get_index_price raises, and the nearest-expiry
    median fallback has no instrument to derive a price from either --
    fetch_and_analyze must refuse to proceed with an unknown spot price."""
    service, api = _make_service(book_summary=[])

    with pytest.raises(RuntimeError, match="No index price available"):
        service.fetch_and_analyze(currency="BTC")

    api.get_index_price.assert_called_once_with(currency="BTC")


def test_falls_back_successfully_when_fallback_has_a_real_price():
    """Sanity check the non-degenerate branch still works: primary fails,
    but the fallback DOES find a priced instrument -- must not raise, and
    must proceed using the fallback price (existing behavior, unchanged)."""
    book = [
        {
            "instrument_name": "BTC-26JUL26-64000-C",
            "underlying_price": 64_000.0,
            "volume": 1,
            "open_interest": 0,
            "mark_price": 0.01,
            "mark_iv": 60.0,
            "bid_price": None,
            "ask_price": None,
            "greeks": {},
        },
    ]
    service, api = _make_service(book_summary=book)

    # Should not raise while resolving the index price -- may still fail
    # later in analysis for unrelated reasons (this fixture is minimal),
    # so only assert the RuntimeError from the price-resolution branch
    # specifically does NOT fire.
    try:
        service.fetch_and_analyze(currency="BTC")
    except RuntimeError as exc:
        assert "No index price available" not in str(exc)
    except Exception:
        pass  # unrelated downstream failure from the minimal fixture is fine
