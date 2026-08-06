"""Tests for wiring daily_oi_snapshots collection into ProspectiveCollector's
hourly cycle (institutional_metrics_spec.md section 7(c) Migration M8,
Task E4).

daily_oi_snapshots has exactly the right shape for Task C8's
FixedStrikeVolCalculator/get_chain_iv_at (per-strike mark_iv) but was
GUI-triggered only ([verified] 5 of the last 40 days present, 87.5%
missing) -- the daemon is now the authority, gated to fire only when the
current UTC hour is exactly Deribit's 08:00 settlement hour so the anchor
cannot be silently overwritten by a later run at a different hour of the
same day (that's what migration 023's widened conflict key protects; this
file tests the daemon-side gate that decides WHEN to write).
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _make_collector():
    """Build a ProspectiveCollector with mocked dependencies."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    collector._dvol_fetcher = MagicMock()
    return collector


_SAMPLE_INSTRUMENTS = [
    {
        "instrument_name": "BTC-31JUL26-65000-C",
        "open_interest": 10.0,
        "mark_iv": 32.0,
        "mark_price": 0.05,
        "volume": 1.0,
        "volume_usd": 100.0,
        "greeks": {"delta": 0.5, "gamma": 0.01, "vega": 10.0, "theta": -5.0},
        "bid_price": 0.049,
        "ask_price": 0.051,
        "underlying_price": 65000.0,
    },
    {
        "instrument_name": "BTC-31JUL26-65000-P",
        "open_interest": 5.0,
        "mark_iv": 33.0,
        "mark_price": 0.04,
        "volume": 1.0,
        "volume_usd": 100.0,
        "greeks": {"delta": -0.5, "gamma": 0.01, "vega": 10.0, "theta": -5.0},
        "bid_price": 0.039,
        "ask_price": 0.041,
        "underlying_price": 65000.0,
    },
]


class TestDailyOiAnchorGating:
    def test_fires_at_exactly_08_00_utc(self):
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        collector.repo.save_daily_oi_snapshot.assert_called()

    def test_fires_anywhere_within_the_08_00_utc_hour(self):
        """Gated on the hour, not the exact minute -- the daemon runs every
        30 minutes (unified_scheduler.py), so both the 08:00 and 08:30
        ticks must fire (both upsert the same anchor row; harmless)."""
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 45, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        collector.repo.save_daily_oi_snapshot.assert_called()

    @pytest.mark.parametrize("hour", [0, 7, 9, 13, 23])
    def test_does_not_fire_at_other_hours(self, hour):
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, hour, 15, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        collector.repo.save_daily_oi_snapshot.assert_not_called()

    def test_defaults_to_real_utc_clock_when_now_utc_not_passed(self):
        """No now_utc override -- must read datetime.now(timezone.utc), not
        naive-local datetime.now() (this exact bug class has bitten this
        table twice already in Task C8)."""
        collector = _make_collector()

        import coding.service.data_collection.prospective_collector as pc_module

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz == timezone.utc
                return datetime(2026, 8, 1, 8, 5, 0, tzinfo=timezone.utc)

        original_datetime = pc_module.datetime
        pc_module.datetime = _Frozen
        try:
            collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS)
        finally:
            pc_module.datetime = original_datetime

        collector.repo.save_daily_oi_snapshot.assert_called()

    def test_no_instruments_is_a_noop_even_at_anchor_hour(self):
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", [], now_utc=now_utc)

        collector.repo.save_daily_oi_snapshot.assert_not_called()


