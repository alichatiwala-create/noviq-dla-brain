-- Run this ONCE against your existing Neon database (same one noviq-dla-brain uses).
-- It upgrades the single generic "basket" table into named, multiple baskets
-- (so the dashboard can show a "Basket" dropdown like "SBTD26", "Default", etc.)
-- It is safe to run more than once.

CREATE TABLE IF NOT EXISTS baskets (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO baskets (name) VALUES ('Default') ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS basket_items (
    basket_id INTEGER NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
    solicitation_line_id BIGINT NOT NULL REFERENCES solicitation_lines(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (basket_id, solicitation_line_id)
);

-- If you had items in the old single "basket" table, move them into "Default"
-- so nothing gets lost.
INSERT INTO basket_items (basket_id, solicitation_line_id, added_at)
SELECT (SELECT id FROM baskets WHERE name = 'Default'), b.solicitation_line_id, b.added_at
FROM basket b
ON CONFLICT DO NOTHING;

-- Also make sure notes / quoted exist (older schema.sql already added these,
-- this is just a safety net in case an older copy of the DB is missing them).
ALTER TABLE solicitation_lines ADD COLUMN IF NOT EXISTS quoted BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS notes (
    id SERIAL PRIMARY KEY,
    solicitation_line_id BIGINT NOT NULL REFERENCES solicitation_lines(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
