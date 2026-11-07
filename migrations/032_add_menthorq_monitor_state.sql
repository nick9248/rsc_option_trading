-- Migration 032: persisted alert state of the MenthorQ-style monitor (Telegram /mute).
--
--   menthorq_monitor_state   one row per currency.
--     alerts_muted_until          URGENT alerts are not sent while now < this instant.
--                                 NULL = not muted (never muted, or /mute off).
--                                 Replies to commands and the weekly brief are never muted.
--     mute_updated_at             when /mute last changed alerts_muted_until.
--     last_suppressed_at          when an urgent message was last held back by the mute.
--     last_suppressed_alert_keys  alert keys of that message (jsonb list of strings).
--
-- Written by coding/service/on_chain/menthorq_monitor/ inside the collector.
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS menthorq_monitor_state (
    currency                      varchar(10) PRIMARY KEY,
    alerts_muted_until            timestamptz,
    mute_updated_at               timestamptz,
    last_suppressed_at            timestamptz,
    last_suppressed_alert_keys    jsonb NOT NULL DEFAULT '[]'::jsonb
);
