"""
rfq_live_fallback.py
---------------------
This is the fallback for the exact problem you described: some nights
DIBBS's daily archive file (IN*.txt) isn't there yet, but the same
solicitations ARE already visible on DIBBS's own live "Recent RFQs"
listing page. Instead of the whole nightly run failing for that day,
this scrapes that live page for the missing date and saves the same
solicitations straight into the database - through the EXACT SAME
save-functions import_in.py itself uses (upsert_solicitation /
upsert_solicitation_line), so there is no separate/parallel logic that
could disagree with it.

WHY THIS CAN'T CREATE DUPLICATES:
    Both this script and import_in.py identify a solicitation the same
    way - by its solicitation_number (e.g. "SPE1C126Q0360"), and each
    line within it by (solicitation_number, purchase_request_number).
    import_in.py's own upsert_solicitation()/upsert_solicitation_line()
    functions already check "do we have this key yet?" before
    inserting - if yes, they UPDATE the existing row instead of
    inserting a new one. So:
      1. Tonight: the real file is missing, this fallback scrapes the
         live listing and creates the solicitation/line rows.
      2. In a few days: the real IN file for that date finally shows up
         and gets imported normally (either automatically that night,
         or you re-run it by hand). import_in.py looks up those same
         keys, finds them ALREADY THERE (from step 1), and updates them
         in place (filling in anything the live page didn't show, like
         buyer code) - it does NOT insert a second copy.
    No new tables, no separate identity scheme - just the same keys,
    used twice, the second time as an update.

WHAT IT CAN AND CAN'T FILL IN:
    The live listing page shows: solicitation number, NSN or part
    number, nomenclature, purchase request number, quantity, issued
    date, and return-by date - enough to create the solicitation and
    its line(s), which is the actual thing you wanted to not miss.
    It does NOT show a few things the real IN file has (buyer code,
    AMSC code, unit of issue, exact set-aside percentage) - those are
    simply left blank until the real file arrives later and fills
    them in, the same way any other field gets updated.

HOW THIS GETS USED:
    You don't run this by hand - nightly_run.py calls it automatically,
    only on a night when the real IN file didn't come through. See
    nightly_run.log afterwards; it will say plainly whether tonight
    used the real file or this fallback (and how many solicitations it
    found), so you always know which one you're looking at.
"""

import logging
import re
import time
from datetime import date

from playwright.sync_api import sync_playwright

import import_in  # reuse its exact upsert_solicitation / upsert_solicitation_line

log = logging.getLogger("rfq_live_fallback")

RECS_URL = "https://www.dibbs.bsm.dla.mil/RFQ/RfqRecs.aspx?category=post&TypeSrch=dt&Value={mmddyyyy}"
MAX_PAGES = 30  # safety cap - a normal day has 1-10 pages of ~10 rows each


