# Foundation Cleanup — Should Be Done

Captured 2026-06-10. **Updated 2026-07-13** after a full project-state audit.
These are deferred tasks to be done as a dedicated cleanup sprint before building
anything else on top of this codebase.

The goal: strip the project down to what it actually is right now —
**data collection + on-chain analysis validation**. Nothing more.

---

## Current state audit (2026-07-13)

What changed since this doc was written:

- **Phase 2 backtest complete** (OI moneyness + max_pain survived FDR at 1h horizon);
  vanna/charm formula fixed, GEX/DEX collection bug fixed in prospective_collector.
- **Phase 3 forward-testing harness built** (migration 013, repository methods, 754 tests pass).
- **This work is UNCOMMITTED** on branch `onchain-analysis-validation`: modified
  `black_scholes_calculator.py`, `volatility_surface_calculator.py`, `vrp_calculator.py`,
  `repository.py`, `prospective_collector.py`; new `forward_testing_harness.py`,
  `volatility_reconstruction_service.py`, migrations 012/013, backfill + phase2 scripts.
- **Displacement system was half-deleted** in the public-release scrub (872454c):
  `coding/core/displacement/` and `coding/service/displacement/` are now only
  `__pycache__` residue. Leftovers: `models/displacement_scorer_v1/scorer.joblib`,
  `tests/unit/displacement/` (pycache only), untracked `scripts/train_conviction_scorer.py`.
- **ML tests already scrubbed** (`tests/unit/ml/` is pycache-only) but ML source remains
  in TWO places: `coding/core/ml/` (13 files) and `coding/service/ml/` (2 files).
  The original doc only listed `coding/service/ml/` — both must go.

---

## Step 0 (NEW — do first): Commit the Phase 2/3 work

Everything currently uncommitted on `onchain-analysis-validation` is on the keep-list.
Commit it before deleting anything, so the cleanup is fully reversible via git.

- Commit modified files + new services + migrations 012/013 + backfill/phase2 scripts
  as one or two logical commits on `onchain-analysis-validation`
- Then create the cleanup branch **from `onchain-analysis-validation`** (NOT from main —
  main lacks the Phase 2/3 work), e.g. `foundation-cleanup`
- Do NOT commit scratch/session files: `TODO.md`, `TASKS.md`, `report_20260605_143609.txt`,
  `scripts/_tmp_*`, `graphify-out/`, `documentation/to_learn_for_myself/`, `.mcp.json`
  (local MCP config). Add appropriate `.gitignore` entries or delete the throwaways.
- Do NOT push or merge — wait for user confirmation (per git workflow rule)

---

## 1. Merge Capture All into the Daemon

**What:** The GUI "Capture All (BTC/ETH)" button manually captures into a separate set
of old tables (max_pain, open_interest, volume, levels, gex_dex) via
`coding/service/database/capture_service.py` + `capture_strategies.py`. The daemon on
the VPS (`ProspectiveCollector`) already collects equivalent data every hour into
different tables (onchain_analysis_snapshots, hourly_snapshots, etc.). Duplicate pipelines.

**What to do:**
- Extend `ProspectiveCollector` to cover anything the old `DatabaseCaptureService`
  captured that the daemon does not already collect (audit field-by-field first —
  much of it likely overlaps already)
- Remove the "Capture All" / "Capture All (BTC/ETH)" buttons from the GUI database tab;
  keep only **Sync from VPS**
- Delete `capture_service.py` + `capture_strategies.py` +
  `tests/unit/test_database_capture_service.py` once the daemon covers the gap
- **Do NOT drop the old DB tables** — stop writing to them and list them in a
  "tables to drop later" note. Actual DROP is a user decision (data loss is irreversible).
- **VPS deployment is out of scope for the coding agent** — code changes only.
  Deploying the updated collector to the VPS is a follow-up done with the user
  (see `scripts/sync_from_vps.py`, `scripts/check_vps_health.py`, VPS memory notes).

---

## 2. Remove Strategies and Special Strategies

