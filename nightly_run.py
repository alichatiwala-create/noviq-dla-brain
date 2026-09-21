"""
nightly_run.py
---------------
The script that runs automatically every night (via Windows Task
Scheduler - see README.md's "Automating the nightly run" section).

WHAT THIS DOES:
  1. Fetches yesterday's IN/AS/BQ files from DIBBS.
  2. If the IN file isn't there yet (DIBBS is sometimes late or briefly
     down), it does NOT give up for the night. Instead it falls back to
     scraping DIBBS's own live "Recent RFQs" listing for that same
     date and saves whatever solicitations are already visible there -
     through the exact same save-functions import_in.py itself uses,
     so nothing is ever duplicated once the real file shows up later
     (see rfq_live_fallback.py for exactly how that's guaranteed).
  3. Imports AS and BQ normally if they came through; if they didn't,
     that's fine too - they'll simply fill in the extra details
     (quantity, price, approved sources) next time they succeed,
     matching onto the solicitations/lines already saved in step 1/2.
  4. Everything - successes, failures, AND every time the fallback gets
     used - is written to nightly_run.log with a plain-English line, so
     you can open that one file any morning and immediately see what
     happened, instead of guessing.

HOW TO SCHEDULE THIS TO RUN EVERY NIGHT (Windows Task Scheduler):
  See the "Automating the nightly run" section in README.md.

This file also exposes run_for_date(target_date), used by both the
nightly run above (for yesterday) and the website's Admin page (for
whatever specific date you type in there) - same logic either way.
"""

import sys
import logging
from datetime import date, timedelta
from pathlib import Path

import db
import import_in
import import_as
import import_bq
import fetch_daily_files
import rfq_live_fallback

LOG_PATH = Path(__file__).parent / "nightly_run.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),  # also print to screen if run manually
    ],
)
log = logging.getLogger("nightly_run")


def run_for_date(target_date: date):
    """
    Runs the full pipeline for ONE specific date: fetch IN/AS/BQ for
    that date, falling back to the live RFQ listing scrape if IN isn't
    available yet, then import whatever came through. Returns a dict
    describing exactly what happened - used both for the nightly log
    and for the website's Admin page to show a result.
    """
    log.info(f"--- Running pipeline for {target_date} ---")
    outcome = {
        "date": str(target_date),
        "in_source": None,   # "real_file" / "live_fallback" / "unavailable"
        "as_imported": False,
        "bq_imported": False,
        "fallback_counts": None,
        "errors": [],
    }

    results = fetch_daily_files.fetch_all(target_date, include_ca=False)

    # --- IN (creates the solicitations/lines) ---
    if results.get("in"):
        try:
            log.info(f"Importing real IN file: {results['in']}")
            import_in.run(str(results["in"]))
            outcome["in_source"] = "real_file"
        except Exception as e:
            log.error(f"IN import failed even though the file was downloaded: {e}")
            outcome["errors"].append(f"IN import failed: {e}")
    else:
        log.warning(
            f"IN file for {target_date} wasn't available from DIBBS's archive yet. "
            f"Falling back to DIBBS's live 'Recent RFQs' listing for this date..."
        )
        conn = db.get_connection()
        try:
            counts = rfq_live_fallback.run_fallback(conn, target_date)
        finally:
            conn.close()
        outcome["fallback_counts"] = counts
        if counts["rows_found"] > 0:
            outcome["in_source"] = "live_fallback"
            log.info(
                f"Live fallback used for {target_date}: found {counts['rows_found']} row(s) - "
                f"{counts['solicitations_new']} new solicitation(s), "
                f"{counts['solicitations_seen']} already known, "
                f"{counts['lines_new']} new line(s), {counts['errors']} error(s). "
                f"These will be filled in/confirmed automatically once the real IN file arrives."
            )
        else:
            outcome["in_source"] = "unavailable"
            log.warning(
                f"Live fallback found NOTHING for {target_date} either - DIBBS itself may be "
                f"down or that date genuinely has no solicitations. Nothing was saved for IN today."
            )

    # --- AS (approved sources - bundled inside BQ's zip) ---
    if results.get("as"):
        try:
            log.info(f"Importing AS file: {results['as']}")
            import_as.run(str(results["as"]))
            outcome["as_imported"] = True
        except Exception as e:
            log.error(f"AS import failed: {e}")
            outcome["errors"].append(f"AS import failed: {e}")
    else:
        log.warning(f"AS file for {target_date} wasn't available - will fill in automatically once it is.")

    # --- BQ (quantity/price/etc, joined onto lines already saved above) ---
    if results.get("bq"):
        try:
            log.info(f"Importing BQ file: {results['bq']}")
            import_bq.run(str(results["bq"]))
            outcome["bq_imported"] = True
        except Exception as e:
            log.error(f"BQ import failed: {e}")
            outcome["errors"].append(f"BQ import failed: {e}")
    else:
        log.warning(f"BQ file for {target_date} wasn't available - will fill in automatically once it is.")

    ok = outcome["in_source"] in ("real_file", "live_fallback") and not outcome["errors"]
    log.info(f"--- Pipeline for {target_date} finished ({'OK' if ok else 'completed with issues'}) ---")
    outcome["ok"] = ok
    return outcome


def run_nightly():
    """The nightly entry point: always runs for YESTERDAY's date."""
    target_date = date.today() - timedelta(days=1)
    log.info(f"=== Nightly run starting (run date: {date.today()}, target: {target_date}) ===")
    outcome = run_for_date(target_date)
    log.info(f"=== Nightly run {'completed successfully' if outcome['ok'] else 'completed WITH ISSUES'} ===")
    return outcome["ok"]


def run_today_live_check():
    """
    The INTRADAY entry point - meant to run every few hours during the day,
    not just once at night. DIBBS's official archive file for TODAY never
    exists yet (it only shows up as "yesterday's file" the next morning),
    but DIBBS's own live "Recent RFQs" page already shows today's
    solicitations as they get posted throughout the day. So instead of
    waiting until tomorrow night to see today's RFQs, this checks the live
    listing for TODAY right now and saves whatever's there - through the
    same safe upsert keys as everything else, so running this every few
    hours (and then having tonight's real run see the same solicitations
    again) never creates duplicates, it just confirms/updates them.

    This is intentionally lightweight - no big zip downloads, just the
    live RFQ listing pages for one date - so it's fine to run often.
    """
    target_date = date.today()
    log.info(f"=== Intraday live-RFQ check starting for {target_date} ===")
    conn = db.get_connection()
    try:
        counts = rfq_live_fallback.run_fallback(conn, target_date)
    finally:
        conn.close()
    log.info(
        f"Intraday check for {target_date}: found {counts['rows_found']} row(s) - "
        f"{counts['solicitations_new']} new, {counts['solicitations_seen']} already known, "
        f"{counts['lines_new']} new line(s), {counts['errors']} error(s)."
    )
    log.info(f"=== Intraday live-RFQ check finished for {target_date} ===")
    return counts


if __name__ == "__main__":
    if "--today-only" in sys.argv:
        run_today_live_check()
        sys.exit(0)
    ok = run_nightly()
    sys.exit(0 if ok else 1)
