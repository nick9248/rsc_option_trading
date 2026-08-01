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

Independent review (Task C8 fix round) added: the settled-expiry skip
gate (Minor #5), the get_latest_chain_iv_date wiring that makes T7.3's
diagnostic message reachable in production (Important #3), and a test
that verifies the caller resolves "today" from a UTC clock specifically
(Minor #6) -- not just that -1-day arithmetic is correct given an
already-resolved date, which is all the pre-existing tests below check.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

# Settles 2026-08-01 08:00 UTC -- DTE > 0 relative to NOW_UTC below, so the
# settled-expiry skip gate never fires in the pre-existing tests (that gate
# gets its own dedicated test class further down).
EXPIRATION = "01AUG26"
NOW_UTC = datetime(2026, 7, 31, 10, 0, 0, tzinfo=timezone.utc)
TODAY_UTC = NOW_UTC.date()  # date(2026, 7, 31)


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
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 65000.0, NOW_UTC,
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
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        repository.get_chain_iv_at.assert_called_once_with(
            "BTC", EXPIRATION, TODAY_UTC - timedelta(days=1),
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
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        assert isinstance(result, FixedStrikeVolResult)
        assert result.n_strikes_matched == 1
        assert result.rows[0].d_iv == 2.5
        assert result.stale_prior is False
        assert result.prior_date == TODAY_UTC - timedelta(days=1)

    def test_today_rows_read_live_never_re_queried(self):
        """'Today' comes from instruments_with_greeks -- get_chain_iv_at is
        called exactly once (for 'prior' only)."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        repository.get_latest_chain_iv_date.return_value = None
        service = _make_service(repository=repository)

        service._calculate_fixed_strike_vol_matrix(
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        assert repository.get_chain_iv_at.call_count == 1

    def test_anchor_price_passed_through_unchanged_to_both_sides(self):
        """This method is anchor-agnostic (Important #1's fix lives at the
        CALLER, which must pass the forward price, not the index price --
        see test_on_chain_analysis_service.py's per-expiry-loop tests for
        that). Here, whatever anchor is passed in must reach both
        spot_today (verbatim) and drive spot_prior's independent read from
        the repository -- this method must never silently substitute
        analyzer.index_price itself."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [_instrument(65000, "C", 32.0)],
            "underlying_price": 65010.0,  # the "forward" the repo returned
            "source": "daily_oi_snapshots",
        }
        service = _make_service(repository=repository)

        forward_price_today = 65005.0  # deliberately different from any index price
        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], forward_price_today, NOW_UTC,
        )

        assert result.spot_today == forward_price_today
        assert result.spot_prior == 65010.0


class TestInsufficientHistoryIsNotAFailure:
    def test_empty_prior_rows_looks_up_latest_available_date(self):
        """Independent review (Important #3): when the exact prior date
        comes up empty, the actual most-recent-available date must be
        looked up and surfaced -- not silently dropped. Here there truly
        is none (get_latest_chain_iv_date returns None), so the message
        degrades to 'no snapshot found at all', not a fabricated date."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        repository.get_latest_chain_iv_date.return_value = None
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        repository.get_latest_chain_iv_date.assert_called_once_with(
            "BTC", EXPIRATION, TODAY_UTC - timedelta(days=1),
        )
        assert result is not None
        assert result.regime == "INDETERMINATE"
        assert result.stale_prior is True
        assert result.prior_date is None

    def test_empty_prior_rows_surfaces_the_real_stale_date(self):
        """Independent review (Important #3): T7.3's diagnostic message
        must show the REAL historical date when one exists -- this is what
        makes that branch reachable in production (previously dead code,
        only exercised by a synthetic unit test that handed prior_date
        directly to the calculator)."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        stale_real_date = date(2026, 7, 24)
        repository.get_latest_chain_iv_date.return_value = stale_real_date
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        assert result.stale_prior is True
        assert result.prior_date == stale_real_date

    def test_latest_date_lookup_failure_is_isolated_not_fatal(self, caplog):
        """Independent review's isolation constraint: a failure in the
        diagnostic-only latest-date lookup must degrade to 'no date
        available', never suppress the (already-computed) main result."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        repository.get_latest_chain_iv_date.side_effect = RuntimeError("lookup boom")
        service = _make_service(repository=repository)

        with caplog.at_level("WARNING"):
            result = service._calculate_fixed_strike_vol_matrix(
                "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
            )

        assert result is not None
        assert result.regime == "INDETERMINATE"
        assert result.prior_date is None
        assert "latest available chain IV date" in caplog.text

    def test_nonempty_prior_rows_never_triggers_latest_date_lookup(self):
        """The diagnostic lookup is only for the empty-rows path -- the
        happy path (exact match found) must not pay for an extra query."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [_instrument(65000, "C", 32.0)],
            "underlying_price": 64182.0,
            "source": "daily_oi_snapshots",
        }
        service = _make_service(repository=repository)

        service._calculate_fixed_strike_vol_matrix(
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        repository.get_latest_chain_iv_date.assert_not_called()


class TestSettledExpirySkip:
    """Independent review (Minor #5 / spec 7(c)): 'Expiry gone (settled)
    between the two days -> skip the expiry' -- a dead, cash-settled
    expiry gets no section at all (not even an INDETERMINATE one), and
    the repository is never even queried for it."""

    def test_expiry_already_settled_returns_none_without_querying_repository(self):
        already_settled_expiration = "31JUL26"  # settles 2026-07-31 08:00 UTC
        now_after_settlement = datetime(2026, 7, 31, 10, 0, 0, tzinfo=timezone.utc)
        repository = MagicMock()
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", already_settled_expiration, [_instrument(65000, "C", 34.5)],
            64182.0, now_after_settlement,
        )

        assert result is None
        repository.get_chain_iv_at.assert_not_called()

    def test_expiry_settling_exactly_now_returns_none(self):
        """DTE == 0 (settling this exact instant) is treated as already
        gone, not as one more valid comparison instant."""
        expiring_now = "31JUL26"
        exactly_at_settlement = datetime(2026, 7, 31, 8, 0, 0, tzinfo=timezone.utc)
        repository = MagicMock()
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", expiring_now, [_instrument(65000, "C", 34.5)],
            64182.0, exactly_at_settlement,
        )

        assert result is None
        repository.get_chain_iv_at.assert_not_called()

    def test_unparseable_expiration_returns_none_without_querying_repository(self):
        """DTE is None (expiration string failed to parse) is treated the
        same as 'cannot confirm this expiry is still live' -- skip, don't
        guess."""
        repository = MagicMock()
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", "NOT-A-DATE", [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        assert result is None
        repository.get_chain_iv_at.assert_not_called()

    def test_expiry_still_live_is_not_skipped(self):
        """Sanity check on the gate's boundary: a still-live expiry (DTE
        clearly positive) must reach the repository as normal."""
        repository = MagicMock()
        repository.get_chain_iv_at.return_value = {
            "rows": [], "underlying_price": None, "source": None,
        }
        repository.get_latest_chain_iv_date.return_value = None
        service = _make_service(repository=repository)

        result = service._calculate_fixed_strike_vol_matrix(
            "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
        )

        assert result is not None
        repository.get_chain_iv_at.assert_called_once()


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
                "BTC", EXPIRATION, [_instrument(65000, "C", 34.5)], 64182.0, NOW_UTC,
            )

        assert result is None
        assert "fixed-strike vol matrix failed unexpectedly" in caplog.text


