-- migrations/013_add_displacement_signals.sql
CREATE TABLE IF NOT EXISTS displacement_signals (
    id               SERIAL PRIMARY KEY,
    asset            VARCHAR(10)   NOT NULL,
    detected_at      TIMESTAMPTZ   NOT NULL,
    drop_24h_pct     NUMERIC(8,6),
    drop_1h_pct      NUMERIC(8,6),
    conviction_pct   NUMERIC(5,2),
    conviction_label VARCHAR(10),
    instrument_name  VARCHAR(60),
    strike           NUMERIC(14,2),
    expiry_date      DATE,
    dte              INTEGER,
    delta            NUMERIC(6,4),
    mark_iv          NUMERIC(6,4),
    premium_usd      NUMERIC(12,2),
    signal_breakdown JSONB,
    telegram_sent    BOOLEAN       DEFAULT FALSE,
    created_at       TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_displacement_signals_asset_detected
    ON displacement_signals (asset, detected_at DESC);
