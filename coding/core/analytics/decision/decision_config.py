"""
Every weight, threshold and prior of the decision maker, in one place.

NONE of these values is calibrated. There is no ground truth yet (no recorded decision has been scored against what the
price did), so each value is a prior: a stated, reasoned starting point that the owner can discuss and that calibration
from ``decision_snapshots`` will later replace. The engine (``scenario_engine.py``, ``price_map.py``) reads only this
module; it hard-codes no number of its own. Each constant carries a docstring saying why it has its value.

Scales used below:
* strength and quality are 0..1 (see ``EvidenceItem``);
* a "group contribution" is a signed number, at most ``GROUP_CONTRIBUTION_CAP`` in size;
* the evidence score is 0..100, uncalibrated, and is NOT a probability (scenario scores do not add up to anything).
"""

from typing import Dict

# ---------------------------------------------------------------- scale of the evidence score

EVIDENCE_SCORE_SCALE = "0-100, uncalibrated: relative support, not a probability"
"""Printed next to every score so no reader takes it for a probability."""

SCORE_HALF_POINT = 1.0
"""
Net support that maps to a score of 50 (score = 100 x net / (net + SCORE_HALF_POINT)). 1.0 is the contribution of one
full-strength, full-quality input group, so a score of 50 means "about one perfect source's worth of net
support". The curve saturates, so adding groups raises the score with diminishing returns and never reaches 100.
"""

CONFLICT_PENALTY = 1.0
"""
How much opposing support is subtracted from supporting support (net = support - CONFLICT_PENALTY x opposition).
1.0 treats one unit of opposition as cancelling one unit of support: no reason yet to weigh either side more.
"""

# ---------------------------------------------------------------- aggregation of input groups

DEFAULT_GROUP_WEIGHT = 1.0
"""Weight of an input group that has no entry in ``GROUP_WEIGHTS``: equal weights, because nothing measures yet which group is better."""

GROUP_WEIGHTS: Dict[str, float] = {
    "deribit_open_interest": 1.0,
    "deribit_trades": 1.0,
    "binance_perpetual_candles": 1.0,
}
"""
Weight of each input group in the combination. All equal (1.0) on purpose: without recorded outcomes there is no basis for
trusting one data source more than another. Quality (per evidence item) already carries the known weaknesses.
"""

GROUP_CONTRIBUTION_CAP = 1.0
"""
Largest size of one input group's contribution to one scenario (and to one level's weight). Stops a group with many
items (for example many open-interest levels) from outvoting the others: one group is one opinion, however many items it
produced. 1.0 = one full-strength, full-quality item.
"""

MINIMUM_AGREEING_CONTRIBUTION = 0.05
"""A group counts as "agreeing" with a scenario only when its contribution is above this; smaller is noise-level support."""

# ---------------------------------------------------------------- price map

LEVEL_BAND_SHARE = 0.01
"""
Width of a price band, as a share of the spot (1 %). Levels of different sources within one band are taken as the same
level. 1 % is the band the Levels page uses for confluences; it is about one or two strike steps for BTC and ETH.
"""

MAXIMUM_LEVEL_DISTANCE_SHARE = 0.15
"""
Levels further than this share of the spot (15 %) are left out of the map. Beyond it a side is reported as "unmapped"
rather than filled with a far level; 15 % is the window the Levels chart draws and roughly a month of typical BTC range.
"""

MINIMUM_LEVEL_WEIGHT = 0.3
"""
A band is a "meaningful" level only when its weight (sum over its distinct input groups of quality-weighted mean strength x quality-weighted mean quality)
reaches this. 0.3 = one item of about medium quality; a lone low-quality item (a failed check, a volume strike) stays below.
"""

# ---------------------------------------------------------------- freshness (applied by the engine to every item)

DEFAULT_FRESHNESS_LIMIT_HOURS = 6.0
"""Age after which an item of an unknown input group counts as stale; 6 h is twice the slowest hourly feed's normal lag."""

FRESHNESS_LIMIT_HOURS: Dict[str, float] = {
    "deribit_open_interest": 3.0,
    "deribit_trades": 3.0,
    "binance_perpetual_candles": 3.0,
}
"""
Age (hours) after which an item of a group counts as stale. 3 h: all three feeds are hourly, and the existing pages warn at
3 h (``STALE_AFTER`` of the taker flow, the option-flow sync warning).
"""

