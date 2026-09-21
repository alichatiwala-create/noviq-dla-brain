"""
Backfill: goes back and reads the PDF for every award that's already saved
in your database but is still missing quantity/unit price/company name.

Why this exists:
  import_current_awards.py normally reads an award's PDF (for quantity, unit
  price, and company name) at the same time it finds that award on DIBBS's
  live award-search page. But DIBBS's own search sometimes stops showing an
  award it showed before (their search index isn't perfectly stable run to
  run) - so an award can get saved successfully, but never get its PDF read,
  simply because a later search for that NSN came back empty.

  This script doesn't depend on DIBBS's search at all. It works straight off
  the PDF link that was saved on the award row itself (award_pdf_url) when
  the award was first found, so it can always go back and fill in the
  missing details later, no matter what DIBBS's search does.

BEFORE RUNNING THIS for the first time, run migration_v4.sql in Neon's SQL
Editor (adds the award_pdf_url column). If you already ran it, you're set.

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python backfill_award_pdfs.py

Optional: to test on just the first N rows before running the full list:
    python backfill_award_pdfs.py --limit 25

This only touches rows where quantity is still blank AND we have a saved PDF
link for that row - it never re-reads a PDF for an award that already has a
quantity, and it never touches a row with no PDF link saved (there's nothing
to backfill from prior to migration_v4.sql; those will get their PDF link
saved automatically the next time import_current_awards.py runs and finds
them again).
"""

import argparse
import time

from playwright.sync_api import sync_playwright

from database import get_connection
from import_current_awards import apply_pdf_details


def get_rows_needing_pdf(conn, limit=None):
    cur = conn.cursor()
    query = """
        SELECT id, award_pdf_url, winning_cage, total_award_value
        FROM dla_award_history
        WHERE quantity IS NULL AND award_pdf_url IS NOT NULL
        ORDER BY id
    """
    if limit:
        cur.execute(query + " LIMIT %s", (limit,))
    else:
        cur.execute(query)
    return cur.fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows (for testing)")
    args = parser.parse_args()

    conn = get_connection()
    rows = get_rows_needing_pdf(conn, args.limit)
    print(f"Found {len(rows)} saved award(s) still missing quantity/unit price, with a PDF link to try.\n")

    filled_in = 0
    failed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for i, (award_id, pdf_url, cage, total_value) in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] award id {award_id} ...", end=" ")
            try:
                if apply_pdf_details(conn, page, award_id, pdf_url, cage, total_value):
                    print("filled in.")
                    filled_in += 1
                else:
                    print("PDF couldn't be read or had nothing usable in it.")
                    failed += 1
            except Exception as e:
                print(f"error ({e})")
                failed += 1
            time.sleep(0.3)

        context.close()
        browser.close()

    print("\n---- Summary ----")
    print(f"Rows checked:     {len(rows)}")
    print(f"Filled in:        {filled_in}")
    print(f"Still missing:    {failed}")
    print("\nDone. Refresh your dashboard to see the filled-in quantity/unit price/company names.")


if __name__ == "__main__":
    main()
