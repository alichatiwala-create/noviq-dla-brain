"""
Test script: look up DLA "Current Awards" by a specific NSN instead of by date.

Why: the full "Awards by Post Date" page returns thousands of records a day
across all of DLA - not practical to scrape in bulk every night. DIBBS's own
search menu has a category literally called "Awards - NSN", which should let
us pull award history for just the NSNs already in our dashboard. This script
checks whether that works and saves the result so we can build the real
importer from it.

HOW TO USE:
1. Open this file in Notepad (or any editor).
2. Look at the NSN_LIST below. Replace those example NSNs with 2-3 REAL NSNs
   you can see on your dashboard right now (copy them straight from the NSN
   column in the table). Standard NSNs are 13 characters, e.g. 5330012345678.
3. Save the file, then in PowerShell:
       cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
       python ca_by_nsn_test.py
4. Send me everything it prints, plus the .html files it saves into debug_html\
"""

import re
from pathlib import Path
from playwright.sync_api import sync_playwright

# ---- EDIT THIS: put 2-3 real NSNs from your dashboard here ----
NSN_LIST = [
    "5975005325870",   # <-- replace with a real NSN from your dashboard
    "5965017237771",   # <-- replace with a real NSN from your dashboard
]
# -----------------------------------------------------------------

OUT_DIR = Path("debug_html")
OUT_DIR.mkdir(exist_ok=True)

FIELD_PATTERN = re.compile(
    r'id="[^"]*_lbl(AwardBasicNumber|DeliveryOrder|Cage|TotalContactPrice|'
    r'AwardDate|PostedDate|Nsn|Nomenclature|PurchaseRequest|Solicitation)"'
    r'[^>]*>(.*?)</span>',
    re.DOTALL,
)
TAG_STRIP = re.compile(r"<[^>]+>")


def accept_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "notice and consent" in page.content().lower():
        print("  Hit the DoD warning page - clicking through it...")
        try:
            page.click("input[type=submit], button[type=submit]", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  (couldn't auto-click warning page: {e})")


def parse_rows(html):
    rows = []
    current = {}
    for match in FIELD_PATTERN.finditer(html):
        field, value = match.group(1), TAG_STRIP.sub("", match.group(2)).strip()
        if field == "AwardBasicNumber" and current:
            rows.append(current)
            current = {}
        current[field] = value
    if current:
        rows.append(current)
    return rows


def investigate(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    for nsn in NSN_LIST:
        url = f"https://www.dibbs.bsm.dla.mil/Awards/AwdRecs.aspx?Category=nsn&Value={nsn}"
        print(f"\nLooking up awards for NSN {nsn} ...")
        try:
            page.goto(url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  Failed to load: {e}")
            continue

        accept_warning_if_present(page)

        html = page.content()
        safe_name = re.sub(r"[^A-Za-z0-9]", "", nsn)
        html_path = OUT_DIR / f"ca_nsn_{safe_name}.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"  Saved {html_path}")

        rec_match = re.search(r"Records Found:\s*<strong>\s*(\d+)", html)
        if rec_match:
            print(f"  Records Found: {rec_match.group(1)}")
        elif "no matching" in html.lower() or "no records" in html.lower():
            print("  Page says no records found for this NSN.")
        else:
            print("  (Couldn't find a 'Records Found' line - check the saved HTML.)")

        rows = parse_rows(html)
        if rows:
            print(f"  Parsed {len(rows)} award row(s):")
            for r in rows[:5]:
                print(
                    f"    Award# {r.get('AwardBasicNumber','?')} | "
                    f"CAGE {r.get('Cage','?')} | "
                    f"Total {r.get('TotalContactPrice','?')} | "
                    f"Award Date {r.get('AwardDate','?')} | "
                    f"Posted {r.get('PostedDate','?')} | "
                    f"NSN {r.get('Nsn','?')}"
                )
            if len(rows) > 5:
                print(f"    ...and {len(rows) - 5} more.")
        else:
            print("  No row data parsed - the page may not have a results table for this NSN.")

    context.close()
    browser.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        investigate(p)
    print("\nDone. Send me everything printed above, plus the ca_nsn_*.html files in debug_html\\.")
