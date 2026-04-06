-- Migration 012: Forward testing — vol predictions table

CREATE TABLE IF NOT EXISTS vol_predictions (
    id                   SERIAL PRIMARY KEY,
    predicted_at         TIMESTAMP NOT NULL,
    currency             VARCHAR(10) NOT NULL,
    model_id             VARCHAR(100) NOT NULL,
    predicted_vol_24h    DECIMAL(8,4) NOT NULL,
    predicted_daily_move DECIMAL(8,4) NOT NULL,
    verified_at          TIMESTAMP,
    actual_vol_24h       DECIMAL(8,4),
    actual_price_change  DECIMAL(8,4),
    within_1sigma        BOOLEAN,
    error_pct            DECIMAL(8,4),
    UNIQUE(predicted_at, currency)
);

CREATE INDEX IF NOT EXISTS idx_vol_predictions_currency ON vol_predictions(currency);
CREATE INDEX IF NOT EXISTS idx_vol_predictions_predicted_at ON vol_predictions(predicted_at DESC);
