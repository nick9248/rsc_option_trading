"""
Task Wave-J-A Fix 3 / Fix 4:
``OnChainAnalysisService._calculate_oi_changes_and_iv_percentile``'s
per-expiry IV percentile section.

Fix 3: the sufficiency gate was ``len(iv_history) >= 5``, inconsistent with
``HistoricalNormalizer.MIN_OBS`` (30) and ``vrp_calculator.py``'s own
``MIN_OBS`` import -- both established elsewhere in this exact codebase for
the identical "enough history to trust a percentile" decision.

Fix 4: ``history_days`` was ``len(historical_ivs)`` -- a raw observation
count -- rendered into a field the formatter labels "N days history".
``get_atm_iv_history`` reads ``daily_oi_snapshots`` (one row per day, real
collection gaps), so N observations commonly span MORE than N calendar
days. Now computed as the real oldest-to-newest calendar span.
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.historical_normalizer import HistoricalNormalizer
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


class _FakeAnalyzer:
    def __init__(self, currency, index_price, enriched_instruments, forward_price_by_expiration):
        self.currency = currency
        self.index_price = index_price
        self.enriched_instruments = enriched_instruments
        self.forward_price_by_expiration = forward_price_by_expiration


def _instrument(strike, option_type, mark_iv=50.0, oi=100.0):
    return {
        "instrument_name": f"BTC-31DEC30-{int(strike)}-{option_type}",
        "strike": strike,
        "option_type": option_type,
        "mark_iv": mark_iv,
        "open_interest": oi,
    }


def _history(n, start=date(2026, 1, 1), mark_iv=50.0):
    """n daily observations, one per calendar day starting at `start` --
    i.e. count == calendar span, for tests that don't care about the
    count-vs-span distinction."""
    from datetime import timedelta

    return [
        {"snapshot_date": start + timedelta(days=i), "mark_iv": mark_iv}
        for i in range(n)
    ]


def _make_repository(atm_iv_history):
    repo = MagicMock()
    repo.save_daily_oi_snapshot.return_value = None
    repo.get_previous_oi_snapshot.return_value = {}
    repo.get_atm_iv_history.return_value = atm_iv_history
    return repo


def _run(service, analyzer):
    builder = MagicMock()
    service._calculate_oi_changes_and_iv_percentile(
        analyzer, progress_callback=lambda msg: None, builder=builder,
    )
    return builder


class TestFix3SufficiencyGateMatchesMinObs:
    def test_below_min_obs_does_not_render_iv_percentile(self):
        """29 observations (MIN_OBS - 1) must NOT render -- the old gate
        (>= 5) would have."""
        repo = _make_repository(_history(HistoricalNormalizer.MIN_OBS - 1))
        service = _make_service(repo)
        instruments = [_instrument(70000, "C", mark_iv=50.0)]
        analyzer = _FakeAnalyzer("BTC", 70000.0, {"31DEC30": instruments}, {"31DEC30": 70000.0})

        builder = _run(service, analyzer)

        builder.set_iv_percentile.assert_not_called()

    def test_five_observations_no_longer_renders(self):
        """
        Pins the actual bug: 5 observations used to be enough to print a
        percentile with a directive and no caveat. Must be gone now.
        """
        repo = _make_repository(_history(5))
        service = _make_service(repo)
        instruments = [_instrument(70000, "C", mark_iv=50.0)]
        analyzer = _FakeAnalyzer("BTC", 70000.0, {"31DEC30": instruments}, {"31DEC30": 70000.0})

        builder = _run(service, analyzer)

        builder.set_iv_percentile.assert_not_called()

    def test_at_or_above_min_obs_renders(self):
        repo = _make_repository(_history(HistoricalNormalizer.MIN_OBS))
        service = _make_service(repo)
        instruments = [_instrument(70000, "C", mark_iv=50.0)]
        analyzer = _FakeAnalyzer("BTC", 70000.0, {"31DEC30": instruments}, {"31DEC30": 70000.0})

        builder = _run(service, analyzer)

        builder.set_iv_percentile.assert_called_once()


class TestFix4HistoryDaysIsCalendarSpanNotCount:
    def test_history_days_is_calendar_span_not_observation_count(self):
        """
        The motivating scenario: 33 observations with real collection gaps
        spanning far more than 33 calendar days (this repo's own recorded
        fixture: 33 rows, 92-day span). history_days must reflect the
        SPAN, not the count.
        """
        from datetime import timedelta

        start = date(2026, 1, 1)
        # 33 observations, but spanning 92 days (gaps between them) --
        # mirrors the real fixture's 33-obs/92-day case.
        gap_days = 92 // 32  # spread 33 points across ~92 days
        history = [
            {"snapshot_date": start + timedelta(days=i * gap_days), "mark_iv": 50.0}
            for i in range(HistoricalNormalizer.MIN_OBS + 3)
        ]
        expected_span = (history[-1]["snapshot_date"] - history[0]["snapshot_date"]).days
        assert expected_span != len(history), "test setup must actually create a gap"

        repo = _make_repository(history)
        service = _make_service(repo)
        instruments = [_instrument(70000, "C", mark_iv=50.0)]
        analyzer = _FakeAnalyzer("BTC", 70000.0, {"31DEC30": instruments}, {"31DEC30": 70000.0})

        builder = _run(service, analyzer)

        builder.set_iv_percentile.assert_called_once()
        _, result = builder.set_iv_percentile.call_args[0]
        assert result.history_days == expected_span
        assert result.history_days != len(history)

    def test_dense_daily_history_span_equals_count_minus_one(self):
        """Sanity: with zero gaps, N observations one day apart span
        exactly N-1 days -- not a magic new number, just the honest
        oldest-to-newest difference."""
        n = HistoricalNormalizer.MIN_OBS + 2
        repo = _make_repository(_history(n))
        service = _make_service(repo)
        instruments = [_instrument(70000, "C", mark_iv=50.0)]
        analyzer = _FakeAnalyzer("BTC", 70000.0, {"31DEC30": instruments}, {"31DEC30": 70000.0})

        builder = _run(service, analyzer)

        _, result = builder.set_iv_percentile.call_args[0]
        assert result.history_days == n - 1