**What:** The entire strategy system, verified still fully present.

**Delete (verified paths):**
- `coding/core/strategy/` — entire tree (~37 files: definitions/, scoring/, models/,
  otm/, chart_generators/, chart_generator.py, pnl_calculator.py, report_generator.py)
- `coding/service/strategy/` — entire tree (~10 files: evaluation, finder, otm/)
- GUI: `tabs/strategy_tab.py`, `tabs/special_strategies_tab.py`,
  `tabs/otm_contracts_view.py`, `components/strategy_config_widgets.py`,
  `components/gate_score_bar.py`, `components/otm_signal_card.py`
- Unwire the above from `coding/gui/main_window.py` (imports at lines 25–32) and
  `coding/gui/tabs/navigation_page.py`
- Check `coding/gui/dialogs/flow_charts_window.py` — keep if it belongs to
  buy/sell flow analysis (on-chain), delete only if strategy-bound
- Tests: `tests/unit/strategy/` (entire tree incl. otm/), `tests/integration/strategy/`
- Strategy-signal DB tables: do NOT drop; add to the "tables to drop later" note
- Do not try to save or refactor any of this code — full removal.
  It will be rewritten from scratch after the data foundation is validated.

---

## 3. Full Project Cleanup

**Delete (verified paths):**
- ML: `coding/core/ml/` (entire tree) AND `coding/service/ml/` (entire tree)
- ML scripts: `scripts/train_ml_model.py`, `scripts/train_ml_models.py`
- Displacement residue: `models/displacement_scorer_v1/`,
  `scripts/train_conviction_scorer.py`, `tests/unit/displacement/` (pycache),
  `coding/core/displacement/` + `coding/service/displacement/` (pycache),
  `tests/unit/ml/` (pycache)
- Scratch: `scripts/_tmp_c2_correlation_check.py`, `scripts/_tmp_c2_correlation_results.csv`,
  `report_20260605_143609.txt`, `TODO.md`, `TASKS.md` (fold anything still relevant
  into this file first)
- `scripts/cleanup_expired_charts.py` — delete if it only serves strategy P&L charts;
  keep if it serves the on-chain chart_generator

**Audit for dead code after deletion (grep for orphaned imports/usages):**
- `coding/core/analytics/feature_engineer.py` — likely ML-only; delete if nothing
  outside ML imports it
- `coding/core/database/regime_dataset_builder.py` — regime-related, KEEP
  (regime detection stays; it has a live test)
- Anything in `coding/core/` / `coding/service/` that only strategies or ML imported

**Undecided — keep for now, flag for user:**
- `coding/service/morning_note/` + `coding/core/analytics/synthesis.py` — no strategy/ML
  dependencies (verified by grep); built entirely on on-chain analytics. Keeping.
- GUI `snapshot_tab.py`, `regime_tab.py`, `api_connection_tab.py` — not on the explicit
  keep-list but not strategy/ML either. Keeping (regime is explicitly foundation).

**After removal:** run full test suite (`pytest`), fix broken imports, verify the GUI
still launches (`python -c "from coding.gui.main_window import ..."` at minimum).

---

## 4. Update System Health to Current Situation

**What:** `scripts/validate_system.py` and the GUI `system_validation_tab.py` were
designed around strategies, ML models, and the manual capture pipeline.

**What to do:**
- Rewrite the health check to reflect the actual current system:
  - Is the daemon running on VPS?
  - Is data being collected every hour (onchain_analysis_snapshots, hourly_snapshots)?
  - Is the local DB in sync (last sync timestamp)?
  - Are there gaps in collection?
  - Is the forward testing harness recording predictions (forward_test_predictions)?
- Remove health checks for: strategy signals, ML models, old capture tables,
  displacement
- Update the GUI System Health tab to match

---

## Priority Order

- **Step 0** — Commit Phase 2/3 work + branch — makes everything reversible
- **Then items 2 + 3** — cleanup, removes the noise so we can see what we have
- **Then item 1** (code only) — merge pipelines, single source of truth
- **Then item 4** — update health check, reflects the clean state
- **After user review** — push, merge, deploy collector changes to VPS

