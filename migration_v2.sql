-- Run this ONCE in Neon's SQL Editor, same as migration_baskets.sql.
-- Adds: quote price/date tracking (with who quoted it), and a place to
-- store vendor company names so award history can show a name, not just
-- a CAGE code. Safe to run more than once.

CREATE TABLE IF NOT EXISTS quotes (
    id SERIAL PRIMARY KEY,
    solicitation_line_id BIGINT NOT NULL REFERENCES solicitation_lines(id) ON DELETE CASCADE,
    nsn TEXT NOT NULL,
    quoted_by TEXT,
    quoted_price NUMERIC,
    quoted_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quotes_nsn ON quotes (nsn);
CREATE INDEX IF NOT EXISTS idx_quotes_line ON quotes (solicitation_line_id);

-- vendors table already exists in schema.sql (cage_code PK, company_name) for
-- when the Vendor file gets imported. This just makes sure it's there.
CREATE TABLE IF NOT EXISTS vendors (
    cage_code TEXT PRIMARY KEY,
    company_name TEXT,
    address TEXT,
    poc_name TEXT,
    poc_email TEXT,
    poc_phone TEXT
);

-- Tag award history rows with which source file they came from, so the
-- dashboard can show "Source: ContractHist 2025" vs "Source: Current Awards"
-- next to every number, per your request.
ALTER TABLE dla_award_history ADD COLUMN IF NOT EXISTS source_type TEXT;