def accept_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "notice and consent" in page.content().lower():
        log.info("  Hit the DoD warning page - clicking through it...")
        try:
            page.click("input[type=submit], button[type=submit]", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            log.warning(f"  (couldn't auto-click warning page: {e})")


def _find_grid_table_html(html: str):
    """
    Finds the <table id="...grdRfqSearch"...> element and returns its
    FULL html (matching nested <table> tags correctly, since the grid
    contains a smaller pagination table inside one of its rows) - or
    None if the grid isn't on the page at all (e.g. zero results for
    that date).
    """
    marker = html.find('id="ctl00_cph1_grdRfqSearch"')
    if marker == -1:
        return None
    tbl_start = html.rfind("<table", 0, marker)
    if tbl_start == -1:
        return None

    depth = 0
    i = tbl_start
    open_re = re.compile(r"<table\b")
    close_re = re.compile(r"</table>")
    while True:
        open_m = open_re.search(html, i)
        close_m = close_re.search(html, i)
        if close_m is None:
            return None  # malformed - give up rather than guess
        if open_m and open_m.start() < close_m.start():
            depth += 1
            i = open_m.end()
        else:
            depth -= 1
            i = close_m.end()
            if depth == 0:
                return html[tbl_start:i]


def _parse_rows_from_grid_html(grid_html: str):
    """
    Pulls out every data row (class BgWhite/BgSilver - the header row
    and the pagination-footer row use different classes and are
    naturally skipped) and returns a list of dicts shaped EXACTLY like
    import_in.parse_file()'s rows, so they can go straight into
    import_in.upsert_solicitation()/upsert_solicitation_line().
    """
    rows_out = []
    for row_html in re.finditer(r'<tr class="Bg(?:White|Silver)".*?</tr>', grid_html, re.DOTALL):
        row = row_html.group(0)

        nsn_m = re.search(r'lblNsn"[^>]*>\s*<a[^>]*>([^<]+)</a>', row)
        nomen_m = re.search(r'lblNomenclature"[^>]*>([^<]*)</span>', row)
        sol_m = re.search(r'lblSolicitation"[^>]*>\s*<a[^>]*>([^<]+)</a>', row)
        pr_m = re.search(r'lblPr"[^>]*>([^<]*)<br\s*/?>\s*QTY:\s*([\d,]*)', row)
        issued_m = re.search(r'lblIssued"[^>]*>([^<]*)</span>', row)
        return_m = re.search(r'lblReturnBy"[^>]*>([^<]*)</span>', row)

        if not sol_m:
            continue  # not a real data row - skip rather than guess

        solicitation_number = sol_m.group(1).replace("-", "").strip()
        purchase_request = pr_m.group(1).strip() if pr_m else ""
        qty = pr_m.group(2).replace(",", "").strip() if pr_m else ""
        nomenclature = (nomen_m.group(1).strip() if nomen_m else "")[:21]
        is_setaside = "iconsb" in row.lower()

        nsn_or_part = nsn_m.group(1).replace("-", "").strip() if nsn_m else ""
        if nsn_or_part.isdigit() and len(nsn_or_part) == 13:
            item_type_indicator = "1"
        else:
            item_type_indicator = "2"

        return_by_raw = return_m.group(1).strip() if return_m else ""
        return_by_slashed = _mmddyyyy_dash_to_slashed_2digit_year(return_by_raw)

        rows_out.append({
            "solicitation": solicitation_number,
            "nsn_part_raw": nsn_or_part,
            "purchase_request": purchase_request,
            "return_by_date": return_by_slashed,
            "file_name": "LIVE_FALLBACK",
            "qty": qty,
            "unit_issue": "",       # not shown on the live listing - real IN file will fill this in later
            "nomenclature": nomenclature,
            "buyer_code": "",       # not shown on the live listing - real IN file will fill this in later
            "amsc": "",             # not shown on the live listing - real IN file will fill this in later
            "item_type_indicator": item_type_indicator,
            "sb_setaside_indicator": "Y" if is_setaside else "",
            "sb_setaside_pct": "",  # exact % not shown - real IN file will fill this in later
        })

    return rows_out


def _mmddyyyy_dash_to_slashed_2digit_year(value: str):
    """Converts the live page's '09-25-2026' into '09/25/26', the exact
    format import_in.py's own parse_mmddyy() already knows how to read -
    so we reuse that function unchanged instead of writing a second
    date parser that could disagree with it."""
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", value or "")
    if not m:
        return ""
    mm, dd, yyyy = m.groups()
    return f"{mm}/{dd}/{yyyy[2:]}"


def fetch_live_rows_for_date(target_date: date):
    """
    Scrapes every page of DIBBS's live "Recent RFQs" listing for one
    date and returns all rows found, in import_in.py's row-dict shape.
    Returns an empty list (not an error) if that date genuinely has no
    solicitations posted - that's a real, valid outcome, not a failure.
    """
    url = RECS_URL.format(mmddyyyy=target_date.strftime("%m-%d-%Y"))
    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        log.info(f"  Opening live RFQ listing for {target_date}: {url}")
        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle", timeout=20000)
        accept_warning_if_present(page)
        page.wait_for_timeout(1000)

        for page_num in range(1, MAX_PAGES + 1):
            html = page.content()
            grid_html = _find_grid_table_html(html)
            if grid_html is None:
                if page_num == 1:
                    log.info(f"  No RFQ listing found for {target_date} (likely zero solicitations that day).")
                break

            rows = _parse_rows_from_grid_html(grid_html)
            all_rows.extend(rows)
            log.info(f"  Page {page_num}: found {len(rows)} row(s) (running total: {len(all_rows)}).")

            next_target = f"Page${page_num + 1}"
            if next_target not in grid_html:
                break  # no more pages

            try:
                page.evaluate(
                    "__doPostBack(arguments[0], arguments[1])",
                    ["ctl00$cph1$grdRfqSearch", f"Page${page_num + 1}"],
                )
                page.wait_for_load_state("networkidle", timeout=20000)
                page.wait_for_timeout(500)
            except Exception as e:
                log.warning(f"  Couldn't move to page {page_num + 1}, stopping here: {e}")
                break

        browser.close()

    return all_rows


def run_fallback(conn, target_date: date):
    """
    The main entry point nightly_run.py calls. Scrapes the live listing
    for target_date and saves every row through import_in.py's own
    upsert functions (same identity keys = no duplicates, ever).
    Returns a dict of counts for the log.
    """
    rows = fetch_live_rows_for_date(target_date)

    counts = {"rows_found": len(rows), "solicitations_new": 0, "solicitations_seen": 0,
              "lines_new": 0, "lines_updated": 0, "lines_return_changed": 0, "errors": 0}

    if not rows:
        return counts

    cur = conn.cursor()
    for row in rows:
        try:
            sol_result = import_in.upsert_solicitation(cur, row["solicitation"], target_date, target_date)
            counts["solicitations_new" if sol_result == "NEW" else "solicitations_seen"] += 1

            line_result = import_in.upsert_solicitation_line(cur, row, target_date)
            if line_result == "NEW":
                counts["lines_new"] += 1
            elif line_result == "RETURN_DATE_CHANGED":
                counts["lines_return_changed"] += 1
            else:
                counts["lines_updated"] += 1

            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            counts["errors"] += 1
            log.error(f"  Error saving live-scraped row for solicitation {row.get('solicitation')}: {e}")

    cur.close()
    return counts


if __name__ == "__main__":
    # Manual test: `python rfq_live_fallback.py 2026-09-21` - scrapes and
    # saves that one date right now, without waiting for a real nightly run.
    import sys
    import db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) != 2:
        print("Usage: python rfq_live_fallback.py YYYY-MM-DD")
        sys.exit(1)

    target = date.fromisoformat(sys.argv[1])
    conn = db.get_connection()
    try:
        result = run_fallback(conn, target)
    finally:
        conn.close()

    print("\n---- Summary ----")
    for k, v in result.items():
        print(f"{k}: {v}")