---

## What stays (do NOT remove)

- Data collection daemon and all its tables: `prospective_collector.py`,
  `collection_daemon.py`, `hourly_aggregation_service.py`, `trade_collector.py`,
  `unified_scheduler.py`
- On-chain analysis: OnChainAnalyzer, VolatilitySurfaceCalculator, GexDexCalculator,
  BlackScholesCalculator, VRPCalculator, MarketWideCalculator, BuySellFlowAnalyzer,
  `on_chain_analysis_service.py`, `volatility_reconstruction_service.py`, `vrp_service.py`
- Forward testing harness (Phase 3): `forward_testing_harness.py`, migration 013
- Database repository and migration system (incl. migrations 012/013)
- Regime detection — keep EVERYTHING: RegimeDetectionService, market_regime_detector,
  regime_weight_optimizer, regime_dataset_builder, `scripts/optimize_regime_weights.py`,
  regime tables (regime_detections, ohlcv_history, technical_indicators,
  funding_rate_history, volatility_index_history, external_metrics)
- Chart generator `coding/core/analytics/chart_generator.py` (the analytics one —
  distinct from the strategy chart generators being deleted)
- GUI: On-chain analysis tab, Database tab (sync only), System Health tab, plus
  snapshot/regime/api-connection tabs and navigation page
- Scripts: all backfill_* scripts, `phase2_backtest.py`, `sync_from_vps.py`,
  `check_vps_health.py`, `check_collection_status.py`, `check_database.py`,
  `run_migration.py`, `validate_system.py` (rewritten, not removed),
  `verify_flow_data.py`, `aggregate_hourly_snapshots.py`, `run_full_backfill.py`
- Phase 2 artifacts worth keeping: `phase2_blueprint.txt`, `phase2_single_metric_results.csv`
  (move into a results folder or commit as-is)
- Snapshot service + morning note service (flagged above, kept for now)

---

## Carried forward from TODO.md / TASKS.md (deleted as scratch in this cleanup)

Those two files tracked the on-chain-analysis-validation work session by session;
their substance is now captured in this repo's committed history (migrations
012/013, `forward_testing_harness.py`, `volatility_reconstruction_service.py`,
`phase2_backtest.py`, `phase2_blueprint.txt`). Remaining open follow-ups that are
NOT yet done and NOT yet tracked elsewhere, preserved here so they aren't lost:

- **`net_vanna`/`net_charm` DB re-backfill** — the vanna/charm formula was
  corrected 2026-06-09 (real closed-form Black-Scholes in
  `BlackScholesCalculator`), but the 37,780 historical rows in
  `onchain_volatility_snapshots` still contain the old (wrong) proxy-formula
  values. Re-run `scripts/backfill_volatility_reconstruction.py` to overwrite
  them with correct values before including vanna/charm in any future analysis.
- **`pc_far_otm_ratio` live computation** — this is the one ETH-only Phase 2
  survivor metric; it needs to be computed from `VolatilitySurfaceCalculator`
  inside `ProspectiveCollector` and passed into the forward-testing harness.
  Currently the harness runs on 5/6 survivor metrics (still directional without
  it, per its own design), but ETH predictions are missing this signal.
- **`iv_percentile_expiry` window length** — uses a 90-day trailing window vs.
  the ~252-365 day institutional convention (C3 audit finding). Either document
  the justification for the shorter window (expiries don't live long enough for
  a 1yr lookback) or align it closer to convention.
- **Wire forward-testing harness output into `on_chain_scorer.py` /
  `composite_scorer.py`** — blocked on `harness.get_track_record(currency)
  ["criteria_met"] == True` (needs N >= 50 signals, hit_rate > 55%,
  information_ratio > 0.30). Harness is live and accumulating; expected to
  reach N >= 50 in ~2 months of live operation from 2026-06-09.
