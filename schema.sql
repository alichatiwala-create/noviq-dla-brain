-- schema.sql
-- ============================================================
-- Noviq DLA Brain — core schema
--
-- Two layers, per the spec:
--   1. STAGING  — raw imported rows, exactly as received, kept forever.
--      Never deleted, never overwritten. This is the audit trail and
--      lets us safely reprocess a source without re-requesting it.
--   2. NORMALIZED — the "one clean brain": deduplicated, joined,
--      NSN/FSC/NIIN-normalized data the dashboard actually queries.
--      The dashboard NEVER reads staging tables directly.
--
-- Run this once against a fresh database:
--   psql -U postgres -d noviq -f schema.sql
-- ============================================================

-- ---------- STAGING LAYER ----------
-- One table per raw source file type. Every row remembers which file
-- it came from and when it was imported, so re-importing the same
-- file twice is safe to detect and nothing is ever silently lost.

CREATE TABLE IF NOT EXISTS staging_in (
    id              BIGSERIAL PRIMARY KEY,
    solicitation    VARCHAR(13) NOT NULL,
    nsn_part_raw    VARCHAR(46) NOT NULL,
    purchase_request VARCHAR(13),
    return_by_date  VARCHAR(8),
    file_name       VARCHAR(19),
    qty             VARCHAR(7),
    unit_issue      VARCHAR(2),
    nomenclature    VARCHAR(21),
    buyer_code      VARCHAR(5),
    amsc            VARCHAR(1),
    item_type_indicator VARCHAR(1),      -- 1=NSN, 2=Part Number
    sb_setaside_indicator VARCHAR(1),    -- Y/H/R/L/A/E/N
    sb_setaside_pct VARCHAR(3),
    source_file     TEXT NOT NULL,       -- e.g. 'IN260911.TXT'
    post_date       DATE NOT NULL,       -- derived from the filename's date
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_bq (
    id              BIGSERIAL PRIMARY KEY,
    solicitation_number VARCHAR(20),
    solicitation_line_number TEXT,
    purchase_request_number TEXT,
    nsn_part        TEXT,
    unit_of_issue   TEXT,
    quantity        TEXT,
    unit_price      TEXT,
    delivery_days   TEXT,
    return_by_date  TEXT,
    quote_for_cage_code TEXT,
    -- The BQ file has ~110 columns; we don't need every one in staging
    -- structured form. raw_row keeps the full row (as JSON) so nothing
    -- is lost even for columns we haven't modeled yet.
    raw_row         JSONB NOT NULL,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_as (
    id              BIGSERIAL PRIMARY KEY,
    nsn             VARCHAR(13) NOT NULL,
    cage_code       VARCHAR(5) NOT NULL,
    part_number     TEXT,
    company_name    TEXT,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_reference (
    id              BIGSERIAL PRIMARY KEY,
    nsn             VARCHAR(13) NOT NULL,
    cage_code       VARCHAR(5) NOT NULL,
    part_number     TEXT,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_vendor (
    id              BIGSERIAL PRIMARY KEY,
    cage_code       VARCHAR(5) NOT NULL,
    company_name    TEXT,
    -- widened once we see a real Vendor file; raw_row preserves everything
    raw_row         JSONB NOT NULL,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_contracthist (
    id              BIGSERIAL PRIMARY KEY,
    nsn             VARCHAR(13),
    award_number    TEXT,
    contract_number TEXT,
    award_date      DATE,
    winning_cage    VARCHAR(5),
    quantity        NUMERIC,
    unit_price      NUMERIC,
    total_award_value NUMERIC,
    solicitation_number TEXT,
    source_year     TEXT,               -- e.g. '2019', 'current'
    raw_row         JSONB NOT NULL,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_current_awards (
    id              BIGSERIAL PRIMARY KEY,
    nsn             VARCHAR(13),
    award_number    TEXT,
    award_date      DATE,
    winning_cage    VARCHAR(5),
    unit_price      NUMERIC,
    raw_row         JSONB NOT NULL,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS staging_management (
    id              BIGSERIAL PRIMARY KEY,
    niin            VARCHAR(9) NOT NULL,   -- ALWAYS text - leading zeros matter
    unit_price      NUMERIC,               -- DRN 7075
    raw_row         JSONB NOT NULL,
    source_file     TEXT NOT NULL,
    post_date       DATE NOT NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------- NORMALIZED LAYER ----------

-- One row per solicitation number (the "parent"). BQ, awards, etc.
-- never create a new row here just because they mention this
-- solicitation again.
CREATE TABLE IF NOT EXISTS solicitations (
    solicitation_number VARCHAR(20) PRIMARY KEY,
    issue_date          DATE,
    first_seen_post_date DATE NOT NULL,
    last_seen_post_date  DATE NOT NULL,
    times_posted         INTEGER NOT NULL DEFAULT 1,
    date_status          VARCHAR(20),   -- CURRENT | FUTURE | OLD_POSTED | OLD_EXTENDED
    status               VARCHAR(20) NOT NULL DEFAULT 'NEW',
        -- NEW | PREVIOUSLY_SEEN | UPDATED | NEW_AMENDMENT | RETURN_DATE_CHANGED
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per solicitation + line. This is where IN rows land, and
-- what BQ rows join onto via (solicitation_number, line_number).
CREATE TABLE IF NOT EXISTS solicitation_lines (
    id                  BIGSERIAL PRIMARY KEY,
    solicitation_number VARCHAR(20) NOT NULL REFERENCES solicitations(solicitation_number),
    line_number         TEXT,                 -- from BQ; nullable until BQ is joined
    purchase_request_number VARCHAR(13),
    nsn                 VARCHAR(13),           -- normalized 13-digit NSN, TEXT (leading zeros matter)
    fsc                 VARCHAR(4),            -- derived: nsn[0:4]
    niin                VARCHAR(9),            -- derived: nsn[4:13], TEXT
    item_type_indicator VARCHAR(1),            -- 1=NSN, 2=Part Number
    part_number_raw     TEXT,                  -- populated when item_type_indicator = 2
    qty                 INTEGER,
    unit_issue          VARCHAR(2),
    nomenclature        TEXT,
    buyer_code          VARCHAR(5),
    amsc                VARCHAR(1),
    set_aside_indicator VARCHAR(1),
    set_aside_pct       NUMERIC,
    return_by_date      DATE,
    return_by_date_prev DATE,                  -- for RETURN_DATE_CHANGED detection
    file_name           VARCHAR(19),
    -- Batch-quote fields, filled in once BQ is joined (see staging_bq)
    unit_price_quoted   NUMERIC,
    delivery_days       NUMERIC,
    -- Derived intelligence (Rules 1-3)
    estimated_value      NUMERIC,
    estimated_value_basis VARCHAR(40),          -- 'Last Award Price' | 'FLIS Management Price - DRN 7075' | 'N/A'
    estimated_value_award_id BIGINT,            -- FK to dla_award_history.id when basis = Last Award Price
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (solicitation_number, purchase_request_number)
);

CREATE INDEX IF NOT EXISTS idx_solicitation_lines_nsn ON solicitation_lines(nsn);
CREATE INDEX IF NOT EXISTS idx_solicitation_lines_niin ON solicitation_lines(niin);

-- One canonical row per NSN + CAGE + normalized Part Number, per the
-- Reference/AS dedup rule (section 6 of the spec). in_reference and
-- in_as are independently true/false; approved mirrors in_as.
CREATE TABLE IF NOT EXISTS sources_canonical (
    id              BIGSERIAL PRIMARY KEY,
    nsn             VARCHAR(13) NOT NULL,
    cage_code       VARCHAR(5) NOT NULL,
    part_number     TEXT NOT NULL,          -- normalized (trimmed/upper) for matching
    company_name    TEXT,                   -- from AS, if present
    in_reference    BOOLEAN NOT NULL DEFAULT FALSE,
    in_as           BOOLEAN NOT NULL DEFAULT FALSE,
    approved        BOOLEAN NOT NULL DEFAULT FALSE,   -- = in_as
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (nsn, cage_code, part_number)
);

CREATE INDEX IF NOT EXISTS idx_sources_canonical_nsn ON sources_canonical(nsn);

CREATE TABLE IF NOT EXISTS vendors (
    cage_code       VARCHAR(5) PRIMARY KEY,
    company_name    TEXT,
    address         TEXT,
    -- Phase 2 (per spec section 7): POC info
    poc_name        TEXT,
    poc_email       TEXT,
    poc_phone       TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- All ContractHist years + Current DIBBS Awards merge into ONE table
-- here, deduplicated by award_number so the same award appearing in
-- both a historical file and a current-awards feed is one row.
CREATE TABLE IF NOT EXISTS dla_award_history (
    id                  BIGSERIAL PRIMARY KEY,
    nsn                 VARCHAR(13) NOT NULL,
    award_number        TEXT NOT NULL,
    contract_number     TEXT,
    award_date          DATE NOT NULL,
    winning_cage        VARCHAR(5),
    quantity            NUMERIC,
    unit_price          NUMERIC,
    total_award_value   NUMERIC,
    solicitation_number TEXT,
    source_year         TEXT,             -- '2019' ... 'current' ... 'dibbs_current_awards'
    source_file          TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (award_number)
);

CREATE INDEX IF NOT EXISTS idx_award_history_nsn_date ON dla_award_history(nsn, award_date DESC);

CREATE TABLE IF NOT EXISTS management_price (
    niin            VARCHAR(9) PRIMARY KEY,   -- TEXT, leading zeros preserved
    unit_price      NUMERIC,                  -- DRN 7075
    source_file     TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ---------- DASHBOARD-SPECIFIC ADDITIONS ----------
-- Lightweight fields the web dashboard's UI needs (notes, basket,
-- marking something as quoted) that aren't part of the core DLA Brain
-- data model itself. Safe to run alongside everything above - IF NOT
-- EXISTS / ADD COLUMN IF NOT EXISTS won't touch anything already there.

ALTER TABLE solicitation_lines ADD COLUMN IF NOT EXISTS quoted BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS notes (
    id                     BIGSERIAL PRIMARY KEY,
    solicitation_line_id   BIGINT NOT NULL REFERENCES solicitation_lines(id) ON DELETE CASCADE,
    note                   TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS basket (
    solicitation_line_id   BIGINT PRIMARY KEY REFERENCES solicitation_lines(id) ON DELETE CASCADE,
    added_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