class TestClockResolutionInCaller:
    """Independent review (Minor #6): the one line that reads the clock
    for 'today' (_fetch_greeks_and_store_gex_dex, resolving
    fixed_strike_vol_now_utc) had no test verifying it actually resolves
    from a UTC clock -- every test above hands an already-resolved
    ``now_utc`` in directly, which would not catch a regression back to
    naive-local ``datetime.now()`` at the call site. Patches the module's
    ``datetime`` and asserts the exact patched value flows through to
    _calculate_fixed_strike_vol_matrix's now_utc parameter."""

    def test_caller_resolves_today_from_utc_clock_via_now(self):
        import coding.service.on_chain.on_chain_analysis_service as svc_module

        fake_utc_now = datetime(2026, 7, 31, 1, 30, 0, tzinfo=timezone.utc)

        analyzer = MagicMock()
        analyzer.get_expirations.return_value = [EXPIRATION]
        analyzer.parsed_data = {
            EXPIRATION: [{"instrument_name": f"BTC-{EXPIRATION}-65000-C"}],
        }
        analyzer.index_price = 65000.0
        analyzer.forward_price_by_expiration = {EXPIRATION: 65010.0}
        analyzer.enriched_instruments = {}
        analyzer.currency = "BTC"

        service = _make_service(repository=MagicMock())
        service.api.get_ticker.return_value = {
            "greeks": {"delta": 0.5, "gamma": 0.001, "theta": -10.0, "vega": 20.0},
            "mark_iv": 60.0, "underlying_price": 65010.0,
            "best_bid_price": 0.01, "best_ask_price": 0.02,
        }

        with patch.object(svc_module, "datetime") as mock_datetime, \
             patch.object(svc_module, "GexDexCalculator") as mock_gex_cls, \
             patch.object(service, "_calculate_inferred_dealer_positioning", return_value=None), \
             patch.object(service, "_calculate_exposure_profile", return_value=None), \
             patch.object(service, "_calculate_fixed_strike_vol_matrix", return_value=None) as mock_fsv, \
             patch.object(service, "_build_gamma_rolloff", return_value=None):
            mock_datetime.now.return_value = fake_utc_now
            mock_gex_cls.return_value.calculate.return_value = MagicMock()

            service._fetch_greeks_and_store_gex_dex(analyzer, lambda msg: None, builder=None)

        mock_datetime.now.assert_any_call(timezone.utc)
        assert mock_fsv.called
        # Positional args: (currency, expiration, instruments_with_greeks,
        # spot_today, now_utc, spot_today_is_forward) -- now_utc is
        # second-to-last since fix round 2 added the is-forward flag
        # after it.
        passed_now_utc = mock_fsv.call_args[0][-2]
        assert passed_now_utc == fake_utc_now


