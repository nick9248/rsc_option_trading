-- Migration 011: Add OTM Contract Finder tables
-- DVOL history and OTM signals storage for contract finder feature

-- ── DVOL history ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dvol_history (
    id          SERIAL PRIMARY KEY,
    asset       VARCHAR(10)   NOT NULL,   -- 'BTC' | 'ETH'
    timestamp   TIMESTAMPTZ   NOT NULL,
    dvol_value  DECIMAL(8,4)  NOT NULL,
    UNIQUE (asset, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_dvol_history_asset_ts
    ON dvol_history (asset, timestamp DESC);

COMMENT ON TABLE dvol_history IS
    'Deribit DVOL index history used for Gate 2 percentile calculation';

-- ── OTM signals ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS otm_signals (
    id                   SERIAL PRIMARY KEY,
    signal_id            UUID          NOT NULL UNIQUE,
    generated_at         TIMESTAMPTZ   NOT NULL,
    asset                VARCHAR(10)   NOT NULL,
    instrument_name      VARCHAR(50)   NOT NULL,
    direction            VARCHAR(4)    NOT NULL,    -- 'call' | 'put'
    strike               DECIMAL(14,2) NOT NULL,
    expiry               VARCHAR(10)   NOT NULL,
    dte                  INTEGER       NOT NULL,
    delta                DECIMAL(8,6)  NOT NULL,
    mark_iv              DECIMAL(8,4),
    entry_premium        DECIMAL(12,4),
    underlying_price     DECIMAL(14,2),
    gate2_score          DECIMAL(6,2),
    gate3_call_score     DECIMAL(6,2),
    gate3_put_score      DECIMAL(6,2),
    conviction_score     DECIMAL(6,2),
    position_usd         DECIMAL(12,2),
    take_profit_multiple DECIMAL(6,2),
    expiry_category      VARCHAR(10),               -- 'short'|'medium'|'long'
    regime_flag          VARCHAR(10),               -- 'bull'|'bear'|'neutral'
    signal_breakdown     JSONB,
    exit_params          JSONB
);
CREATE INDEX IF NOT EXISTS idx_otm_signals_asset_ts
    ON otm_signals (asset, generated_at DESC);

COMMENT ON TABLE otm_signals IS
    'OTM contract finder output — one row per signal generated';
