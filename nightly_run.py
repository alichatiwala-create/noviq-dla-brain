"""
nightly_run.py
---------------
This is the script that will eventually run automatically every night,
with nobody watching it. It's built now so the scheduling and pipeline
plumbing is ready - the one piece still missing is fetch_todays_files(),
which needs a real URL/login before it can be written for real.

WHAT THIS DOES RIGHT NOW:
  1. Calls fetch_todays_files() - currently a placeholder that raises
     NotImplementedError with a clear message, since we don't yet know
     where IN/AS/BQ actually come from.
  2. Once that's filled in, it will run import_in.py, import_as.py,
     and import_bq.py automatically, in the correct order, against
     whatever files were just fetched.
  3. Everything is logged to nightly_run.log (with a timestamp) instead
     of just printing to a terminal nobody is watching overnight - so
     if something breaks at 2 AM, there's a record to check in the
     morning instead of just silence.

HOW TO FILL IN fetch_todays_files() ONCE WE KNOW THE SOURCE:
  - If it's a plain public URL: use `requests` (or Playwright if the
    site needs a real browser, same as our DIBBS RFQ scraper) to
    download the file, save it with today's date in the filename,
    return the three file paths.
  - If it needs a DIBBS vendor login: read the username/password from
    environment variables (same pattern as NOVIQ_DB_PASSWORD - never
    hardcoded), use Playwright to log in, navigate, and download.

HOW TO SCHEDULE THIS TO RUN EVERY NIGHT (Windows Task Scheduler):
  See the "Automating the nightly run" section in README.md.
"""

import sys
import logging
from datetime import date, timedelta
from pathlib import Path

import import_in
import import_as
import import_bq
import fetch_daily_files

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


def fetch_todays_files():
    """
    Fetches YESTERDAY's IN and BQ (which bundles AS inside it) - not
    today's. CONFIRMED from two real runs: DIBBS never has the current
    calendar day's file available (it 404s every time we've tried),
    while the previous day's file has worked every single time. This
    matches the standard "nightly batch job processes yesterday's
    completed data" pattern anyway - a job running at 2 AM shouldn't
    expect today's file to exist when today has barely started.

    Raises if any of the three expected files didn't come through, so
    a broken night fails loudly in the log rather than silently
    importing nothing or partial data.
    """
    target_date = date.today() - timedelta(days=1)
    results = fetch_daily_files.fetch_all(target_date, include_ca=False)

    missing = [k for k in ("in", "as", "bq") if not results.get(k)]
    if missing:
        raise RuntimeError(f"Failed to fetch: {', '.join(missing)} for {target_date}")

    return results["in"], results["as"], results["bq"]


def run_nightly():
    log.info(f"=== Nightly run starting (run date: {date.today()}) ===")

    try:
        in_path, as_path, bq_path = fetch_todays_files()
    except Exception as e:
        log.error(f"Fetching yesterday's files failed: {e}")
        log.info("=== Nightly run FAILED (fetch step) ===")
        return False

    success = True

    try:
        log.info(f"Importing IN file: {in_path}")
        import_in.run(str(in_path))
    except Exception as e:
        log.error(f"IN import failed: {e}")
        success = False

    try:
        log.info(f"Importing AS file: {as_path}")
        import_as.run(str(as_path))
    except Exception as e:
        log.error(f"AS import failed: {e}")
        success = False

    try:
        log.info(f"Importing BQ file: {bq_path}")
        import_bq.run(str(bq_path))
    except Exception as e:
        log.error(f"BQ import failed: {e}")
        success = False

    log.info(f"=== Nightly run {'completed successfully' if success else 'completed WITH ERRORS'} ===")
    return success


if __name__ == "__main__":
    ok = run_nightly()
    sys.exit(0 if ok else 1)
