"""
MenthorqReplicationService with in-memory fake repositories (no database):
point-in-time guards, the older-open-interest option, and the MenthorQ
daily-close and live entry points (hand-calculated levels).
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import pytest
from matplotlib.figure import Figure

from coding.service.on_chain.menthorq_replication_service import (
    MenthorqReplicationService,
    last_complete_utc_day,
    menthorq_daily_close_instants,
)

AS_OF = datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)
EARLIER = datetime(2026, 11, 6, 8, 59, tzinfo=timezone.utc)


class FakeRepository:
    """Serves a 'current' snapshot at/before AS_OF and an older one at/before EARLIER."""

    def __init__(self, has_index=True):
        self.has_index = has_index

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        if as_of >= AS_OF:
            return AS_OF - timedelta(minutes=10), [
                {"instrument_name": "A", "expiration": "27NOV26", "strike": 1.0,
                 "option_type": "C", "open_interest": 500.0, "mark_iv": 40.0,
                 "underlying_price": 1.0},
                {"instrument_name": "B", "expiration": "27NOV26", "strike": 1.0,
                 "option_type": "P", "open_interest": 7.0, "mark_iv": 40.0,
                 "underlying_price": 1.0},
            ]
        if as_of >= EARLIER:
            return EARLIER - timedelta(minutes=10), [
                {"instrument_name": "A", "open_interest": 200.0},
            ]
        return None

    def get_index_price_at_or_before(self, currency, as_of):
        return (75000.0, as_of - timedelta(seconds=7)) if self.has_index else None


def test_open_interest_taken_from_older_snapshot_and_missing_stays_none():
    inputs = MenthorqReplicationService(cast(Any, FakeRepository())).load_inputs("BTC", AS_OF, EARLIER)
    by_name = {row["instrument_name"]: row for row in inputs["chain_rows"]}
    assert by_name["A"]["open_interest"] == 200.0
    assert by_name["A"]["mark_iv"] == 40.0          # IV still from AS_OF
    assert by_name["B"]["open_interest"] is None    # absent earlier -> None, not 0
    assert inputs["open_interest_captured_at_utc"] == EARLIER - timedelta(minutes=10)
    assert by_name["A"]["iv_source"] == "mark_iv"


class FakeRepositoryWithoutMarkIv(FakeRepository):
    """One row without mark_iv but with mark_price, one with neither."""

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        # 27NOV26 settles 2026-11-27 08:00; captured 2025-11-27 08:00 -> tau = 1 year.
        # Black-76 at F = K = 100, sigma 0.5: 19.7413 USD = 0.197413 BTC.
        captured = datetime(2025, 11, 27, 8, 0, tzinfo=timezone.utc)
        return captured, [
            {"instrument_name": "A", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "C", "open_interest": 1.0, "mark_iv": None,
             "mark_price": 0.197413, "underlying_price": 100.0},
            {"instrument_name": "B", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "P", "open_interest": 1.0, "mark_iv": None,
             "mark_price": None, "underlying_price": 100.0},
        ]


def test_missing_mark_iv_is_rebuilt_from_mark_price_and_labelled(caplog):
    with caplog.at_level("WARNING"):
        inputs = MenthorqReplicationService(cast(Any, FakeRepositoryWithoutMarkIv())).load_inputs("BTC", AS_OF)
    by_name = {row["instrument_name"]: row for row in inputs["chain_rows"]}
    assert by_name["A"]["mark_iv"] == pytest.approx(50.0, abs=1e-3)
    assert by_name["A"]["iv_source"] == "rebuilt_from_mark_price"
    assert by_name["B"]["mark_iv"] is None
    assert by_name["B"]["iv_source"] == "missing"
    assert inputs["iv_source_counts"] == {"mark_iv": 0, "rebuilt_from_mark_price": 1, "missing": 1}
    assert "rebuilt from mark_price" in caplog.text


def test_open_interest_after_as_of_is_rejected_as_lookahead():
    service = MenthorqReplicationService(cast(Any, FakeRepository()))
    with pytest.raises(ValueError):
        service.load_inputs("BTC", EARLIER, AS_OF)


def test_missing_index_price_raises_instead_of_using_a_default():
    service = MenthorqReplicationService(cast(Any, FakeRepository(has_index=False)))
    with pytest.raises(LookupError):
        service.load_inputs("BTC", AS_OF)


def test_missing_snapshot_raises():
    service = MenthorqReplicationService(cast(Any, FakeRepository()))
    with pytest.raises(LookupError):
        service.load_inputs("BTC", EARLIER - timedelta(days=1))


# --------------------------------------------------------------------------
# MenthorQ daily close and live entry points (used by the GUI tab)
# --------------------------------------------------------------------------

DAILY_CLOSE_AS_OF = datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)
LATEST_CAPTURE = datetime(2026, 11, 7, 9, 48, 59, tzinfo=timezone.utc)

# Hand calculation (Black-Scholes, r = 0) for the chain below at index 100,
# IV 50%, tau = 27NOV26 08:00 - 2026-11-06 23:59 = 0.0557097 years:
#   call K=100 OI 10: gamma * OI * S^2 * 0.01 = +33.7457 USD per 1% move
#   put  K=90  OI 30:                         = -64.4739 (net GEX sign)
# Call Resistance = 100 (only positive), Put Support = 90 (only negative),
# HVL = 90 (cumulative net GEX -64.47 at 90 < -30.73 at 100), and the only
# expiry carries net / sum|net| = -100% of the GEX.
HAND_CALCULATED_CALL_GEX_AT_100 = 33.7457
HAND_CALCULATED_NET_GEX_AT_90 = -64.4739


class RecordingRepository:
    """Serves one two-row chain at or before any instant and records each request."""

    def __init__(self, captured_at=LATEST_CAPTURE, open_interest_scale=1.0):
        self.captured_at = captured_at
        self.open_interest_scale = open_interest_scale
        self.snapshot_requests = []
        self.index_requests = []

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        self.snapshot_requests.append(as_of)
        if as_of < datetime(2026, 1, 1, tzinfo=timezone.utc):
            return None
        captured = min(as_of, self.captured_at)
        return captured, [
            {"instrument_name": "BTC-27NOV26-100-C", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "C", "open_interest": 10.0 * self.open_interest_scale,
             "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-27NOV26-90-P", "expiration": "27NOV26", "strike": 90.0,
             "option_type": "P", "open_interest": 30.0 * self.open_interest_scale,
             "mark_iv": 50.0, "underlying_price": 100.0, "mark_price": None},
        ]

    def get_index_price_at_or_before(self, currency, as_of):
        self.index_requests.append(as_of)
        return 100.0, as_of - timedelta(seconds=30)


def _service(repository) -> MenthorqReplicationService:
    """Service on an in-memory fake (duck-typed stand-in for DatabaseRepository)."""
    return MenthorqReplicationService(cast(Any, repository))


def test_daily_close_uses_2359_and_open_interest_from_midnight_with_hand_calculated_levels(tmp_path):
    repository = RecordingRepository(captured_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    output = _service(repository).run_menthorq_daily_close(
        "BTC", date(2026, 11, 6), tmp_path, now=LATEST_CAPTURE)

    assert output["run_mode"] == "menthorq_daily_close"
    assert output["as_of_utc"] == "2026-11-06T23:59:00+00:00"
    assert repository.snapshot_requests == [DAILY_CLOSE_AS_OF,
                                            datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)]
    assert repository.index_requests == [DAILY_CLOSE_AS_OF]
    assert output["inputs"]["open_interest_captured_at_utc"] == "2026-11-06T00:00:00+00:00"

    replication = output["configurations"][output["primary_configuration"]]["replication"]
    strikes = {row["strike"]: row for row in replication["gex_by_expiration"]["27NOV26"]["strikes"]}
    assert strikes[100.0]["net_gex"] == pytest.approx(HAND_CALCULATED_CALL_GEX_AT_100, abs=1e-3)
    assert strikes[90.0]["net_gex"] == pytest.approx(HAND_CALCULATED_NET_GEX_AT_90, abs=1e-3)
    levels = output["per_expiry_iv_times_open_interest"][0]["levels"]
    assert levels == {"call_resistance": 100.0, "put_support": 90.0, "hvl": 90.0}
    assert replication["gex_expiring_percent"]["27NOV26"]["net_over_absolute_net_sum"] == -100.0
    for file_name in output["charts"].values():
        assert (tmp_path / file_name).stat().st_size > 0


def test_daily_close_of_a_day_that_has_not_ended_is_rejected(tmp_path):
    service = _service(RecordingRepository())
    with pytest.raises(ValueError, match="has not ended"):
        service.run_menthorq_daily_close(
            "BTC", date(2026, 11, 7), tmp_path,
            now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize("now", [
    datetime(2026, 11, 6, 23, 59, 0, tzinfo=timezone.utc),    # the 23:59 instant itself
    datetime(2026, 11, 6, 23, 59, 30, tzinfo=timezone.utc),   # after 23:59, day still running
    datetime(2026, 11, 6, 23, 59, 59, 999999, tzinfo=timezone.utc),
])
def test_daily_close_before_the_utc_day_ended_is_rejected(tmp_path, now):
    """last_complete_utc_day(6 Nov 23:59:xx) = 5 Nov < 6 Nov: rejected (the old guard
    compared 23:59:00 with now and let these through)."""
    service = _service(RecordingRepository(captured_at=datetime(2026, 12, 1, tzinfo=timezone.utc)))
    with pytest.raises(ValueError, match=r"2026-11-06 has not ended .*\(newest complete UTC day: 2026-11-05"):
        service.run_menthorq_daily_close("BTC", date(2026, 11, 6), tmp_path, now=now)


def test_daily_close_at_midnight_of_the_next_day_is_allowed(tmp_path):
    """last_complete_utc_day(7 Nov 00:00:00) = 6 Nov: the 6 Nov close may be replayed."""
    repository = RecordingRepository(captured_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    output = _service(repository).run_menthorq_daily_close(
        "BTC", date(2026, 11, 6), tmp_path, now=datetime(2026, 11, 7, 0, 0, 0, tzinfo=timezone.utc))
    assert output["run_mode"] == "menthorq_daily_close"


def test_live_uses_the_newest_snapshot_capture_time_as_as_of(tmp_path):
    repository = RecordingRepository()
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    output = _service(repository).run_live("BTC", tmp_path, now=now)

    assert output["run_mode"] == "live"
    assert output["as_of_utc"] == LATEST_CAPTURE.isoformat()
    # First lookup finds the capture; the replay then reads at that capture.
    assert repository.snapshot_requests == [now, LATEST_CAPTURE]
    assert repository.index_requests == [LATEST_CAPTURE]
    assert output["inputs"]["open_interest_captured_at_utc"] == LATEST_CAPTURE.isoformat()
    assert output["inputs"]["index_price_time_utc"] == (LATEST_CAPTURE - timedelta(seconds=30)).isoformat()
    assert all("_live_" in file_name for file_name in output["charts"].values())


def test_live_without_any_snapshot_raises(tmp_path):
    service = _service(RecordingRepository())
    with pytest.raises(LookupError):
        service.run_live("BTC", tmp_path, now=datetime(2025, 6, 1, tzinfo=timezone.utc))


def test_eth_charts_are_titled_with_eth(tmp_path, monkeypatch):
    titles = []
    original_text = Figure.text

    def recording_text(figure, x, y, text, *arguments, **keywords):
        titles.append(text)
        return original_text(figure, x, y, text, *arguments, **keywords)

    monkeypatch.setattr(Figure, "text", recording_text)
    _service(RecordingRepository()).run_live(
        "ETH", tmp_path, now=datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc))
    assert any("ETHUSD" in title for title in titles)
    assert not any("BTCUSD" in title for title in titles)


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc), date(2026, 11, 6)),
    (datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc), date(2026, 11, 6)),
    # 01:00 on 7 Nov in UTC+02:00 is 23:00 UTC on 6 Nov: 5 Nov is the last complete day.
    (datetime(2026, 11, 7, 1, 0, tzinfo=timezone(timedelta(hours=2))), date(2026, 11, 5)),
])
def test_last_complete_utc_day(now, expected):
    assert last_complete_utc_day(now) == expected


def test_last_complete_utc_day_rejects_naive_time():
    with pytest.raises(ValueError):
        last_complete_utc_day(datetime(2026, 11, 7, 12, 0))


def test_daily_close_instants():
    assert menthorq_daily_close_instants(date(2026, 8, 24)) == (
        datetime(2026, 8, 24, 23, 59, tzinfo=timezone.utc),
        datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------------
# Output layouts: "flat" (default) and "nested" (MenthorQ Charts tab)
# --------------------------------------------------------------------------

# 23:59 UTC replay with open interest from 00:00 UTC of 6 Nov 2026.
DAILY_CLOSE_STEM = "btc_20261106T2359Z_open_interest_20261106T0000Z"
# RecordingRepository lists only 27NOV26, a monthly expiry: three chart kinds
# for the monthly universe and the all-listed variant, one per-expiry chart.
EXPECTED_NESTED_FILES = sorted([
    f"01_iv_times_open_interest/{DAILY_CLOSE_STEM}_iv_times_open_interest.png",
    f"01_iv_times_open_interest/{DAILY_CLOSE_STEM}_iv_times_open_interest_all_listed_expiries.png",
    f"02_iv_times_open_interest_per_expiry/{DAILY_CLOSE_STEM}_iv_times_open_interest_27NOV26.png",
    f"03_net_gex_panels/{DAILY_CLOSE_STEM}_net_gex_panels.png",
    f"03_net_gex_panels/{DAILY_CLOSE_STEM}_net_gex_panels_all_listed_expiries.png",
    f"04_net_dex/{DAILY_CLOSE_STEM}_net_dex.png",
    f"04_net_dex/{DAILY_CLOSE_STEM}_net_dex_all_listed_expiries.png",
    "README.txt",
    "replication.json",
])


def _daily_close(directory, **keywords):
    """Daily close of 6 Nov 2026 on a repository whose snapshot is newer than any request."""
    repository = RecordingRepository(captured_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    return _service(repository).run_menthorq_daily_close(
        "BTC", date(2026, 11, 6), directory, now=LATEST_CAPTURE, **keywords)


def _files_under(directory):
    return sorted(path.relative_to(directory).as_posix()
                  for path in directory.rglob("*") if path.is_file())


def test_flat_layout_is_the_default_and_unchanged(tmp_path):
    output = _daily_close(tmp_path)
    assert _files_under(tmp_path) == sorted(
        [name.split("/")[-1] for name in EXPECTED_NESTED_FILES if name.endswith(".png")]
        + [f"{DAILY_CLOSE_STEM}_replication.json"])
    assert output["charts"]["net_gex_panels"] == f"{DAILY_CLOSE_STEM}_net_gex_panels.png"
    assert output["per_expiry_iv_times_open_interest"][0]["chart"] == (
        f"{DAILY_CLOSE_STEM}_iv_times_open_interest_27NOV26.png")
    assert "chart_layout" not in output


def test_nested_layout_writes_the_dated_subfolders(tmp_path):
    output = _daily_close(tmp_path, chart_layout="nested")
    date_folder = tmp_path / "2026-11-06"
    assert _files_under(tmp_path) == [f"2026-11-06/{name}" for name in EXPECTED_NESTED_FILES]

    assert output["chart_layout"] == "nested"
    assert output["charts"] == {
        "iv_times_open_interest":
            f"01_iv_times_open_interest/{DAILY_CLOSE_STEM}_iv_times_open_interest.png",
        "net_gex_panels": f"03_net_gex_panels/{DAILY_CLOSE_STEM}_net_gex_panels.png",
        "net_dex": f"04_net_dex/{DAILY_CLOSE_STEM}_net_dex.png",
        "iv_times_open_interest_all_listed_expiries":
            f"01_iv_times_open_interest/{DAILY_CLOSE_STEM}_iv_times_open_interest_all_listed_expiries.png",
        "net_gex_panels_all_listed_expiries":
            f"03_net_gex_panels/{DAILY_CLOSE_STEM}_net_gex_panels_all_listed_expiries.png",
        "net_dex_all_listed_expiries":
            f"04_net_dex/{DAILY_CLOSE_STEM}_net_dex_all_listed_expiries.png",
    }
    # Every chart reference in the written JSON resolves from the date folder.
    written = json.loads((date_folder / "replication.json").read_text(encoding="utf-8"))
    references = list(written["charts"].values()) + [
        entry["chart"] for entry in written["per_expiry_iv_times_open_interest"]]
    assert len(references) == 7
    assert all((date_folder / reference).stat().st_size > 0 for reference in references)


def test_nested_numbers_equal_the_flat_ones(tmp_path):
    flat = json.loads(json.dumps(_daily_close(tmp_path / "flat"), default=str))
    nested = json.loads(json.dumps(_daily_close(tmp_path / "nested", chart_layout="nested"),
                                   default=str))
    assert nested.pop("chart_layout") == "nested"
    for output in (flat, nested):
        output["charts"] = {key: name.split("/")[-1] for key, name in output["charts"].items()}
        for entry in output["per_expiry_iv_times_open_interest"]:
            entry["chart"] = entry["chart"].split("/")[-1]
    assert nested == flat


def test_nested_readme_lists_the_folders_and_run_settings(tmp_path):
    _daily_close(tmp_path, chart_layout="nested")
    readme = (tmp_path / "2026-11-06" / "README.txt").read_bytes()
    assert readme.isascii()
    lines = readme.decode("ascii").splitlines()
    assert lines[0] == "MenthorQ-style charts, BTC, menthorq_daily_close, 2026-11-06"
    for expected in (
        "  As of (spot and IV):  2026-11-06 23:59:00 UTC",
        "  Snapshot captured:    2026-11-06 23:59:00 UTC",
        "  Open interest:        snapshot at or before 2026-11-06 00:00:00 UTC, "
        "captured 2026-11-06 00:00:00 UTC",
        "  Index price:          100.00 (trade at 2026-11-06 23:58:30 UTC)",
        "  IV source:            mark_iv (all 2 rows)",
        "  Expiry universes:     monthly (file names without suffix) and all listed "
        "(suffix _all_listed_expiries)",
        "  Gamma at:             index",
        "  01_iv_times_open_interest/            IV*OI over all expiries of the universe (2 files)",
        "  02_iv_times_open_interest_per_expiry/ IV*OI of one listed expiry per chart (1 files)",
        "  03_net_gex_panels/                    Net GEX, four expiry panels (2 files)",
        "  04_net_dex/                           Net DEX over all expiries of the universe (2 files)",
    ):
        assert expected in lines


def test_nested_rerun_overwrites_its_files_and_keeps_other_files(tmp_path):
    _daily_close(tmp_path, chart_layout="nested")
    date_folder = tmp_path / "2026-11-06"
    (date_folder / "my_notes.txt").write_text("keep me", encoding="utf-8")
    (date_folder / "03_net_gex_panels" / "my_copy.png").write_bytes(b"keep")
    readme_before = (date_folder / "README.txt").read_bytes()

    _daily_close(tmp_path, chart_layout="nested")
    assert _files_under(date_folder) == sorted(
        EXPECTED_NESTED_FILES + ["my_notes.txt", "03_net_gex_panels/my_copy.png"])
    assert (date_folder / "my_notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (date_folder / "README.txt").read_bytes() == readme_before


def test_unknown_chart_layout_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="chart_layout"):
        _daily_close(tmp_path, chart_layout="tree")
