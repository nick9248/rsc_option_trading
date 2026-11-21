"""
How the picks of the model did against the baselines (pure).

For every evaluated pick the realised result is in units of the maximum loss (R). The model's result is the chosen candidate's R, or 0 when it
chose no_trade (it risked nothing). It is compared, date by date, with the deterministic baseline's top candidate, the average of the shortlist
and the average of all candidates of that instant.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import numpy as np


@dataclass(frozen=True)
class PickReport:
    """Paired comparison over the evaluated picks."""

    picks: int
    no_trade_share: Optional[float]
    mean_model_r: Optional[float]
    mean_baseline_r: Optional[float]
    mean_shortlist_r: Optional[float]
    mean_all_r: Optional[float]
    model_minus_baseline: Optional[float]
    model_minus_baseline_t: Optional[float]
    model_minus_shortlist: Optional[float]
    model_minus_shortlist_t: Optional[float]
    model_minus_all: Optional[float]
    model_minus_all_t: Optional[float]
    model_beat_baseline_share: Optional[float]


def _paired(model: np.ndarray, other: np.ndarray):
    difference = model - other
    if len(difference) < 2 or difference.std(ddof=1) == 0:
        return float(difference.mean()), None
    return float(difference.mean()), float(difference.mean() / (difference.std(ddof=1) / math.sqrt(len(difference))))


def summarize_picks(rows: Sequence[Dict[str, Any]]) -> PickReport:
    """
    The report of the evaluated picks (rows of ``strategy_picks`` with their ``outcomes``); a pick without a usable choice or evaluation is left out.
    """
    model: List[float] = []
    baseline: List[float] = []
    shortlist: List[float] = []
    everything: List[float] = []
    no_trade = 0
    for row in rows:
        outcomes = row.get("outcomes")
        if not outcomes or row.get("choice") is None or row.get("baseline_id") not in outcomes:
            continue
        chosen = row["choice"]
        if chosen != "no_trade" and chosen not in outcomes:
            continue
        no_trade += chosen == "no_trade"
        model.append(0.0 if chosen == "no_trade" else outcomes[chosen]["r"])
        baseline.append(outcomes[row["baseline_id"]]["r"])
        shortlist.append(outcomes["_shortlist_mean_r"])
        everything.append(outcomes["_all_mean_r"])
    count = len(model)
    if count == 0:
        return PickReport(0, None, None, None, None, None, None, None, None, None, None, None, None)
    model_array = np.asarray(model)
    versus_baseline, baseline_t = _paired(model_array, np.asarray(baseline))
    versus_shortlist, shortlist_t = _paired(model_array, np.asarray(shortlist))
    versus_all, all_t = _paired(model_array, np.asarray(everything))
    return PickReport(count, no_trade / count, float(model_array.mean()), float(np.mean(baseline)), float(np.mean(shortlist)),
                      float(np.mean(everything)), versus_baseline, baseline_t, versus_shortlist, shortlist_t, versus_all, all_t,
                      float(np.mean(model_array > np.asarray(baseline))))