def _make_analyzer(expirations, forward_price_by_expiration):
    analyzer = MagicMock()
    analyzer.get_expirations.return_value = expirations
    analyzer.parsed_data = {
        exp: [{"instrument_name": f"BTC-{exp}-65000-C"}] for exp in expirations
    }
    analyzer.index_price = 65000.0
    analyzer.forward_price_by_expiration = forward_price_by_expiration
    analyzer.enriched_instruments = {}
    analyzer.currency = "BTC"
    return analyzer


def _run_loop_with_mocked_fsv(service, analyzer):
    """Shared harness: runs _fetch_greeks_and_store_gex_dex with GEX/DEX
    and the other additive calls stubbed out, capturing every call made
    to _calculate_fixed_strike_vol_matrix. Returns the mock so callers can
    inspect .call_args_list."""
    import coding.service.on_chain.on_chain_analysis_service as svc_module

    service.api.get_ticker.return_value = {
        "greeks": {"delta": 0.5, "gamma": 0.001, "theta": -10.0, "vega": 20.0},
        "mark_iv": 60.0, "underlying_price": 65010.0,
        "best_bid_price": 0.01, "best_ask_price": 0.02,
    }

    with patch.object(svc_module, "GexDexCalculator") as mock_gex_cls, \
         patch.object(service, "_calculate_inferred_dealer_positioning", return_value=None), \
         patch.object(service, "_calculate_exposure_profile", return_value=None), \
         patch.object(service, "_calculate_fixed_strike_vol_matrix", return_value=None) as mock_fsv, \
         patch.object(service, "_build_gamma_rolloff", return_value=None):
        mock_gex_cls.return_value.calculate.return_value = MagicMock()
        service._fetch_greeks_and_store_gex_dex(analyzer, lambda msg: None, builder=None)

    return mock_fsv


class TestCallerForwardPriceIsForwardFlag:
    """Fix round 2 (Low #3): the caller must compute the disclosure flag
    BEFORE substituting the index-price fallback, so it accurately
    reflects what was ACTUALLY used, not just whether a forward price
    happens to exist somewhere."""

    def test_forward_price_available_passes_true(self):
        analyzer = _make_analyzer([EXPIRATION], {EXPIRATION: 65010.0})
        service = _make_service(repository=MagicMock())

        mock_fsv = _run_loop_with_mocked_fsv(service, analyzer)

        assert mock_fsv.called
        spot_today, is_forward = mock_fsv.call_args[0][-3], mock_fsv.call_args[0][-1]
        assert spot_today == 65010.0
        assert is_forward is True

    def test_forward_price_missing_falls_back_and_passes_false(self):
        analyzer = _make_analyzer([EXPIRATION], {})  # no forward price for this expiration
        service = _make_service(repository=MagicMock())

        mock_fsv = _run_loop_with_mocked_fsv(service, analyzer)

        assert mock_fsv.called
        spot_today, is_forward = mock_fsv.call_args[0][-3], mock_fsv.call_args[0][-1]
        assert spot_today == 65000.0  # analyzer.index_price fallback
        assert is_forward is False


class TestCallerResolutionIsolation:
    """Fix round 2 (Low #1): the forward-price resolution + the call into
    _calculate_fixed_strike_vol_matrix now live in their OWN try/except at
    the call site -- a failure there must degrade to 'no fixed-strike-vol
    section for THIS expiry', never abort the remaining per-expiry loop
    (previously an unhandled exception here would have propagated past
    every subsequent expiration's GEX/DEX/exposure-profile processing
    too)."""

    def test_exception_resolving_forward_price_does_not_abort_remaining_expirations(self, caplog):
        expirations = ["01AUG26", "02AUG26"]
        analyzer = _make_analyzer(expirations, {"01AUG26": 65010.0, "02AUG26": 65020.0})

        # Make the FIRST expiration's forward-price lookup raise --
        # .get() on a real dict can't raise, so swap in a dict-like stub
        # that raises only for "01AUG26".
        class _RaisingForwardPrices(dict):
            def get(self, key, default=None):
                if key == "01AUG26":
                    raise RuntimeError("boom")
                return super().get(key, default)

        analyzer.forward_price_by_expiration = _RaisingForwardPrices(
            {"01AUG26": 65010.0, "02AUG26": 65020.0}
        )

        service = _make_service(repository=MagicMock())

        with caplog.at_level("ERROR"):
            mock_fsv = _run_loop_with_mocked_fsv(service, analyzer)

        # The second expiration must still have been processed normally --
        # this is the actual "did it abort the loop" assertion.
        called_expirations = [call.args[1] for call in mock_fsv.call_args_list]
        assert called_expirations == ["02AUG26"]
        assert "failed to resolve the fixed-strike vol matrix anchor price" in caplog.text
