"""
Test doubles for the MenthorQ-style monitor: a record factory, an in-memory
repository with the monitor's DatabaseRepository methods, a fake input
loader and a Telegram fake that records messages (nothing is sent).

The fakes subclass the real classes WITHOUT calling their __init__ (no
connection pool, no .env, no network), so the type checker verifies that
their method signatures match production.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from coding.core.analytics.menthorq_monitor.alert_mute import MenthorqMonitorState
from coding.core.analytics.menthorq_monitor.level_record import (
    MODE_LIVE,
    MenthorqLevelRecord,
    classify_gamma_regime,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader
from coding.service.notifications.telegram_alert_service import TelegramAlertService

BASE_TIME = datetime(2026, 11, 6, 9, 10, tzinfo=timezone.utc)   # a Friday
FAKE_CHAIN_SPOT = 100000.0


def make_record(
    as_of: datetime = BASE_TIME,
    spot: Optional[float] = 100000.0,
    call_resistance: Optional[float] = 110000.0,
    put_support: Optional[float] = 90000.0,
    hvl: Optional[float] = 95000.0,
    total_net_gex: Optional[float] = 5e6,
    expirations: Optional[List[str]] = None,
    mode: str = MODE_LIVE,
    **overrides: Any,
) -> MenthorqLevelRecord:
    """A computed live record with round numbers (override any field)."""
    fields: Dict[str, Any] = dict(
        currency="BTC", mode=mode, as_of_utc=as_of, computed_at_utc=as_of,
        expiry_universe="monthly", gamma_underlying="index", spot=spot,
        spot_time_utc=as_of - timedelta(seconds=30),
        snapshot_captured_at_utc=as_of - timedelta(minutes=20),
        open_interest_captured_at_utc=as_of - timedelta(minutes=20),
        call_resistance=call_resistance, put_support=put_support, hvl=hvl,
        total_net_gex=total_net_gex, gamma_regime=classify_gamma_regime(spot, hvl),
        expirations=expirations if expirations is not None else ["27NOV26", "25DEC26", "29JAN27"],
        panel_levels={}, iv_source_counts={"mark_iv": 900, "rebuilt_from_mark_price": 0, "missing": 0},
        not_computed_reasons=[],
    )
    fields.update(overrides)
    return MenthorqLevelRecord(**fields)


def make_record_without_levels(
    as_of: datetime = BASE_TIME, spot: Optional[float] = 100000.0, **overrides: Any,
) -> MenthorqLevelRecord:
    """
    A NOT-computed record (``has_levels`` False): the four level columns
    None; spot set by default (inputs loaded, nothing computed from them).
    """
    fields: Dict[str, Any] = dict(call_resistance=None, put_support=None, hvl=None,
                                  total_net_gex=None,
                                  not_computed_reasons=["total net GEX not computed: no leg has "
                                                        "both gamma and open interest"])
    fields.update(overrides)
    return make_record(as_of=as_of, spot=spot, **fields)


class FakeMonitorRepository(DatabaseRepository):
    """In-memory version of the monitor's DatabaseRepository methods."""

    def __init__(self, records: Optional[List[MenthorqLevelRecord]] = None):
        self.records: List[MenthorqLevelRecord] = list(records or [])
        self.deliveries: List[Dict[str, Any]] = []
        self.states: Dict[str, MenthorqMonitorState] = {}
        self.fail_state = False   # True: the state methods raise (table missing)
        self.fail_deliveries = False   # True: save_menthorq_alert_deliveries raises, saving nothing
        # Capture times of the "snapshots" table (get_latest_option_chain_capture_at_or_before).
        self.option_chain_captures: List[datetime] = []
        self.capture_queries = 0

    def _same_run_index(self, record: MenthorqLevelRecord) -> Optional[int]:
        for index, stored in enumerate(self.records):
            if (stored.currency, stored.mode, stored.expiry_universe, stored.gamma_underlying,
                    stored.as_of_utc) == (record.currency, record.mode, record.expiry_universe,
                                          record.gamma_underlying, record.as_of_utc):
                return index
        return None

    def save_menthorq_level_snapshot(self, record: MenthorqLevelRecord) -> Optional[int]:
        # Like the real one: a not-computed row is replaced by a computed one, nothing else.
        index = self._same_run_index(record)
        if index is None:
            self.records.append(record)
            return len(self.records)
        if not self.records[index].has_levels and record.has_levels:
            self.records[index] = record
            return index + 1
        return None

    def get_menthorq_level_snapshot(self, currency: str, mode: str, expiry_universe: str,
                                    gamma_underlying: str, as_of_utc: datetime) -> Optional[MenthorqLevelRecord]:
        return next((r for r in self.records
                     if (r.currency, r.mode, r.expiry_universe, r.gamma_underlying, r.as_of_utc)
                     == (currency, mode, expiry_universe, gamma_underlying, as_of_utc)), None)

    def get_latest_option_chain_capture_at_or_before(self, currency: str, as_of: datetime) -> Optional[datetime]:
        self.capture_queries += 1
        earlier = [capture for capture in self.option_chain_captures if capture <= as_of]
        return max(earlier) if earlier else None

    def get_menthorq_level_snapshot_at_or_before(
        self, currency: str, mode: str, expiry_universe: str, gamma_underlying: str,
        instant: datetime, earliest: Optional[datetime] = None, strictly_before: bool = False,
    ) -> Optional[MenthorqLevelRecord]:
        candidates = [
            r for r in self.records
            if r.currency == currency and r.mode == mode and r.expiry_universe == expiry_universe
            and r.gamma_underlying == gamma_underlying and r.has_levels
            and (r.as_of_utc < instant if strictly_before else r.as_of_utc <= instant)
            and (earliest is None or r.as_of_utc >= earliest)
        ]
        return max(candidates, key=lambda r: r.as_of_utc) if candidates else None

    def save_menthorq_alert_deliveries(self, currency: str, alert_keys: List[str], delivered_at: datetime,
                                       message: str, level_snapshot_id: Optional[int] = None) -> None:
        if self.fail_deliveries:
            raise RuntimeError("database down")   # like the real one: nothing is saved
        for alert_key in alert_keys:
            self.deliveries.append({"currency": currency, "alert_key": alert_key,
                                    "delivered_at": delivered_at, "message": message,
                                    "level_snapshot_id": level_snapshot_id})

    def get_last_menthorq_alert_delivery(self, currency: str, alert_key: str) -> Optional[datetime]:
        times = [d["delivered_at"] for d in self.deliveries
                 if d["currency"] == currency and d["alert_key"] == alert_key]
        return max(times) if times else None

    def get_latest_menthorq_level_run(self, currency: str, mode: str, expiry_universe: str,
                                      gamma_underlying: str) -> Optional[MenthorqLevelRecord]:
        candidates = [r for r in self.records
                      if r.currency == currency and r.mode == mode
                      and r.expiry_universe == expiry_universe and r.gamma_underlying == gamma_underlying]
        return max(candidates, key=lambda r: r.as_of_utc) if candidates else None

    def get_last_menthorq_alert_delivery_of_keys(
        self, currency: str, alert_keys: List[str],
    ) -> Optional[Tuple[str, datetime]]:
        matching = [d for d in self.deliveries
                    if d["currency"] == currency and d["alert_key"] in alert_keys]
        if not matching:
            return None
        newest = max(matching, key=lambda d: d["delivered_at"])
        return newest["alert_key"], newest["delivered_at"]

    def _state(self, currency: str) -> MenthorqMonitorState:
        if self.fail_state:
            raise RuntimeError('relation "menthorq_monitor_state" does not exist')
        return self.states.setdefault(currency, MenthorqMonitorState(currency=currency))

    def get_menthorq_monitor_state(self, currency: str) -> Optional[MenthorqMonitorState]:
        if self.fail_state:
            raise RuntimeError('relation "menthorq_monitor_state" does not exist')
        return self.states.get(currency)

    def save_menthorq_alert_mute(self, currency: str, alerts_muted_until: Optional[datetime],
                                 updated_at: datetime) -> None:
        state = self._state(currency)
        state.alerts_muted_until = alerts_muted_until
        state.mute_updated_at = updated_at

    def save_menthorq_suppressed_alerts(self, currency: str, alert_keys: List[str],
                                        suppressed_at: datetime) -> None:
        state = self._state(currency)
        state.last_suppressed_at = suppressed_at
        state.last_suppressed_alert_keys = list(alert_keys)


