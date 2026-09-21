-- Adds a column to track when DLA posts a modification to an existing award,
-- so the Current Awards importer can tell "already have this, unchanged"
-- (skip) apart from "already have this, but it changed" (update in place)
-- rather than ever saving the same award twice.
ALTER TABLE dla_award_history ADD COLUMN IF NOT EXISTS last_mod_posting_date DATE;
