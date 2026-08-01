"""
Unit tests for OnChainAnalysisService._calculate_fixed_strike_vol_matrix
(institutional_metrics_spec.md section 7 / Task C8: report-path service
wiring).

Mirrors test_on_chain_analysis_service_gamma_rolloff.py's/test_on_chain_
analysis_service_exposure_profile.py's pattern: additive computation --
any unexpected failure must degrade to None (no section), never crash the
GEX/DEX pipeline it runs alongside. Distinct from the calculator
legitimately returning ``regime == "INDETERMINATE"`` (missing/stale prior
data): that is NOT a failure and must still be returned, not swallowed.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

TODAY_UTC = date(2026, 7, 31)


def _make_service(repository=None):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


def _instrument(strike, option_type, mark_iv):
    return {"strike": strike, "option_type": option_type, "mark_iv": mark_iv}


class TestNoRepository:
    def test_returns_none_when_no_repository(self):
        service = _make_service(repository=None)
        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", "31JUL26", [_instrument(65000, "C", 34.5)], 65000.0, TODAY_UTC,
        )
        assert result is None


class TestHappyPath:
    def test_queries_repository_for_exactly_yesterday_utc(self):
        """Day-boundary correctness: the repository must be asked for
        EXACTLY today_date_utc - 1 day, never a naive-local-derived date."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [_instrument(65000, "C", 32.0)],
            "underlying_price": 64000.0,
            "source": "daily_oi_snapshots",
        }
        service = _make_service(repository=repository)

        service._calculate_fixed_strike_vol_matrix(
            "BTC", "31JUL26", [_instrument(65000, "C", 34.5)], 64182.0, TODAY_UTC,
        )

        repository.get_chain_iv_at.assert_called_once_with(
            "BTC", "31JUL26", TODAY_UTC - timedelta(days=1),
        )

    def test_returns_populated_result_with_matched_strike(self):
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [_instrument(65000, "C", 32.0)],
            "underlying_price": 64182.0,
            "source": "daily_oi_snapshots",
        }
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", "31JUL26", [_instrument(65000, "C", 34.5)], 64182.0, TODAY_UTC,
        )

        assert isinstance(result, FixedStrikeVolResult)
        assert result.n_strikes_matched == 1
        assert result.rows[0].d_iv == 2.5
        assert result.stale_prior is False
        assert result.prior_date == TODAY_UTC - timedelta(days=1)

    def test_today_rows_read_live_never_re_queried(self):
        """'Today' comes from instruments_with_greeks -- the repository is
        called exactly once (for 'prior' only)."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        service = _make_service(repository=repository)

        service._calculate_fixed_strike_vol_matrix(
            "BTC", "31JUL26", [_instrument(65000, "C", 34.5)], 64182.0, TODAY_UTC,
        )

        assert repository.get_chain_iv_at.call_count == 1


class TestInsufficientHistoryIsNotAFailure:
    def test_empty_prior_rows_still_returns_a_result_marked_indeterminate(self):
        """The repository legitimately finding nothing for 'yesterday' is
        the expected common case right now (section 11 judgment call #4)
        -- must NOT be swallowed into None; the caller needs the actual
        FixedStrikeVolResult to render the insufficient-history message."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", "31JUL26", [_instrument(65000, "C", 34.5)], 64182.0, TODAY_UTC,
        )

        assert result is not None
        assert result.regime == "INDETERMINATE"
        assert result.stale_prior is False  # correct date was queried, it was just empty
        assert result.prior_date == TODAY_UTC - timedelta(days=1)


class TestAdditiveOnlyGuard:
    """A failure computing the matrix must never crash the caller --
    degrades to None, matching _calculate_exposure_profile's and
    _build_gamma_rolloff's established guard."""

    def test_repository_exception_returns_none_not_raises(self, caplog):
        repository = MagicMock()
        repository.get_chain_iv_at.side_effect = RuntimeError("db boom")
        service = _make_service(repository=repository)

        with caplog.at_level("ERROR"):
            result = service._calculate_fixed_strike_vol_matrix(
                "BTC", "31JUL26", [_instrument(65000, "C", 34.5)], 64182.0, TODAY_UTC,
            )

        assert result is None
        assert "fixed-strike vol matrix failed unexpectedly" in caplog.text
