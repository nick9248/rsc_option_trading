-- Migration 031: MenthorQ-style level monitor (numbers only, no charts).
--
--   menthorq_level_snapshots    one row per monitor run: the MenthorQ-style levels
--                               (Call Resistance / Put Support / HVL over all expiries of
--                               the universe, the four panel level sets), total net GEX,
--                               gamma regime and the inputs used. Written by
--                               coding/service/on_chain/menthorq_monitor/ inside the
--                               collector (unified_scheduler).
--   menthorq_alert_deliveries   every Telegram message of the monitor that was delivered
--                               (urgent alerts, weekly and on-demand briefs). Used for the
--                               alert cooldown and so a weekly brief is sent once per week.
--
-- mode:
--   'live'         spot, IV and open interest from the newest data at or before as_of_utc
--                  (hourly runs).
--   'daily_close'  the MenthorQ-style daily record: as_of_utc = 23:59 UTC of a date, open
--                  interest from the snapshot at or before 00:00 UTC of that same date
--                  (a consistent choice, not a proven instant; see
--                  menthorq_replication_calculator.start_of_utc_day).
-- Levels are strikes in USD; total_net_gex is USD per 1% move (gamma * OI * S^2 * 0.01).
-- A value that could not be computed is NULL and not_computed_reasons says why.
--
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS menthorq_level_snapshots (
    id                              serial PRIMARY KEY,
    currency                        varchar(10) NOT NULL,
    mode                            varchar(20) NOT NULL CHECK (mode IN ('live', 'daily_close')),
    as_of_utc                       timestamptz NOT NULL,
    computed_at                     timestamptz NOT NULL,
    expiry_universe                 varchar(40) NOT NULL,
    gamma_underlying                varchar(20) NOT NULL,
    spot                            numeric,
    spot_time_utc                   timestamptz,
    snapshot_captured_at_utc        timestamptz,
    open_interest_captured_at_utc   timestamptz,
    call_resistance                 numeric,
    put_support                     numeric,
    hvl                             numeric,
    total_net_gex                   numeric,
    gamma_regime                    varchar(20),
    expirations                     jsonb NOT NULL,
    panel_levels                    jsonb NOT NULL,
    iv_source_counts                jsonb NOT NULL,
    not_computed_reasons            jsonb NOT NULL,
    CONSTRAINT unique_menthorq_level_snapshot
        UNIQUE (currency, mode, expiry_universe, gamma_underlying, as_of_utc)
);
CREATE INDEX IF NOT EXISTS idx_menthorq_level_snapshots_lookup
    ON menthorq_level_snapshots (currency, mode, as_of_utc);

CREATE TABLE IF NOT EXISTS menthorq_alert_deliveries (
    id                  serial PRIMARY KEY,
    currency            varchar(10) NOT NULL,
    alert_key           varchar(80) NOT NULL,
    delivered_at        timestamptz NOT NULL,
    level_snapshot_id   integer,
    message             text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_menthorq_alert_deliveries_lookup
    ON menthorq_alert_deliveries (currency, alert_key, delivered_at);