SPOT_FRESHNESS_LIMIT_HOURS = 3.0
"""An index price (the spot of the map) older than this is flagged in the report: the map is measured from it."""

STALE_QUALITY_FACTOR = 0.5
"""Quality is multiplied by this when an item is older than its freshness limit: the signal may still hold, but less surely."""

VERY_STALE_MULTIPLE = 4.0
"""An item older than this many freshness limits is "very stale"."""

VERY_STALE_QUALITY_FACTOR = 0.1
"""Quality factor of a very stale item: it is kept visible in the evidence list but carries almost no weight."""

# ---------------------------------------------------------------- how each kind of evidence relates to each scenario

LEVEL_SUPPORTS_INSIDE = 1.0
"""A level that bounds the range supports "stays inside" with full strength: a mapped boundary is what "inside" means."""

LEVEL_OPPOSES_BREAK = 1.0
"""The same level opposes the break through it with full strength (it is the obstacle the break has to pass)."""

DIRECTIONAL_SUPPORTS_BREAK = 1.0
"""Bullish evidence supports the break up (bearish: the break down) with full strength."""

DIRECTIONAL_OPPOSES_OTHER_BREAK = 1.0
"""Bullish evidence opposes the break down (bearish: the break up) with full strength: it points the other way."""

DIRECTIONAL_OPPOSES_INSIDE = 0.5
"""A one-sided flow opposes "stays inside" only half: pressure in one direction does not mean the range breaks."""

BALANCED_SUPPORTS_INSIDE = 1.0
"""Balanced (neutral) directional evidence supports "stays inside"; its strength already says how balanced it is."""

RANGE_BOUND_SUPPORTS_INSIDE = 1.0
"""A range-bound regime hint supports "stays inside" fully (its weakness is in its strength and quality, not here)."""

RANGE_BOUND_OPPOSES_BREAK = 0.5
"""A range-bound regime hint opposes each break half: it says moves are damped, not that a given level holds."""

EXPANSION_OPPOSES_INSIDE = 1.0
"""An expansion regime hint (moves amplified) opposes "stays inside" fully."""

ONE_SIDED_EXPANSION_OPPOSES_INSIDE = 0.5
"""
On a one-sided map ("stays above support" with the upside unmapped) an expansion hint opposes "inside" only half: a large
move toward the unmapped side does not contradict it, a move through the mapped bound does.
"""

EXPANSION_SUPPORTS_BREAK = 0.5
"""An expansion regime hint supports each break only half: it has no direction, so it splits between both sides."""

# ---------------------------------------------------------------- confidence label (low / medium / high)

HIGH_CONFIDENCE_MINIMUM_GROUPS = 3
"""
"high" needs at least three distinct input groups agreeing. Distinct data feeds, not independent evidence: open interest accumulates the trades and all groups react to the same price moves. Today only three groups exist (Deribit open interest,
Deribit trades, Binance perpetual candles), so "high" needs all of them on one side.
"""

LOW_CONFIDENCE_MAXIMUM_GROUPS = 1
"""With at most one agreeing input group the label is "low" whatever else holds: one data source is one opinion."""

HIGH_CONFIDENCE_MINIMUM_POINTS = 2
"""Net points (each confidence reason adds +1, 0 or -1) needed for "high"."""

LOW_CONFIDENCE_MAXIMUM_POINTS = -1
"""Net points at or below which the label is "low"."""

STALE_SUPPORT_BLOCKS_HIGH = True
"""A scenario with any stale supporting item is at most "medium": "high" should never rest on old data."""

HIGH_CONFLICT_SHARE = 0.35
"""
Opposition above this share costs a point: more than a third of the weight disagrees. The share is measured per item
(sum of quality x |value| of the opposing items over that of all items speaking to the scenario), because the group means
of the score net a conflict away inside a group.
"""

LOW_CONFLICT_SHARE = 0.10
"""Opposition below this share earns a point: the evidence that has a say is almost one-sided."""

GOOD_MEAN_QUALITY = 0.6
"""Mean quality of the supporting items at or above this earns a point."""

POOR_MEAN_QUALITY = 0.35
"""Mean quality of the supporting items below this costs a point (assumption-based or failed-check inputs dominate)."""

WIDE_MARGIN_POINTS = 20.0
"""The top scenario earns a point when it leads the second by at least this many score points."""

