-- Stores each award's PDF link permanently, so we can always go back and
-- read quantity/unit price/company name from it later - even if DIBBS's
-- live award search stops showing that award in results (which can happen -
-- their search index isn't perfectly stable run to run).
ALTER TABLE dla_award_history ADD COLUMN IF NOT EXISTS award_pdf_url TEXT;
