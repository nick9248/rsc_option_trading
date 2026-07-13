# Tables to drop later (user decision required)

Written during the foundation-cleanup sprint (2026-07-13). These tables are no
longer written to by any surviving code path, but **no DROP TABLE has been run
against them** — dropping tables is irreversible data loss and is left to an
explicit user decision, per this repo's CLAUDE.md rules and the cleanup spec
(`SHOULD_BE_DONE.md`).

## Strategy system tables (item 2 - strategy system removed entirely)

- `strategy_signals` (migrations 001, 002) - scored strategy evaluation output.
  No writer remains after `coding/core/strategy/` + `coding/service/strategy/`
  were deleted.
- `otm_signals` (migration 011) - OTM contract finder signal output. No writer
  remains after `coding/service/strategy/otm/` was deleted.

Not dropped: `dvol_history` (migration 011) - despite living in the same
migration file as `otm_signals`, this table is NOT strategy-specific. It is
written by `DVOLFetcher` (relocated to `coding/service/deribit/dvol_fetcher.py`,
still in active use by `scripts/backfill_dvol_history.py`) and read by
`DatabaseRepository.get_dvol_history()` / `get_dvol_history_before()`, which
feed IV-percentile and expected-move calculations. **Keep.**

## Legacy manual-capture GUI tables (item 1 - capture pipeline merged into daemon)

All defined in `migrations/000_base_schema.sql` under "Legacy on-chain GUI
tables". Written only by the deleted `coding/service/database/capture_service.py`
+ `capture_strategies.py` (the old "Capture All" GUI button), triggered
manually and never on a schedule - NOT a systematic dataset. The VPS daemon
(`ProspectiveCollector`) already collects equivalent (in most cases richer)
data every hour into `onchain_analysis_snapshots` / `hourly_snapshots`:

- `snapshots` - raw per-instrument book-summary rows. Fully superseded: the
  daemon's `ProspectiveCollector._fetch_book_summary()` calls the exact same
  `DatabaseRepository.save_snapshot()` method, writing to this same table
  every collection cycle. (Not obsolete as a table - still actively written
  by the daemon. Listed here only for completeness / to note the old GUI
  capture path into it is gone.)
- `max_pain` - per-expiration max pain strike + distance. Superseded by
  `onchain_analysis_snapshots.max_pain_strike` / `max_pain_distance_pct`,
  computed and stored every hour.
- `open_interest` - per-expiration call/put OI totals + P/C ratio. Superseded
  by `onchain_analysis_snapshots.total_call_oi` / `total_put_oi` /
  `put_call_ratio_oi`.
- `volume` - per-expiration call/put volume totals + P/C ratio. **Partial
  gap**: `onchain_analysis_snapshots` stores `total_volume` (combined) and
  `put_call_ratio_volume` (ratio) but not the separate absolute
  `total_call_volume` / `total_put_volume` values this table had. Not
  backfilled into the daemon (see audit note below) - nothing outside the
  deleted capture pipeline's own trend charts ever consumed the split.
- `levels` - per-expiration support/resistance levels, multiple ranked levels
  plus short-term categories. **Partial gap**: `onchain_analysis_snapshots`
  only stores the single top resistance/support level
  (`resistance_1_strike`/`support_1_strike` + OI), not the full ranked list or
  the short-term categorization this table had. Not backfilled (see audit
  note below).
- `gex_dex` - per-expiration GEX/DEX + key levels. Superseded by
  `onchain_analysis_snapshots.total_net_gex` / `total_net_dex` /
  `call_resistance_strike` / `put_support_strike` / `hvl_level`.

### Audit note on the `volume`/`levels` partial gaps

These two narrower fields (absolute call/put volume split, multi-level/
short-term S/R) were never consumed by anything except the deleted capture
pipeline's own legacy trend-chart functions in
`coding/core/analytics/chart_generator.py` (`generate_volume_trend`,
`generate_levels_trend`, and siblings `generate_max_pain_trend`,
`generate_open_interest_trend`, `generate_pc_ratio_trend`,
`generate_gex_dex_trend`, `generate_oi_distribution`,
`generate_snapshot_oi_distribution`, `generate_snapshot_volume_distribution`).
Those 9 functions have no other caller now that `capture_strategies.py` is
gone - they are dead code inside an otherwise-kept file (flagged for the
user, not removed in this pass; see the cleanup report). None of the Phase 2
validated predictive metrics (`itm/otm_*_oi_pct`, `max_pain_distance_pct`,
the vol-surface family) depend on the call/put volume split or multi-level
S/R, so `ProspectiveCollector` was NOT extended to backfill these two fields.
If a future need for them resurfaces, add them to `onchain_analysis_snapshots`
(or a companion table) computed from the same `analyzer.analyze_expiration()`
call already running every hour - the raw data to compute them is already
being parsed, just not persisted.