class FakeInputLoader(MenthorqInputLoader):
    """
    MenthorqInputLoader stand-in. One monthly expiry (27NOV26): a 110,000 call
    and a 90,000 put, 100 BTC open interest each, plus an 80,000 call with no
    open interest (so HVL 90,000 is not at the chain edge), IV 50%, spot 100,000.
    Records every (as_of, open_interest_as_of) it is asked for. The capture
    times are the given ages before their instants (defaults: 21 min for the
    snapshots, 5 s for the index trade).
    """

    def __init__(self, available: bool = True, snapshot_age: timedelta = timedelta(minutes=21),
                 open_interest_age: timedelta = timedelta(minutes=21),
                 index_age: timedelta = timedelta(seconds=5)):
        self.available = available
        self.snapshot_age = snapshot_age
        self.open_interest_age = open_interest_age
        self.index_age = index_age
        self.calls: List[Any] = []

    def load_inputs(
        self, currency: str, as_of: datetime, open_interest_as_of: Optional[datetime] = None
    ) -> Dict[str, Any]:
        self.calls.append((as_of, open_interest_as_of))
        if not self.available:
            raise LookupError("No BTC option-chain snapshot at or before ...")
        rows = [
            {"instrument_name": "BTC-27NOV26-110000-C", "expiration": "27NOV26", "strike": 110000.0,
             "option_type": "C", "open_interest": 100.0, "mark_iv": 50.0,
             "underlying_price": FAKE_CHAIN_SPOT, "mark_price": 0.01, "iv_source": "mark_iv"},
            {"instrument_name": "BTC-27NOV26-90000-P", "expiration": "27NOV26", "strike": 90000.0,
             "option_type": "P", "open_interest": 100.0, "mark_iv": 50.0,
             "underlying_price": FAKE_CHAIN_SPOT, "mark_price": 0.01, "iv_source": "mark_iv"},
            # A listed strike without open interest (net GEX 0) below the put, so
            # the cumulative net GEX minimum (90,000) is not at the chain edge.
            {"instrument_name": "BTC-27NOV26-80000-C", "expiration": "27NOV26", "strike": 80000.0,
             "option_type": "C", "open_interest": 0.0, "mark_iv": 50.0,
             "underlying_price": FAKE_CHAIN_SPOT, "mark_price": 0.2, "iv_source": "mark_iv"},
        ]
        return {
            "snapshot_captured_at_utc": as_of - self.snapshot_age,
            "open_interest_captured_at_utc": (open_interest_as_of or as_of) - self.open_interest_age,
            "chain_rows": rows,
            "iv_source_counts": {"mark_iv": 3, "rebuilt_from_mark_price": 0, "missing": 0},
            "index_price": FAKE_CHAIN_SPOT,
            "index_price_time_utc": as_of - self.index_age,
        }


