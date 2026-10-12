-- Migration 029: store our own Deribit trading activity (read-only API).
--
--   account_snapshots   hourly equity / margin / session P&L / portfolio Greeks per currency
--   position_snapshots  hourly copy of every open position
--   user_trades         every fill of ours (immutable, one row per trade_id)
--   orders              our order history (rows change while an order is open)
--   settlements         settlement / delivery events (what happened to expired positions)
--
-- Filled by coding/service/data_collection/account_collector.py through
-- DeribitPrivateClient, which can only call private/get_* endpoints.
-- Timestamps are stored as timestamptz (converted from Deribit's epoch milliseconds).
-- `raw` (all tables except account_snapshots) keeps the full Deribit record so no field is
-- lost when Deribit adds one. account_snapshots has no raw column: the account summary
-- also carries personal data (email, username, deposit address) that must not be stored.
--
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS account_snapshots (
    id                  serial PRIMARY KEY,
    snapshot_hour       timestamptz NOT NULL,
    currency            varchar(10) NOT NULL,
    equity              numeric,
    balance             numeric,
    available_funds     numeric,
    margin_balance      numeric,
    initial_margin      numeric,
    maintenance_margin  numeric,
    session_upl         numeric,
    session_rpl         numeric,
    total_pl            numeric,
    futures_pl          numeric,
    options_pl          numeric,
    options_value       numeric,
    delta_total         numeric,
    options_delta       numeric,
    options_gamma       numeric,
    options_vega        numeric,
    options_theta       numeric,
    margin_model        varchar(30),
    CONSTRAINT unique_account_snapshot UNIQUE (currency, snapshot_hour)
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id                      serial PRIMARY KEY,
    snapshot_hour           timestamptz NOT NULL,
    currency                varchar(10) NOT NULL,
    instrument_name         varchar(50) NOT NULL,
    kind                    varchar(20),
    direction               varchar(10),
    size                    numeric,
    average_price           numeric,
    mark_price              numeric,
    index_price             numeric,
    delta                   numeric,
    gamma                   numeric,
    vega                    numeric,
    theta                   numeric,
    initial_margin          numeric,
    maintenance_margin      numeric,
    floating_profit_loss    numeric,
    realized_profit_loss    numeric,
    total_profit_loss       numeric,
    raw                     jsonb NOT NULL,
    CONSTRAINT unique_position_snapshot UNIQUE (snapshot_hour, instrument_name)
);

CREATE TABLE IF NOT EXISTS user_trades (
    id                  serial PRIMARY KEY,
    trade_id            varchar(50) NOT NULL UNIQUE,
    currency            varchar(10) NOT NULL,
    trade_time          timestamptz NOT NULL,
    instrument_name     varchar(50) NOT NULL,
    order_id            varchar(50),
    label               text,
    direction           varchar(10),
    price               numeric,
    amount              numeric,
    index_price         numeric,
    mark_price          numeric,
    iv                  numeric,
    fee                 numeric,
    fee_currency        varchar(10),
    liquidity           varchar(5),
    order_type          varchar(20),
    profit_loss         numeric,
    raw                 jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_trades_time ON user_trades (trade_time);

CREATE TABLE IF NOT EXISTS orders (
    id                      serial PRIMARY KEY,
    order_id                varchar(50) NOT NULL UNIQUE,
    currency                varchar(10) NOT NULL,
    created_at              timestamptz NOT NULL,
    last_update_at          timestamptz NOT NULL,
    instrument_name         varchar(50) NOT NULL,
    label                   text,
    direction               varchar(10),
    order_state             varchar(20),
    order_type              varchar(20),
    time_in_force           varchar(30),
    price                   numeric,
    amount                  numeric,
    filled_amount           numeric,
    average_price           numeric,
    post_only               boolean,
    reduce_only             boolean,
    api                     boolean,
    cancel_reason           varchar(40),
    raw                     jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_last_update ON orders (last_update_at);

CREATE TABLE IF NOT EXISTS settlements (
    id                  serial PRIMARY KEY,
    currency            varchar(10) NOT NULL,
    settled_at          timestamptz NOT NULL,
    type                varchar(20) NOT NULL,
    instrument_name     varchar(50) NOT NULL DEFAULT '',
    position            numeric,
    index_price         numeric,
    mark_price          numeric,
    profit_loss         numeric,
    session_profit_loss numeric,
    raw                 jsonb NOT NULL,
    CONSTRAINT unique_settlement UNIQUE (currency, settled_at, type, instrument_name)
);
CREATE INDEX IF NOT EXISTS idx_settlements_time ON settlements (settled_at);
