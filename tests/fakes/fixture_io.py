"""
Shared gzip-JSON I/O helpers for recording and replaying the on-chain fixture.

Gzip keeps the recorded fixture (book summary + ~1000+ tickers) small enough to
commit to git without a new dependency (stdlib gzip + json only) — see
refactor_design_spec.md section 7.1.
"""

import datetime as _datetime
import gzip
import json
from decimal import Decimal
from pathlib import Path
from typing import Any


def _json_default(o: Any) -> Any:
    """
    Serialize DB-native types raw psycopg2 rows carry but plain JSON does not:
    Decimal (NUMERIC columns) -> float, date/datetime -> ISO string.

    These fields are not re-derived by the pipeline from their exact recorded
    type (production code that consumes them always calls float()/None-checks,
    never relies on Decimal vs float identity), so lossy-but-equivalent
    conversion here is safe for replay.
    """
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (_datetime.date, _datetime.datetime)):
        return o.isoformat()
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def dump_json_gz(path: Path, obj: Any) -> None:
    """Write ``obj`` as gzip-compressed JSON to ``path``, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, default=_json_default)


def load_json_gz(path: Path) -> Any:
    """Read gzip-compressed JSON from ``path``."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
