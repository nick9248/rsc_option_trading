"""
Test doubles for the MenthorQ brief preview and the MenthorQ Monitor tab:
a monitor service whose records are fixed per instant (no input loading),
and a tiny option-chain repository for a real chart run without a database.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.menthorq_monitor.level_record import (
    MODE_DAILY_CLOSE,
    MODE_LIVE,
    MenthorqLevelRecord,
    build_not_computed_record,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import (
    MenthorqMonitorService,
    daily_close_as_of,
)
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from tests.fakes.fake_menthorq_monitor import (
    BASE_TIME,
    FakeInputLoader,
    FakeMonitorRepository,
    make_record,
)

CHART_CAPTURED_AT = datetime(2026, 11, 6, 23, 50, tzinfo=timezone.utc)


RecordKey = Tuple[str, datetime]   # (mode, as_of)


def moving_records(latest_as_of: datetime = BASE_TIME) -> Dict[RecordKey, MenthorqLevelRecord]:
    """
    Records by (mode, instant), chosen so four alert rules fire at ``latest_as_of``
    (spot 100,000 everywhere):
      - latest: CR 110,000, PS 90,000, HVL 95,000, net GEX +5M (positive gamma)
      - 1 h earlier: HVL 101,000 (negative gamma), net GEX -1M
          -> spot crossed ABOVE HVL, net GEX turned POSITIVE
      - 24 h earlier: CR 106,000, PS 84,000, HVL 91,000
          -> HVL +4,000 = +4.00% of spot (> 3: fires),
             CR +4,000 = +4.00% (not > 5: quiet), PS +6,000 = +6.00% (> 5: fires)
      - 7 d earlier: same as latest
      - daily close of the previous UTC date: same levels as latest
    """
    previous_day = (latest_as_of - timedelta(days=1)).date()
    return {
        (MODE_LIVE, latest_as_of): make_record(latest_as_of),
        (MODE_LIVE, latest_as_of - timedelta(hours=1)): make_record(
            latest_as_of - timedelta(hours=1), hvl=101000.0, total_net_gex=-1e6),
        (MODE_LIVE, latest_as_of - timedelta(hours=24)): make_record(
            latest_as_of - timedelta(hours=24), call_resistance=106000.0, put_support=84000.0,
            hvl=91000.0),
        (MODE_LIVE, latest_as_of - timedelta(days=7)): make_record(latest_as_of - timedelta(days=7)),
        (MODE_DAILY_CLOSE, daily_close_as_of(previous_day)): make_record(
            daily_close_as_of(previous_day), mode=MODE_DAILY_CLOSE),
    }


class FixedRecordMonitorService(MenthorqMonitorService):
    """
    MenthorqMonitorService whose ``compute_record`` returns the record for
    the asked (mode, instant), or a not-computed record when there is none.
    ``store`` fails: a preview must never store.
    """

    def __init__(self, records: Dict[RecordKey, MenthorqLevelRecord],
                 settings: Optional[MenthorqMonitorSettings] = None):
        self.fake_repository = FakeMonitorRepository()
        super().__init__(repository=self.fake_repository, settings=settings,
                         input_loader=FakeInputLoader(available=False),
                         clock=lambda: BASE_TIME)
        self.records = records
        self.asked: List[Any] = []

    def compute_record(self, mode: str, as_of: datetime,
                       open_interest_as_of: Optional[datetime] = None) -> MenthorqLevelRecord:
        self.asked.append((mode, as_of, open_interest_as_of))
        record = self.records.get((mode, as_of))
        if record is None:
            return build_not_computed_record(
                self.settings.currency, mode, as_of, BASE_TIME, self.settings.expiry_universe,
                self.settings.gamma_underlying, "inputs not available: no snapshot")
        return record

    def store(self, record: MenthorqLevelRecord) -> MenthorqLevelRecord:
        raise AssertionError("a preview must not store records")


class TinyChainRepository(DatabaseRepository):
    """
    Option chain at index 100 (no connection pool: __init__ is not called):
    27NOV26 with a call (K=100) and a put (K=90), and 4DEC26 whose only row
    has no open interest.
    """

    def __init__(self):
        pass

    def get_option_chain_snapshot_at_or_before(self, currency: str, as_of: datetime) -> Any:
        return min(as_of, CHART_CAPTURED_AT), [
            {"instrument_name": "BTC-27NOV26-100-C", "expiration": "27NOV26", "strike": 100.0,
             "option_type": "C", "open_interest": 10.0, "mark_iv": 50.0,
             "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-27NOV26-90-P", "expiration": "27NOV26", "strike": 90.0,
             "option_type": "P", "open_interest": 30.0, "mark_iv": 50.0,
             "underlying_price": 100.0, "mark_price": None},
            {"instrument_name": "BTC-4DEC26-100-C", "expiration": "4DEC26", "strike": 100.0,
             "option_type": "C", "open_interest": None, "mark_iv": 50.0,
             "underlying_price": 100.0, "mark_price": None},
        ]

    def get_index_price_at_or_before(self, currency: str, as_of: datetime) -> Any:
        return 100.0, as_of - timedelta(seconds=30)


DAILY_CLOSE_DATE = date(2026, 11, 6)