class TestDailyOiAnchorWritePayload:
    def test_writes_snapshot_hour_utc_8_and_snapshot_date_from_now_utc(self):
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        call_kwargs = collector.repo.save_daily_oi_snapshot.call_args[1]
        assert call_kwargs["snapshot_hour_utc"] == 8
        assert call_kwargs["snapshot_date"] == now_utc.date()

    def test_writes_one_call_per_expiration(self):
        """Instruments span exactly one expiration (31JUL26) in the fixture
        -- exactly one save_daily_oi_snapshot call."""
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        assert collector.repo.save_daily_oi_snapshot.call_count == 1
        call_kwargs = collector.repo.save_daily_oi_snapshot.call_args[1]
        assert call_kwargs["currency"] == "BTC"
        assert call_kwargs["expiration"] == "31JUL26"

    def test_instruments_payload_carries_strike_option_type_oi_mark_iv(self):
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        call_kwargs = collector.repo.save_daily_oi_snapshot.call_args[1]
        payload = call_kwargs["instruments"]
        strikes_and_types = {(inst["strike"], inst["option_type"]) for inst in payload}
        assert strikes_and_types == {(65000.0, "C"), (65000.0, "P")}
        for inst in payload:
            assert inst["open_interest"] in (10.0, 5.0)
            assert inst["mark_iv"] in (32.0, 33.0)

    def test_underlying_price_uses_forward_price_not_zero(self):
        """underlying_price passed to the repository must be this expiry's
        own forward price (bugfix_spec.md Item 7 settlement-space
        convention, matching the existing GUI call), never 0/None even
        though this method never calls set_index_price."""
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        call_kwargs = collector.repo.save_daily_oi_snapshot.call_args[1]
        assert call_kwargs["underlying_price"] == pytest.approx(65000.0)

    def test_makes_no_api_calls(self):
        """Reuses the already-fetched book-summary instrument list -- must
        not call self.api at all (no index price fetch, no extra book
        summary fetch)."""
        collector = _make_collector()
        now_utc = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)

        collector._save_daily_oi_anchor("BTC", _SAMPLE_INSTRUMENTS, now_utc=now_utc)

        assert collector.api.method_calls == []


class TestCollectCurrencyWiring:
    def test_collect_currency_calls_save_daily_oi_anchor_with_book_instruments(self):
        collector = _make_collector()
        collector._fetch_trades = MagicMock(return_value={"count": 5})
        collector._fetch_book_summary = MagicMock(
            return_value={"count": 2, "instruments": _SAMPLE_INSTRUMENTS}
        )
        collector._run_onchain_analysis = MagicMock()
        collector._fetch_dvol = MagicMock()
        collector._fetch_dvol_history_row = MagicMock()
        collector._fetch_funding_rate = MagicMock()
        collector._fetch_ohlcv = MagicMock()
        collector._persist_delta_flow = MagicMock()
        collector._save_daily_oi_anchor = MagicMock()

        collector._collect_currency("BTC", datetime(2026, 3, 14))

        collector._save_daily_oi_anchor.assert_called_once_with("BTC", _SAMPLE_INSTRUMENTS)

    def test_collect_currency_isolates_daily_oi_anchor_failure(self):
        """A failure in _save_daily_oi_anchor must not prevent any other
        per-currency step from running, and must not propagate out of
        _collect_currency -- same per-step isolation pattern used
        throughout this collector."""
        collector = _make_collector()
        collector._fetch_trades = MagicMock(return_value={"count": 5})
        collector._fetch_book_summary = MagicMock(
            return_value={"count": 2, "instruments": _SAMPLE_INSTRUMENTS}
        )
        collector._run_onchain_analysis = MagicMock()
        collector._fetch_dvol = MagicMock()
        collector._fetch_dvol_history_row = MagicMock()
        collector._fetch_funding_rate = MagicMock()
        collector._fetch_ohlcv = MagicMock()
        collector._persist_delta_flow = MagicMock()
        collector._save_daily_oi_anchor = MagicMock(side_effect=RuntimeError("boom"))

        collector._collect_currency("BTC", datetime(2026, 3, 14))  # must not raise

        collector._fetch_dvol.assert_called_once_with("BTC")
        collector._fetch_funding_rate.assert_called_once_with("BTC")

    def test_collect_currency_skips_anchor_when_no_instruments(self):
        collector = _make_collector()
        collector._fetch_trades = MagicMock(return_value={"count": 5})
        collector._fetch_book_summary = MagicMock(return_value={"count": 0, "instruments": []})
        collector._run_onchain_analysis = MagicMock()
        collector._fetch_dvol = MagicMock()
        collector._fetch_dvol_history_row = MagicMock()
        collector._fetch_funding_rate = MagicMock()
        collector._fetch_ohlcv = MagicMock()
        collector._persist_delta_flow = MagicMock()
        collector._save_daily_oi_anchor = MagicMock()

        collector._collect_currency("BTC", datetime(2026, 3, 14))

        collector._save_daily_oi_anchor.assert_not_called()
