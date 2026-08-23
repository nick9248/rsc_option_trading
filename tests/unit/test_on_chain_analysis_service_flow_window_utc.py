"""
Regression test for the 24h flow-window clock in
OnChainAnalysisService._calculate_buy_sell_flow and
.get_filtered_aggregate_flow (Wave-I-C Fix 1).

Both sites computed ``window_end = datetime.now()`` -- a naive,
host-local datetime -- and then derived millisecond timestamps from it via
``.timestamp()``. ``.timestamp()`` on a naive datetime assumes it is
LOCAL time and converts it to UTC using the host's tz rules, so in the
ordinary case (no DST transition in play) the resulting window is correct
*by accident*, not by construction: during the repeated local hour at a
DST fall-back transition, a naive local ``datetime.now()`` is genuinely
ambiguous -- the same wall-clock reading maps to two different UTC
instants depending on ``fold``, and Python's default ``fold=0`` picks one
arbitrarily. This silently produces a 24h window that is off by exactly
the DST offset (e.g. 1h) twice a year.

A true DST-fold reproduction would require mocking the host's tzdata
resolution inside ``.timestamp()`` itself (not just ``datetime.now()``),
which is impractical to construct as a unit test. The fix instead makes
the clock timezone-AWARE (``datetime.now(timezone.utc)``) and keeps it
aware all the way through the ``.timestamp()`` call -- an aware
datetime's ``.timestamp()`` uses its attached UTC offset directly and is
never ambiguous, regardless of host tz or DST state. (Note this
deliberately does NOT follow prospective_collector.py's collect_hour
pattern of stripping tzinfo after attaching UTC: that pattern is only
correct when the naive-UTC value is stored directly into a `timestamp
without time zone` column with no further conversion. Here, window_end /
window_start are used ONLY to derive *_ms via `.timestamp()`, which
assumes NAIVE input is host-local -- stripping tzinfo would make it
reinterpret an already-UTC wall clock as local, shifting the window by
the host's UTC offset on every call, not just the DST-ambiguous hour.)

This test pins down the straightforward, always-applicable regression:
the window-end clock must be UTC-anchored and stay aware, not host-local
-- mirroring test_on_chain_analysis_service_staleness_utc.py's
``_FrozenDateTime`` pattern (Wave H Task H-F, Fix 1) and
test_prospective_collector_default_hour_utc.py's proven approach. Because
this test's dev/CI host is not UTC (W. Europe, UTC+2 in August), the
``_FrozenDateTime`` local-clock simulation is sufficient to catch a
regression to the naive/local form.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


class _FrozenDateTime(datetime):
    """
    Freezes `now` to a fixed UTC instant while simulating a UTC+2 host's
    naive local clock for the tz=None call -- mirrors
    test_on_chain_analysis_service_staleness_utc.py's proven pattern.
    """
    _FIXED_UTC = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    _LOCAL_OFFSET_HOURS = 2

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls._FIXED_UTC.astimezone(tz)
        # Naive datetime.now() (no tz) -- simulates a UTC+2 host's local
        # wall clock, which reads 2 hours AHEAD of the real UTC instant.
        return cls._FIXED_UTC.replace(tzinfo=None) + timedelta(hours=cls._LOCAL_OFFSET_HOURS)


def _make_analyzer(currency="BTC", expirations=("27MAR26",)):
    from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer

    analyzer = OnChainAnalyzer([], currency)
    analyzer.parsed_data = {exp: [] for exp in expirations}
    analyzer.set_index_price(64_000.0)
    return analyzer


class TestCalculateBuySellFlowWindowClock:
    """Fix 1, site 1: _calculate_buy_sell_flow's window_end."""

    def test_window_end_uses_utc_not_local_clock(self):
        mock_repo = MagicMock()
        mock_repo.get_trades_for_flow_analysis.return_value = []
        service = OnChainAnalysisService(repository=mock_repo)
        analyzer = _make_analyzer()

        expected_utc_ms = int(_FrozenDateTime._FIXED_UTC.timestamp() * 1000)

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.datetime", _FrozenDateTime
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer, patch(
            "coding.service.on_chain.on_chain_analysis_service.generate_flow_distribution_chart"
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.generate_net_flow_chart"
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.generate_flow_trend_chart"
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.save_chart", return_value=""
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.inject_hover_js"
        ):
            instance = MagicMock()
            instance.calculate.return_value.to_dict.return_value = {"flow_data": {}}
            MockAnalyzer.return_value = instance

            service._calculate_buy_sell_flow(analyzer, progress_callback=lambda msg: None)

        _, fetch_kwargs = mock_repo.get_trades_for_flow_analysis.call_args
        # Before the fix, a host reading 2h ahead of UTC (simulated by
        # _FrozenDateTime.now() with tz=None) would push end_ts 2h into
        # the future relative to the real UTC instant.
        assert fetch_kwargs["end_ts"] == expected_utc_ms, (
            f"window_end is not UTC-anchored: got {fetch_kwargs['end_ts']}, "
            f"expected {expected_utc_ms} (off by "
            f"{(fetch_kwargs['end_ts'] - expected_utc_ms) / 3_600_000}h)"
        )


class TestGetFilteredAggregateFlowWindowClock:
    """Fix 1, site 2: get_filtered_aggregate_flow's window_end."""

    def test_window_end_uses_utc_not_local_clock(self):
        mock_repo = MagicMock()
        mock_repo.get_active_expirations_with_flow.return_value = [
            {"expiration": "28MAR26"}
        ]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}
        mock_repo.get_trades_for_flow_analysis.return_value = []
        service = OnChainAnalysisService(repository=mock_repo)

        expected_utc_ms = int(_FrozenDateTime._FIXED_UTC.timestamp() * 1000)

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.datetime", _FrozenDateTime
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            instance = MagicMock()
            instance.calculate.return_value.to_dict.return_value = {"flow_data": {}}
            MockAnalyzer.return_value = instance

            service.get_filtered_aggregate_flow("BTC", "block")

        _, fetch_kwargs = mock_repo.get_trades_for_flow_analysis.call_args
        assert fetch_kwargs["end_ts"] == expected_utc_ms, (
            f"window_end is not UTC-anchored: got {fetch_kwargs['end_ts']}, "
            f"expected {expected_utc_ms} (off by "
            f"{(fetch_kwargs['end_ts'] - expected_utc_ms) / 3_600_000}h)"
        )