NARROW_MARGIN_POINTS = 10.0
"""The top scenario loses a point when it leads the second by less than this: the ranking could flip on little."""

# ---------------------------------------------------------------- extractor priors (base quality and strength per source)

MENTHORQ_LEVEL_QUALITY = 0.7
"""
Quality of the MenthorQ replica levels. Highest of the level sources because the replica reproduces a published method on
the full open interest of the monthly expiries, but below 1: it is a replica, and open interest does not say who holds what.
"""

MENTHORQ_FLIP_STRENGTH = 0.5
"""Strength of the HVL: a strike-axis point where cumulative GEX changes sign, weaker as a level than the walls (1.0)."""

OPEN_INTEREST_DEALER_LEVEL_QUALITY = 0.6
"""
Quality of the open-interest dealer walls: assumption-based (dealers long every call, short every put), which nothing in
the data confirms; below the MenthorQ replica, which is the published form of the same assumption.
"""

FLOW_DEALER_LEVEL_QUALITY = 0.6
"""Quality of the flow-based dealer walls when their plausibility check passes (the taker = customer assumption remains)."""

FAILED_CHECK_QUALITY_FACTOR = 0.2
"""
Quality factor when the flow-based plausibility check fails (dealer positions larger than the open interest, gaps in the
trade history): the inferred inventory is then known to be wrong somewhere, so it keeps only a fifth of its weight.
"""

DEALER_FLIP_STRENGTH = 0.5
"""Strength of a dealer gamma flip: a strike-axis point, weaker as a level than a wall (1.0)."""

OPEN_INTEREST_SHARE_FOR_FULL_STRENGTH = 0.25
"""
An expiry holding this share (25 %) of all open interest gives its dealer levels full strength; smaller expiries scale
down linearly. 25 % is about the share of one big monthly expiry.
"""


FLOW_STRIKE_LEVEL_QUALITY = 0.35
"""
Quality of an option-flow strike as a level: a strike where much traded in 24 h is where interest is, not a barrier
(the trade may close a position, hedge another or be one leg of a structure).
"""

REGIME_HINT_STRENGTH = 0.5
"""Strength of the total-GEX sign as a regime hint: the sign alone, half strength, because it says nothing about levels."""

OPEN_INTEREST_REGIME_QUALITY = 0.3
"""Quality of the open-interest total GEX sign: weak and assumption-based (the dealer positioning is assumed, not observed)."""

FLOW_REGIME_QUALITY = 0.3
"""Quality of the flow-based total GEX sign before the plausibility factor (the same weakness, plus the taker = customer assumption)."""

OPTION_FLOW_QUALITY = 0.5
"""
Quality of the net taker flow of single-sided option trades: the taker's side is known, but not whether it opens or closes
a position, nor whether it hedges something else.
"""

MULTI_LEG_BLOCK_QUALITY_FACTOR = 0.15
"""
Quality factor of the flow of multi-leg block structures: the side of one leg of a structure is not a view, so their net
call/put flow keeps only 15 % of the weight.
"""

DIRECTIONAL_IMBALANCE_THRESHOLD = 0.2
"""
Net option flow (calls bought minus sold minus puts bought plus puts sold, over all of it) of at least 0.2 in size is
directional; below it the flow is "balanced". 0.2 = a 60/40 split of the notional.
"""

TAKER_RATIO_QUALITY = 0.4
"""Quality of the futures taker ratio: one venue's perpetual, aggressor side only, known to revert to its mean within days."""

TAKER_BAND_STANDARD_DEVIATIONS = 1.0
"""The band of the taker ratio is its 30-day mean +- this many standard deviations."""

TAKER_FULL_STRENGTH_STANDARD_DEVIATIONS = 3.0
"""A ratio this many standard deviations from its mean gives full strength (strength = |z| / 3, capped at 1)."""

TAKER_BAND_MINIMUM_POINTS = 240
"""At least 240 hourly points (10 days) are needed for the 30-day band; fewer give no evidence (not computed)."""

TAKER_HORIZON_DAYS = 3.0
"""Horizon of the taker evidence: 1 to 3 days, the time an aggressor imbalance usually takes to fade."""

OPTION_FLOW_WINDOW_HOURS = 24
"""Window of the option flow (screen and block) read for the directional evidence and the flow strikes."""

FLOW_STRIKES_PER_SIDE = 2
"""How many strikes per option type and taker side become level evidence (the Levels page's default)."""