class FakeTelegram(TelegramAlertService):
    """
    Records sent messages; ``accept`` decides whether a send succeeds.
    ``updates`` are served by get_updates (None when ``fail_updates``; a
    negative offset keeps and returns only the last updates, like Telegram);
    set_my_commands calls are kept in ``registered_commands``; getMe answers
    ``bot_username`` (None: getMe fails).
    """

    def __init__(self, accept: bool = True, chat_id: str = "111",
                 updates: Optional[List[Dict[str, Any]]] = None, fail_updates: bool = False):
        self._chat_id = chat_id
        self._token = "fake-token"
        self.accept = accept
        self.fail_updates = fail_updates
        self.sent: List[Dict[str, Any]] = []
        self.updates = list(updates or [])
        self.get_updates_calls: List[Dict[str, Any]] = []
        self.registered_commands: List[List[Tuple[str, str]]] = []
        self.accept_commands = True
        self.bot_username: Optional[str] = "MenthorBot"   # None: getMe fails
        self.get_bot_username_calls = 0

    def send(self, message: str, parse_mode: Optional[str] = None) -> bool:
        self.sent.append({"message": message, "parse_mode": parse_mode})
        return self.accept

    def set_my_commands(self, commands: Sequence[Tuple[str, str]]) -> bool:
        self.registered_commands.append(list(commands))
        return self.accept_commands

    def get_bot_username(self) -> Optional[str]:
        self.get_bot_username_calls += 1
        return self.bot_username

    def get_updates(self, offset: Optional[int], timeout_seconds: int) -> Optional[List[Dict[str, Any]]]:
        self.get_updates_calls.append({"offset": offset, "timeout_seconds": timeout_seconds})
        if self.fail_updates:
            return None
        if offset is not None and offset < 0:
            # Like Telegram: the last -offset updates; every earlier one is forgotten.
            self.updates = self.updates[offset:]
            return list(self.updates)
        return [update for update in self.updates if offset is None or update["update_id"] >= offset]
