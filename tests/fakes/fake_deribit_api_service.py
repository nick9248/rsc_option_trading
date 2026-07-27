"""
Fake DeribitApiService that replays a recorded fixture instead of hitting the
live network.

Same public surface as
``coding.service.deribit.deribit_api_service.DeribitApiService`` for the
methods ``OnChainAnalysisService.fetch_and_analyze`` calls. Every call is
matched by exact (method, kwargs) against what ``scripts/record_onchain_fixture.py``
recorded; an unmatched call raises ``KeyError`` instead of silently returning
``{}`` — so a new/changed API call introduced by a refactor fails loudly. See
refactor_design_spec.md section 7.2.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.fakes.fixture_io import load_json_gz

logger = logging.getLogger(__name__)


def _normalize(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys that never affect the response shape (e.g. save_to_csv)."""
    return {k: v for k, v in kwargs.items() if k != "save_to_csv"}


class FakeDeribitApiService:
    """Dict-lookup replayer for DeribitApiService, backed by a fixture directory."""

    def __init__(self, fixture_dir: Path):
        self.fixture_dir = Path(fixture_dir)

        self._book_summary_calls = load_json_gz(self.fixture_dir / "book_summary.json.gz")
        self._tickers: Dict[str, Any] = load_json_gz(self.fixture_dir / "tickers.json.gz")
        self._last_trades_calls = load_json_gz(self.fixture_dir / "last_trades.json.gz")
        self._instruments_calls = load_json_gz(self.fixture_dir / "instruments_future.json.gz")
        self._funding_chart_calls = load_json_gz(self.fixture_dir / "funding_chart.json.gz")

        meta = load_json_gz(self.fixture_dir / "meta.json.gz")
        currency = meta["currency"]
        other_currency = meta["other_currency"]

        # bugfix_spec.md Item 7: get_index_price wasn't recorded by fixtures
        # captured before this item (record_onchain_fixture.py now records
        # it explicitly to index_price.json.gz for anything recorded after).
        # For an older fixture directory that lacks that file, derive the
        # same quantity from data ALREADY recorded in the same session: the
        # perpetual ticker's own `index_price` field (Deribit ticker
        # responses always carry it) is the live index snapshot at
        # approximately that same moment -- not a fabricated number, a
        # different field of the same already-recorded observation.
        index_price_path = self.fixture_dir / "index_price.json.gz"
        if index_price_path.exists():
            self._index_price_calls = load_json_gz(index_price_path)
            self._index_price_fallback = None
        else:
            self._index_price_calls = []
            perp_ticker = self._tickers.get(f"{currency}-PERPETUAL", {})
            fallback = perp_ticker.get("index_price")
            if fallback is None:
                raise KeyError(
                    f"No recorded get_index_price fixture and no "
                    f"{currency}-PERPETUAL ticker index_price fallback in "
                    f"{self.fixture_dir} -- re-record the fixture."
                )
            logger.warning(
                "No index_price.json.gz in %s -- falling back to the "
                "%s-PERPETUAL ticker's own recorded index_price (%.2f). "
                "Re-record the fixture to record get_index_price directly.",
                self.fixture_dir, currency, fallback,
            )
            self._index_price_fallback = fallback

        self._tradingview_calls: List[Dict[str, Any]] = []
        self._volatility_index_calls: List[Dict[str, Any]] = []
        for ccy in (currency, other_currency):
            self._tradingview_calls += load_json_gz(self.fixture_dir / f"tradingview_{ccy}.json.gz")
            self._volatility_index_calls += load_json_gz(
                self.fixture_dir / f"volatility_index_{ccy}.json.gz"
            )

    def _lookup(self, calls: List[Dict[str, Any]], method_name: str, kwargs: Dict[str, Any]) -> Any:
        key = _normalize(kwargs)
        for entry in calls:
            if _normalize(entry["kwargs"]) == key:
                return entry["response"]
        raise KeyError(
            f"No recorded fixture entry for {method_name}({kwargs}) — this call was not "
            "present when the fixture was recorded. If this is expected new behavior, "
            "re-record the fixture; otherwise this is a regression."
        )

    def get_index_price(self, currency: str = "BTC", save_to_csv: bool = False) -> float:
        if self._index_price_calls:
            return self._lookup(
                self._index_price_calls, "get_index_price", {"currency": currency}
            )
        return self._index_price_fallback

    def get_book_summary(
        self, currency: str = "ETH", kind: Optional[str] = "option", save_to_csv: bool = False
    ) -> List[Dict[str, Any]]:
        return self._lookup(
            self._book_summary_calls, "get_book_summary", {"currency": currency, "kind": kind}
        )

    def get_ticker(self, instrument_name: str, save_to_csv: bool = False) -> Dict[str, Any]:
        try:
            return self._tickers[instrument_name]
        except KeyError:
            raise KeyError(
                f"No recorded ticker fixture for instrument {instrument_name!r}"
            )

    def get_last_trades_by_currency(
        self,
        currency: str = "ETH",
        kind: Optional[str] = "option",
        count: int = 1000,
        save_to_csv: bool = False,
    ) -> Dict[str, Any]:
        return self._lookup(
            self._last_trades_calls,
            "get_last_trades_by_currency",
            {"currency": currency, "kind": kind, "count": count},
        )

    def get_instruments(
        self,
        currency: str = "ETH",
        kind: Optional[str] = "option",
        expired: bool = False,
        save_to_csv: bool = False,
    ) -> List[Dict[str, Any]]:
        return self._lookup(
            self._instruments_calls,
            "get_instruments",
            {"currency": currency, "kind": kind, "expired": expired},
        )

    def get_tradingview_chart_data(
        self,
        instrument_name: str = "BTC-PERPETUAL",
        resolution: str = "1D",
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        save_to_csv: bool = False,
    ) -> Dict[str, Any]:
        return self._lookup(
            self._tradingview_calls,
            "get_tradingview_chart_data",
            {
                "instrument_name": instrument_name,
                "resolution": resolution,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )

    def get_funding_chart_data(
        self,
        instrument_name: str = "ETH-PERPETUAL",
        length: str = "8h",
        save_to_csv: bool = False,
    ) -> Dict[str, Any]:
        return self._lookup(
            self._funding_chart_calls,
            "get_funding_chart_data",
            {"instrument_name": instrument_name, "length": length},
        )

    def get_volatility_index_data(
        self,
        currency: str = "ETH",
        resolution: int = 3600,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        save_to_csv: bool = False,
    ) -> Dict[str, Any]:
        return self._lookup(
            self._volatility_index_calls,
            "get_volatility_index_data",
            {
                "currency": currency,
                "resolution": resolution,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )
