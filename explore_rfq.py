"""
explore_rfq.py
--------------
ONE-TIME exploration script - not part of the automated pipeline.

This just opens DIBBS's live "Recent RFQs" page and saves what it looks
like (the HTML) into debug_html/, so Claude can see its real structure
and build the live fallback importer around it. Nothing is saved to the
database - this only writes one file to your project folder.

HOW TO RUN:
    cd "$env:USERPROFILE\OneDrive\Documents\noviq-dla-brain\noviq-dla-brain"
    python explore_rfq.py

After it finishes, just tell Claude it's done - no need to copy/paste
anything, Claude can read the saved file directly from your project folder.
"""

from playwright.sync_api import sync_playwright

LIST_URL = "https://www.dibbs.bsm.dla.mil/RFQ/RFQDates.aspx?category=recent"


def accept_warning_if_present(page):
    if "dodwarning" in page.url.lower() or "notice and consent" in page.content().lower():
        print("  Hit the DoD warning page - clicking through it...")
        try:
            page.click("input[type=submit], button[type=submit]", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  (couldn't auto-click warning page: {e})")


def dump(page, name):
    html = page.content()
    path = f"debug_html/{name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Saved {path} ({len(html)} chars), current URL: {page.url}")
    return html


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print(f"Opening {LIST_URL} ...")
        page.goto(LIST_URL, timeout=60000)
        page.wait_for_load_state("networkidle", timeout=20000)
        accept_warning_if_present(page)
        page.wait_for_timeout(1500)

        dump(page, "rfqdates_recent")

        # Try to find and click something that looks like a date link/row,
        # so we also capture whatever RESULTS page it leads to (the actual
        # list of solicitations for a date).
        clicked = False
        for sel in ["a[href*='RFQRecs']", "a[href*='Recs']", "table a"]:
            try:
                links = page.locator(sel)
                count = links.count()
                if count > 0:
                    print(f"Found {count} link(s) matching {sel!r} - clicking the first one...")
                    href = links.first.get_attribute("href")
                    print(f"  href = {href}")
                    links.first.click()
                    page.wait_for_load_state("networkidle", timeout=20000)
                    accept_warning_if_present(page)
                    dump(page, "rfqdates_recent_results")
                    clicked = True
                    break
            except Exception as e:
                print(f"  ({sel} didn't work: {e})")

        if not clicked:
            print("Didn't find an obvious date link to click - rfqdates_recent.html "
                  "alone should still show the page structure/links.")

        browser.close()
        print("\nDone. Files saved in the debug_html folder.")


if __name__ == "__main__":
    main()
